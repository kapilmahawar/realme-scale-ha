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

from .const import CONF_ADDRESS, DOMAIN, MANUFACTURER, MODEL
from .coordinator import RealmeScaleCoordinator
from .scale_controller import ScaleUser


def _option_for(user: ScaleUser) -> str:
    """Stable, human-readable select option for one user."""
    return f"{user.name or 'User'} ({user.user_id})"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the active-user select."""
    coordinator: RealmeScaleCoordinator = entry.runtime_data
    async_add_entities([RealmeScaleActiveUserSelect(coordinator, entry)])


class RealmeScaleActiveUserSelect(SelectEntity):
    """A dropdown listing every user; picking one makes it active."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Active user"
    _attr_icon = "mdi:account"

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
            name=entry.title or "Realme Smart Scale",
            model=MODEL,
        )
        self._remove_listener: Callable[[], None] | None = None

    @property
    def options(self) -> list[str]:
        """One option per configured user."""
        return [_option_for(user) for user in self.coordinator.users]

    @property
    def current_option(self) -> str | None:
        """The active user as an option string."""
        active = self.coordinator.active_user()
        return _option_for(active) if active is not None else None

    async def async_select_option(self, option: str) -> None:
        """Make the selected user active (reconnects the scale)."""
        for user in self.coordinator.users:
            if option == _option_for(user):
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
