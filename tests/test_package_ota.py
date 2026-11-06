import importlib.util
from pathlib import Path
import sys
import unittest
import binascii
import hashlib
import struct
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from inspect_partition_table import Partition


SCRIPT = ROOT / "scripts/package_ota.py"
SPEC = importlib.util.spec_from_file_location("package_ota", SCRIPT)
package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package
SPEC.loader.exec_module(package)

MINIR3_SCRIPT = ROOT / "scripts/build_minir3_ota.py"
MINIR3_SPEC = importlib.util.spec_from_file_location("build_minir3_ota", MINIR3_SCRIPT)
minir3 = importlib.util.module_from_spec(MINIR3_SPEC)
sys.modules[MINIR3_SPEC.name] = minir3
MINIR3_SPEC.loader.exec_module(minir3)


def make_test_backup():
    backup = bytearray(0x40000)
    app = bytes(range(16))
    header = bytearray(176)
    header[:4] = b"BFNP"
    struct.pack_into("<I", header, 120, len(app))
    header[132:164] = hashlib.sha256(app).digest()
    struct.pack_into("<I", header, 172, binascii.crc32(header[:172]) & 0xFFFFFFFF)
    backup[0x10000:0x10000 + 176] = header
    backup[0x10000 + 0x1000:0x10000 + 0x1000 + len(app)] = app

    entries = [
        Partition(0, 0, 0, "FW", (0x10000, 0x20000), (0x20000, 0x10000), 0x1010, 1),
        Partition(1, 0, 0, "mfg", (0x20000, 0), (0x1000, 0), 0, 0),
    ]
    table_header = bytearray(struct.pack("<IHHII", 0x54504642, 0, len(entries), 1, 0))
    struct.pack_into("<I", table_header, 12, binascii.crc32(table_header[:12]) & 0xFFFFFFFF)
    raw_entries = b"".join(
        struct.pack("<BBB9s6I", e.kind, e.device, e.active_index, e.name.encode(),
                    e.address[0], e.address[1], e.max_len[0], e.max_len[1], e.length, e.age)
        for e in entries
    )
    ptable = bytes(table_header) + raw_entries + struct.pack("<I", binascii.crc32(raw_entries) & 0xFFFFFFFF)
    backup[0xE000:0xE000 + len(ptable)] = ptable
    return bytes(backup)


class OtaPackageTests(unittest.TestCase):
    def setUp(self):
        self.bootinfo = b"BFNP" + b"\xff" * (package.BOOTINFO_SIZE - 4)
        self.app = b"test application image"

    def test_builds_sdk_raw_ota_wrapper_and_body_hash(self):
        result, body = package.build_ota(self.bootinfo, self.app, "MINIR3", "dev1", 0xB4000)
        self.assertEqual(len(result), package.OTA_HEADER_SIZE + len(body))
        self.assertEqual(result[:16], b"BL60X_OTA_Ver1.0")
        self.assertEqual(result[16:20], b"RAW ")
        self.assertEqual(int.from_bytes(result[20:24], "little"), len(body))
        self.assertEqual(result[32:48], b"MINIR3".ljust(16, b"\0"))
        self.assertEqual(result[48:64], b"dev1".ljust(16, b"\0"))
        self.assertEqual(result[64:96], __import__("hashlib").sha256(body).digest())
        self.assertEqual(result[package.OTA_HEADER_SIZE:], body)

    def test_refuses_non_bl602_header_and_oversized_image(self):
        with self.assertRaises(package.PackageError):
            package.build_ota(b"NOPE" + self.bootinfo[4:], self.app, "MINIR3", "dev1", 0xB4000)
        with self.assertRaises(package.PackageError):
            package.build_ota(self.bootinfo, self.app, "MINIR3", "dev1", len(self.bootinfo) + len(self.app) - 1)

    def test_versions_are_bounded_ascii(self):
        with self.assertRaises(package.PackageError):
            package.build_ota(self.bootinfo, self.app, "x" * 16, "dev1", 0xB4000)
        with self.assertRaises(package.PackageError):
            package.build_ota(self.bootinfo, self.app, "MINIR3", "bad\N{SNOWMAN}", 0xB4000)

    def test_minir3_builder_reuses_stock_flash_header_and_updates_hashes(self):
        ota, partition_image = minir3.make_minir3_ota(make_test_backup(), b"new app", "MINIR3", "dev2")
        header = partition_image[:176]
        body = partition_image[4096:]
        self.assertEqual(len(body) % 16, 0)
        self.assertEqual(struct.unpack_from("<I", header, 120)[0], len(body))
        self.assertEqual(header[132:164], hashlib.sha256(body).digest())
        self.assertEqual(struct.unpack_from("<I", header, 172)[0], binascii.crc32(header[:172]) & 0xFFFFFFFF)
        self.assertEqual(ota[package.OTA_HEADER_SIZE:], partition_image)

    def test_minir3_serial_image_uses_active_slot_capacity(self):
        backup = make_test_backup()
        firmware = minir3.make_minir3_active_slot_image(backup, b"x" * 0x11000)
        self.assertEqual(len(firmware), 0x1000 + 0x11000)
        with self.assertRaises(package.PackageError):
            minir3.make_minir3_ota(backup, b"x" * 0x11000, "MINIR3", "dev3")


if __name__ == "__main__":
    unittest.main()
