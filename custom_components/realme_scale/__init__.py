"""The Realme Smart Scale (RMH2011) integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothReachabilityIntent
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    SERVICE_RECONNECT,
)
from .coordinator import RealmeScaleCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

type RealmeScaleConfigEntry = ConfigEntry[RealmeScaleCoordinator]

CONF_DEVICE_ID = "device_id"

RECONNECT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_DEVICE_ID): vol.Any(str, [str]),
    }
)


async def async_setup_entry(
    hass: HomeAssistant, entry: RealmeScaleConfigEntry
) -> bool:
    """Set up Realme Smart Scale from a config entry."""
    coordinator = RealmeScaleCoordinator(hass, entry)

    # If HA has never seen the scale as connectable, the GATT session cannot
    # be established; surface a helpful error instead of spinning forever.
    if not await coordinator.async_resolve_ble_device():
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={
                "address": coordinator.address,
                "reason": bluetooth.async_address_reachability_diagnostics(
                    hass,
                    coordinator.address,
                    BluetoothReachabilityIntent.CONNECTION,
                ),
            },
        )

    await coordinator.async_start()

    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Ensure the service exists once, then dispatch to the right entry.
    if not hass.services.has_service(DOMAIN, SERVICE_RECONNECT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RECONNECT,
            _async_reconnect_service,
            schema=RECONNECT_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: RealmeScaleConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_shutdown()
    if unload_ok and not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_RECONNECT)
    return unload_ok


async def _async_reconnect_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Drop and re-establish the BLE session for the targeted scale."""
    device_ids = call.data.get(CONF_DEVICE_ID)
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    coordinators = list(hass.data.get(DOMAIN, {}).values())

    if device_ids:
        dev_reg = dr.async_get(hass)
        wanted: set[str] = set()
        for device_id in device_ids:
            device = dev_reg.async_get(device_id)
            if device is None:
                _LOGGER.warning("Reconnect: unknown device %s", device_id)
                continue
            wanted.update(
                identifier[1].upper()
                for domain, identifier in device.identifiers
                if domain == DOMAIN
            )
        coordinators = [
            coord for coord in coordinators if coord.address in wanted
        ]

    for coordinator in coordinators:
        _LOGGER.info("Reconnecting Realme Smart Scale %s", coordinator.address)
        await coordinator.async_reconnect()

    if not coordinators:
        _LOGGER.warning("Reconnect: no Realme scale configured")
