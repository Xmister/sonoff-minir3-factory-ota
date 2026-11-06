import importlib.util
from pathlib import Path
import sys
import unittest
import hashlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


SCRIPT = ROOT / "scripts/package_ota.py"
SPEC = importlib.util.spec_from_file_location("package_ota", SCRIPT)
package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = package
SPEC.loader.exec_module(package)


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
        self.assertEqual(result[64:96], hashlib.sha256(body).digest())
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

    def test_builds_xz_wrapper(self):
        firmware = b"BFNP" + b"\0" * (package.BOOTINFO_SIZE - 4)
        result, body = package.build_ota_xz_image(
            firmware, "MINIR3", "dev2", 0xB4000
        )
        self.assertEqual(result[:16], package.OTA_MAGIC)
        self.assertEqual(result[16:20], b"XZ  ")
        self.assertEqual(result[20:24], len(body).to_bytes(4, "little"))
        self.assertEqual(result[96:100], b"\xff\xff\xff\xff")
        self.assertEqual(result[package.OTA_HEADER_SIZE :], body)


if __name__ == "__main__":
    unittest.main()
