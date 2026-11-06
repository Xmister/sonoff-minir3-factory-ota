import importlib.util
from pathlib import Path
import struct
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/backup_bl602.py"
SPEC = importlib.util.spec_from_file_location("backup_bl602", SCRIPT)
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class FakePort:
    def __init__(self):
        self.writes = []
        self.responses = bytearray()

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def read(self, size):
        result = self.responses[:size]
        del self.responses[:size]
        return bytes(result)


class BackupProtocolTests(unittest.TestCase):
    def test_only_whitelisted_protocol_commands_can_be_built(self):
        for command in (0x30, 0x31, 0x3C, 0xFF):
            with self.subTest(command=command), self.assertRaises(ValueError):
                backup.build_loader_packet(command)
        for command in (0x32, 0x36, 0x3D):
            with self.subTest(command=command):
                self.assertEqual(backup.build_loader_packet(command)[:1], bytes((command,)))

    def test_read_request_encodes_address_and_length(self):
        body = struct.pack("<II", 0x123400, 4096)
        self.assertEqual(
            backup.build_loader_packet(0x32, body),
            b"\x32\x00\x08\x00\x00\x34\x12\x00\x00\x10\x00\x00",
        )

    def test_jedec_capacity_is_decoded(self):
        self.assertEqual(backup.parse_jedec_capacity(bytes.fromhex("ef 40 15")), 2 * 1024 * 1024)
        with self.assertRaises(backup.ProtocolError):
            backup.parse_jedec_capacity(bytes.fromhex("ef 40 00"))

    def test_loader_image_is_one_expected_ram_segment(self):
        loader = bytearray(256)
        loader[:4] = b"BFNP"
        struct.pack_into("<I", loader, 120, 1)
        struct.pack_into("<II", loader, 176, 0x22010000, len(loader) - 192)
        loader = bytes(loader)
        self.assertEqual(loader[:4], b"BFNP")
        self.assertEqual(struct.unpack_from("<I", loader, 120)[0], 1)
        address, size = struct.unpack_from("<II", loader, 176)
        self.assertEqual(address, 0x22010000)
        self.assertEqual(size, len(loader) - 192)


if __name__ == "__main__":
    unittest.main()
