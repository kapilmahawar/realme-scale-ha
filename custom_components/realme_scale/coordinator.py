"""Coordinator for the Realme Smart Scale integration.

Responsibilities:
- Resolve a connectable BLE device through Home Assistant's bluetooth
  integration (local adapter or a proxy that supports active connections).
- Keep a persistent GATT session: 6-step handshake on 0xA624, 1-second
  keep-alive on 0xA622, notifications on 0xA621 / 0xA625.
- Maintain the per-entry *user registry* (multiple profiles), of which one
  is the "active" user whose profile is written into the handshake.
- Decode live measurements, attribute them to a user (weight proximity) or
  to the unknown queue, compute body composition under the attributed
  profile, persist every record and fan them out to listeners / HA events.
- Let unknown measurements be assigned to a user later (options flow menu
  or the ``realme_scale.assign_measurement`` service).

The RMH2011 does not broadcast its measurement passively (like Xiaomi scales
do): it only streams data to an actively connected GATT client, which is why
this integration requires a *connectable* Bluetooth source.  An ESPHome BLE
proxy that merely relays advertisements is not sufficient for the data path
(it may still assist discovery if the scale is not seen by a local adapter).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .assignment import UserReadingRef, attribute_measurement
from .const import (
    CHR_A621,
    CHR_A622,
    CHR_A624,
    CHR_A625,
    CONF_ACTIVE_USER_ID,
    CONF_ADDRESS,
    CONNECT_RETRY_BASE_SECONDS,
    CONNECT_RETRY_MAX_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    DOMAIN,
    EVENT_MEASUREMENT,
    FIELD_MEASUREMENT_ID,
    FIELD_STATUS,
    FIELD_USER,
    FIELD_USER_ID,
    KEEP_ALIVE_CMD,
    KEEP_ALIVE_INITIAL_DELAY,
    KEEP_ALIVE_INTERVAL,
    STATUS_ASSIGNED,
    STATUS_UNKNOWN,
)
from .records import measurement_from_record, record_from_measurement
from .scale_controller import (
    DecodedMeasurement,
    ScaleMeasurement,
    ScaleUser,
    build_handshake,
    compute_body_composition,
    decode_measurement,
    is_measurement_packet,
    mac_string_to_bytes,
    parse_user_options,
)
from .store import MeasurementStore

_LOGGER = logging.getLogger(__name__)


def _tz_offset_minutes(hass: HomeAssistant) -> int:
    """UTC offset in minutes for the HA-configured time zone."""
    import datetime as _dt

    import homeassistant.util.dt as dt_util

    now = _dt.datetime.now(tz=dt_util.get_time_zone(hass.config.time_zone))
    offset = now.utcoffset()
    if offset is None:
        return 0
    return int(offset.total_seconds() // 60)


class RealmeScaleCoordinator:
    """Maintains the active BLE session, users and the measurement queue."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS].upper()
        self.mac = mac_string_to_bytes(self.address)

        # User registry.  Active user's profile is written into the scale
        # handshake at (re)connect; every other profile exists for the local
        # BIA math and attribution.
        users, active_user_id, tolerance_kg, impedance_tol = parse_user_options(
            entry.options
        )
        self.users = users
        self.active_user_id = active_user_id
        self.tolerance_kg = tolerance_kg
        self.impedance_tolerance_ohm = impedance_tol
        self._user_by_id: dict[str, ScaleUser] = {
            user.user_id: user for user in self.users
        }
        # Snapshot for the options-update listener: reload only when the
        # stored options actually changed (avoids pointless reconnects).
        self._options_snapshot: dict[str, Any] = dict(entry.options)

        # Latest *attributed* measurement per user (from live packets or
        # from a later manual assignment).
        self.latest_by_user: dict[str, ScaleMeasurement] = {}

        self.connected = False
        self.store = MeasurementStore(hass, entry.entry_id)

        self._client: BleakClient | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._connect_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._closing = False
        self._disconnected_future: asyncio.Future | None = None
        self._lock = asyncio.Lock()
        self._store_loaded = False

    # ------------------------------------------------------------------
    # User registry API
    # ------------------------------------------------------------------

    def get_user(self, user_id: str) -> ScaleUser | None:
        """A user by id, or ``None``."""
        return self._user_by_id.get(user_id)

    def find_user(self, name_or_id: str) -> ScaleUser | None:
        """Resolve a user by id first, then by exact name."""
        user = self._user_by_id.get(name_or_id)
        if user is not None:
            return user
        lowered = name_or_id.casefold()
        for candidate in self.users:
            if candidate.name.casefold() == lowered:
                return candidate
        return None

    def active_user(self) -> ScaleUser | None:
        """The profile currently written into the scale handshake."""
        return self._user_by_id.get(self.active_user_id)

    def latest_measurement(self, user_id: str) -> ScaleMeasurement | None:
        """Latest attributed measurement for a user, or ``None``."""
        return self.latest_by_user.get(user_id)

    @property
    def unknown_count(self) -> int:
        """Number of measurements waiting to be assigned to a user."""
        return len(self.store.unknown_records())

    def unknown_records(self) -> list[dict[str, Any]]:
        """Unassigned measurement records (for UI / automation)."""
        return self.store.unknown_records()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Listen for measurement / connection state changes."""
        self._listeners.add(update_callback)
        return lambda: self._listeners.discard(update_callback)

    @callback
    def _async_notify_listeners(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Error notifying listener")

    async def async_start(self) -> None:
        """Load persisted records and begin the connect/run loop."""
        self._closing = False
        try:
            await self.store.async_load()
            self._store_loaded = True
        except Exception:  # pragma: no cover - storage must not block setup
            _LOGGER.exception("Failed to load measurement store")
        self._rehydrate_latest()
        self._connect_task = asyncio.create_task(
            self._run_connection_loop(),
            name=f"{DOMAIN}_connect_{self.address}",
        )

    def _rehydrate_latest(self) -> None:
        """Rebuild latest_by_user from stored records after a restart."""
        for user in self.users:
            record = self.store.latest_assigned_for_user(user.user_id)
            if record is not None:
                self.latest_by_user[user.user_id] = measurement_from_record(record)

    async def async_shutdown(self) -> None:
        """Cancel all background work and close the client."""
        self._closing = True
        if self._connect_task:
            self._connect_task.cancel()
            await asyncio.gather(self._connect_task, return_exceptions=True)
        await self._disconnect_client()

    async def async_reconnect(self) -> None:
        """Drop the current session and reconnect immediately."""
        if self._connect_task and not self._connect_task.done():
            self._connect_task.cancel()
            await asyncio.gather(self._connect_task, return_exceptions=True)
        self._connect_task = None
        await self._disconnect_client()
        self._closing = False
        await self.async_start()

    async def async_set_active_user(self, user_id: str) -> bool:
        """Switch the active user and re-handshake with their profile."""
        if user_id not in self._user_by_id:
            return False
        options = dict(self.entry.options)
        options[CONF_ACTIVE_USER_ID] = user_id
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        # The options-update listener reloads the entry (new handshake).
        return True

    # ------------------------------------------------------------------
    # Connection loop
    # ------------------------------------------------------------------

    @callback
    def _handle_disconnected(self) -> None:
        """Runs on the event loop when the remote disconnects."""
        self.connected = False
        if self._disconnected_future and not self._disconnected_future.done():
            self._disconnected_future.set_result(None)
        self._async_notify_listeners()

    async def _run_connection_loop(self) -> None:
        """Endlessly try to establish and hold a GATT session."""
        retry_delay = CONNECT_RETRY_BASE_SECONDS
        while not self._closing:
            try:
                connected = await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - outer reconnect loop
                _LOGGER.warning(
                    "Connection attempt to %s failed: %s", self.address, err
                )
                connected = False

            if connected:
                retry_delay = CONNECT_RETRY_BASE_SECONDS
                # Block until the link drops (or shutdown is requested).
                try:
                    if self._disconnected_future is not None:
                        await asyncio.wait_for(
                            self._disconnected_future,
                            timeout=None,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:  # pragma: no cover
                    _LOGGER.exception("Unexpected error while connected")
                # Clean up the dead session before the next attempt.
                await self._disconnect_client()
            else:
                # Scale is probably sleeping or out of range; back off.
                _LOGGER.debug(
                    "Retrying %s in %.0fs", self.address, retry_delay
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, CONNECT_RETRY_MAX_SECONDS)

    async def async_resolve_ble_device(self) -> BLEDevice | None:
        """Return the connectable BLEDevice or None when unreachable."""
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )

    async def _connect_once(self) -> bool:
        """Try a single connect + handshake.  True when the link came up."""
        async with self._lock:
            ble_device = await self.async_resolve_ble_device()
            if ble_device is None:
                return False

            _LOGGER.debug("Connecting to %s", self.address)

            self._disconnected_future = asyncio.get_running_loop().create_future()

            def _on_disconnect(*_args) -> None:
                # bleak may invoke this from a worker thread; hop onto the
                # event loop before touching the future.
                self.hass.loop.call_soon_threadsafe(self._handle_disconnected)

            client = BleakClient(
                ble_device,
                disconnected_callback=_on_disconnect,
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
            self._client = client

            try:
                await client.connect(timeout=CONNECT_TIMEOUT_SECONDS)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Connect to %s failed: %s", self.address, err)
                await self._disconnect_client()
                return False

            try:
                await self._start_notifications(client)
                await self._perform_handshake(client)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Session setup with %s failed: %s", self.address, err)
                await self._disconnect_client()
                return False

            self.connected = True
            self._async_notify_listeners()
            _LOGGER.info(
                "Realme Smart Scale %s connected (active user: %s)",
                self.address,
                (self.active_user() or ScaleUser()).name,
            )

            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(client),
                name=f"{DOMAIN}_keepalive_{self.address}",
            )
            return True

    async def _start_notifications(self, client: BleakClient) -> None:
        """Subscribe to the measurement notification characteristics.

        bleak reports the characteristic *handle* (int), not the UUID, so we
        bind each UUID through a closure to keep the A621/A625 distinction.
        """
        await client.start_notify(
            CHR_A621, self._notification_handler(CHR_A621)
        )
        await client.start_notify(
            CHR_A625, self._notification_handler(CHR_A625)
        )

    def _notification_handler(self, uuid: str):
        """Return a bleak notification callback bound to a characteristic."""

        def _callback(_sender: int, data: bytearray) -> None:
            self._handle_notification(uuid, data)

        return _callback

    def _write_response(self, client: BleakClient, uuid: str) -> bool:
        """Choose the GATT write type like openScale's GattScaleAdapter.

        openScale uses write-with-response when the characteristic advertises
        PROPERTY_WRITE and write-without-response when it only has
        PROPERTY_WRITE_NO_RESPONSE.  bleak exposes these as characteristic
        properties ("write" / "write-without-response").
        """
        try:
            characteristic = client.services.get_characteristic(uuid)
        except Exception:  # noqa: BLE001 - service cache may not be ready
            characteristic = None
        if characteristic is None:
            # Default to with-response, matching openScale's writeTo default.
            return True
        properties = set(characteristic.properties)
        # Write-without-response only when that is the sole advertised type.
        return not (
            "write-without-response" in properties
            and "write" not in properties
        )

    async def _write_to(
        self, client: BleakClient, uuid: str, data: bytes
    ) -> None:
        """Write a command using openScale's characteristic-driven type."""
        await client.write_gatt_char(
            uuid, data, response=self._write_response(client, uuid)
        )

    async def _perform_handshake(self, client: BleakClient) -> None:
        """Write the 6-step handshake to 0xA624 using the active profile."""
        # Timezone offset (minutes) of the HA-configured time zone, matching
        # Kotlin's TimeZone.getDefault().getOffset(ms)/60000 semantics.
        tz_offset_min = _tz_offset_minutes(self.hass)
        user = self.active_user() or ScaleUser()
        commands = build_handshake(
            user, self.mac, tz_offset_min=tz_offset_min
        )
        for cmd in commands:
            await self._write_to(client, CHR_A624, cmd)
            # Let the peripheral breathe between writes.
            await asyncio.sleep(0.1)

    async def _keepalive_loop(self, client: BleakClient) -> None:
        """Send the keep-alive ping every second (mirrors Kotlin Timer)."""
        try:
            await asyncio.sleep(KEEP_ALIVE_INITIAL_DELAY)
            while not self._closing:
                try:
                    await self._write_to(client, CHR_A622, KEEP_ALIVE_CMD)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug("Keep-alive write failed: %s", err)
                    break
                await asyncio.sleep(KEEP_ALIVE_INTERVAL)
        except asyncio.CancelledError:
            pass
        finally:
            # If we end up here unexpectedly, nudge the reconnect loop.
            if (
                not self._closing
                and self._disconnected_future
                and not self._disconnected_future.done()
            ):
                self._disconnected_future.set_result(None)

    async def _disconnect_client(self) -> None:
        """Tear down the current session (idempotent)."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
            await asyncio.gather(self._keepalive_task, return_exceptions=True)
            self._keepalive_task = None

        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as err:  # noqa: BLE001 - best effort teardown
                _LOGGER.debug("Disconnect failed: %s", err)
        if self._disconnected_future and not self._disconnected_future.done():
            self._disconnected_future.set_result(None)
        self.connected = False

    # ------------------------------------------------------------------
    # Data path
    # ------------------------------------------------------------------

    def _handle_notification(self, characteristic: str, data: bytearray) -> None:
        """BLE notification callback (runs on the event loop)."""
        try:
            if characteristic != CHR_A621:
                return
            payload = bytes(data)
            if not is_measurement_packet(payload):
                return
            self._process_packet(payload)
        except Exception:
            _LOGGER.exception("Failed to handle scale notification")

    def _process_packet(self, data: bytes) -> None:
        """Decode, attribute, compute and publish one measurement packet."""
        decoded = decode_measurement(data, self.mac)
        if decoded is None:
            return

        user, candidates = self._attribute(decoded)
        measurement = ScaleMeasurement(
            weight_kg=decoded.weight_kg,
            measured_at=decoded.measured_at,
            impedance=decoded.impedance,
            raw=decoded.raw,
        )

        status = STATUS_UNKNOWN
        if user is not None:
            status = STATUS_ASSIGNED
            measurement.user_id = user.user_id
            measurement.user_name = user.name
            metrics = compute_body_composition(
                user, decoded.weight_kg, decoded.impedance
            )
            if metrics is not None:
                (
                    measurement.body_fat,
                    measurement.muscle,
                    measurement.water,
                    measurement.bone_kg,
                    measurement.lean_body_mass_kg,
                    measurement.visceral_fat,
                ) = metrics
            self.latest_by_user[user.user_id] = measurement
        elif candidates:
            # Ambiguous but plausible owners -> remember them so a prompt
            # (dashboard / notification) can offer one-tap assignment.
            measurement.candidate_user_ids = candidates

        record = record_from_measurement(measurement, status=status)
        self._publish_measurement(measurement, record, status)

        # Persist in the background; measurement handling must stay sync.
        if self._store_loaded:
            asyncio.create_task(self._async_persist_record(record))

    def _attribute(
        self, decoded: DecodedMeasurement
    ) -> tuple[ScaleUser | None, tuple[str, ...]]:
        """Attribute a decoded measurement; see assignment module policy.

        Returns ``(user, candidate_ids)`` where ``user`` is set for a
        confident match and ``candidate_ids`` lists plausible owners when
        the reading is ambiguous (both never set for the same reading).
        """
        if not self.users:
            return None, ()
        if len(self.users) == 1:
            return self.users[0], ()

        refs = [UserReadingRef(*self._reading_ref(user)) for user in self.users]
        result = attribute_measurement(
            refs,
            decoded.weight_kg,
            decoded.impedance,
            self.tolerance_kg,
            self.impedance_tolerance_ohm,
        )
        if result.user_id is not None:
            user = self._user_by_id.get(result.user_id)
            if user is not None:
                return user, ()
        return None, result.candidates

    def _reading_ref(self, user: ScaleUser) -> tuple[str, float | None, int | None]:
        """A user's best-known (weight, impedance) for attribution.

        Uses the user's latest attributed measurement when available, falling
        back to their configured initial weight (no impedance) otherwise.
        """
        latest = self.latest_by_user.get(user.user_id)
        if latest is not None:
            return user.user_id, latest.weight_kg, latest.impedance
        return (
            user.user_id,
            user.initial_weight if user.initial_weight > 0 else None,
            None,
        )

    async def _async_persist_record(self, record: dict[str, Any]) -> None:
        """Write one record; failures must never affect the live session."""
        try:
            await self.store.async_add(record)
        except Exception:  # pragma: no cover
            _LOGGER.exception("Failed to persist measurement record")
        self._async_notify_listeners()

    def _publish_measurement(
        self,
        measurement: ScaleMeasurement,
        record: dict[str, Any],
        status: str,
    ) -> None:
        """Notify listeners and fire an HA event for one measurement."""
        self._async_notify_listeners()

        # Fire an event so automations can react without polling sensors.
        event_data: dict[str, Any] = {
            "address": self.address,
            FIELD_MEASUREMENT_ID: record[FIELD_MEASUREMENT_ID],
            FIELD_STATUS: status,
            "weight": round(measurement.weight_kg, 2),
            "measured_at": measurement.measured_at.isoformat(),
        }
        if status == STATUS_ASSIGNED:
            event_data[FIELD_USER_ID] = measurement.user_id
            event_data[FIELD_USER] = measurement.user_name
        elif measurement.candidate_user_ids:
            # Offer the best guesses to a confirmation prompt.
            event_data["candidate_users"] = [
                self._user_by_id[candidate].name
                for candidate in measurement.candidate_user_ids
                if candidate in self._user_by_id
            ]
            event_data["candidate_user_ids"] = [
                candidate
                for candidate in measurement.candidate_user_ids
                if candidate in self._user_by_id
            ]
        for key in (
            "impedance",
            "body_fat",
            "muscle",
            "water",
            "bone_kg",
            "lean_body_mass_kg",
            "visceral_fat",
        ):
            value = getattr(measurement, key)
            if value is not None:
                event_data[key] = round(float(value), 2)
        self.hass.bus.async_fire(EVENT_MEASUREMENT, event_data)

    # ------------------------------------------------------------------
    # Manual (re)assignment (unknown queue / mis-assigned -> a user)
    # ------------------------------------------------------------------

    async def async_assign_measurement(
        self, measurement_id: str, user: ScaleUser
    ) -> tuple[bool, str]:
        """Assign (or re-assign) a stored measurement to ``user``.

        Recomputes the BIA figures under the chosen profile and refreshes
        the affected users' "latest" so sensors stay correct after a
        re-assignment.  Returns ``(success, message)``.
        """
        record = self.store.record(measurement_id)
        if record is None:
            return False, f"No stored measurement with id {measurement_id}"

        previous_owner = record.get("user_id")
        measurement = self._measurement_for_record(record, user)
        # Rewrite the record in place: same id + received order, new owner
        # and BIA figures recomputed under the assigned user's profile.
        updated = record_from_measurement(
            measurement,
            measurement_id=measurement_id,
            status=STATUS_ASSIGNED,
            received_at=record.get("received_at"),
        )
        record.clear()
        record.update(updated)
        await self.store.async_save()

        # Keep every user's "latest" consistent with the store afterwards
        # (a re-assignment may have taken a reading away from someone).
        for affected in {previous_owner, user.user_id}:
            if affected is not None:
                self._refresh_latest_from_store(affected)

        self._async_notify_listeners()
        _LOGGER.info(
            "Assigned measurement %s to %s (%.1f kg)",
            measurement_id,
            user.name,
            measurement.weight_kg,
        )
        return True, f"Assigned {measurement_id} to {user.name}"

    def _refresh_latest_from_store(self, user_id: str) -> None:
        """Point a user's latest at their most recent stored record."""
        record = self.store.latest_assigned_for_user(user_id)
        if record is None:
            self.latest_by_user.pop(user_id, None)
        else:
            self.latest_by_user[user_id] = measurement_from_record(record)

    def assigned_records(self, limit: int = 25) -> list[dict[str, Any]]:
        """Most recent assigned records (for the reassign menu)."""
        return self.store.assigned_records(limit)

    def _measurement_for_record(
        self, record: dict[str, Any], user: ScaleUser
    ) -> ScaleMeasurement:
        """Rebuild a measurement from a record, recomputed under ``user``."""
        measurement = measurement_from_record(record)
        measurement.user_id = user.user_id
        measurement.user_name = user.name
        metrics = compute_body_composition(
            user, measurement.weight_kg, measurement.impedance
        )
        for field in (
            "body_fat",
            "muscle",
            "water",
            "bone_kg",
            "lean_body_mass_kg",
            "visceral_fat",
        ):
            setattr(measurement, field, None)
        if metrics is not None:
            (
                measurement.body_fat,
                measurement.muscle,
                measurement.water,
                measurement.bone_kg,
                measurement.lean_body_mass_kg,
                measurement.visceral_fat,
            ) = metrics
        return measurement

    async def async_drop_user_records(self, user_id: str) -> None:
        """Remove stored records of a user that is being deleted."""
        await self.store.async_drop_user(user_id)
        self.latest_by_user.pop(user_id, None)
        self._async_notify_listeners()
