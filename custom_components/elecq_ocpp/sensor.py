from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from datetime import datetime, timezone

from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .const import DOMAIN, SIGNAL_STATE_UPDATED
from .ocpp_server import ElecqOcppManager
from .readings import effective_limit, find_value, summarize_phases, summarize_temperature


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    manager: ElecqOcppManager = data["manager"]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, "elecq_au101")},
        name="Elecq AU101 Charger",
        manufacturer="Elecq",
        model="AU101",
    )

    entities: list[SensorEntity] = [
        ElecqPowerSensor(manager, device_info),
        ElecqSmoothedPowerSensor(manager, device_info),
        ElecqEnergySensor(manager, device_info),
        ElecqSessionEnergySensor(manager, device_info),
        ElecqStatusSensor(manager, device_info),
        ElecqChargingStateSensor(manager, device_info),  # 👈 NEW
        ElecqCurrentSensor(manager, device_info, "L1"),
        ElecqCurrentSensor(manager, device_info, "L2"),
        ElecqCurrentSensor(manager, device_info, "L3"),
        ElecqVoltageSensor(manager, device_info),
        ElecqFrequencySensor(manager, device_info),
        ElecqTemperatureSensor(manager, device_info),
        ElecqPowerFactorSensor(manager, device_info),
        ElecqEffectiveLimitSensor(manager, device_info),
        ElecqMeterValuesSensor(manager, device_info),
    ]
    async_add_entities(entities)


class _BaseElecqSensor(SensorEntity):
    def __init__(self, manager: ElecqOcppManager, device_info: DeviceInfo) -> None:
        self._manager = manager
        self._attr_device_info = device_info

    def _find(self, measurand, phase=None, location=None):
        return find_value(self._manager.state.meter_values, measurand, phase, location)

    async def async_added_to_hass(self) -> None:
        async def _handle_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_STATE_UPDATED, _handle_update)
        )


class ElecqPowerSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Power"
    _attr_unique_id = "elecq_au101_power"
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._manager.state.power_kw


class ElecqSmoothedPowerSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Power (Smoothed)"
    _attr_unique_id = "elecq_au101_power_smoothed"
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._manager.state.power_kw_smoothed


class ElecqEnergySensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Total Energy"
    _attr_unique_id = "elecq_au101_energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        return self._manager.state.energy_kwh


class ElecqSessionEnergySensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Session Energy"
    _attr_unique_id = "elecq_au101_session_energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return self._manager.state.session_energy_kwh


class ElecqStatusSensor(_BaseElecqSensor):
    """Connector / charger status (Available, Occupied, etc.)."""

    _attr_has_entity_name = True
    _attr_name = "Charger Status"
    _attr_unique_id = "elecq_au101_status"

    @property
    def native_value(self):
        return self._manager.state.last_status or "Unknown"


class ElecqChargingStateSensor(_BaseElecqSensor):
    """
    Charging state as reported in TransactionEvent.transactionInfo.chargingState.

    - Shows raw values like 'Charging', 'Idle', 'SuspendedEV', etc.
    - For SuspendedEV (often EV fully charged) we show a friendlier label.
    """

    _attr_has_entity_name = True
    _attr_name = "Info"
    _attr_unique_id = "elecq_au101_charging_state"

    @property
    def native_value(self):
        cs = self._manager.state.last_charging_state
        if cs is None:
            return "Unknown"

        if cs == "SuspendedEV":
            # Typically means the EV has paused charging itself (often because it's full)
            return "Suspended by EV (possibly full)"

        return cs

    @property
    def extra_state_attributes(self):
        """Expose raw state for debugging/automation if desired."""
        st = self._manager.state
        attrs = {}
        if st.last_charging_state is not None:
            attrs["raw_charging_state"] = st.last_charging_state
        if st.transaction_id is not None:
            attrs["transaction_id"] = st.transaction_id
        return attrs

class ElecqCurrentSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        manager: ElecqOcppManager,
        device_info: DeviceInfo,
        phase: str,
    ) -> None:
        super().__init__(manager, device_info)
        self._phase = phase
        self._attr_name = f"Current {phase}"
        self._attr_unique_id = f"elecq_au101_current_{phase.lower()}"

    @property
    def native_value(self):
        return self._find("Current.Import", phase=self._phase)


class ElecqVoltageSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Voltage"
    _attr_unique_id = "elecq_au101_voltage"
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        value, _ = summarize_phases(self._manager.state.meter_values, "Voltage")
        return value

    @property
    def extra_state_attributes(self):
        _, phases = summarize_phases(self._manager.state.meter_values, "Voltage")
        return {"phases": phases} if phases else {}


class ElecqFrequencySensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Frequency"
    _attr_unique_id = "elecq_au101_frequency"
    _attr_native_unit_of_measurement = UnitOfFrequency.HERTZ
    _attr_device_class = SensorDeviceClass.FREQUENCY
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._find("Frequency")


class ElecqTemperatureSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Temperature"
    _attr_unique_id = "elecq_au101_temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        value, _ = summarize_temperature(self._manager.state.meter_values)
        return value

    @property
    def extra_state_attributes(self):
        _, locations = summarize_temperature(self._manager.state.meter_values)
        return {"locations": locations} if locations else {}


class ElecqPowerFactorSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Power Factor"
    _attr_unique_id = "elecq_au101_power_factor"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:angle-acute"

    @property
    def native_value(self):
        value, _ = summarize_phases(self._manager.state.meter_values, "Power.Factor")
        return value

    @property
    def extra_state_attributes(self):
        _, phases = summarize_phases(self._manager.state.meter_values, "Power.Factor")
        return {"phases": phases} if phases else {}


class ElecqEffectiveLimitSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Effective Limit"
    _attr_unique_id = "elecq_au101_effective_limit"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:speedometer"

    @property
    def _schedule(self):
        return self._manager.effective_schedule

    @property
    def native_value(self):
        limit, _, _ = effective_limit(self._schedule, datetime.now(timezone.utc))
        return limit

    @property
    def native_unit_of_measurement(self):
        schedule = self._schedule
        if not schedule:
            return None
        return schedule.get("charging_rate_unit")

    @property
    def extra_state_attributes(self):
        st = self._manager.state
        schedule = self._schedule
        _, periods, expired = effective_limit(schedule, datetime.now(timezone.utc))
        attrs = {
            "status": st.composite_schedule_status,
            "status_info": st.composite_schedule_status_info,
            "limit_source": self._manager.effective_schedule_source,
            "expired": expired,
            "periods": periods,
            "grid_critical": st.charging_limit_grid_critical,
            "last_updated": st.composite_schedule_last_update
            or st.charging_limit_last_update,
        }
        if schedule:
            attrs.update(
                {
                    "evse_id": schedule.get("evse_id"),
                    "schedule_start": schedule.get("schedule_start"),
                    "duration": schedule.get("duration"),
                    "charging_rate_unit": schedule.get("charging_rate_unit"),
                }
            )
        return attrs


class ElecqMeterValuesSensor(_BaseElecqSensor):
    _attr_has_entity_name = True
    _attr_name = "Meter Values"
    _attr_unique_id = "elecq_au101_meter_values"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:meter-electric"

    @property
    def native_value(self):
        return self._manager.state.last_meter_timestamp

    @property
    def extra_state_attributes(self):
        return {"values": self._manager.state.meter_values}

