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



class AdditionalNormalizationTest(unittest.TestCase):
    def test_standard_kelvin_unit(self):
        values, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [
                        {
                            "value": "273.15",
                            "measurand": "Temperature",
                            "unit_of_measure": {"unit": "K"},
                        }
                    ],
                }
            ]
        )
        self.assertAlmostEqual(values["Temperature|-|Outlet"]["value"], 0.0)
        self.assertEqual(values["Temperature|-|Outlet"]["unit"], "Celsius")

    def test_multiplier_is_applied(self):
        values, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [
                        {
                            "value": "12.5",
                            "measurand": "Energy.Active.Import.Register",
                            "unit_of_measure": {"unit": "kWh", "multiplier": 1},
                        }
                    ],
                }
            ]
        )
        self.assertEqual(
            values["Energy.Active.Import.Register|-|Outlet"]["value"], 125000.0
        )

    def test_milliamp_and_millivolt(self):
        values, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [
                        {
                            "value": "16000",
                            "measurand": "Current.Import",
                            "phase": "L1",
                            "unit_of_measure": {"unit": "mA"},
                        },
                        {
                            "value": "230000",
                            "measurand": "Voltage",
                            "phase": "L1",
                            "unit_of_measure": {"unit": "mV"},
                        },
                    ],
                }
            ]
        )
        self.assertEqual(values["Current.Import|L1|Outlet"]["value"], 16.0)
        self.assertEqual(values["Voltage|L1|Outlet"]["value"], 230.0)

    def test_unknown_measurand_is_retained(self):
        values, _ = readings.normalize_meter_values(
            [
                {
                    "timestamp": "2026-11-22T10:00:00Z",
                    "sampled_value": [
                        {
                            "value": "7",
                            "measurand": "Vendor.Metric",
                            "unit_of_measure": {"unit": "x"},
                        }
                    ],
                }
            ]
        )
        self.assertEqual(values["Vendor.Metric|-|Outlet"]["value"], 7.0)
        self.assertEqual(values["Vendor.Metric|-|Outlet"]["unit"], "x")


class FallbackLocationTest(unittest.TestCase):
    def test_fallback_location_does_not_mix_locations(self):
        values = {
            "Voltage|L1|Body": {"value": 100.0},
            "Voltage|L1|Inlet": {"value": 230.0},
        }
        self.assertEqual(readings.find_value(values, "Voltage"), 100.0)


class EffectiveScheduleSelectionTest(unittest.TestCase):
    def setUp(self):
        self.schedule = {"schedule_start": "2026-11-22T10:00:00+00:00"}
        self.fallback = {"schedule_start": "2026-11-22T09:00:00+00:00"}

    def test_accepted_schedule_is_used(self):
        self.assertEqual(
            readings.select_effective_schedule(
                self.schedule, "Accepted", self.fallback
            ),
            (self.schedule, "CompositeSchedule"),
        )

    def test_disconnected_schedule_is_still_used(self):
        self.assertEqual(
            readings.select_effective_schedule(
                self.schedule, "Disconnected", self.fallback
            ),
            (self.schedule, "CompositeSchedule"),
        )

    def test_rejected_schedule_falls_back(self):
        self.assertEqual(
            readings.select_effective_schedule(
                self.schedule, "Rejected", self.fallback
            ),
            (self.fallback, "NotifyChargingLimit"),
        )

    def test_error_schedule_falls_back(self):
        self.assertEqual(
            readings.select_effective_schedule(self.schedule, "Error", self.fallback),
            (self.fallback, "NotifyChargingLimit"),
        )

    def test_no_schedule_returns_none(self):
        self.assertEqual(
            readings.select_effective_schedule(None, None, None), (None, None)
        )


class NotificationHandlerSourceTest(unittest.TestCase):
    def test_notification_handlers_use_the_guarded_poll_loop(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "elecq_ocpp"
            / "ocpp_server.py"
        ).read_text()
        notify = source.split('@on("NotifyChargingLimit")')[1].split(
            '@on("ClearedChargingLimit")'
        )[0]
        cleared = source.split('@on("ClearedChargingLimit")')[1]
        for section in (notify, cleared):
            self.assertNotIn("async_create_task", section)
            self.assertIn("request_schedule_refresh", section)


if __name__ == "__main__":
    unittest.main()
