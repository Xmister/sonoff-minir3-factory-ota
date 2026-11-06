import importlib.util
from pathlib import Path
import struct
import sys
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bundle = load_script("build_ewelink_bl602_bundle")
server = load_script("serve_range")


class BundleTests(unittest.TestCase):
    def test_builds_checked_eweLink_container(self):
        payload = b"BL60X_OTA_Ver1.0" + b"test-payload"
        result = bundle.build_bundle(payload)

        self.assertEqual(len(result), bundle.HEADER_SIZE + bundle.RECORD_SIZE + len(payload))
        self.assertEqual(result[0], bundle.BUNDLE_TYPE)
        self.assertEqual(result[20:24], struct.pack(">I", zlib.crc32(result[:20]) & 0xFFFFFFFF))
        self.assertEqual(result[-len(payload) :], payload)
        record = bundle.HEADER_SIZE
        self.assertEqual(
            struct.unpack_from(">I", result, record + 48)[0],
            bundle.HEADER_SIZE + bundle.RECORD_SIZE,
        )
        self.assertEqual(struct.unpack_from(">I", result, record + 52)[0], len(payload))
        self.assertEqual(
            struct.unpack_from(">I", result, record + 56)[0],
            zlib.crc32(payload) & 0xFFFFFFFF,
        )

    def test_rejects_non_bl602_payload(self):
        with self.assertRaises(ValueError):
            bundle.build_bundle(b"not-a-bl602-package")


class RangeTests(unittest.TestCase):
    def test_parses_standard_ranges(self):
        self.assertEqual(server.parse_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(server.parse_range("bytes=900-", 1000), (900, 999))
        self.assertEqual(server.parse_range("bytes=-20", 1000), (980, 999))
        self.assertEqual(server.parse_range("bytes=990-2000", 1000), (990, 999))

    def test_rejects_invalid_ranges(self):
        for value in ("items=0-1", "bytes=", "bytes=1000-", "bytes=20-10", "bytes=-0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.parse_range(value, 1000)


if __name__ == "__main__":
    unittest.main()

