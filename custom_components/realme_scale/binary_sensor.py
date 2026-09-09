# Copyright (C) 2026 Kapil Mahawar
#
# This file is part of the realme-scale-ha Home Assistant integration.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Binary sensor platform for the Realme Smart Scale integration."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import RealmeScaleCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Realme scale binary sensors from a config entry."""
    coordinator: RealmeScaleCoordinator = entry.runtime_data
    async_add_entities([RealmeScaleConnectivity(coordinator, entry)])


class RealmeScaleConnectivity(BinarySensorEntity):
    """Exposes the live GATT connection state of the scale."""

    _attr_should_poll = False
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RealmeScaleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.unique_id}_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_ADDRESS])},
            manufacturer=MANUFACTURER,
            name=DEFAULT_NAME,
            model=MODEL,
        )
        self._remove_listener: Callable[[], None] | None = None

    @property
    def is_on(self) -> bool:
        return self.coordinator.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Start listening to coordinator state changes."""
        self._remove_listener = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Stop listening to coordinator state changes."""
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()
