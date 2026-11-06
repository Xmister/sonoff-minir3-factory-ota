#!/usr/bin/env python3
"""Build an SDK-format BL602 OTA image using a verified MINIR3 flash backup.

This creates a local package only. It never opens a serial port or writes flash.
The original active boot header supplies the MINIR3's flash/startup settings.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inspect_partition_table import inspect  # noqa: E402
from package_ota import build_ota, build_ota_xz_image, PackageError  # noqa: E402


BOOT_HEADER_SIZE = 176
FW_PARTITION_HEADER_SIZE = 0x1000


def _verified_minir3_layout(backup: bytes) -> tuple[bytes, int, int]:
    table, _ = inspect(backup)
    fw = next((part for part in table.partitions if part.name == "FW"), None)
    if fw is None:
        raise PackageError("verified backup has no FW partition")
    active_offset = fw.address[fw.active_index]
    active_capacity = fw.max_len[fw.active_index]
    inactive_capacity = fw.max_len[1 - fw.active_index]
    if not active_offset or not active_capacity or not inactive_capacity:
        raise PackageError("partition table does not describe an active and inactive firmware slot")

    original_header = backup[active_offset : active_offset + BOOT_HEADER_SIZE]
    if len(original_header) != BOOT_HEADER_SIZE or original_header[:4] != b"BFNP":
        raise PackageError("active partition has no complete BL602 BFNP boot header")
    stored_crc = struct.unpack_from("<I", original_header, 172)[0]
    actual_crc = binascii.crc32(original_header[:172]) & 0xFFFFFFFF
    if stored_crc != actual_crc:
        raise PackageError("active firmware boot-header CRC does not match")
    # Signed/encrypted boot images need their corresponding keys and image
    # transforms; never silently emit an incompatible replacement.
    if original_header[116] & 0x0F:
        raise PackageError("active firmware enables signing or encryption; keyed image generation is unsupported")

    old_code_len = struct.unpack_from("<I", original_header, 120)[0]
    old_code = backup[active_offset + FW_PARTITION_HEADER_SIZE :
                      active_offset + FW_PARTITION_HEADER_SIZE + old_code_len]
    if len(old_code) != old_code_len or fw.length != FW_PARTITION_HEADER_SIZE + old_code_len:
        raise PackageError("active image length does not agree with the partition table and boot header")
    if hashlib.sha256(old_code).digest() != original_header[132:164]:
        raise PackageError("active image SHA-256 does not match its boot header")

    return original_header, active_capacity, inactive_capacity


def make_minir3_active_slot_image(backup: bytes, raw_application: bytes) -> bytes:
    """Build a checked bootinfo+application image for the verified active slot."""
    header, active_capacity, _ = _verified_minir3_layout(backup)
    return make_minir3_firmware_from_template(header, raw_application, active_capacity)


def make_minir3_ota(backup: bytes, raw_application: bytes, hw_version: str,
                    sw_version: str) -> tuple[bytes, bytes]:
    header, _, inactive_capacity = _verified_minir3_layout(backup)
    return make_minir3_ota_from_template(header, raw_application, hw_version,
                                         sw_version, inactive_capacity)


def make_minir3_xz_ota(backup: bytes, raw_application: bytes, hw_version: str,
                       sw_version: str) -> tuple[bytes, bytes]:
    """Build an XZ OTA that can expand into the larger active slot."""
    header, active_capacity, inactive_capacity = _verified_minir3_layout(backup)
    firmware = make_minir3_firmware_from_template(header, raw_application, active_capacity)
    package, _ = build_ota_xz_image(
        firmware, hw_version, sw_version, inactive_capacity - 32
    )
    return package, firmware


def make_minir3_firmware_from_template(header_template: bytes, raw_application: bytes,
                                       slot_capacity: int) -> bytes:
    """Build the raw firmware partition payload without assuming OTA will fit."""
    if len(header_template) != BOOT_HEADER_SIZE or header_template[:4] != b"BFNP":
        raise PackageError("boot-header template must be the 176-byte BL602 BFNP header")
    header = bytearray(header_template)
    stored_crc = struct.unpack_from("<I", header, 172)[0]
    if stored_crc != (binascii.crc32(header[:172]) & 0xFFFFFFFF):
        raise PackageError("boot-header template CRC does not match")
    if header[116] & 0x0F:
        raise PackageError("boot header enables signing or encryption; keyed image generation is unsupported")
    if not raw_application:
        raise PackageError("application binary is empty")

    app_data = raw_application + b"\xff" * ((-len(raw_application)) % 16)
    firmware_size = FW_PARTITION_HEADER_SIZE + len(app_data)
    if firmware_size > slot_capacity:
        raise PackageError(f"firmware is {firmware_size} bytes; active slot holds only {slot_capacity}")
    header[120:124] = struct.pack("<I", len(app_data))
    header[132:164] = hashlib.sha256(app_data).digest()
    header[172:176] = struct.pack("<I", binascii.crc32(header[:172]) & 0xFFFFFFFF)
    bootinfo = bytes(header) + b"\xff" * (FW_PARTITION_HEADER_SIZE - BOOT_HEADER_SIZE)
    return bootinfo + app_data


def make_minir3_ota_from_template(header_template: bytes, raw_application: bytes,
                                  hw_version: str, sw_version: str,
                                  slot_capacity: int = 0xB4000) -> tuple[bytes, bytes]:
    """Build from the checked-in, backup-verified 176-byte MINIR3 boot header."""
    if len(header_template) != BOOT_HEADER_SIZE or header_template[:4] != b"BFNP":
        raise PackageError("boot-header template must be the 176-byte BL602 BFNP header")
    firmware = make_minir3_firmware_from_template(header_template, raw_application,
                                                   slot_capacity - 32)
    bootinfo, app_data = firmware[:FW_PARTITION_HEADER_SIZE], firmware[FW_PARTITION_HEADER_SIZE:]
    ota_package, _ = build_ota(bootinfo, app_data, hw_version, sw_version, slot_capacity - 32)
    return ota_package, firmware


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True, help="verified full-flash backup")
    parser.add_argument("--application", type=Path, required=True, help="raw BL602 application binary")
    parser.add_argument("--hardware-version", required=True)
    parser.add_argument("--software-version", required=True)
    parser.add_argument("--output", type=Path,
                        help="write an OTA package (requires it to fit the inactive slot)")
    parser.add_argument("--xz", action="store_true",
                        help="compress the full firmware image with XZ")
    parser.add_argument("--active-slot-output", type=Path,
                        help="also write the raw bootinfo+application image for serial programming at the verified active FW offset")
    args = parser.parse_args()
    try:
        if not args.output and not args.active_slot_output:
            raise PackageError("specify --output and/or --active-slot-output")
        backup = args.backup.read_bytes()
        application = args.application.read_bytes()
        firmware = None
        if args.output:
            builder = make_minir3_xz_ota if args.xz else make_minir3_ota
            package, firmware = builder(
                backup, application, args.hardware_version, args.software_version
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as stream:
                stream.write(package)
            print(f"OTA package: {args.output} ({len(package)} bytes)")
        if args.active_slot_output:
            firmware = make_minir3_active_slot_image(backup, application)
            args.active_slot_output.parent.mkdir(parents=True, exist_ok=True)
            with args.active_slot_output.open("xb") as stream:
                stream.write(firmware)
            print(f"Active-slot image: {args.active_slot_output} ({len(firmware)} bytes)")
        if firmware:
            print(f"Firmware partition payload SHA-256: {hashlib.sha256(firmware).hexdigest()}")
    except (OSError, PackageError) as err:
        print(f"MINIR3 OTA packaging failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
