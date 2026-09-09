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
"""Select platform: choose which user profile is "active".

The active user's profile (sex / age / height / initial weight) is what gets
written into the scale during the connection handshake, and it is the
fallback profile for attribution.  Switching it here reconnects the scale
so the new profile takes effect immediately.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import RealmeScaleCoordinator
from .scale_controller import ScaleUser


def _display_name(user: ScaleUser) -> str:
    return (user.name or "User").strip()


def _option_for(user: ScaleUser, duplicate_names: bool = False) -> str:
    """Human option; hides the stable user id unless names collide."""
    name = _display_name(user)
    if duplicate_names:
        return f"{name} ({user.user_id[-6:]})"
    return name


def _name_counts(users: list[ScaleUser]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for user in users:
        name = _display_name(user)
        counts[name] = counts.get(name, 0) + 1
    return counts


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the active-user select (only when a user exists)."""
    coordinator: RealmeScaleCoordinator = entry.runtime_data
    if coordinator.users:
        async_add_entities([RealmeScaleActiveUserSelect(coordinator, entry)])


class RealmeScaleActiveUserSelect(SelectEntity):
    """Dropdown for the scale's BLE handshake profile.

    This is NOT the person detected for a measurement - automatic
    identification decides ownership independently.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Scale handshake profile"
    _attr_icon = "mdi:account-sync-outline"

    def __init__(
        self,
        coordinator: RealmeScaleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.unique_id}_active_user"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_ADDRESS])},
            manufacturer=MANUFACTURER,
            name=DEFAULT_NAME,
            model=MODEL,
        )
        self._remove_listener: Callable[[], None] | None = None

    @property
    def options(self) -> list[str]:
        """One human option per configured user."""
        counts = _name_counts(self.coordinator.users)
        return [
            _option_for(user, counts.get(_display_name(user), 1) > 1)
            for user in self.coordinator.users
        ]

    @property
    def current_option(self) -> str | None:
        """The active user as an option string."""
        active = self.coordinator.active_user()
        if active is None:
            return None
        counts = _name_counts(self.coordinator.users)
        duplicate = counts.get(_display_name(active), 1) > 1
        return _option_for(active, duplicate)

    async def async_select_option(self, option: str) -> None:
        """Make the selected user active (reconnects the scale)."""
        for user in self.coordinator.users:
            counts = _name_counts(self.coordinator.users)
            duplicate = counts.get(_display_name(user), 1) > 1
            if option == _option_for(user, duplicate):
                await self.coordinator.async_set_active_user(user.user_id)
                return
        # No match: nothing to do, but surface a refresh so stale states
        # (e.g. after a user was removed) settle.
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Start listening to coordinator updates."""
        self._remove_listener = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Remove the coordinator listener."""
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()
