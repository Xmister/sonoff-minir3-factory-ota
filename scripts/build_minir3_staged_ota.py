#!/usr/bin/env python3
"""Wrap a LibreTiny BL602 staged-OTA capsule for the stock eWeLink updater."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import sys
import zlib

from build_minir3_ota import _verified_minir3_layout
from package_ota import build_ota_xz_image, PackageError


STAGE_MAGIC = b"LT602STG"
STAGE_MANIFEST_SIZE = 96


def make_minir3_staged_ota(backup: bytes, staged_image: bytes, hw_version: str,
                           sw_version: str) -> tuple[bytes, bytes]:
    _, _, inactive_capacity = _verified_minir3_layout(backup)
    if len(staged_image) < 0x1000 or not staged_image.startswith(b"BFNP"):
        raise PackageError("staged image has no BL602 BFNP boot header")
    helper_length = 0x1000 + struct.unpack_from("<I", staged_image, 120)[0]
    manifest_offset = helper_length
    if staged_image[manifest_offset : manifest_offset + 8] != STAGE_MAGIC:
        raise PackageError("staged image has no LT602STG manifest")
    version, manifest_size = struct.unpack_from("<HH", staged_image, manifest_offset + 8)
    if version != 1 or manifest_size != STAGE_MANIFEST_SIZE:
        raise PackageError("staged image manifest version or size is unsupported")
    payload_offset, compressed_size, firmware_size = struct.unpack_from(
        "<III", staged_image, manifest_offset + 12
    )
    if payload_offset != helper_length + STAGE_MANIFEST_SIZE:
        raise PackageError("staged image payload offset does not follow its manifest")
    expected_length = payload_offset + compressed_size
    if expected_length != len(staged_image):
        raise PackageError("staged image length does not match its manifest")
    if len(staged_image) > inactive_capacity - 32:
        raise PackageError(
            f"staged image is {len(staged_image)} bytes; inactive slot usable capacity is "
            f"{inactive_capacity - 32}"
        )
    manifest = staged_image[manifest_offset : manifest_offset + STAGE_MANIFEST_SIZE]
    if struct.unpack_from("<I", manifest, STAGE_MANIFEST_SIZE - 4)[0] != (
        zlib.crc32(manifest[:-4]) & 0xFFFFFFFF
    ):
        raise PackageError("staged image manifest CRC mismatch")
    compressed = staged_image[payload_offset : payload_offset + compressed_size]
    if hashlib.sha256(compressed).digest() != manifest[24:56]:
        raise PackageError("staged image compressed payload SHA-256 mismatch")
    if firmware_size <= 0:
        raise PackageError("staged image firmware length is zero")
    return build_ota_xz_image(staged_image, hw_version, sw_version, inactive_capacity - 32)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True, help="verified full-flash backup")
    parser.add_argument("--staged-image", type=Path, required=True, help="LibreTiny image_firmware_staged.bin")
    parser.add_argument("--hardware-version", required=True)
    parser.add_argument("--software-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        package, staged = make_minir3_staged_ota(
            args.backup.read_bytes(),
            args.staged_image.read_bytes(),
            args.hardware_version,
            args.software_version,
        )
        if args.output.exists():
            raise PackageError(f"refusing to overwrite {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(package)
        print(f"Stock-updater OTA package: {args.output} ({len(package)} bytes)")
        print(f"Staged firmware payload: {len(staged)} bytes; SHA-256 {hashlib.sha256(staged).hexdigest()}")
    except (OSError, PackageError) as err:
        print(f"MINIR3 staged OTA packaging failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
