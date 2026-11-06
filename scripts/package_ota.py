#!/usr/bin/env python3
"""Package a BL602 raw application binary using the pinned SDK OTA format.

The caller must provide the target-specific 4096-byte bootinfo header emitted
by the Bouffalo image builder. This tool does not create flash headers, alter
partition tables, erase flash, or upload anything.
"""

from __future__ import annotations

import argparse
import hashlib
import lzma
from pathlib import Path
import struct
import sys


OTA_HEADER_SIZE = 512
BOOTINFO_SIZE = 4096
OTA_MAGIC = b"BL60X_OTA_Ver1.0"
BFNP_MAGIC = b"BFNP"


class PackageError(ValueError):
    pass


def fixed_version(value: str, label: str) -> bytes:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as err:
        raise PackageError(f"{label} must contain ASCII only") from err
    if not encoded or len(encoded) > 15 or b"\0" in encoded:
        raise PackageError(f"{label} must be 1-15 ASCII bytes")
    return encoded.ljust(16, b"\0")


def build_ota(bootinfo: bytes, raw_application: bytes, hw_version: str, sw_version: str,
              slot_capacity: int) -> tuple[bytes, bytes]:
    if len(bootinfo) != BOOTINFO_SIZE:
        raise PackageError(f"bootinfo must be exactly {BOOTINFO_SIZE} bytes")
    if not bootinfo.startswith(BFNP_MAGIC):
        raise PackageError("bootinfo does not start with the BL602 BFNP image magic")
    if not raw_application:
        raise PackageError("application binary is empty")
    return build_ota_image(bootinfo + raw_application, hw_version, sw_version, slot_capacity)


def build_ota_image(firmware_image: bytes, hw_version: str, sw_version: str,
                    slot_capacity: int) -> tuple[bytes, bytes]:
    """Wrap a complete BL602 firmware partition payload in the SDK OTA header."""
    if len(firmware_image) < BOOTINFO_SIZE or not firmware_image.startswith(BFNP_MAGIC):
        raise PackageError("firmware image must start with a complete BL602 BFNP header")
    return build_ota_body(firmware_image, b"RAW ", len(firmware_image), hw_version,
                          sw_version, slot_capacity)


def build_ota_xz_image(firmware_image: bytes, hw_version: str, sw_version: str,
                       slot_capacity: int) -> tuple[bytes, bytes]:
    """Compress and wrap a BL602 firmware partition payload with the SDK OTA header."""
    if len(firmware_image) < BOOTINFO_SIZE or not firmware_image.startswith(BFNP_MAGIC):
        raise PackageError("firmware image must start with a complete BL602 BFNP header")
    compressed = lzma.compress(
        firmware_image,
        format=lzma.FORMAT_XZ,
        check=lzma.CHECK_CRC32,
        filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 32768}],
    )
    return build_ota_body(compressed, b"XZ  ", 0xFFFFFFFF, hw_version,
                          sw_version, slot_capacity)


def build_ota_body(body: bytes, body_type: bytes, original_length: int, hw_version: str,
                   sw_version: str, slot_capacity: int) -> tuple[bytes, bytes]:
    if len(body_type) != 4:
        raise PackageError("OTA body type must be exactly 4 bytes")
    if len(body) > slot_capacity:
        raise PackageError(f"payload is {len(body)} bytes; inactive slot holds only {slot_capacity}")

    header = bytearray()
    header.extend(OTA_MAGIC)
    header.extend(body_type)
    header.extend(struct.pack("<I", len(body)))
    header.extend(bytes(range(1, 9)))
    header.extend(fixed_version(hw_version, "hardware version"))
    header.extend(fixed_version(sw_version, "software version"))
    header.extend(hashlib.sha256(body).digest())
    header.extend(struct.pack("<I", original_length))
    if len(header) > OTA_HEADER_SIZE:
        raise AssertionError("SDK OTA header fields exceed 512 bytes")
    header.extend(b"\xff" * (OTA_HEADER_SIZE - len(header)))
    return bytes(header) + body, body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootinfo", type=Path, required=True, help="target-specific 4096-byte BFNP header")
    parser.add_argument("--application", type=Path, required=True, help="raw application .bin (without bootinfo)")
    parser.add_argument("--hardware-version", required=True)
    parser.add_argument("--software-version", required=True)
    parser.add_argument("--slot-capacity", type=lambda s: int(s, 0), required=True,
                        help="verified inactive firmware slot size in bytes")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        package, body = build_ota(args.bootinfo.read_bytes(), args.application.read_bytes(),
                                  args.hardware_version, args.software_version, args.slot_capacity)
        if args.output.exists():
            raise PackageError(f"refusing to overwrite {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(package)
        print(f"OTA package: {args.output} ({len(package)} bytes)")
        print(f"Firmware payload: {len(body)} bytes; SHA-256 {hashlib.sha256(body).hexdigest()}")
    except (OSError, PackageError) as err:
        print(f"OTA packaging failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
