import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "elecq_ocpp" / "manifest.json"


class ManifestDependenciesTest(unittest.TestCase):
    def test_ocpp_pin_uses_the_api_the_code_calls(self):
        manifest = json.loads(MANIFEST.read_text())
        pins = [
            requirement
            for requirement in manifest["requirements"]
            if requirement.lower().startswith("ocpp")
        ]
        self.assertEqual(len(pins), 1, pins)
        self.assertRegex(pins[0], r"^ocpp==2\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
