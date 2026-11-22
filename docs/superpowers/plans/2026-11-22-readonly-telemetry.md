# Read-only Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add read-only electrical telemetry and charger-schedule visibility to the Elecq integration without adding any control messages.

**Architecture:** A pure `readings.py` module normalizes OCPP meter values and composite schedules. `ocpp_server.py` feeds it from `MeterValues`/`TransactionEvent`, polls `GetCompositeSchedule`, and handles `NotifyChargingLimit`/`ClearedChargingLimit`. `sensor.py` exposes the readings as fixed entities plus a raw diagnostic sensor.

**Tech Stack:** Home Assistant custom integration, Python standard library, `ocpp==2.1.0`, PyYAML (tests only).

**Spec:** `docs/superpowers/specs/2026-11-22-readonly-telemetry-design.md`

## Global Constraints

- Strictly read-only: no `SetChargingProfile`, `SetVariables`, `SetNetworkProfile`, or any other write message.
- Existing nine entity IDs and names must not change.
- `ocpp==2.1.0` must remain pinned.
- Composite schedule: `duration=3600`, `evse_id` from config.
- First poll 10 s after each connect; 60 s after an accepted reply; 300 s after a rejection/error.
- No new dependencies.
- `readings.py` must not import Home Assistant or `ocpp`.
- New sensor names, units, and IDs exactly as listed in the plan.
- No dashboard changes.

## Review Focus

- **Charger rejects `GetCompositeSchedule`:** the sensor must stay numeric-free with a clear `status`, polling must back off, and the integration must keep working. Task 1 tests missing/expired schedules; Task 2 implements the backoff; Task 4 documents the status attribute.
- **Poll task leaks or outlives unload:** the task must cancel on unload and reset per connection. Task 2 owns the event/lifecycle; the final manual check must include a reload.
- **Start/stop starvation:** `GetCompositeSchedule` must not block a user command indefinitely. Task 2 skips new polls while `command_in_flight`; the final manual check covers start/stop.
- **Odd units/defaults from the charger:** values must be converted or defaulted, not silently mis-scaled. Task 1 tests multipliers, kW/Wh/mA/mV, Fahrenheit, and Percent.
- **Stale schedule:** after `schedule_start + duration`, the limit must become unknown. Task 1 tests expiry and no-duration continuation.
- **Missing measurands:** sensors may be unknown, but the raw diagnostic sensor must retain every value. Task 1 tests defaults and unknown keys; Task 3 exposes the raw map.

---

### Task 1: Pure readings module and tests

**Files:**
- Create: `custom_components/elecq_ocpp/readings.py`
- Create: `tests/test_readings.py`

**Interfaces:**
- Produces:
  - `normalize_meter_values(meter_value, previous=None) -> tuple[dict[str, dict], str | None]`
  - `find_value(values, measurand, phase=None, location=None) -> float | None`
  - `summarize_phases(values, measurand, location="Outlet") -> tuple[float | None, dict[str, float]]`
  - `summarize_temperature(values) -> tuple[float | None, dict[str, float]]`
  - `effective_limit(schedule, now) -> tuple[float | None, list[dict], bool]`
- Consumed by Task 2 (`ocpp_server.py`) and Task 3 (`sensor.py`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_readings.py` with this content. The file loader avoids running the integration `__init__.py`, which imports Home Assistant.

```python
import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path

READINGS_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "elecq_ocpp"
    / "readings.py"
)
_spec = importlib.util.spec_from_file_location("elecq_readings", READINGS_PATH)
readings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readings)

UTC = timezone.utc


class NormalizeMeterValuesTest(unittest.TestCase):
    def test_defaults_missing_measurand_location_and_unit(self):
        values, timestamp = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [{"value": "100"}],
                }
            ]
        )
        self.assertEqual(timestamp, "2026-11-22T10:00:00Z")
        self.assertEqual(
            values["Energy.Active.Import.Register|-|Outlet"]["value"], 100.0
        )
        self.assertEqual(values["Energy.Active.Import.Register|-|Outlet"]["unit"], "Wh")
        self.assertEqual(
            values["Energy.Active.Import.Register|-|Outlet"]["raw_measurand"], None
        )

    def test_multiplier_and_canonical_units(self):
        values, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [
                        {
                            "value": "7.2",
                            "measurand": "Power.Active.Import",
                            "unit_of_measure": {"unit": "kW"},
                        },
                        {
                            "value": "12.5",
                            "measurand": "Energy.Active.Import.Register",
                            "unit_of_measure": {"unit": "kWh"},
                        },
                        {
                            "value": "95",
                            "measurand": "Power.Factor",
                            "unit_of_measure": {"unit": "Percent"},
                        },
                        {
                            "value": "95",
                            "measurand": "Temperature",
                            "location": "Body",
                            "unit_of_measure": {"unit": "Fahrenheit"},
                        },
                    ],
                }
            ]
        )
        self.assertEqual(values["Power.Active.Import|-|Outlet"]["value"], 7200.0)
        self.assertEqual(values["Power.Active.Import|-|Outlet"]["unit"], "W")
        self.assertEqual(
            values["Energy.Active.Import.Register|-|Outlet"]["value"], 12500.0
        )
        self.assertEqual(values["Power.Factor|-|Outlet"]["value"], 0.95)
        self.assertEqual(values["Temperature|-|Body"]["value"], 35.0)
        self.assertEqual(values["Temperature|-|Body"]["unit"], "Celsius")

    def test_phase_normalization_and_invalid_value_skip(self):
        values, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [
                        {
                            "value": "16",
                            "measurand": "Current.Import",
                            "phase": "L1-N",
                            "unit_of_measure": {"unit": "A"},
                        },
                        {"value": "not-a-number", "measurand": "Voltage"},
                    ],
                }
            ]
        )
        self.assertEqual(values["Current.Import|L1|Outlet"]["value"], 16.0)
        self.assertNotIn("Voltage|-|Outlet", values)

    def test_merges_with_previous_values(self):
        first, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [
                        {
                            "value": "1",
                            "measurand": "Current.Import",
                            "phase": "L1",
                            "unit_of_measure": {"unit": "A"},
                        }
                    ],
                }
            ]
        )
        merged, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:01:00Z",
                    "sampled_value": [
                        {
                            "value": "2",
                            "measurand": "Current.Import",
                            "phase": "L2",
                            "unit_of_measure": {"unit": "A"},
                        }
                    ],
                }
            ],
            first,
        )
        self.assertIn("Current.Import|L1|Outlet", merged)
        self.assertIn("Current.Import|L2|Outlet", merged)


class FindAndSummarizeTest(unittest.TestCase):
    def setUp(self):
        self.values = {
            "Current.Import|L1|Inlet": {"value": 10.0},
            "Current.Import|L1|Outlet": {"value": 16.0},
            "Current.Import|L2|Outlet": {"value": 15.0},
            "Voltage|L1|Outlet": {"value": 230.0},
            "Voltage|L2|Outlet": {"value": 231.0},
            "Voltage|L3|Outlet": {"value": 229.0},
            "Temperature|-|Body": {"value": 35.0},
            "Temperature|-|Cable": {"value": 45.0},
        }

    def test_prefers_outlet_and_sums_phases(self):
        self.assertEqual(readings.find_value(self.values, "Current.Import", phase="L1"), 16.0)
        self.assertEqual(readings.find_value(self.values, "Current.Import"), 31.0)

    def test_voltage_average_and_phase_attributes(self):
        average, phases = readings.summarize_phases(self.values, "Voltage")
        self.assertAlmostEqual(average, 230.0)
        self.assertEqual(phases["L1"], 230.0)
        self.assertEqual(phases["L2"], 231.0)

    def test_temperature_max_and_locations(self):
        maximum, locations = readings.summarize_temperature(self.values)
        self.assertEqual(maximum, 45.0)
        self.assertEqual(locations, {"Body": 35.0, "Cable": 45.0})


class EffectiveLimitTest(unittest.TestCase):
    def setUp(self):
        self.schedule = {
            "schedule_start": "2026-11-22T10:00:00+00:00",
            "duration": 3600,
            "charging_rate_unit": "A",
            "charging_schedule_period": [
                {"start_period": 0, "limit": 16.0, "number_phases": 3},
                {"start_period": 1800, "limit": 8.0, "number_phases": 3},
            ],
        }

    def test_before_start_uses_first_period(self):
        limit, periods, expired = readings.effective_limit(
            self.schedule, datetime(2026, 11, 22, 9, 59, tzinfo=UTC)
        )
        self.assertEqual(limit, 16.0)
        self.assertFalse(expired)
        self.assertEqual(len(periods), 2)
        self.assertTrue(periods[0]["start"].startswith("2026-11-22T10:00:00"))

    def test_mid_schedule_uses_active_period(self):
        limit, _, _ = readings.effective_limit(
            self.schedule, datetime(2026, 11, 22, 10, 40, tzinfo=UTC)
        )
        self.assertEqual(limit, 8.0)

    def test_expired_schedule_has_no_limit(self):
        limit, _, expired = readings.effective_limit(
            self.schedule, datetime(2026, 11, 22, 11, 30, tzinfo=UTC)
        )
        self.assertIsNone(limit)
        self.assertTrue(expired)

    def test_no_duration_keeps_last_period(self):
        schedule = {**self.schedule, "duration": None}
        limit, _, expired = readings.effective_limit(
            schedule, datetime(2026, 11, 22, 23, 0, tzinfo=UTC)
        )
        self.assertEqual(limit, 8.0)
        self.assertFalse(expired)

    def test_missing_schedule_is_safe(self):
        self.assertEqual(readings.effective_limit(None, datetime.now(UTC)), (None, [], False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `python3 -m unittest discover -s tests -v`

Expected: ERROR importing `custom_components/elecq_ocpp/readings.py` because the file does not exist.

- [ ] **Step 3: Implement `readings.py`**

Implement the five public functions with these rules:

- `normalize_meter_values`:
  - Defaults: measurand `Energy.Active.Import.Register`, location `Outlet`, context `Sample.Periodic`.
  - Default unit by measurand: energy `Wh`, power `W`, current `A`, voltage `V`, frequency `Hz`, temperature `Celsius`.
  - Apply `unit_of_measure.multiplier` as `10 ** multiplier`.
  - Normalize phase `L1-N`/`L2-N`/`L3-N` to `L1`/`L2`/`L3`; missing phase is `-`.
  - Convert to canonical units: power to W, energy to Wh, current to A, voltage to V, frequency to Hz, temperature to Celsius, power factor Percent to 0–1.
  - Store `{value, unit, raw_value, raw_unit, raw_measurand, raw_phase, raw_location, context}`.
  - Skip a sampled value when its value is not numeric.
  - Key as `f"{measurand}|{phase}|{location}"`; merge over `previous`.
  - Return the map and the last non-empty `timestamp`.
- `find_value`:
  - `None` is a wildcard. Prefer location `Outlet`, then `ChargingStation`, then the first sorted location.
  - Prefer a no-phase total; otherwise sum Current/Power, average Voltage/Power.Factor, and max Temperature across phases.
- `summarize_phases`: filter by measurand and `location` (default `Outlet`), return the average of `L1`/`L2`/`L3` and a phase dict.
- `summarize_temperature`: return the maximum and a location dict.
- `effective_limit`:
  - Parse `schedule_start`; treat a naive datetime as UTC.
  - Sort periods; before start use the first; otherwise use the last period whose start has passed.
  - If `duration` is set and now is past start + duration, return `(None, formatted_periods, True)`.
  - Format each period start as an ISO string from `schedule_start + start_period`.

- [ ] **Step 4: Run the tests and verify they pass**

Run: `python3 -m unittest discover -s tests -v`

Expected: the new readings tests plus the existing 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/elecq_ocpp/readings.py tests/test_readings.py
git commit -m "feat: add read-only reading normalizers"
```

---

### Task 2: OCPP server state, meter parsing, and schedule polling

**Files:**
- Modify: `custom_components/elecq_ocpp/const.py`
- Modify: `custom_components/elecq_ocpp/ocpp_server.py`

**Interfaces:**
- Consumes: all `readings.py` functions from Task 1.
- Produces for Task 3:
  - State fields: `meter_values`, `last_meter_timestamp`, `composite_schedule`, `composite_schedule_status`, `composite_schedule_status_info`, `composite_schedule_last_update`, `charging_limit_source`, `charging_limit_grid_critical`, `charging_limit_schedule`, `charging_limit_last_update`.
  - Properties: `ElecqOcppManager.effective_schedule -> dict | None`, `.effective_schedule_source -> str | None`.
  - `async_request_composite_schedule() -> dict | None`.

- [ ] **Step 1: Add constants**

In `const.py`, add:

```python
COMPOSITE_SCHEDULE_DURATION = 3600
COMPOSITE_SCHEDULE_INITIAL_DELAY = 10
COMPOSITE_SCHEDULE_POLL_INTERVAL = 60
COMPOSITE_SCHEDULE_RETRY_INTERVAL = 300
```

- [ ] **Step 2: Extend state and meter parsing**

In `ocpp_server.py`:

- Add `field` to the `dataclasses` import and import the readings functions plus the new constants.
- Add the state fields listed in Interfaces. Use `field(default_factory=dict)` for `meter_values`.
- Rewrite `update_meter_values` to:
  - call `normalize_meter_values(meter_value, st.meter_values)`;
  - store the merged map and latest timestamp;
  - derive `power_kw` from `find_value(values, "Power.Active.Import") / 1000`;
  - derive `total_kwh` from `find_value(values, "Energy.Active.Import.Register") / 1000`;
  - keep the existing smoothing/session logic and `last_meter_value`.

- [ ] **Step 3: Add composite schedule request, flattening, and properties**

- `async_request_composite_schedule` sends `call.GetCompositeSchedule(duration=3600, evse_id=self.evse_id)`.
  - On success, flatten to `{evse_id, duration, schedule_start, charging_rate_unit, charging_schedule_period}`; store status/status_info and an ISO `composite_schedule_last_update`.
  - On rejection, store the status/status_info.
  - On exception, store `status = "Error"` and `status_info = str(err)`; log at debug.
  - Notify and return the flattened dict or `None`.
- `effective_schedule` returns the accepted composite schedule, else `charging_limit_schedule`.
- `effective_schedule_source` returns `"CompositeSchedule"`, `"NotifyChargingLimit"`, or `None`.
- Add `_flatten_composite_schedule(schedule)` and `_flatten_charging_schedule(schedule, fallback_start)` helpers; convert enums with `str()`.

- [ ] **Step 4: Add handlers and polling lifecycle**

- Add `@on("MeterValues")` → update meter values, return `call_result.MeterValues()`.
- Add `@on("NotifyChargingLimit")` → store source/grid-critical/schedule, timestamp, notify, create a background refresh task (do not await inline), return `call_result.NotifyChargingLimit()`.
- Add `@on("ClearedChargingLimit")` → clear source/grid-critical/schedule, timestamp, notify, create a background refresh, return `call_result.ClearedChargingLimit()`.
- Add a per-connection `asyncio.Event` and `_async_schedule_poll_loop` with the 10/60/300 second timing.
- Start the poll task in `async_start_server` with `hass.async_create_task`; cancel and await it in `async_stop_server`.
- In `_on_connect`, set the event; in the `finally`, clear the event and set `self._cp = None` only if it still points at this connection.
- Set `command_in_flight` around the `_cp.call()` in start, stop, and refresh; the poll loop skips a new poll while it is true.
- On disconnect, set `composite_schedule_status = "Disconnected"` and notify; keep the last values.

- [ ] **Step 5: Verify syntax, tests, and read-only behavior**

Run:

```bash
python3 -m py_compile custom_components/elecq_ocpp/const.py custom_components/elecq_ocpp/ocpp_server.py
python3 -m unittest discover -s tests -v
! grep -nE "SetChargingProfile|SetVariables|SetNetworkProfile" custom_components/elecq_ocpp/*.py
```

Expected: compilation succeeds, all tests pass, and the grep finds nothing. Manual charger verification is listed in Task 4.

- [ ] **Step 6: Commit**

```bash
git add custom_components/elecq_ocpp/const.py custom_components/elecq_ocpp/ocpp_server.py
git commit -m "feat: read charger telemetry and composite schedule"
```

---

### Task 3: Read-only sensors

**Files:**
- Modify: `custom_components/elecq_ocpp/sensor.py`

**Interfaces:**
- Consumes: state fields and `effective_schedule` / `effective_schedule_source` from Task 2; readings helpers from Task 1.
- Produces the entities listed below; no other task depends on their code.

- [ ] **Step 1: Add imports and helpers**

Add `UnitOfElectricCurrent`, `UnitOfElectricPotential`, `UnitOfFrequency`, `UnitOfTemperature`, and `EntityCategory` imports. Add a `_find` helper to `_BaseElecqSensor` that calls `readings.find_value(self._manager.state.meter_values, ...)`.

- [ ] **Step 2: Add the sensor classes**

Add and register these classes in `async_setup_entry`:

- `ElecqCurrentSensor(manager, device_info, phase)` for `L1`, `L2`, `L3`:
  - names `Current L1`/`L2`/`L3`; unique IDs `elecq_au101_current_l1`/`_l2`/`_l3`; A, current, measurement; state from `Current.Import` at that phase.
- `ElecqVoltageSensor`: name `Voltage`; `elecq_au101_voltage`; V, voltage, measurement; state `summarize_phases(..., "Voltage")[0]`; attributes `phases`.
- `ElecqFrequencySensor`: `Frequency`; `elecq_au101_frequency`; Hz, frequency, measurement.
- `ElecqTemperatureSensor`: `Temperature`; `elecq_au101_temperature`; °C, temperature, measurement; `summarize_temperature`; attributes `locations`.
- `ElecqPowerFactorSensor`: `Power Factor`; `elecq_au101_power_factor`; unitless, measurement; `summarize_phases(..., "Power.Factor")`; attributes `phases`.
- `ElecqEffectiveLimitSensor`: `Effective Limit`; `elecq_au101_effective_limit`; measurement; native value from `readings.effective_limit(self._manager.effective_schedule, now)[0]`; native unit from the schedule's `charging_rate_unit`; attributes `status`, `status_info`, `evse_id`, `schedule_start`, `duration`, `charging_rate_unit`, `periods`, `expired`, `limit_source`, `grid_critical`, `last_updated`.
- `ElecqMeterValuesSensor`: `Meter Values`; `elecq_au101_meter_values`; `EntityCategory.DIAGNOSTIC`; state = `last_meter_timestamp`; attributes = `{"values": state.meter_values}`.

Keep all existing classes and their unique IDs unchanged.

- [ ] **Step 3: Verify syntax, tests, and entity names**

Run:

```bash
python3 -m py_compile custom_components/elecq_ocpp/sensor.py
python3 -m unittest discover -s tests -v
grep -q "elecq_au101_effective_limit" custom_components/elecq_ocpp/sensor.py
grep -q "elecq_au101_meter_values" custom_components/elecq_ocpp/sensor.py
```

Expected: compilation succeeds, all tests pass, both greps succeed.

- [ ] **Step 4: Commit**

```bash
git add custom_components/elecq_ocpp/sensor.py
git commit -m "feat: expose read-only telemetry sensors"
```

---

### Task 4: Documentation, version, and release

**Files:**
- Modify: `README.md`
- Modify: `info.md`
- Modify: `CHANGELOG.md`
- Modify: `custom_components/elecq_ocpp/manifest.json`

**Interfaces:**
- Consumes: the entity names and IDs from Task 3.
- Produces: user documentation and release tag `v1.1.0`.

- [ ] **Step 1: Update the README entity list**

Under `# 🧩 Entities`, add the new read-only sensors with their default IDs:
`current_l1`, `current_l2`, `current_l3`, `voltage`, `frequency`, `temperature`, `power_factor`, `effective_limit`, `meter_values`.

Add a short `### Read-only telemetry` note explaining that:
- these are observations only;
- `Effective Limit` comes from the charger's composite schedule and shows `status` if the charger rejects the query;
- raw sampled values are on the diagnostic `Meter Values` sensor;
- no `SetChargingProfile`/`SetVariables` calls are made.

- [ ] **Step 2: Update `info.md`**

Add one bullet to the feature list: read-only electrical telemetry and charger schedule visibility.

- [ ] **Step 3: Update the changelog and manifest**

- Add `## v1.1.0 – Read-only telemetry` to `CHANGELOG.md` with the new sensors and the no-write note.
- Change `manifest.json` version to `1.1.0`.

- [ ] **Step 4: Run all verification**

```bash
python3 -m json.tool custom_components/elecq_ocpp/manifest.json >/dev/null
python3 -m unittest discover -s tests -v
grep -q "v1.1.0" CHANGELOG.md
grep -q "Read-only telemetry" README.md
! grep -nE "SetChargingProfile|SetVariables|SetNetworkProfile" custom_components/elecq_ocpp/*.py
```

Expected: all commands succeed.

- [ ] **Step 5: Commit, tag, and push**

```bash
git add README.md info.md CHANGELOG.md custom_components/elecq_ocpp/manifest.json
git commit -m "docs: release read-only telemetry v1.1.0"
git tag -a v1.1.0 -m "v1.1.0 — read-only telemetry"
git push origin main
git push origin v1.1.0
git push gitea main
git push gitea v1.1.0
```

- [ ] **Step 6: Manual verification (user)**

With the charger connected:
- confirm the new sensors populate;
- confirm `Effective Limit` shows a number, or a clear `status` if rejected;
- confirm start/stop still works;
- confirm the OCPP log contains no `SetChargingProfile`/`SetVariables`.
If no charger is available, record this as pending; do not claim it passed.

---

## Final verification

- [ ] `python3 -m unittest discover -s tests -v` → all tests pass.
- [ ] `git status --short` → clean.
- [ ] No control messages in the integration source.
- [ ] `main` and `v1.1.0` match on GitHub and Gitea.
