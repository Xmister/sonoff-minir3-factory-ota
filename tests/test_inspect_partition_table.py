import importlib.util
from pathlib import Path
import struct
import unittest
import binascii
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/inspect_partition_table.py"
SPEC = importlib.util.spec_from_file_location("inspect_partition_table", SCRIPT)
inspector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = inspector
SPEC.loader.exec_module(inspector)


def make_table(offset, age, entries):
    header = bytearray(inspector.TABLE_HEADER.pack(inspector.MAGIC, 0, len(entries), age, 0))
    struct.pack_into("<I", header, inspector.TABLE_HEADER.size - 4,
                     binascii.crc32(header[:-4]) & 0xFFFFFFFF)
    encoded = bytearray()
    for name, addr, size in entries:
        encoded.extend(inspector.ENTRY.pack(0, 0, 0, name.encode()[:9], addr, 0, size, 0, 0, 0))
    encoded.extend(struct.pack("<I", binascii.crc32(encoded) & 0xFFFFFFFF))
    return bytes(header + encoded)


class PartitionTableTests(unittest.TestCase):
    def test_validates_crcs_and_selects_newest_valid_table(self):
        flash = bytearray(0x10000)
        flash[0xE000:0xE000 + len(make_table(0xE000, 7, [("FW", 0x10000, 0x1000)]))] = make_table(
            0xE000, 7, [("FW", 0x10000, 0x1000)]
        )
        flash[0xF000:0xF000 + len(make_table(0xF000, 8, [("FW", 0x20000, 0x1000)]))] = make_table(
            0xF000, 8, [("FW", 0x20000, 0x1000)]
        )
        table, _ = inspector.inspect(flash)
        self.assertEqual(table.offset, 0xF000)
        self.assertEqual(table.age, 8)
        self.assertEqual(table.partitions[0].address[0], 0x20000)

    def test_reports_overlapping_firmware_and_mfg_ranges(self):
        flash = bytearray(0x10000)
        table = make_table(0xE000, 3, [("FW", 0x10000, 0x1000), ("mfg", 0x10000, 0x800)])
        flash[0xE000:0xE000 + len(table)] = table
        selected, _ = inspector.inspect(flash)
        self.assertEqual(inspector.overlaps(selected), [("FW", 0, "mfg", 0x10000, 0x10800)])

    def test_rejects_corrupt_entry_crc(self):
        table = bytearray(make_table(0xE000, 1, [("FW", 0x10000, 0x1000)]))
        table[-1] ^= 0x01
        flash = bytearray(0x10000)
        flash[0xE000:0xE000 + len(table)] = table
        with self.assertRaises(inspector.TableError):
            inspector.inspect(flash)


if __name__ == "__main__":
    unittest.main()
