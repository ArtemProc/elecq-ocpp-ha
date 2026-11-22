from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import websockets
from websockets.server import WebSocketServer
from websockets.exceptions import ConnectionClosed

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from ocpp.routing import on
from ocpp.v201 import ChargePoint as OcppChargePointBase
from ocpp.v201 import call, call_result
from ocpp.v201.enums import (
    RegistrationStatusEnumType,
    RequestStartStopStatusEnumType,
    MessageTriggerEnumType,  # 👈 NEW
)

from .const import (
    COMPOSITE_SCHEDULE_DURATION,
    COMPOSITE_SCHEDULE_INITIAL_DELAY,
    COMPOSITE_SCHEDULE_POLL_INTERVAL,
    COMPOSITE_SCHEDULE_RETRY_INTERVAL,
    SIGNAL_STATE_UPDATED,
)
from .readings import find_value, normalize_meter_values

_LOGGER = logging.getLogger(__name__)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_info_text(status_info: Any) -> str | None:
    if status_info is None:
        return None
    if isinstance(status_info, dict):
        reason = status_info.get("reason_code") or status_info.get("reasonCode")
        additional = status_info.get("additional_info") or status_info.get("additionalInfo")
    else:
        reason = getattr(status_info, "reason_code", None)
        additional = getattr(status_info, "additional_info", None)
    parts = [str(part) for part in (reason, additional) if part]
    return " — ".join(parts) or None


def _flatten_composite_schedule(schedule: Any) -> dict[str, Any] | None:
    if schedule is None:
        return None
    if isinstance(schedule, dict):
        return schedule
    periods = []
    for period in getattr(schedule, "charging_schedule_period", []) or []:
        periods.append(
            {
                "start_period": getattr(period, "start_period", None),
                "limit": getattr(period, "limit", None),
                "number_phases": getattr(period, "number_phases", None),
                "phase_to_use": getattr(period, "phase_to_use", None),
            }
        )
    return {
        "evse_id": getattr(schedule, "evse_id", None),
        "duration": getattr(schedule, "duration", None),
        "schedule_start": _text(getattr(schedule, "schedule_start", None)),
        "charging_rate_unit": _text(getattr(schedule, "charging_rate_unit", None)),
        "charging_schedule_period": periods,
    }


def _flatten_charging_schedule(schedule: Any, fallback_start: str) -> dict[str, Any] | None:
    if schedule is None:
        return None

    def get(key: str, default: Any = None) -> Any:
        if isinstance(schedule, dict):
            return schedule.get(key, default)
        return getattr(schedule, key, default)

    periods = []
    for period in get("charging_schedule_period", []) or []:
        if isinstance(period, dict):
            periods.append(
                {
                    "start_period": period.get("start_period"),
                    "limit": period.get("limit"),
                    "number_phases": period.get("number_phases"),
                    "phase_to_use": period.get("phase_to_use"),
                }
            )
        else:
            periods.append(
                {
                    "start_period": getattr(period, "start_period", None),
                    "limit": getattr(period, "limit", None),
                    "number_phases": getattr(period, "number_phases", None),
                    "phase_to_use": getattr(period, "phase_to_use", None),
                }
            )
    return {
        "evse_id": get("evse_id"),
        "duration": get("duration"),
        "schedule_start": _text(get("start_schedule")) or fallback_start,
        "charging_rate_unit": _text(get("charging_rate_unit")),
        "charging_schedule_period": periods,
    }


@dataclass
class ElecqChargerState:
    """Live state from Elecq charger."""

    power_kw: Optional[float] = None
    power_kw_smoothed: Optional[float] = None

    energy_kwh: Optional[float] = None

    session_energy_kwh: Optional[float] = None
    session_start: Optional[datetime] = None
    session_start_meter_kwh: Optional[float] = None
    session_event_type: Optional[str] = None
    session_trigger_reason: Optional[str] = None

    plugged_in: bool = False
    charging: bool = False

    remote_stop_requested: bool = False

    transaction_id: Optional[str] = None
    last_status: Optional[str] = None
    last_charging_state: Optional[str] = None

    last_transaction_info: Optional[dict[str, Any]] = None
    last_meter_value: Optional[list[dict[str, Any]]] = None
    meter_values: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_meter_timestamp: Optional[str] = None

    composite_schedule: Optional[dict[str, Any]] = None
    composite_schedule_status: Optional[str] = None
    composite_schedule_status_info: Optional[str] = None
    composite_schedule_last_update: Optional[str] = None

    charging_limit_source: Optional[str] = None
    charging_limit_grid_critical: Optional[bool] = None
    charging_limit_schedule: Optional[dict[str, Any]] = None
    charging_limit_last_update: Optional[str] = None

    command_in_flight: bool = False

    last_update: Optional[datetime] = None


class ElecqOcppManager:
    """Manager running OCPP server & storing charger state."""

    def __init__(
        self,
        hass: HomeAssistant,
        port: int,
        id_token: str,
        evse_id: int,
        connector_id: int,
    ) -> None:
        self.hass = hass
        self.port = port
        self.id_token = id_token
        self.evse_id = evse_id
        self.connector_id = connector_id

        self._cp: Optional[ElecqChargePoint] = None
        self._server: Optional[WebSocketServer] = None

        self.state = ElecqChargerState()

        self._power_window: list[float] = []
        self._max_power_samples: int = 5
        self._schedule_task: Optional[asyncio.Task[None]] = None
        self._connected_event = asyncio.Event()
        self._schedule_wakeup = asyncio.Event()
        self._connection_generation = 0

    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_STATE_UPDATED)

    def _update_power_smoothing(self, power_kw: float) -> None:
        self._power_window.append(power_kw)
        if len(self._power_window) > self._max_power_samples:
            self._power_window.pop(0)
        self.state.power_kw_smoothed = sum(self._power_window) / len(self._power_window)

    def _update_session_energy(self, total_kwh: float) -> None:
        st = self.state
        if st.session_start is None:
            return
        if st.session_start_meter_kwh is None:
            st.session_start_meter_kwh = total_kwh
        st.session_energy_kwh = max(0.0, total_kwh - st.session_start_meter_kwh)

    def update_meter_values(self, meter_value: list[dict[str, Any]]) -> None:
        """Normalize and store all sampled values from meterValue[]."""
        st = self.state
        current_values, latest_timestamp = normalize_meter_values(meter_value)
        st.meter_values = {**st.meter_values, **current_values}
        if latest_timestamp:
            st.last_meter_timestamp = latest_timestamp

        power_w = find_value(current_values, "Power.Active.Import")
        if power_w is not None:
            st.power_kw = power_w / 1000.0
            self._update_power_smoothing(st.power_kw)

        total_wh = find_value(current_values, "Energy.Active.Import.Register")
        if total_wh is not None:
            st.energy_kwh = total_wh / 1000.0
            self._update_session_energy(st.energy_kwh)

        st.last_meter_value = meter_value
        st.last_update = datetime.now(timezone.utc)
        self._notify()

    def update_transaction_event(
        self,
        event_type: str,
        trigger_reason: str | None,
        transaction_info: dict[str, Any] | None,
        meter_value: list[dict[str, Any]] | None,
    ) -> None:
        """Handle TransactionEvent from charger."""
        st = self.state

        st.session_event_type = event_type
        st.session_trigger_reason = trigger_reason
        st.last_transaction_info = transaction_info

        if meter_value:
            self.update_meter_values(meter_value)

        if transaction_info:
            charging_state = (
                transaction_info.get("charging_state")
                or transaction_info.get("chargingState")
            )
            transaction_id = (
                transaction_info.get("transaction_id")
                or transaction_info.get("transactionId")
            )
            stopped_reason = (
                transaction_info.get("stopped_reason")
                or transaction_info.get("stoppedReason")
            )

            if stopped_reason == "EVDisconnected":
                _LOGGER.info(
                    "EV disconnected (stoppedReason=EVDisconnected). "
                    "Marking unplugged and clearing session."
                )
                st.plugged_in = False
                st.charging = False
                st.transaction_id = None
                st.last_charging_state = "Idle"
                st.remote_stop_requested = False
                st.session_start = None
                st.session_start_meter_kwh = None
                st.last_update = datetime.now(timezone.utc)
                self._notify()
                return

            st.last_charging_state = charging_state
            st.transaction_id = transaction_id

            # Treat EVConnected as "charging session active" for UI
            if charging_state in ("Charging", "EVConnected"):
                if st.remote_stop_requested:
                    _LOGGER.info(
                        "Charger reports %s but remote stop requested; "
                        "keeping charging=False.",
                        charging_state,
                    )
                    st.charging = False
                else:
                    st.charging = True
            elif charging_state in ("Idle", "Finished", "SuspendedEV", "SuspendedEVSE"):
                st.charging = False

        if event_type == "Started":
            st.session_start = datetime.now(timezone.utc)
            st.session_start_meter_kwh = st.energy_kwh
            st.session_energy_kwh = 0.0
            st.remote_stop_requested = False
        elif event_type in ("Ended", "Stopped"):
            st.session_start = None
            st.session_start_meter_kwh = None
            st.remote_stop_requested = False
            st.charging = False

        st.last_update = datetime.now(timezone.utc)
        self._notify()

    @property
    def is_available(self) -> bool:
        return self._cp is not None

    @property
    def effective_schedule(self) -> Optional[dict[str, Any]]:
        if (
            self.state.composite_schedule is not None
            and (self.state.composite_schedule_status or "").lower() == "accepted"
        ):
            return self.state.composite_schedule
        return self.state.charging_limit_schedule

    @property
    def effective_schedule_source(self) -> Optional[str]:
        if (
            self.state.composite_schedule is not None
            and (self.state.composite_schedule_status or "").lower() == "accepted"
        ):
            return "CompositeSchedule"
        if self.state.charging_limit_schedule is not None:
            return "NotifyChargingLimit"
        return None

    async def async_request_composite_schedule(self) -> Optional[dict[str, Any]]:
        if self._cp is None:
            return None

        try:
            response = await self._cp.call(
                call.GetCompositeSchedule(
                    duration=COMPOSITE_SCHEDULE_DURATION,
                    evse_id=self.evse_id,
                )
            )
        except Exception as err:  # noqa: BLE001
            self._set_composite_schedule_error(err)
            _LOGGER.debug("GetCompositeSchedule failed: %s", err)
            return None

        if response is None:
            self._set_composite_schedule_error("No response")
            _LOGGER.debug("GetCompositeSchedule returned no response")
            return None

        st = self.state
        st.composite_schedule_status = _text(getattr(response, "status", None))
        st.composite_schedule_status_info = _status_info_text(
            getattr(response, "status_info", None)
        )
        st.composite_schedule = _flatten_composite_schedule(
            getattr(response, "schedule", None)
        )
        st.composite_schedule_last_update = _now_iso()
        self._notify()
        return st.composite_schedule

    def _set_composite_schedule_error(self, error: Any) -> None:
        st = self.state
        st.composite_schedule_status = "Error"
        st.composite_schedule_status_info = str(error)
        st.composite_schedule_last_update = _now_iso()
        self._notify()

    async def _sleep_or_wake(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._schedule_wakeup.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            self._schedule_wakeup.clear()

    async def _async_schedule_poll_loop(self) -> None:
        try:
            while True:
                await self._connected_event.wait()
                generation = self._connection_generation
                await asyncio.sleep(COMPOSITE_SCHEDULE_INITIAL_DELAY)
                self._schedule_wakeup.clear()

                while (
                    self._cp is not None
                    and self._connection_generation == generation
                ):
                    if not self.state.command_in_flight:
                        await self.async_request_composite_schedule()

                    interval = (
                        COMPOSITE_SCHEDULE_POLL_INTERVAL
                        if (self.state.composite_schedule_status or "").lower()
                        == "accepted"
                        else COMPOSITE_SCHEDULE_RETRY_INTERVAL
                    )
                    if (
                        self._cp is None
                        or self._connection_generation != generation
                    ):
                        break
                    await self._sleep_or_wake(interval)

                if self._cp is None:
                    self._connected_event.clear()
        except asyncio.CancelledError:
            raise

    async def async_request_start(self) -> bool:
        if self._cp is None:
            _LOGGER.warning("Cannot start transaction: no charger connected.")
            return False

        request = call.RequestStartTransaction(
            evse_id=self.evse_id,
            id_token={"idToken": self.id_token, "type": "Local"},
            remote_start_id=int(datetime.now().timestamp()),
        )
        _LOGGER.info("Sending RequestStartTransaction: %s", request)
        self.state.command_in_flight = True
        try:
            response = await self._cp.call(request)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Error sending RequestStartTransaction: %s", err)
            return False
        finally:
            self.state.command_in_flight = False

        _LOGGER.info("RequestStartTransaction response: %s", response)
        ok = (
            getattr(response, "status", None)
            == RequestStartStopStatusEnumType.accepted
        )
        if ok:
            self.state.remote_stop_requested = False
            self._notify()
        return ok

    async def async_request_stop(self) -> bool:
        st = self.state
        if self._cp is None or not st.transaction_id:
            _LOGGER.warning(
                "Cannot stop transaction: no active transaction_id or CP."
            )
            return False

        request = call.RequestStopTransaction(transaction_id=st.transaction_id)
        _LOGGER.info("Sending RequestStopTransaction: %s", request)
        self.state.command_in_flight = True
        try:
            response = await self._cp.call(request)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Error sending RequestStopTransaction: %s", err)
            return False
        finally:
            self.state.command_in_flight = False

        _LOGGER.info("RequestStopTransaction response: %s", response)
        ok = (
            getattr(response, "status", None)
            == RequestStartStopStatusEnumType.accepted
        )
        if ok:
            st.remote_stop_requested = True
            st.charging = False
            self._notify()
        return ok

    async def async_request_refresh(self) -> None:
        """Ask the charger to send a fresh StatusNotification via TriggerMessage."""
        if self._cp is None:
            _LOGGER.debug(
                "Refresh request skipped: no charge point connected yet."
            )
            return

        self.state.command_in_flight = True
        try:
            # NOTE: do NOT pass evse_id here; this ocpp version doesn't accept it
            req = call.TriggerMessage(
                requested_message=MessageTriggerEnumType.status_notification,
            )
            _LOGGER.info(
                "Sending TriggerMessage(StatusNotification) to Elecq for manual refresh: %s",
                req,
            )
            resp = await self._cp.call(req)
            _LOGGER.info(
                "TriggerMessage(StatusNotification) response from Elecq: %s", resp
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Error sending TriggerMessage(StatusNotification)")
        finally:
            self.state.command_in_flight = False

    async def async_start_server(self) -> None:
        async def _on_connect(websocket):
            req = getattr(websocket, "request", None)
            if websocket.subprotocol != "ocpp2.0.1":
                _LOGGER.warning(
                    "Client did not negotiate ocpp2.0.1 (got %s) - closing.",
                    websocket.subprotocol,
                )
                await websocket.close()
                return

            path = req.path if req is not None else "/"
            cp_id = path.strip("/") or "unknown"
            _LOGGER.info("Elecq OCPP: new connection id=%s path=%s", cp_id, path)

            cp = ElecqChargePoint(cp_id, websocket, self)
            self._cp = cp
            self._connection_generation += 1
            self._connected_event.set()
            self._schedule_wakeup.set()

            try:
                await cp.start()
            except ConnectionClosed:
                _LOGGER.info("Elecq OCPP: connection closed for %s", cp_id)
            finally:
                if self._cp is cp:
                    st = self.state
                    st.charging = False
                    st.plugged_in = False
                    st.transaction_id = None
                    st.last_charging_state = None
                    st.last_status = "Disconnected"
                    st.remote_stop_requested = False
                    st.composite_schedule_status = "Disconnected"
                    st.last_update = datetime.now(timezone.utc)
                    self._cp = None
                    self._connected_event.clear()
                    self._schedule_wakeup.set()
                    self._notify()

        self._server = await websockets.serve(
            _on_connect,
            host="0.0.0.0",
            port=self.port,
            subprotocols=["ocpp2.0.1"],
        )
        _LOGGER.info(
            "Elecq OCPP 2.0.1 server listening on 0.0.0.0:%s",
            self.port,
        )
        if self._schedule_task is None:
            self._schedule_task = self.hass.async_create_task(
                self._async_schedule_poll_loop()
            )

    async def async_stop_server(self) -> None:
        if self._schedule_task is not None:
            self._schedule_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._schedule_task
            self._schedule_task = None

        self._connected_event.clear()
        self._schedule_wakeup.set()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            _LOGGER.info("Elecq OCPP server stopped.")


class ElecqChargePoint(OcppChargePointBase):
    """OCPP 2.0.1 ChargePoint handlers."""

    def __init__(self, cp_id: str, websocket, manager: ElecqOcppManager) -> None:
        super().__init__(cp_id, websocket)
        self._manager = manager

    @on("BootNotification")
    async def on_boot(self, charging_station, reason, **kwargs):
        _LOGGER.info(
            "BootNotification: model=%s, vendor=%s, reason=%s",
            charging_station.get("model"),
            charging_station.get("vendor_name")
            or charging_station.get("vendorName"),
            reason,
        )

        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=60,
            status=RegistrationStatusEnumType.accepted,
        )

    @on("Heartbeat")
    async def on_heartbeat(self, **kwargs):
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).isoformat(),
        )

    @on("StatusNotification")
    async def on_status(
        self,
        timestamp,
        evse_id,
        connector_id,
        connector_status,
        **kwargs,
    ):
        st = self._manager.state
        status_upper = (connector_status or "").upper()
        st.last_status = connector_status

        if status_upper in ("AVAILABLE", "FAULTED"):
            st.plugged_in = False
        else:
            st.plugged_in = True

        if st.last_charging_state in ("Charging", "EVConnected"):
            st.charging = not st.remote_stop_requested
        elif st.last_charging_state in ("Idle", "Finished", "SuspendedEV", "SuspendedEVSE"):
            st.charging = False
        else:
            st.charging = (
                status_upper == "CHARGING"
                and not st.remote_stop_requested
            )

        st.last_update = datetime.now(timezone.utc)
        self._manager._notify()

        return call_result.StatusNotification()

    @on("TransactionEvent")
    async def on_transaction_event(
        self,
        event_type,
        timestamp,
        trigger_reason,
        seq_no,
        transaction_info,
        evse=None,
        id_token=None,
        meter_value=None,
        **kwargs,
    ):
        _LOGGER.debug(
            "TransactionEvent: event_type=%s trigger_reason=%s seq_no=%s "
            "transaction_info=%s meter_value=%s",
            event_type,
            trigger_reason,
            seq_no,
            transaction_info,
            meter_value,
        )

        self._manager.update_transaction_event(
            event_type=event_type,
            trigger_reason=trigger_reason,
            transaction_info=transaction_info,
            meter_value=meter_value,
        )

        return call_result.TransactionEvent()

    @on("MeterValues")
    async def on_meter_values(self, evse_id, meter_value, **kwargs):
        self._manager.update_meter_values(meter_value)
        return call_result.MeterValues()

    @on("NotifyChargingLimit")
    async def on_notify_charging_limit(
        self,
        charging_limit,
        charging_schedule=None,
        evse_id=None,
        **kwargs,
    ):
        st = self._manager.state
        if isinstance(charging_limit, dict):
            source = charging_limit.get("charging_limit_source") or charging_limit.get(
                "chargingLimitSource"
            )
            grid_critical = charging_limit.get("is_grid_critical")
        else:
            source = getattr(charging_limit, "charging_limit_source", None)
            grid_critical = getattr(charging_limit, "is_grid_critical", None)

        st.charging_limit_source = _text(source)
        st.charging_limit_grid_critical = grid_critical
        if charging_schedule:
            st.charging_limit_schedule = _flatten_charging_schedule(
                charging_schedule[0], _now_iso()
            )
        else:
            st.charging_limit_schedule = None
        st.charging_limit_last_update = _now_iso()
        self._manager._notify()
        self._manager.hass.async_create_task(
            self._manager.async_request_composite_schedule()
        )
        return call_result.NotifyChargingLimit()

    @on("ClearedChargingLimit")
    async def on_cleared_charging_limit(
        self,
        charging_limit_source,
        evse_id=None,
        **kwargs,
    ):
        st = self._manager.state
        st.charging_limit_source = None
        st.charging_limit_grid_critical = None
        st.charging_limit_schedule = None
        st.charging_limit_last_update = _now_iso()
        self._manager._notify()
        self._manager.hass.async_create_task(
            self._manager.async_request_composite_schedule()
        )
        return call_result.ClearedChargingLimit()
