"""Coordinator for the Realme Smart Scale integration.

Responsibilities:
- Resolve a connectable BLE device through Home Assistant's bluetooth
  integration (local adapter or a proxy that supports active connections).
- Keep a persistent GATT session: 6-step handshake on 0xA624, 1-second
  keep-alive on 0xA622, notifications on 0xA621 / 0xA625.
- Parse live measurements and fan them out to listeners / HA events.
- Reconnect with bounded backoff whenever the link drops.

The RMH2011 does not broadcast its measurement passively (like Xiaomi scales
do): it only streams data to an actively connected GATT client, which is why
this integration requires a *connectable* Bluetooth source.  An ESPHome BLE
proxy that merely relays advertisements is not sufficient for the data path
(it may still assist discovery if the scale is not seen by a local adapter).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CHR_A621,
    CHR_A622,
    CHR_A624,
    CHR_A625,
    CONF_ADDRESS,
    CONNECT_RETRY_BASE_SECONDS,
    CONNECT_RETRY_MAX_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    DOMAIN,
    EVENT_MEASUREMENT,
    KEEP_ALIVE_CMD,
    KEEP_ALIVE_INITIAL_DELAY,
    KEEP_ALIVE_INTERVAL,
)
from .scale_controller import (
    ScaleMeasurement,
    ScaleUser,
    build_handshake,
    is_measurement_packet,
    mac_string_to_bytes,
    parse_measurement,
)

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
    """Maintains the active BLE session and the latest measurement."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS].upper()
        self.mac = mac_string_to_bytes(self.address)

        # The user profile is kept in entry.options so the options flow can
        # edit it without rewriting immutable data; reload picks up changes.
        profile = entry.options
        self.user = ScaleUser(
            name=str(profile.get("user_name", "")),
            sex=str(profile.get("sex", "male")),
            age=int(profile.get("age", 30)),
            height_cm=float(profile.get("height", 175.0)),
            activity_level=str(profile.get("activity_level", "moderate")),
            initial_weight=float(profile.get("initial_weight", 0.0)),
        )

        self.latest: ScaleMeasurement | None = None
        self.connected = False

        self._client: BleakClient | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._connect_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._closing = False
        self._disconnected_future: asyncio.Future | None = None
        self._lock = asyncio.Lock()

    def update_profile(self, options: dict[str, object]) -> None:
        """Swap the active user profile from an options-flow update.

        Local BIA re-computes from the new profile on the next measurement.
        The handshake profile is re-applied on the next reconnect (we do not
        tear down a healthy session just because the profile changed).
        """
        self.user = ScaleUser(
            name=str(options.get("user_name", self.user.name)),
            sex=str(options.get("sex", self.user.sex)),
            age=int(options.get("age", self.user.age)),
            height_cm=float(options.get("height", self.user.height_cm)),
            activity_level=str(
                options.get("activity_level", self.user.activity_level)
            ),
            initial_weight=float(
                options.get("initial_weight", self.user.initial_weight)
            ),
        )

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
        """Begin the background connect/run loop."""
        self._closing = False
        self._connect_task = asyncio.create_task(
            self._run_connection_loop(),
            name=f"{DOMAIN}_connect_{self.address}",
        )

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
                try:
                    await asyncio.sleep(retry_delay)
                except asyncio.CancelledError:
                    raise
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
            _LOGGER.info("Realme Smart Scale %s connected", self.address)

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
        if "write-without-response" in properties and "write" not in properties:
            return False
        return True

    async def _write_to(
        self, client: BleakClient, uuid: str, data: bytes
    ) -> None:
        """Write a command using openScale's characteristic-driven type."""
        await client.write_gatt_char(
            uuid, data, response=self._write_response(client, uuid)
        )

    async def _perform_handshake(self, client: BleakClient) -> None:
        """Write the 6-step handshake to 0xA624."""
        # Timezone offset (minutes) of the HA-configured time zone, matching
        # Kotlin's TimeZone.getDefault().getOffset(ms)/60000 semantics.
        tz_offset_min = _tz_offset_minutes(self.hass)
        commands = build_handshake(
            self.user, self.mac, tz_offset_min=tz_offset_min
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
            except Exception:  # noqa: BLE001
                pass
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
            measurement = parse_measurement(payload, self.mac, self.user)
            if measurement is None:
                return
            self._publish_measurement(measurement)
        except Exception:  # noqa: BLE001 - never let the stack die here
            _LOGGER.exception("Failed to handle scale notification")
    def _publish_measurement(self, measurement: ScaleMeasurement) -> None:
        """Store, notify and fire an HA event for one measurement."""
        self.latest = measurement
        self._async_notify_listeners()

        # Fire an event so automations can react without polling sensors.
        event_data: dict[str, Any] = {
            "address": self.address,
            "weight": round(measurement.weight_kg, 2),
            "measured_at": measurement.measured_at.isoformat(),
        }
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
