#!/usr/bin/env python3
"""Read and verify a BL602 SPI flash dump through the ROM UART ISP.

This deliberately has no flash erase or flash program operation. It loads
Bouffalo's eflash loader into SRAM, then uses only its flash-ID, read, and
SHA-256 commands. Keep mains disconnected while using a serial adapter.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
from pathlib import Path
import struct
import sys
import time

import serial


ROM_BAUD = 115200
LOADER_BAUD = 115200
MAX_FRAME = 4096
MAX_FLASH_READ = 8192
READ_CHUNK = 4096
ALLOWED_LOADER_COMMANDS = frozenset({0x32, 0x36, 0x3D})


class ProtocolError(RuntimeError):
    pass


def build_rom_packet(command: int, payload: bytes = b"") -> bytes:
    if command not in {0x10, 0x11, 0x17, 0x18, 0x19, 0x1A}:
        raise ValueError(f"not an allowed ROM boot command: 0x{command:02x}")
    if len(payload) > MAX_FRAME - 4:
        raise ValueError("ROM frame exceeds the documented 4096-byte limit")
    return bytes((command, 0, len(payload) & 0xFF, len(payload) >> 8)) + payload


def build_loader_packet(command: int, payload: bytes = b"") -> bytes:
    if command not in ALLOWED_LOADER_COMMANDS:
        raise ValueError(f"not an allowed read-only loader command: 0x{command:02x}")
    if len(payload) > MAX_FLASH_READ + 8:
        raise ValueError("loader packet exceeds the read limit")
    # Bouffalo documents checksum zero as disabling the optional checksum.
    return bytes((command, 0, len(payload) & 0xFF, len(payload) >> 8)) + payload


def parse_jedec_capacity(flash_id: bytes) -> int:
    """Decode the standard three-byte JEDEC ID capacity exponent."""
    if len(flash_id) < 3:
        raise ProtocolError(f"short JEDEC ID: {flash_id.hex()}")
    exponent = flash_id[2]
    if exponent < 17 or exponent > 26:
        raise ProtocolError(f"unknown or implausible JEDEC capacity byte: 0x{exponent:02x}")
    return 1 << exponent


class BL602Link:
    def __init__(self, port: serial.SerialBase):
        self.port = port

    def _read_exact(self, count: int, timeout: float = 5.0) -> bytes:
        deadline = time.monotonic() + timeout
        result = bytearray()
        while len(result) < count and time.monotonic() < deadline:
            part = self.port.read(count - len(result))
            if part:
                result.extend(part)
        if len(result) != count:
            raise ProtocolError(f"timed out waiting for {count} bytes (received {len(result)})")
        return bytes(result)

    def _response(self, with_payload: bool = False) -> bytes:
        ack = self._read_exact(2)
        if ack[:2] != b"OK":
            if ack == b"FL":
                code = self._read_exact(2)
                raise ProtocolError(f"device returned loader error 0x{int.from_bytes(code, 'little'):04x}")
            raise ProtocolError(f"unexpected response prefix {ack.hex()}")
        if not with_payload:
            return b""
        size = int.from_bytes(self._read_exact(2), "little")
        if size > MAX_FLASH_READ:
            raise ProtocolError(f"device response exceeds 8 KiB limit: {size}")
        return self._read_exact(size, timeout=max(5.0, size / 1024))

    def rom_command(self, command: int, payload: bytes = b"", *, response_payload: bool = False) -> bytes:
        self.port.write(build_rom_packet(command, payload))
        self.port.flush()
        return self._response(response_payload)

    def loader_command(self, command: int, payload: bytes = b"", *, response_payload: bool = False) -> bytes:
        self.port.write(build_loader_packet(command, payload))
        self.port.flush()
        return self._response(response_payload)

    def handshake(self) -> None:
        last_rx = b""
        for attempt in range(3):
            self.port.reset_input_buffer()
            self.port.write(b"\x55" * 70)
            self.port.flush()
            deadline = time.monotonic() + 1.0
            received = bytearray()
            while time.monotonic() < deadline:
                data = self.port.read(64)
                if data:
                    received.extend(data)
                    if b"OK" in received:
                        time.sleep(0.02)
                        return
            last_rx = bytes(received)
            if attempt < 2:
                time.sleep(0.1)
        raise ProtocolError(
            "BL602 ROM did not acknowledge UART synchronization after 3 attempts "
            f"(received {last_rx[:16].hex() or 'no data'})"
        )

    def load_eflash_loader(self, loader_path: Path) -> None:
        image = loader_path.read_bytes()
        if len(image) < 192 or image[:4] != b"BFNP":
            raise ProtocolError("unexpected vendor eflash-loader image header")
        segment_count = struct.unpack_from("<I", image, 120)[0]
        if segment_count != 1:
            raise ProtocolError(f"expected one RAM loader segment, found {segment_count}")
        address, size = struct.unpack_from("<II", image, 176)
        if address != 0x22010000 or size != len(image) - 192:
            raise ProtocolError(
                f"unexpected RAM loader segment address/size: 0x{address:08x}/{size}, image {len(image)}"
            )
        # Read ROM boot information before loading any image. The protocol
        # response is length-checked so an unexpected target stops here.
        boot_info = self.rom_command(0x10, response_payload=True)
        if len(boot_info) != 20:
            raise ProtocolError(f"unexpected BL602 boot-info length: {len(boot_info)}")
        # Loader is an unencrypted, unsigned RAM image with one segment.
        self.rom_command(0x11, image[:176])
        self.rom_command(0x17, image[176:192], response_payload=True)
        offset = 192
        while offset < len(image):
            chunk = image[offset : offset + 2048]
            self.rom_command(0x18, chunk)
            offset += len(chunk)
        self.rom_command(0x19)
        self.rom_command(0x1A)
        time.sleep(0.2)
        self.port.baudrate = LOADER_BAUD
        self.handshake()

    def flash_id(self) -> bytes:
        return self.loader_command(0x36, response_payload=True)

    def read_flash(self, size: int) -> bytes:
        chunks = bytearray()
        for address in range(0, size, READ_CHUNK):
            count = min(READ_CHUNK, size - address)
            if count > MAX_FLASH_READ:
                raise AssertionError("read chunk unexpectedly exceeds protocol limit")
            args = struct.pack("<II", address, count)
            chunks.extend(self.loader_command(0x32, args, response_payload=True))
            if len(chunks) != address + count:
                raise ProtocolError("flash read returned an unexpected byte count")
        return bytes(chunks)

    def flash_sha256(self, size: int) -> bytes:
        return self.loader_command(0x3D, struct.pack("<II", 0, size), response_payload=True)


def private_output_path(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out_dir, 0o700)
    stamp = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f%z")
    output = out_dir / f"minir3-bl602-{stamp}.bin"
    if output.exists() or output.with_suffix(output.suffix + ".partial").exists():
        raise FileExistsError(f"refusing to overwrite an existing backup: {output}")
    return output


def run_backup(port_name: str, out_dir: Path, loader_path: Path) -> Path:
    output = private_output_path(out_dir)
    partial = output.with_suffix(output.suffix + ".partial")
    # The serial adapter may assert/deassert modem-control lines while opening
    # and closing the port. Keep DTR/RTS physically disconnected from the
    # target; reset is performed manually with GPIO8 already strapped high.
    with serial.Serial(port_name, ROM_BAUD, timeout=0.2, write_timeout=3, rtscts=False, dsrdtr=False) as port:
        link = BL602Link(port)
        link.handshake()
        link.load_eflash_loader(loader_path)
        flash_id = link.flash_id()
        size = parse_jedec_capacity(flash_id)
        print(f"JEDEC ID: {flash_id.hex()} ({size} bytes)")
        device_hash = link.flash_sha256(size)
        if len(device_hash) != 32:
            raise ProtocolError(f"unexpected device SHA-256 length: {len(device_hash)}")
        data = link.read_flash(size)
        local_hash = hashlib.sha256(data).digest()
        if local_hash != device_hash:
            with partial.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(partial, 0o600)
            raise ProtocolError(
                f"backup hash mismatch: device {device_hash.hex()}, local {local_hash.hex()}; "
                f"partial dump retained at {partial}"
            )
        with partial.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(partial, 0o600)
        partial.replace(output)
        print(f"Verified backup: {output}")
        print(f"SHA-256: {local_hash.hex()}")
        return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument(
        "--loader",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "openbl602-reference/tools/flash_tool/chips/bl602/eflash_loader/eflash_loader_rc32m.bin",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "bl602-flash-backups",
    )
    args = parser.parse_args()
    try:
        run_backup(args.port, args.out_dir, args.loader)
    except (OSError, ProtocolError, serial.SerialException) as err:
        print(f"backup failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
