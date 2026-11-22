import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "custom_components" / "elecq_ocpp" / "dashboards" / "elecq_ocpp.yaml"

EXPECTED_ENTITIES = {
    "sensor.elecq_au101_charger_power",
    "sensor.elecq_au101_charger_power_smoothed",
    "sensor.elecq_au101_charger_total_energy",
    "sensor.elecq_au101_charger_session_energy",
    "sensor.elecq_au101_charger_charger_status",
    "sensor.elecq_au101_charger_info",
    "binary_sensor.elecq_au101_charger_plugged_in",
    "binary_sensor.elecq_au101_charger_charging",
    "switch.elecq_au101_charger_remote_charging",
}

ALLOWED_CARDS = {"heading", "gauge", "tile", "history-graph"}


def _entity_refs(node):
    if isinstance(node, dict):
        if isinstance(node.get("entity"), str):
            yield node["entity"]
        for value in node.values():
            yield from _entity_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _entity_refs(item)


class DashboardTest(unittest.TestCase):
    def setUp(self):
        if not DASHBOARD.is_file():
            self.fail(f"missing dashboard file: {DASHBOARD}")
        self.data = yaml.safe_load(DASHBOARD.read_text())

    def _cards(self):
        view = self.data["views"][0]
        for section in view["sections"]:
            yield from section["cards"]

    def test_is_a_sections_view(self):
        view = self.data["views"][0]
        self.assertEqual(view["type"], "sections")
        self.assertEqual(view["max_columns"], 2)

    def test_uses_exactly_the_default_entity_ids(self):
        self.assertEqual(set(_entity_refs(self.data)), EXPECTED_ENTITIES)

    def test_uses_only_builtin_cards(self):
        self.assertEqual({card["type"] for card in self._cards()}, ALLOWED_CARDS)

    def test_gauge_is_22_kw(self):
        gauges = [card for card in self._cards() if card["type"] == "gauge"]
        self.assertEqual(len(gauges), 1)
        self.assertEqual(gauges[0]["max"], 22)
        self.assertEqual(gauges[0]["min"], 0)
        self.assertTrue(gauges[0]["needle"])

    def test_three_sections_have_expected_widths(self):
        sections = self.data["views"][0]["sections"]
        self.assertEqual(len(sections), 3)
        self.assertEqual([section["column_span"] for section in sections], [1, 1, 2])

    def test_no_grid_or_layout_options_anywhere(self):
        def keys(node):
            if isinstance(node, dict):
                yield from node
                for value in node.values():
                    yield from keys(value)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        found = set(keys(self.data))
        self.assertNotIn("grid_options", found)
        self.assertNotIn("layout_options", found)

    def test_readme_requires_the_heading_card_floor(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("2024.10", readme)
        self.assertNotIn("2024.3", readme)


if __name__ == "__main__":
    unittest.main()
