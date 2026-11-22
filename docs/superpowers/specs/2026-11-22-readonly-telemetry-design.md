# Read-only telemetry and charging schedule — design

**Date:** 2026-11-22
**Status:** proposed, awaiting approval
**Size:** project

## Intent

Add read-only visibility into the AE104's live electrical telemetry and into the charger's own effective charging limit/schedule. The charger's Power Monitor keeps doing all control; this integration only observes and reports. No `SetChargingProfile`, `SetVariables`, or other control messages are added.

## Why

The charger already adjusts power based on home usage, but Home Assistant only exposes power, energy, status, and charging state. The integration receives `meterValue` data and ignores everything except total power and energy. It also ignores the charger's smart-charging data (`GetCompositeSchedule`, `NotifyChargingLimit`, `ClearedChargingLimit`). The user wants both the electrical values and what the Power Monitor is currently doing.

## Goals

- Parse every `sampledValue` from `MeterValues` and `TransactionEvent.meterValue`, not just power/energy.
- Add first-class sensors for per-phase current, voltage, frequency, temperature, and power factor.
- Add a numeric `Effective limit` sensor derived from `GetCompositeSchedule`, with schedule periods and source as attributes.
- Update the effective limit immediately on `NotifyChargingLimit` and clear it on `ClearedChargingLimit`; poll the composite schedule every 60 seconds as a fallback.
- Preserve every raw sampled value on a diagnostic `Meter Values` sensor.
- Keep the integration strictly read-only.
- Document the new sensors and release as `1.1.0`.

## Non-goals

- No changes to `RequestStartTransaction` / `RequestStopTransaction` or the charging switch.
- No `SetChargingProfile`, `SetVariables`, `SetNetworkProfile`, or any other write message.
- No attempt to reproduce the charger's Power Monitor or override its limits.
- No dynamic entity creation for every possible measurand.
- No dashboard changes in this project; the new sensors are documented for users to add.
- No `GetChargingProfiles`, `GetVariables`, or `GetBaseReport` yet; those are possible follow-ups.
- No multi-charger or multi-EVSE support; the integration still handles one connected charge point.

## Anti-goals

- A sensor that silently fails when the charger rejects `GetCompositeSchedule`.
- Polling an unsupported message every 60 seconds forever with no backoff.
- Blocking start/stop commands behind a long schedule poll.
- Changing any existing entity ID or name.
- Adding a dependency beyond the already-required `ocpp==2.1.0`.

## Constraints

- `ocpp==2.1.0` (the 2.x API the code already uses).
- The integration minimum stays 2024.1; the dashboard still needs 2024.10+.
- One EVSE, identified by the configured `evse_id`.
- Composite schedule request: `duration: 3600`, `evse_id` from config.
- Poll interval: 60 s after a successful poll, 300 s after a rejection/error, first poll 10 s after each connect.
- No new runtime dependencies.
- Pure parsing and schedule calculation live in a module that does not import Home Assistant or `ocpp`, so it is unit-testable here.

## Approaches considered

- **A — diagnostic-only attributes.** One sensor with all meter values and one with the raw schedule. Smallest change, but nothing is graphable per phase or as a limit.
- **B — first-class sensors. Chosen.** Per-phase current, aggregate voltage, frequency, temperature, power factor, a numeric effective limit, and a raw diagnostic sensor. More entities and code, but useful on a dashboard and in history.
- **C — dynamic discovery.** Create a sensor for every measurand/phase the charger reports. Most complete, but requires runtime entity creation, naming rules, and registry churn for a small integration. Rejected.

## Design

### 1. Pure readings module

Create `custom_components/elecq_ocpp/readings.py` with no HA/OCPP imports.

**`normalize_meter_values(meter_value, previous=None) -> tuple[dict[str, dict], str | None]`**

- Iterate each `meterValue`, then each `sampledValue`.
- Apply OCPP defaults for absent fields:
  - `measurand` → `Energy.Active.Import.Register`
  - `location` → `Outlet`
  - `context` → `Sample.Periodic`
  - default unit by measurand: energy `Wh`, power `W`, current `A`, voltage `V`, frequency `Hz`, temperature `Celsius`; power factor stays unitless.
- Normalize the phase label: `L1-N`/`L2-N`/`L3-N` become `L1`/`L2`/`L3`; `L1`/`L2`/`L3` stay; no phase becomes `-`. Line-to-line phases are kept as-is in the raw map but are not used by the aggregate voltage sensor.
- Convert the value to canonical units:
  - power `W`/`kW`/`MW` → W
  - energy `Wh`/`kWh`/`MWh` → Wh
  - current `A`/`mA` → A
  - voltage `V`/`mV`/`kV` → V
  - frequency `Hz`/`kHz` → Hz
  - temperature `Celsius`/`Fahrenheit`/`Kelvin` → °C
  - power factor `Percent` → 0–1
- Key each normalized value as `"<measurand>|<phase>|<location>"`.
- Store `{value, unit, raw_value, raw_unit, raw_measurand, raw_phase, raw_location, context}`.
- Skip entries with a missing/non-numeric value, per entry.
- Return the merged map and the latest `timestamp`.

**`find_value(values, measurand, phase=None, location=None) -> float | None`**

- `None` is a wildcard for that dimension.
- If multiple locations match, prefer `Outlet`, then `ChargingStation`, then the first sorted key.
- If multiple phases match and `phase` is `None`, prefer the no-phase total; otherwise sum current/power, average voltage/power factor, and take the maximum temperature.
- This makes `Voltage` an average of `L1`/`L2`/`L3` while keeping per-phase values in attributes.

**`summarize_phases(values, measurand) -> tuple[float | None, dict[str, float]]`** returns the aggregate state and per-phase attributes.

**`summarize_temperature(values) -> tuple[float | None, dict[str, float]]`** returns the maximum and per-location attributes.

**`effective_limit(schedule, now) -> tuple[float | None, list[dict], bool]`**

- Input is a flattened `CompositeSchedule` with `schedule_start`, `duration`, `charging_rate_unit`, and `charging_schedule_period`.
- Sort periods by `start_period`; select the last period whose start has passed.
- Before `schedule_start`, use the first period.
- If `duration` is present and `now > schedule_start + duration`, return `(None, formatted_periods, True)` so a stale schedule does not display a number forever. With no duration, the last period continues (OCPP semantics).
- Return formatted periods with absolute ISO start times, limit, and `number_phases`.

### 2. State and OCPP handling

Extend `ElecqChargerState` with:

- `meter_values: dict[str, dict]`, `last_meter_timestamp: str | None`
- `composite_schedule: dict | None`, `composite_schedule_status: str | None`,
  `composite_schedule_status_info: str | None`, `composite_schedule_last_update: str | None`
- `charging_limit_source: str | None`, `charging_limit_grid_critical: bool | None`,
  `charging_limit_schedule: dict | None`, `charging_limit_last_update: str | None`
- `command_in_flight: bool` (skip new polls while a start/stop/refresh command is being sent)

In `ElecqOcppManager`:

- `update_meter_values()` calls `normalize_meter_values`, merges into state, and keeps the existing power/energy/session calculations.
  - Power state: prefer `Power.Active.Import` at `Outlet` with no phase; if only per-phase values exist, sum them at `Outlet`. Convert W → kW.
  - Energy state: prefer `Energy.Active.Import.Register` at `Outlet`; convert Wh → kWh. Session energy works as today.
- `async_request_composite_schedule()` sends `call.GetCompositeSchedule(duration=3600, evse_id=self.evse_id)`.
  - On success: flatten the returned `CompositeScheduleType` into a plain dict, store status/status_info, set `composite_schedule_last_update`, notify.
  - On rejection: store the status/status_info, notify, log at debug.
  - On exception/timeout: store `status = "Error"` and the exception text, notify, log at debug.
- `_async_schedule_poll_loop()`:
  - Wait for a per-connection `asyncio.Event` that is set on connect and cleared on disconnect.
  - After the event is set: sleep 10 s, poll if connected, then sleep 60 s after an accepted response or 300 s after a rejection/error.
  - Exit the inner loop on disconnect, then wait for the next connection.
  - Catch `asyncio.CancelledError` and exit.
- Start the poll task from `async_start_server()` with `hass.async_create_task`; cancel and await it in `async_stop_server()`.
- On disconnect, set `composite_schedule_status = "Disconnected"` and notify; keep the last values for display.
- On reconnect, the event triggers a fresh first poll.

Add handlers to `ElecqChargePoint`:

- `@on("MeterValues")` → `update_meter_values(meter_value)`, return `call_result.MeterValues()`.
- `@on("NotifyChargingLimit")` → store `charging_limit.source`, `is_grid_critical`, and the flattened `charging_schedule` (if present); timestamp it; schedule a background `async_request_composite_schedule()`; return `call_result.NotifyChargingLimit()`. Do not await the refresh inline.
- `@on("ClearedChargingLimit")` → clear source/grid-critical/schedule, timestamp it, schedule a refresh; return `call_result.ClearedChargingLimit()`.

Start/stop/refresh set `command_in_flight` around their `_cp.call()` so a new poll is skipped. An already-running poll may still delay a command; this is accepted and documented.

**Effective limit precedence:** use the last accepted `GetCompositeSchedule` result when available. If there is no accepted schedule, fall back to the schedule attached to `NotifyChargingLimit`. The sensor attributes say which source was used. This keeps a numeric limit visible if the charger rejects the poll but does send a local limit notification.

### 3. Sensors

Add to `sensor.py` and register in `async_setup_entry`:

| Entity name | Suggested ID | Unit / class | State |
|---|---|---|---|
| Current L1 | `sensor.elecq_au101_charger_current_l1` | A, current, measurement | `Current.Import` phase L1 |
| Current L2 | `..._current_l2` | A, current, measurement | phase L2 |
| Current L3 | `..._current_l3` | A, current, measurement | phase L3 |
| Voltage | `..._voltage` | V, voltage, measurement | average phase voltage; attributes per phase |
| Frequency | `..._frequency` | Hz, frequency, measurement | `Frequency` |
| Temperature | `..._temperature` | °C, temperature, measurement | max over locations; attributes per location |
| Power Factor | `..._power_factor` | none, measurement | average over phases; attributes per phase |
| Effective Limit | `..._effective_limit` | A or W, measurement | current limit from the schedule |
| Meter Values | `..._meter_values` | diagnostic | latest meter timestamp; attributes are the raw normalized map |

Effective Limit attributes: `status`, `status_info`, `evse_id`, `schedule_start`, `duration`, `charging_rate_unit`, `periods`, `expired`, `limit_source`, `grid_critical`, `last_updated`. Its native unit follows the schedule's A/W unit; state is `None` when no schedule or when expired.

No existing entity is renamed. The nine current entities keep their IDs.

### 4. Failure handling

- Unsupported/rejected `GetCompositeSchedule`: Effective Limit has no numeric state unless a `NotifyChargingLimit` fallback exists; its `status` attribute explains why. Polling backs off to 300 s.
- `NotifyChargingLimit`/`ClearedChargingLimit` are optional; absence is not an error, the poll covers it.
- Missing measurands leave the corresponding sensor unknown; the raw diagnostic sensor still shows everything received.
- Malformed meter values are skipped per value, not per message.
- On unload, the poll task is cancelled; on disconnect, the poll loop waits for the next connection.

### 5. Documentation and release

- README and `info.md`: add the new read-only sensors and note that no control messages were added.
- CHANGELOG: `v1.1.0 — read-only telemetry`.
- `manifest.json`: version `1.1.0`.
- Tag `v1.1.0` after verification. The existing dashboard is unchanged.

## My calls

- Aggregate voltage rather than three voltage sensors; per-phase values remain attributes. Current stays per-phase because imbalance matters on a 3-phase charger.
- Temperature is one max sensor with per-location attributes rather than one entity per probe.
- Effective Limit keeps the charger's native A/W unit and changes unit dynamically; this is a diagnostic-style reading, not a controlled setpoint.
- Raw values live on one diagnostic sensor so future measurands are not lost while keeping the entity count fixed.
- Poll interval 60 s / retry 300 s / first poll 10 s after each connect.
- Bump to 1.1.0 because this adds new entities, not a patch.
- OCPP defaults are filled during normalization; the raw fields preserve what the charger actually sent.
- `find_value(None, None)` is a wildcard, not a literal `-` bucket; location preference is `Outlet`, then `ChargingStation`, then first sorted.
- Units are converted to canonical values; `Power.Factor: Percent` is divided by 100, temperatures are converted to °C, and the existing power/energy entities keep their kW/kWh units.
- An expired schedule reports no numeric limit rather than the last period forever.
- A local limit attached to `NotifyChargingLimit` is used only as a fallback when no accepted composite schedule exists.
- "First poll 10 s after connect" is per connection via an event, not a one-time delay after task start.

## Verification

- Unit tests for `readings.py`: multiplier conversion, canonical unit conversion, OCPP defaults, phase/location keying, location preference, malformed values, effective-limit selection before/during/after periods, expiry, missing schedule, formatted period timestamps.
- Existing 8 dashboard/manifest tests still pass.
- Manual: with the charger connected, confirm the new sensors populate; confirm `Effective Limit` shows a number and schedule attributes; confirm the status attribute when the charger rejects the message.
- Manual: confirm start/stop still works and no control writes appear in the OCPP log.
- If the charger's field names/units differ from expectations, capture one debug `TransactionEvent`/`MeterValues` payload and adjust the normalizer; the design deliberately keeps that logic isolated.

## Left to the builder

Exact icons, log wording, sensor ordering, and the precise wording of the README table.
