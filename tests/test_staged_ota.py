import importlib.util
from pathlib import Path
import struct
import sys
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from test_package_ota import make_test_backup


def load_script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


package = load_script("package_ota")
staged = load_script("build_minir3_staged_ota")


class StagedOtaTests(unittest.TestCase):
    def test_wraps_a_valid_staged_capsule(self):
        helper_code_len = 16
        helper_len = 0x1000 + helper_code_len
        compressed = b"compressed firmware"
        manifest = bytearray(staged.STAGE_MANIFEST_SIZE)
        manifest[:8] = staged.STAGE_MAGIC
        struct.pack_into("<HH", manifest, 8, 1, staged.STAGE_MANIFEST_SIZE)
        struct.pack_into(
            "<III",
            manifest,
            12,
            helper_len + staged.STAGE_MANIFEST_SIZE,
            len(compressed),
            4096,
        )
        manifest[24:56] = __import__("hashlib").sha256(compressed).digest()
        struct.pack_into(
            "<I",
            manifest,
            staged.STAGE_MANIFEST_SIZE - 4,
            zlib.crc32(manifest[:-4]) & 0xFFFFFFFF,
        )

        image = bytearray(helper_len + staged.STAGE_MANIFEST_SIZE + len(compressed))
        image[:4] = b"BFNP"
        struct.pack_into("<I", image, 120, helper_code_len)
        image[helper_len : helper_len + staged.STAGE_MANIFEST_SIZE] = manifest
        image[-len(compressed) :] = compressed

        result, payload = staged.make_minir3_staged_ota(
            make_test_backup(), bytes(image), "MINIR3", "test"
        )
        self.assertTrue(result.startswith(package.OTA_MAGIC))
        self.assertEqual(result[package.OTA_HEADER_SIZE :], payload)


if __name__ == "__main__":
    unittest.main()

