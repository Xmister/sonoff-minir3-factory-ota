#!/usr/bin/env python3
"""Validate and summarize BL602 partition tables from a full flash backup."""

from __future__ import annotations

import argparse
import binascii
from dataclasses import dataclass
import lzma
from pathlib import Path
import struct
import sys


MAGIC = 0x54504642  # bytes: BFPT
TABLE_OFFSETS = (0xE000, 0xF000)
TABLE_HEADER = struct.Struct("<IHHII")
ENTRY = struct.Struct("<BBB9s6I")
MAX_ENTRIES = 16


class TableError(ValueError):
    pass


@dataclass(frozen=True)
class Partition:
    kind: int
    device: int
    active_index: int
    name: str
    address: tuple[int, int]
    max_len: tuple[int, int]
    length: int
    age: int


@dataclass(frozen=True)
class Table:
    offset: int
    version: int
    age: int
    partitions: tuple[Partition, ...]


def parse_table(data: bytes, offset: int) -> Table:
    if offset < 0 or offset + TABLE_HEADER.size > len(data):
        raise TableError(f"table header at 0x{offset:x} is outside the dump")
    magic, version, count, age, table_crc = TABLE_HEADER.unpack_from(data, offset)
    if magic != MAGIC:
        raise TableError(f"invalid magic at 0x{offset:x}: 0x{magic:08x}")
    if count == 0 or count > MAX_ENTRIES:
        raise TableError(f"invalid entry count at 0x{offset:x}: {count}")
    if binascii.crc32(data[offset : offset + TABLE_HEADER.size - 4]) & 0xFFFFFFFF != table_crc:
        raise TableError(f"header CRC mismatch at 0x{offset:x}")

    entries_start = offset + TABLE_HEADER.size
    entries_end = entries_start + count * ENTRY.size
    crc_end = entries_end + 4
    if crc_end > len(data):
        raise TableError(f"entries at 0x{offset:x} are truncated")
    expected_entries_crc = struct.unpack_from("<I", data, entries_end)[0]
    actual_entries_crc = binascii.crc32(data[entries_start:entries_end]) & 0xFFFFFFFF
    if expected_entries_crc != actual_entries_crc:
        raise TableError(f"entries CRC mismatch at 0x{offset:x}")

    partitions = []
    for i in range(count):
        values = ENTRY.unpack_from(data, entries_start + i * ENTRY.size)
        kind, device, active_index, raw_name, addr0, addr1, len0, len1, length, entry_age = values
        if active_index not in (0, 1):
            raise TableError(f"invalid active index for entry {i}: {active_index}")
        partitions.append(
            Partition(
                kind=kind,
                device=device,
                active_index=active_index,
                name=raw_name.split(b"\0", 1)[0].decode("ascii", "replace"),
                address=(addr0, addr1),
                max_len=(len0, len1),
                length=length,
                age=entry_age,
            )
        )
    return Table(offset=offset, version=version, age=age, partitions=tuple(partitions))


def inspect(data: bytes) -> tuple[Table, tuple[TableError, ...]]:
    valid = []
    errors = []
    for offset in TABLE_OFFSETS:
        try:
            valid.append(parse_table(data, offset))
        except TableError as err:
            errors.append(err)
    if not valid:
        raise TableError("no valid BL602 partition table found: " + "; ".join(map(str, errors)))
    # BL602 boot2 selects the valid table with the greatest age.
    return max(valid, key=lambda table: table.age), tuple(errors)


def overlaps(table: Table) -> list[tuple[str, int, str, int, int]]:
    ranges = []
    for part in table.partitions:
        for slot, (address, length) in enumerate(zip(part.address, part.max_len)):
            if address and length:
                ranges.append((part.name, slot, address, address + length))
    result = []
    for i, (name_a, slot_a, start_a, end_a) in enumerate(ranges):
        for name_b, slot_b, start_b, end_b in ranges[i + 1 :]:
            if start_a < end_b and start_b < end_a:
                result.append((name_a, slot_a, name_b, max(start_a, start_b), min(end_a, end_b)))
    return result


def verify_mfg_firmware_fallback(data: bytes, table: Table) -> bool | None:
    """Return whether mfg is an XZ-compressed exact copy of active FW, if applicable."""
    fw = next((part for part in table.partitions if part.name == "FW"), None)
    mfg = next((part for part in table.partitions if part.name == "mfg"), None)
    if fw is None or mfg is None or fw.active_index not in (0, 1):
        return None
    mfg_addr = mfg.address[0]
    inactive_addr = fw.address[1 - fw.active_index]
    if not mfg_addr or mfg_addr != inactive_addr:
        return None
    if data[mfg_addr : mfg_addr + 6] != bytes.fromhex("fd 37 7a 58 5a 00"):
        return None
    active_addr = fw.address[fw.active_index]
    active_image = data[active_addr : active_addr + fw.length]
    if not active_image:
        return False
    compressed = data[mfg_addr : inactive_addr + fw.max_len[1 - fw.active_index]]
    try:
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        decoded = decoder.decompress(compressed, max_length=len(active_image) + 1)
    except lzma.LZMAError:
        return False
    return decoder.eof and decoded == active_image


def format_table(table: Table) -> str:
    lines = [f"Active table: 0x{table.offset:04x}, version {table.version}, age {table.age}"]
    for part in table.partitions:
        active_addr = part.address[part.active_index]
        active_size = part.max_len[part.active_index]
        lines.append(
            f"{part.name or '<unnamed>'}: active slot {part.active_index}, "
            f"slot0=0x{part.address[0]:06x}+0x{part.max_len[0]:x}, "
            f"slot1=0x{part.address[1]:06x}+0x{part.max_len[1]:x}, "
            f"active=0x{active_addr:06x}+0x{active_size:x}, image_len=0x{part.length:x}"
        )
    for name_a, slot_a, name_b, start, end in overlaps(table):
        lines.append(
            f"OVERLAP: {name_a}[{slot_a}] and {name_b} at 0x{start:06x}-0x{end:06x}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("flash_dump", type=Path)
    args = parser.parse_args()
    try:
        table, rejected = inspect(args.flash_dump.read_bytes())
    except (OSError, TableError) as err:
        print(f"partition inspection failed: {err}", file=sys.stderr)
        return 1
    print(format_table(table))
    fallback = verify_mfg_firmware_fallback(args.flash_dump.read_bytes(), table)
    if fallback is True:
        print("Verified mfg XZ stream decompresses byte-for-byte to the active FW image.")
    elif fallback is False:
        print("mfg overlaps inactive FW, but its XZ stream did not verify as an active-FW copy.")
    for err in rejected:
        print(f"Ignored invalid older/alternate table: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
