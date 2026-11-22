from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_MEASURAND = "Energy.Active.Import.Register"
DEFAULT_LOCATION = "Outlet"
DEFAULT_CONTEXT = "Sample.Periodic"

DEFAULT_UNITS = {
    "Energy.Active.Import.Register": "Wh",
    "Energy.Active.Export.Register": "Wh",
    "Power.Active.Import": "W",
    "Power.Active.Export": "W",
    "Current.Import": "A",
    "Current.Export": "A",
    "Current.Offered": "A",
    "Voltage": "V",
    "Frequency": "Hz",
    "Temperature": "Celsius",
}

PHASE_ALIASES = {"L1-N": "L1", "L2-N": "L2", "L3-N": "L3"}
CURRENT_MEASURANDS = {"Current.Import", "Current.Export", "Current.Offered"}
POWER_MEASURANDS = {
    "Power.Active.Import",
    "Power.Active.Export",
    "Power.Reactive.Import",
    "Power.Reactive.Export",
}
AVERAGE_MEASURANDS = {"Voltage", "Power.Factor"}
LOCATION_PRIORITY = {"Outlet": 0, "ChargingStation": 1}


def _text(value: Any) -> str | None:
    """Return an OCPP enum/string as plain text."""
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _phase(value: Any) -> str:
    phase = _text(value)
    if not phase:
        return "-"
    return PHASE_ALIASES.get(phase, phase)


def _canonical_value(measurand: str, value: float, unit: str | None) -> tuple[float, str | None]:
    unit = _text(unit)

    if measurand in ("Power.Active.Import", "Power.Active.Export"):
        if unit == "kW":
            return value * 1000.0, "W"
        if unit == "MW":
            return value * 1_000_000.0, "W"
        if unit in (None, "", "W"):
            return value, "W"

    if measurand in ("Energy.Active.Import.Register", "Energy.Active.Export.Register"):
        if unit == "kWh":
            return value * 1000.0, "Wh"
        if unit == "MWh":
            return value * 1_000_000.0, "Wh"
        if unit in (None, "", "Wh"):
            return value, "Wh"

    if measurand in CURRENT_MEASURANDS:
        if unit == "mA":
            return value / 1000.0, "A"
        if unit in (None, "", "A"):
            return value, "A"

    if measurand == "Voltage":
        if unit == "mV":
            return value / 1000.0, "V"
        if unit == "kV":
            return value * 1000.0, "V"
        if unit in (None, "", "V"):
            return value, "V"

    if measurand == "Frequency":
        if unit == "kHz":
            return value * 1000.0, "Hz"
        if unit in (None, "", "Hz"):
            return value, "Hz"

    if measurand == "Temperature":
        if unit == "Fahrenheit":
            return (value - 32.0) * 5.0 / 9.0, "Celsius"
        if unit == "Kelvin":
            return value - 273.15, "Celsius"
        if unit in (None, "", "Celsius"):
            return value, "Celsius"

    if measurand == "Power.Factor":
        if unit == "Percent":
            return value / 100.0, None
        return value, unit

    return value, unit


def normalize_meter_values(
    meter_value: list[dict[str, Any]],
    previous: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Normalize OCPP sampled values into a flat, canonical map."""
    values = dict(previous or {})
    latest_timestamp: str | None = None

    for mv in meter_value or []:
        timestamp = _text(mv.get("timestamp"))
        if timestamp:
            latest_timestamp = timestamp

        for sv in mv.get("sampled_value") or []:
            raw_measurand = _text(sv.get("measurand"))
            measurand = raw_measurand or DEFAULT_MEASURAND
            raw_phase = _text(sv.get("phase"))
            phase = _phase(raw_phase)
            raw_location = _text(sv.get("location")) or DEFAULT_LOCATION
            context = _text(sv.get("context")) or DEFAULT_CONTEXT

            uom = sv.get("unit_of_measure") or {}
            raw_unit = _text(uom.get("unit"))
            multiplier = uom.get("multiplier", 0) or 0
            raw_value = sv.get("value")

            try:
                value = float(raw_value) * (10 ** int(multiplier))
            except (TypeError, ValueError):
                continue

            unit = raw_unit or DEFAULT_UNITS.get(measurand)
            value, unit = _canonical_value(measurand, value, unit)

            key = f"{measurand}|{phase}|{raw_location}"
            values[key] = {
                "value": value,
                "unit": unit,
                "raw_value": raw_value,
                "raw_unit": raw_unit,
                "raw_measurand": raw_measurand,
                "raw_phase": raw_phase,
                "raw_location": raw_location,
                "context": context,
            }

    return values, latest_timestamp


def _entries(values: dict[str, dict[str, Any]], measurand: str) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for key, entry in values.items():
        parts = key.split("|", 2)
        if len(parts) != 3 or parts[0] != measurand:
            continue
        out.append((parts[1], parts[2], entry))
    return out


def _preferred_location(entries: list[tuple[str, str, dict[str, Any]]], location: str | None) -> list[tuple[str, str, dict[str, Any]]]:
    if location is not None:
        return [item for item in entries if item[1] == location]
    if not entries:
        return []
    ranked = sorted(
        entries,
        key=lambda item: (LOCATION_PRIORITY.get(item[1], 2), item[1]),
    )
    best_rank = LOCATION_PRIORITY.get(ranked[0][1], 2)
    return [item for item in ranked if LOCATION_PRIORITY.get(item[1], 2) == best_rank]


def find_value(
    values: dict[str, dict[str, Any]],
    measurand: str,
    phase: str | None = None,
    location: str | None = None,
) -> float | None:
    """Find a normalized value, preferring Outlet and a no-phase total."""
    entries = _preferred_location(_entries(values, measurand), location)
    if not entries:
        return None

    if phase is not None:
        wanted = _phase(phase)
        for item_phase, _item_location, entry in entries:
            if item_phase == wanted:
                return entry.get("value")
        return None

    for item_phase, _item_location, entry in entries:
        if item_phase == "-":
            return entry.get("value")

    phase_values = [
        entry.get("value")
        for item_phase, _item_location, entry in entries
        if item_phase in ("L1", "L2", "L3") and entry.get("value") is not None
    ]
    if not phase_values:
        return entries[0][2].get("value")

    if measurand in CURRENT_MEASURANDS or measurand in POWER_MEASURANDS:
        return sum(phase_values)
    if measurand in AVERAGE_MEASURANDS:
        return sum(phase_values) / len(phase_values)
    if measurand == "Temperature":
        return max(phase_values)
    return phase_values[0]


def summarize_phases(
    values: dict[str, dict[str, Any]],
    measurand: str,
    location: str = "Outlet",
) -> tuple[float | None, dict[str, float]]:
    """Return an average phase value and the per-phase values."""
    phases: dict[str, float] = {}
    for item_phase, item_location, entry in _entries(values, measurand):
        if item_location != location:
            continue
        if item_phase not in ("L1", "L2", "L3"):
            continue
        value = entry.get("value")
        if value is not None:
            phases[item_phase] = value

    if not phases:
        return None, {}
    return sum(phases.values()) / len(phases), phases


def summarize_temperature(
    values: dict[str, dict[str, Any]],
) -> tuple[float | None, dict[str, float]]:
    """Return the maximum temperature and the per-location values."""
    locations: dict[str, float] = {}
    for _item_phase, item_location, entry in _entries(values, "Temperature"):
        value = entry.get("value")
        if value is not None:
            locations[item_location] = value

    if not locations:
        return None, {}
    return max(locations.values()), locations


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def effective_limit(
    schedule: dict[str, Any] | None,
    now: datetime,
) -> tuple[float | None, list[dict[str, Any]], bool]:
    """Return the current limit, formatted periods, and an expired flag."""
    if not schedule:
        return None, [], False

    start = _parse_datetime(schedule.get("schedule_start"))
    periods_raw = schedule.get("charging_schedule_period") or []
    if start is None or not periods_raw:
        return None, [], False

    periods = []
    for period in periods_raw:
        try:
            start_period = int(period["start_period"])
            limit = float(period["limit"])
        except (KeyError, TypeError, ValueError):
            continue
        periods.append(
            {
                "start_period": start_period,
                "limit": limit,
                "number_phases": period.get("number_phases"),
            }
        )

    if not periods:
        return None, [], False

    periods.sort(key=lambda item: item["start_period"])
    formatted = [
        {
            "start": (start + timedelta(seconds=period["start_period"])).isoformat(),
            "limit": period["limit"],
            "number_phases": period["number_phases"],
        }
        for period in periods
    ]

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    duration = schedule.get("duration")
    if duration is not None:
        try:
            duration_seconds = int(duration)
        except (TypeError, ValueError):
            duration_seconds = None
        if duration_seconds is not None and now > start + timedelta(seconds=duration_seconds):
            return None, formatted, True

    if now < start:
        chosen = periods[0]
    else:
        elapsed = (now - start).total_seconds()
        chosen = periods[0]
        for period in periods:
            if period["start_period"] <= elapsed:
                chosen = period
            else:
                break

    return chosen["limit"], formatted, False
