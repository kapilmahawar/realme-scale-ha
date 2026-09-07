"""Config flow for the Realme Smart Scale integration.

The scale is discovered through Home Assistant's Bluetooth integration
(local adapter or proxy that relays advertisements).  Because the RMH2011
only streams data to an *active* GATT client, the flow additionally asks for
the user profile (sex / age / height / activity level / initial weight) that
is written into the handshake and used for the local BIA calculation.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

from .const import (
    ACTIVITY_LEVELS,
    CONF_ACTIVITY_LEVEL,
    CONF_AGE,
    CONF_HEIGHT,
    CONF_INITIAL_WEIGHT,
    CONF_SEX,
    CONF_USER_NAME,
    DEFAULT_NAME,
    DOMAIN,
    SEX_FEMALE,
    SEX_MALE,
    SVC_A602,
)
from .coordinator import RealmeScaleCoordinator

# ---------------------------------------------------------------------------
# Schema fragments
# ---------------------------------------------------------------------------


def _sex_options() -> list[str]:
    return [SEX_MALE, SEX_FEMALE]


def user_profile_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Voluptuous schema for the user profile (config + options flow)."""
    data = data or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_USER_NAME,
                default=data.get(CONF_USER_NAME, ""),
            ): str,
            vol.Required(
                CONF_SEX,
                default=data.get(CONF_SEX, SEX_MALE),
            ): vol.In(_sex_options()),
            vol.Required(
                CONF_AGE,
                default=data.get(CONF_AGE, 30),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
            vol.Required(
                CONF_HEIGHT,
                default=data.get(CONF_HEIGHT, 175.0),
            ): vol.All(
                vol.Coerce(float), vol.Range(min=50.0, max=250.0)
            ),
            vol.Required(
                CONF_ACTIVITY_LEVEL,
                default=data.get(CONF_ACTIVITY_LEVEL, "moderate"),
            ): vol.In(ACTIVITY_LEVELS),
            vol.Optional(
                CONF_INITIAL_WEIGHT,
                default=data.get(CONF_INITIAL_WEIGHT, 0.0),
            ): vol.All(
                vol.Coerce(float), vol.Range(min=0.0, max=300.0)
            ),
        }
    )


def _normalize_profile(user_input: dict[str, Any]) -> dict[str, Any]:
    """Coerce profile values to stable types for storage."""
    return {
        CONF_USER_NAME: str(user_input.get(CONF_USER_NAME, "")),
        CONF_SEX: str(user_input.get(CONF_SEX, SEX_MALE)),
        CONF_AGE: int(user_input[CONF_AGE]),
        CONF_HEIGHT: float(user_input[CONF_HEIGHT]),
        CONF_ACTIVITY_LEVEL: str(user_input[CONF_ACTIVITY_LEVEL]),
        CONF_INITIAL_WEIGHT: float(user_input.get(CONF_INITIAL_WEIGHT, 0.0)),
    }


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def device_matches(discovery: BluetoothServiceInfoBleak) -> bool:
    """Whether a discovered advertisement looks like a Realme scale."""
    name = (discovery.name or "").lower()
    return "realme" in name or SVC_A602 in discovery.service_uuids


def _discovered_devices(hass: HomeAssistant) -> list[BluetoothServiceInfoBleak]:
    """All currently known advertisements that could be this scale."""
    return [
        discovery
        for discovery in async_discovered_service_info(hass)
        if device_matches(discovery)
    ]


def _mac_from_service(discovery: BluetoothServiceInfoBleak) -> str:
    """Return a stable uppercase MAC for use as a unique id."""
    return discovery.address.upper()


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


class RealmeScaleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Realme Smart Scale."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._discovered: BluetoothServiceInfoBleak | None = None
        self._address: str | None = None
        self._name: str = DEFAULT_NAME

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        if not device_matches(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovered = discovery_info
        self._address = _mac_from_service(discovery_info)
        self._name = discovery_info.name or DEFAULT_NAME

        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()
        return await self.async_step_profile()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initiated by the user (manual fallback)."""
        # Offer discovered devices when available, otherwise let the user
        # type the MAC (e.g. the scale is asleep / out of advertising range).
        discovered = _discovered_devices(self.hass)
        discovered_by_address = {
            discovery.address.upper(): discovery for discovery in discovered
        }

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            self._address = address
            if address in discovered_by_address:
                self._name = discovered_by_address[address].name or DEFAULT_NAME
            else:
                self._name = user_input.get(CONF_NAME, DEFAULT_NAME)
            return await self.async_step_profile()

        if discovered:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            discovery.address.upper(): (
                                f"{discovery.name} ({discovery.address})"
                            )
                            for discovery in discovered
                        }
                    ),
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): cv.matches_regex(
                        r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
                    ),
                    vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={
                "found": str(len(discovered)),
                "name": DEFAULT_NAME,
            },
        )

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the user profile written into the scale handshake."""
        errors: dict[str, str] = {}
        if user_input is not None:
            profile = _normalize_profile(user_input)
            return self.async_create_entry(
                title=f"{self._name} ({self._address})",
                data={CONF_ADDRESS: self._address, CONF_NAME: self._name},
                options=profile,
            )

        return self.async_show_form(
            step_id="profile",
            data_schema=user_profile_schema(),
            errors=errors,
            description_placeholders={"name": self._name},
        )


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


class RealmeScaleOptionsFlow(OptionsFlow):
    """Handle options for the Realme Smart Scale integration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the user profile options."""
        if user_input is not None:
            profile = _normalize_profile(user_input)
            coordinator: RealmeScaleCoordinator | None = self.hass.data.get(
                DOMAIN, {}
            ).get(self.config_entry.entry_id)
            if coordinator is not None:
                coordinator.update_profile(profile)
            return self.async_create_entry(title="", data=profile)

        return self.async_show_form(
            step_id="init",
            data_schema=user_profile_schema(dict(self.config_entry.options)),
            description_placeholders={
                "name": self.config_entry.data.get(CONF_NAME, DEFAULT_NAME)
            },
        )
