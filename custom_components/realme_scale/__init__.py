"""The Realme Smart Scale (RMH2011) integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    FIELD_MEASUREMENT_ID,
    FIELD_USER,
    SERVICE_ASSIGN_MEASUREMENT,
    SERVICE_RECONNECT,
)
from .coordinator import RealmeScaleCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
]

CONF_DEVICE_ID = "device_id"

RECONNECT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_DEVICE_ID): vol.Any(str, [str]),
    }
)

ASSIGN_SCHEMA = vol.Schema(
    {
        vol.Required(FIELD_MEASUREMENT_ID): str,
        vol.Required(FIELD_USER): str,
    }
)


def _targeted_coordinators(
    hass: HomeAssistant, device_ids: str | list[str] | None
) -> list[RealmeScaleCoordinator]:
    """Resolve coordinators, optionally filtered by target device ids."""
    coordinators = list(hass.data.get(DOMAIN, {}).values())
    if not device_ids:
        return coordinators
    if isinstance(device_ids, str):
        device_ids = [device_ids]

    dev_reg = dr.async_get(hass)
    wanted: set[str] = set()
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            _LOGGER.warning("Unknown Realme scale device %s", device_id)
            continue
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            # identifier is the MAC, optionally followed by "_<user id>".
            wanted.add(identifier.split("_", 1)[0].upper())
    return [coord for coord in coordinators if coord.address in wanted]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up Realme Smart Scale from a config entry.

    Setup must never depend on the scale being reachable right now: the
    coordinator owns the BLE lifecycle and keeps retrying in the background
    until the scale appears.  This keeps Options-flow saves/reloads working
    while the scale is asleep or offline.
    """
    coordinator = RealmeScaleCoordinator(hass, entry)

    await coordinator.async_start()

    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Reload when options (users / handshake profile / tolerances) change,
    # so new user devices appear and the new handshake profile takes effect.
    entry.async_on_unload(
        entry.add_update_listener(_async_options_updated)
    )

    _register_services_once(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload an entry whose stored options changed (users/settings).

    The config-entry update listener awaits this coroutine, so it must be an
    ``async def`` (a sync function returning ``None`` produced the
    "a coroutine was expected, got None" error on save).
    """
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return

    if dict(entry.options) == coordinator._options_snapshot:
        return  # Options flow round-trip with no actual change.

    _LOGGER.debug(
        "Realme Smart Scale options changed; reloading %s",
        entry.title,
    )
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _register_services_once(hass: HomeAssistant) -> None:
    """Register entry services once for all configured scales."""
    if not hass.services.has_service(DOMAIN, SERVICE_RECONNECT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RECONNECT,
            _async_reconnect_service,
            schema=RECONNECT_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_ASSIGN_MEASUREMENT):
        hass.services.async_register(
            DOMAIN,
            SERVICE_ASSIGN_MEASUREMENT,
            _async_assign_service,
            schema=ASSIGN_SCHEMA,
        )


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_shutdown()
    if unload_ok and not hass.data.get(DOMAIN):
        for service in (SERVICE_RECONNECT, SERVICE_ASSIGN_MEASUREMENT):
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    return unload_ok


async def _async_reconnect_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Drop and re-establish the BLE session for the targeted scale."""
    coordinators = _targeted_coordinators(
        hass, call.data.get(CONF_DEVICE_ID)
    )
    for coordinator in coordinators:
        _LOGGER.info("Reconnecting Realme Smart Scale %s", coordinator.address)
        await coordinator.async_reconnect()

    if not coordinators:
        _LOGGER.warning("Reconnect: no Realme scale configured")


async def _async_assign_service(hass: HomeAssistant, call: ServiceCall) -> None:
    """Assign an unknown measurement to a specific user.

    ``user`` accepts either the stable user id or the user's name.  Without
    a device target every configured scale is searched.
    """
    measurement_id: str = call.data[FIELD_MEASUREMENT_ID]
    user_ref: str = call.data[FIELD_USER]

    coordinators = _targeted_coordinators(
        hass, call.data.get(CONF_DEVICE_ID)
    )
    handled = False
    for coordinator in coordinators:
        user = coordinator.find_user(user_ref)
        if user is None:
            _LOGGER.warning(
                "Assign %s: no user %r on scale %s",
                measurement_id,
                user_ref,
                coordinator.address,
            )
            continue
        ok, message = await coordinator.async_assign_measurement(
            measurement_id, user
        )
        handled = True
        if not ok:
            _LOGGER.warning("Assign %s on %s failed: %s",
                            measurement_id, coordinator.address, message)

    if not coordinators:
        _LOGGER.warning("Assign: no Realme scale configured")
    elif not handled:
        _LOGGER.warning(
            "Assign %s: no scale stores this measurement (already assigned?)",
            measurement_id,
        )
