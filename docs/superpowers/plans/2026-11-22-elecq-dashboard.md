# Elecq Charger Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a ready-to-paste Home Assistant dashboard for the Elecq integration, using only built-in cards, and replace the stale dashboard documentation.

**Architecture:** One static Lovelace `sections` view stored inside the integration folder so HACS installs it. A stdlib `unittest` file parses the YAML and pins the entity-ID contract, card types, path, and gauge maximum. README and `info.md` point users at the file and document the two charger-specific adjustments.

**Tech Stack:** Home Assistant Lovelace YAML, Python 3 standard library `unittest`, PyYAML (already a Home Assistant dependency).

**Spec:** `docs/superpowers/specs/2026-11-22-elecq-dashboard-design.md`

## Global Constraints

- Use only built-in cards: `heading`, `gauge`, `tile`, `history-graph`.
- No custom Lovelace cards, custom strategies, frontend JavaScript, or `auto-entities`.
- No changes to integration Python code, entity names, `unique_id`s, or `manifest.json`.
- Dashboard file path must be `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`.
- Default entity IDs, verbatim:
  - `sensor.elecq_au101_charger_power`
  - `sensor.elecq_au101_charger_power_smoothed`
  - `sensor.elecq_au101_charger_total_energy`
  - `sensor.elecq_au101_charger_session_energy`
  - `sensor.elecq_au101_charger_charger_status`
  - `sensor.elecq_au101_charger_info`
  - `binary_sensor.elecq_au101_charger_plugged_in`
  - `binary_sensor.elecq_au101_charger_charging`
  - `switch.elecq_au101_charger_remote_charging`
- View must be `type: sections` with `max_columns: 2`; sections use `column_span` 1, 1, and 2.
- Gauge is `min: 0`, `max: 22`, `needle: true`.
- Do not add `grid_options`; defaults keep the file compatible with the documented HA 2024.10 floor.
- Documentation must say the dashboard needs HA 2024.10+ (heading cards), how to adjust the gauge for 7.4/11 kW chargers, and how to fix renamed entity IDs.

## Review Focus

- **Renamed entity IDs:** a user who renamed a device/entity sees unavailable cards. The exact-ID test in Task 1 pins the default contract; Task 2 documents the find/replace fallback and verifies the wording exists.
- **Gauge scale on non-22 kW chargers:** a 7.4/11 kW owner sees an almost-empty gauge. Task 1 pins `max: 22`; Task 2 must document changing it and the verification greps for the 7.4/11 wording.
- **HACS install location:** a dashboard at repo root never reaches HACS users. Task 1 asserts the file is under `custom_components/elecq_ocpp/dashboards/`.
- **Older Home Assistant:** a user older than 2024.10 gets broken heading cards. Task 2 documents the 2024.10 requirement and the verification greps for it.
- **Narrow screens:** two side-by-side sections must stack on a phone. Task 1 includes a manual narrow-viewport check; this is the one item with no automated test.

---

### Task 1: Dashboard YAML and contract test

**Files:**
- Create: `tests/test_dashboard_yaml.py`
- Create: `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`

**Interfaces:**
- Consumes: the entity IDs and layout rules in the spec.
- Produces: `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`, consumed by Task 2's documentation; `tests/test_dashboard_yaml.py`, run by both tasks.

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard_yaml.py` with exactly this content:

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python3 -m unittest discover -s tests -v`

Expected: ERROR or FAIL naming the missing file `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`.

- [ ] **Step 3: Create the dashboard YAML**

Create `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`. Top level is:

```yaml
title: Elecq Charger
views:
  - title: Charger
    path: charger
    icon: mdi:ev-station
    type: sections
    max_columns: 2
    sections:
      # three sections described below
```

Create exactly three `type: grid` sections, in this order and with these `column_span` values:

1. `column_span: 1` — **Control**, cards in order:
   - `heading`: `heading: Elecq Charger`, icon `mdi:ev-station`, `heading_style: title`
   - `gauge`: entity `sensor.elecq_au101_charger_power`, `name: Power now`, `unit: kW`, `min: 0`, `max: 22`, `needle: true`
   - `tile`: entity `sensor.elecq_au101_charger_info`, `name: Charging state`, icon of your choice
   - `tile`: entity `switch.elecq_au101_charger_remote_charging`, `name: Remote charging`, icon of your choice, and `features: [{type: toggle}]`
2. `column_span: 1` — **Details**, cards in order:
   - `heading`: `heading: Details`, icon of your choice, `heading_style: subtitle`
   - `tile`: `sensor.elecq_au101_charger_charger_status`, `name: Charger status`
   - `tile`: `binary_sensor.elecq_au101_charger_plugged_in`, `name: Plugged in`
   - `tile`: `binary_sensor.elecq_au101_charger_charging`, `name: Charging`
   - `tile`: `sensor.elecq_au101_charger_session_energy`, `name: Session energy`
   - `tile`: `sensor.elecq_au101_charger_total_energy`, `name: Total energy`
   - `tile`: `sensor.elecq_au101_charger_power_smoothed`, `name: Smoothed power`
3. `column_span: 2` — **History**, cards in order:
   - `heading`: `heading: Power history`, icon of your choice, `heading_style: subtitle`
   - `history-graph`: `title: Power · last 24 hours`, `hours_to_show: 24`, `min_y_axis: 0`, entities:
     - `entity: sensor.elecq_au101_charger_power`, `name: Power`
     - `entity: sensor.elecq_au101_charger_power_smoothed`, `name: Smoothed`

Do not add `grid_options`, `layout_options`, custom cards, or extra entities.

- [ ] **Step 4: Run the test and verify it passes**

Run: `python3 -m unittest discover -s tests -v`

Expected: 4 tests, `OK`.

- [ ] **Step 5: Manual render check (narrow and wide)**

In a Home Assistant 2024.10+ instance with the integration configured, create a dashboard and paste the YAML into the raw configuration editor. Confirm:
- Wide window: Control and Details are side by side; History spans the width below.
- Phone-width window: sections stack vertically.
- The switch toggles and the history graph draws two lines.
If no HA instance is available, state that this check is pending in the task summary; do not claim it passed.

- [ ] **Step 6: Commit**

```bash
git add tests/test_dashboard_yaml.py custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml
git commit -m "feat: add Elecq charger dashboard"
```

---

### Task 2: Replace stale dashboard documentation

**Files:**
- Modify: `README.md` (sections `# 🧩 Entities` and `# 🖥 Example Lovelace Card`)
- Modify: `info.md` (section `## Example Lovelace Card`)
- Add to the same commit: `docs/superpowers/specs/2026-11-22-elecq-dashboard-design.md`, `docs/superpowers/plans/2026-11-22-elecq-dashboard.md`

**Interfaces:**
- Consumes: the dashboard path and default entity IDs from Task 1.
- Produces: user-facing installation instructions; no code interface.

- [ ] **Step 1: Update `README.md` entity lists**

Replace the stale sensor/binary-sensor/switch lists under `# 🧩 Entities` with the nine default IDs from Global Constraints. Group them as Sensors, Binary sensors, and Switch, keeping the existing heading style. Include the one-line note that IDs use the device name `Elecq AU101 Charger`, so the status sensor contains `charger_charger`, and that the charging-state sensor is named `info` in the registry.

- [ ] **Step 2: Replace the README example card section**

Replace `# 🖥 Example Lovelace Card` (the YAML block with `vertical-stack`) with `# 🖥 Dashboard` containing:

- Short intro: a ready-to-paste dashboard ships with the integration.
- **Install (UI):** Settings → Dashboards → Add dashboard → New dashboard from scratch → open it → Edit dashboard → Take control if prompted → three-dot menu → Raw configuration editor → replace everything with the contents of `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml` → Save.
- **Alternative (YAML mode):** show the `lovelace:` `dashboards:` entry with `mode: yaml`, `filename: dashboards/elecq_ocpp.yaml`, `title: Elecq`, `icon: mdi:ev-station`, and say to copy the file there first.
- **Requirements note:** heading cards need Home Assistant 2024.10+ (the sections view itself is 2024.3+).
- **Gauge note:** `max: 22` matches a 22 kW charger; change it to `7.4` or `11` if yours is smaller.
- **Renamed entities note:** if you renamed the device or entities, update the nine IDs in the YAML (or use your own entity IDs); the defaults are listed in the Entities section.
- A link to the file on GitHub: `https://github.com/BashTheDog/elecq-ocpp-ha/blob/main/custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml`.

- [ ] **Step 3: Update `info.md`**

Replace `## Example Lovelace Card` and its YAML block with `## Dashboard`:

- One sentence: a ready-to-paste built-in-card dashboard ships with the integration.
- One sentence: paste `custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml` into a new dashboard's raw editor.
- One sentence: see the README for install steps and the 22 kW gauge adjustment.
- Keep the rest of `info.md` unchanged.

- [ ] **Step 4: Verify the documentation**

Run these commands from the repository root:

```bash
grep -q "custom_components/elecq_ocpp/dashboards/elecq_ocpp.yaml" README.md
grep -q "sensor.elecq_au101_charger_power" README.md
grep -q "2024.10" README.md
grep -q "7.4" README.md
grep -q "## Dashboard" info.md
! grep -q "sensor.elecq_au101_power" README.md info.md
! grep -q "sensor.elecq_au101_charger_charging_state" README.md info.md
```

Expected: every command exits 0 with no output.

- [ ] **Step 5: Re-run the dashboard test**

Run: `python3 -m unittest discover -s tests -v`

Expected: 4 tests, `OK`.

- [ ] **Step 6: Commit**

```bash
git add README.md info.md docs/superpowers/specs/2026-11-22-elecq-dashboard-design.md docs/superpowers/plans/2026-11-22-elecq-dashboard.md
git commit -m "docs: document the Elecq dashboard"
```

---

## Final verification

- [ ] `python3 -m unittest discover -s tests -v` → `OK`.
- [ ] `git status --short` → clean.
- [ ] README has no old `vertical-stack` example and no old IDs.
- [ ] Dashboard file is present under `custom_components/elecq_ocpp/dashboards/`.
