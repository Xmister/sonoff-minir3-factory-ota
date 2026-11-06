from pathlib import Path
import json
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ewelink_common import device_model, load_sonoff_config


class EWeLinkCommonTests(unittest.TestCase):
    def test_loads_sonoff_integration_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            storage = config_dir / ".storage"
            storage.mkdir()
            (storage / "core.config_entries").write_text(
                json.dumps(
                    {
                        "data": {
                            "entries": [
                                {"domain": "other", "data": {"value": "ignore"}},
                                {"domain": "sonoff", "data": {"region": "eu"}},
                            ]
                        }
                    }
                )
            )
            self.assertEqual(load_sonoff_config(config_dir), {"region": "eu"})

    def test_reports_missing_sonoff_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory)
            storage = config_dir / ".storage"
            storage.mkdir()
            (storage / "core.config_entries").write_text(
                json.dumps({"data": {"entries": []}})
            )
            with self.assertRaisesRegex(RuntimeError, "no Sonoff integration"):
                load_sonoff_config(config_dir)

    def test_reads_model_from_either_cloud_shape(self):
        self.assertEqual(
            device_model({"extra": {"model": "BL1-S04-01"}}),
            "BL1-S04-01",
        )
        self.assertEqual(
            device_model({"productModel": "BL1-S04-01"}),
            "BL1-S04-01",
        )
        self.assertEqual(device_model({}), "")


if __name__ == "__main__":
    unittest.main()

