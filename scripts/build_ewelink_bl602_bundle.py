#!/usr/bin/env python3
"""Wrap a BL602 OTA package in the eWeLink multi-component bundle format."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import sys
import zlib


HEADER_SIZE = 24
RECORD_SIZE = 76
BUNDLE_TYPE = 3
BUNDLE_VERSION = b"9.9.0"
COMPONENT_NAME = b"SWITCHES2G4-BL602L"


def fixed(value: bytes, size: int, label: str) -> bytes:
    if not value or len(value) > size:
        raise ValueError(f"{label} must fit in {size} bytes")
    return value.ljust(size, b"\0")


def build_bundle(payload: bytes, component_name: bytes = COMPONENT_NAME,
                 component_version: bytes = BUNDLE_VERSION) -> bytes:
    if not payload.startswith(b"BL60X_OTA_Ver1.0"):
        raise ValueError("payload is not a BL60X OTA package")
    if not 0 < len(payload) < 0xFFFFFFFF:
        raise ValueError("payload length is unsupported")

    header = bytearray(HEADER_SIZE)
    header[0] = BUNDLE_TYPE
    header[1] = 1
    header[2:8] = fixed(component_version, 6, "bundle version")
    struct.pack_into(">I", header, 20, zlib.crc32(header[:20]) & 0xFFFFFFFF)

    record = bytearray(RECORD_SIZE)
    record[0:32] = fixed(component_name, 32, "component name")
    record[32:48] = fixed(component_version, 16, "component version")
    struct.pack_into(">I", record, 48, HEADER_SIZE + RECORD_SIZE)
    struct.pack_into(">I", record, 52, len(payload))
    struct.pack_into(">I", record, 56, zlib.crc32(payload) & 0xFFFFFFFF)
    struct.pack_into(">I", record, 60, zlib.crc32(record[:60]) & 0xFFFFFFFF)
    return bytes(header + record) + payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--component-name", default=COMPONENT_NAME.decode())
    parser.add_argument("--component-version", default=BUNDLE_VERSION.decode())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = args.payload.read_bytes()
        bundle = build_bundle(
            payload,
            args.component_name.encode("ascii"),
            args.component_version.encode("ascii"),
        )
        if args.output.exists():
            raise ValueError(f"refusing to overwrite {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            stream.write(bundle)
        print(f"eWeLink BL602 bundle: {args.output} ({len(bundle)} bytes)")
        print(f"SHA-256: {hashlib.sha256(bundle).hexdigest()}")
        print(f"Payload: {len(payload)} bytes; CRC-32 {zlib.crc32(payload) & 0xFFFFFFFF:08x}")
    except (OSError, ValueError, UnicodeEncodeError) as err:
        print(f"eWeLink bundle packaging failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
