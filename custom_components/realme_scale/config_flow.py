"""Config flow for the Realme Smart Scale integration.

The scale is discovered through Home Assistant's Bluetooth integration
(local adapter or proxy that relays advertisements).  Because the RMH2011
only streams data to an *active* GATT client, the flow additionally asks for
the first user profile (sex / age / height / activity level / initial
weight) that is written into the handshake and used for the local BIA
calculation.

The options flow is a menu for managing **multiple users** after install:
add / edit / remove users, pick the active (handshake) user, tune the
auto-assignment weight tolerance and assign "unknown" measurements to a
specific user from a list.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant

from .const import (
    ACTIVITY_LEVELS,
    CONF_ACTIVITY_LEVEL,
    CONF_AGE,
    CONF_AUTO_ASSIGN_KG,
    CONF_HEIGHT,
    CONF_IMPEDANCE_TOL_OHM,
    CONF_INITIAL_WEIGHT,
    CONF_PERSON_ENTITY,
    CONF_SEX,
    CONF_USER_NAME,
    DEFAULT_AUTO_ASSIGN_KG,
    DEFAULT_IMPEDANCE_TOL_OHM,
    DEFAULT_NAME,
    DOMAIN,
    FIELD_MEASUREMENT_ID,
    FIELD_USER,
    SEX_FEMALE,
    SEX_MALE,
    SVC_A602,
)
from .coordinator import RealmeScaleCoordinator
from .records import display_label
from .scale_controller import (
    ScaleUser,
    build_user_options,
    parse_user_options,
)

# Options-flow menu actions (option key -> step id to run).
ACTION_ADD_USER = "add_user"
ACTION_EDIT_USER = "edit_user"
ACTION_REMOVE_USER = "remove_user"
ACTION_ACTIVE_USER = "active_user"
ACTION_SETTINGS = "settings"
ACTION_ASSIGN = "assign_unknown"
ACTION_REASSIGN = "reassign_measurement"
ACTION_SAVE = "save_close"

# Local schema field names (not stored in options).
FIELD_USER_SELECT = "user"
FIELD_KEEP_UNASSIGNED = "__keep_unassigned__"


def _new_user_id() -> str:
    """A short user id, distinct from measurement ids (u-prefixed)."""
    return f"u{uuid4().hex[:10]}"


def _user_label(user: ScaleUser) -> str:
    return user.name or f"User ({user.user_id[:8]})"


# ---------------------------------------------------------------------------
# Schema fragments
# ---------------------------------------------------------------------------


def _sex_options() -> list[str]:
    return [SEX_MALE, SEX_FEMALE]


def person_choices(hass: HomeAssistant) -> dict[str, str]:
    """Existing HA People (person.* entities) as {entity_id: name}.

    The empty option means "standalone profile, not linked to a person".
    """
    choices: dict[str, str] = {"": "Not linked to a person"}
    for state in hass.states.async_all("person"):
        choices[state.entity_id] = str(state.name or state.entity_id)
    return choices


def profile_schema(
    data: dict[str, Any] | None = None,
    persons: dict[str, str] | None = None,
) -> vol.Schema:
    """Voluptuous schema for one user profile.

    ``persons`` adds an optional dropdown linking the profile to an existing
    Home Assistant person entity (the profile name auto-fills from it).
    """
    data = data or {}
    schema: dict[vol.Marker, Any] = {
        vol.Required(
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
    if persons:
        schema.update(
            {
                vol.Optional(
                    CONF_PERSON_ENTITY,
                    default=data.get(CONF_PERSON_ENTITY, ""),
                ): vol.In(persons),
            }
        )
    return vol.Schema(schema)


def _profile_from_input(
    user_input: dict[str, Any],
    user_id: str,
    persons: dict[str, str] | None = None,
) -> ScaleUser:
    """Build a ScaleUser from validated form input.

    When no name was typed but a person was picked, the profile name is
    taken from that person so setup can be a single dropdown + confirm.
    """
    name = str(user_input.get(CONF_USER_NAME, "")).strip()
    person = str(user_input.get(CONF_PERSON_ENTITY, ""))
    if not name and person and persons:
        name = str(persons.get(person, "")).strip()
    return ScaleUser(
        user_id=user_id,
        name=name,
        person_entity_id=person,
        sex=str(user_input[CONF_SEX]),
        age=int(user_input[CONF_AGE]),
        height_cm=float(user_input[CONF_HEIGHT]),
        activity_level=str(user_input[CONF_ACTIVITY_LEVEL]),
        initial_weight=float(user_input.get(CONF_INITIAL_WEIGHT, 0.0)),
    )


def _profile_prefill(user: ScaleUser) -> dict[str, Any]:
    """Map a ScaleUser back into form values."""
    return {
        CONF_USER_NAME: user.name,
        CONF_SEX: user.sex,
        CONF_AGE: user.age,
        CONF_HEIGHT: user.height_cm,
        CONF_ACTIVITY_LEVEL: user.activity_level,
        CONF_INITIAL_WEIGHT: user.initial_weight,
    }


def _user_choices(users: list[ScaleUser]) -> dict[str, str]:
    """id -> readable name, for select menus."""
    return {user.user_id: _user_label(user) for user in users}


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def device_matches(discovery: BluetoothServiceInfoBleak) -> bool:
    """Whether a discovered advertisement looks like a Realme *scale*.

    Deliberately stricter than openScale's ``name contains "realme"`` test:
    HA auto-discovery would otherwise also offer unrelated realme BLE gear
    (e.g. "realme Buds Air7"), which does not speak the scale protocol.
    A candidate must advertise the scale service UUID (``a602``) or carry a
    scale-ish name token.
    """
    name = (discovery.name or "").lower()
    if SVC_A602 in discovery.service_uuids:
        return True
    return "scale" in name or "rmh2011" in name


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
# Config flow (initial setup)
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

# MAC address pattern for the manual fallback entry.  Validated in code:
# newer Home Assistant versions cannot serialize arbitrary callable schema
# validators (e.g. matches_regex) into the frontend field list.
MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _user_step_schema(discovered: list[BluetoothServiceInfoBleak]) -> vol.Schema:
    """Schema for the address step: dropdown when devices are known.

    Otherwise a plain text field; the MAC format is validated in code so the
    schema stays serializable on every Home Assistant version.
    """
    if discovered:
        return vol.Schema(
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
    return vol.Schema(
        {
            vol.Required(CONF_ADDRESS): str,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        }
    )


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
        raw_address = str(user_input[CONF_ADDRESS]).strip()
        address = raw_address.upper()
        if not MAC_PATTERN.fullmatch(raw_address):
            return self.async_show_form(
                step_id="user",
                data_schema=_user_step_schema(discovered),
                errors={CONF_ADDRESS: "invalid_mac"},
                description_placeholders={
                    "found": str(len(discovered)),
                    "name": DEFAULT_NAME,
                },
            )
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._address = address
        if address in discovered_by_address:
            self._name = discovered_by_address[address].name or DEFAULT_NAME
        else:
            self._name = user_input.get(CONF_NAME, DEFAULT_NAME)
        return await self.async_step_profile()

    return self.async_show_form(
        step_id="user",
        data_schema=_user_step_schema(discovered),
        description_placeholders={
            "found": str(len(discovered)),
            "name": DEFAULT_NAME,
        },
    )

    async def async_step_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the first user profile written into the handshake.

        Offers a dropdown of existing HA People: picking one auto-fills the
        profile name; the profile numbers still need confirming once
        (they drive the handshake and body-composition math).
        """
        errors: dict[str, str] = {}
        persons = person_choices(self.hass)
        if user_input is not None:
            user = _profile_from_input(user_input, _new_user_id(), persons)
            return self.async_create_entry(
                title=f"{self._name} ({self._address})",
                data={CONF_ADDRESS: self._address, CONF_NAME: self._name},
                options=build_user_options(
                    [user], user.user_id, DEFAULT_AUTO_ASSIGN_KG
                ),
            )

        return self.async_show_form(
            step_id="profile",
            data_schema=profile_schema(persons=persons),
            errors=errors,
            description_placeholders={"name": self._name},
        )


# ---------------------------------------------------------------------------
# Options flow (manage users + assignment after install)
# ---------------------------------------------------------------------------


class RealmeScaleOptionsFlow(OptionsFlow):
    """Menu-driven manager for users and the unknown-measurement queue."""

    def __init__(self) -> None:
        """Initialize the options flow state."""
        self._users: list[ScaleUser] | None = None
        self._active_user_id: str | None = None
        self._tolerance_kg: float = DEFAULT_AUTO_ASSIGN_KG
        self._impedance_tol_ohm: float = DEFAULT_IMPEDANCE_TOL_OHM
        self._removed_user_ids: list[str] = []
        self._menu_options: dict[str, str] = {}
        self._edit_user_id: str | None = None

    # -- helpers -----------------------------------------------------------

    def _load_state(self) -> None:
        """Mirror the current entry.options into mutable flow state."""
        users, active_user_id, tolerance, impedance_tol = parse_user_options(
            self.config_entry.options
        )
        self._users = users
        self._active_user_id = active_user_id
        self._tolerance_kg = tolerance
        self._impedance_tol_ohm = impedance_tol

    def _coordinator(self) -> RealmeScaleCoordinator | None:
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    def _users_or_default(self) -> list[ScaleUser]:
        if self._users is None:
            self._load_state()
        assert self._users is not None
        return self._users

    def _build_menu(self) -> dict[str, str]:
        """Menu entries; assign options only appear when there is work."""
        menu: dict[str, str] = {
            ACTION_ADD_USER: "add_user",
            ACTION_EDIT_USER: "edit_user",
            ACTION_REMOVE_USER: "remove_user",
            ACTION_ACTIVE_USER: "active_user",
            ACTION_SETTINGS: "settings",
        }
        coordinator = self._coordinator()
        if coordinator is None:
            menu[ACTION_SAVE] = "save_close"
            return menu
        if coordinator.unknown_count:
            menu[ACTION_ASSIGN] = "assign_pick"
        if coordinator.assigned_records(1):
            menu[ACTION_REASSIGN] = "reassign_pick"
        menu[ACTION_SAVE] = "save_close"
        return menu

    # -- main menu ---------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Present the user-management menu."""
        if self._users is None:
            self._load_state()
        self._menu_options = self._build_menu()
        coordinator = self._coordinator()
        pending = coordinator.unknown_count if coordinator is not None else 0
        return self.async_show_menu(
            step_id="menu",
            menu_options=self._menu_options,
            description_placeholders={
                "count": str(len(self._users or [])),
                "pending": str(pending),
            },
        )

    # -- add / edit / remove users ----------------------------------------

    async def async_step_add_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect a new user profile (optionally linked to an HA person)."""
        errors: dict[str, str] = {}
        persons = person_choices(self.hass)
        if user_input is not None:
            name_blank = not str(user_input.get(CONF_USER_NAME, "")).strip()
            person = str(user_input.get(CONF_PERSON_ENTITY, ""))
            if name_blank and not person:
                errors[CONF_USER_NAME] = "name_required"
            else:
                user = _profile_from_input(user_input, _new_user_id(), persons)
                self._users_or_default().append(user)
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_user",
            data_schema=profile_schema(persons=persons),
            errors=errors,
        )

    async def async_step_edit_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which user to edit."""
        if user_input is not None:
            self._edit_user_id = user_input[FIELD_USER_SELECT]
            return await self.async_step_edit_user_form()
        return self.async_show_form(
            step_id="edit_user",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_USER_SELECT): vol.In(
                        _user_choices(self._users_or_default())
                    ),
                }
            ),
        )

    async def async_step_edit_user_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one user's profile (person link optional)."""
        users = self._users_or_default()
        target_id = self._edit_user_id
        target = next((u for u in users if u.user_id == target_id), None)
        if target is None:
            return await self.async_step_init()

        errors: dict[str, str] = {}
        persons = person_choices(self.hass)
        if user_input is not None:
            name_blank = not str(user_input.get(CONF_USER_NAME, "")).strip()
            person = str(user_input.get(CONF_PERSON_ENTITY, ""))
            if name_blank and not person:
                errors[CONF_USER_NAME] = "name_required"
            else:
                updated = _profile_from_input(user_input, target_id, persons)
                users[users.index(target)] = updated
                return await self.async_step_init()

        return self.async_show_form(
            step_id="edit_user_form",
            data_schema=profile_schema(_profile_prefill(target), persons),
            errors=errors,
            description_placeholders={"user": _user_label(target)},
        )

    async def async_step_remove_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one user (the last user cannot be removed)."""
        users = self._users_or_default()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_id = user_input[FIELD_USER_SELECT]
            if len(users) <= 1:
                errors[FIELD_USER_SELECT] = "last_user"
            else:
                users[:] = [u for u in users if u.user_id != user_id]
                self._removed_user_ids.append(user_id)
                if self._active_user_id == user_id:
                    self._active_user_id = users[0].user_id if users else None
                return await self.async_step_init()

        return self.async_show_form(
            step_id="remove_user",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_USER_SELECT): vol.In(
                        _user_choices(users)
                    ),
                }
            ),
            errors=errors,
        )

    # -- active user & settings -------------------------------------------

    async def async_step_active_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the profile written into the scale handshake."""
        if user_input is not None:
            self._active_user_id = user_input[FIELD_USER_SELECT]
            return await self.async_step_init()
        return self.async_show_form(
            step_id="active_user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        FIELD_USER_SELECT, default=self._active_user_id
                    ): vol.In(_user_choices(self._users_or_default())),
                }
            ),
            description_placeholders={
                "active": _user_label(
                    next(
                        (
                            u
                            for u in self._users_or_default()
                            if u.user_id == self._active_user_id
                        ),
                        self._users_or_default()[0],
                    )
                )
            },
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Tune auto-assignment (weight + impedance tolerances)."""
        if user_input is not None:
            self._tolerance_kg = float(user_input[CONF_AUTO_ASSIGN_KG])
            self._impedance_tol_ohm = float(user_input[CONF_IMPEDANCE_TOL_OHM])
            return await self.async_step_init()
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AUTO_ASSIGN_KG, default=self._tolerance_kg
                    ): vol.All(
                        vol.Coerce(float), vol.Range(min=0.0, max=50.0)
                    ),
                    vol.Required(
                        CONF_IMPEDANCE_TOL_OHM, default=self._impedance_tol_ohm
                    ): vol.All(
                        vol.Coerce(float), vol.Range(min=0.0, max=500.0)
                    ),
                }
            ),
        )

    # -- assign / reassign measurements -----------------------------------

    async def _assign_via_form(
        self,
        coordinator: RealmeScaleCoordinator,
        measurement_id: str,
        user_id: str,
    ) -> tuple[bool, str]:
        """Resolve a user choice and (re)assign; True when done."""
        if user_id == FIELD_KEEP_UNASSIGNED:
            return True, ""
        user = coordinator.get_user(user_id) if coordinator else None
        if user is None:
            return False, "user_missing"
        return await coordinator.async_assign_measurement(measurement_id, user)

    async def async_step_assign_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick an unknown measurement and the user it belongs to."""
        coordinator = self._coordinator()
        pending = coordinator.unknown_records() if coordinator is not None else []
        errors: dict[str, str] = {}

        if user_input is not None and not pending:
            # Informational form was dismissed; nothing left to assign.
            return await self.async_step_init()

        if user_input is not None:
            done, message = await self._assign_via_form(
                coordinator,
                user_input[FIELD_MEASUREMENT_ID],
                user_input[FIELD_USER],
            )
            if not done:
                errors["base"] = message
            else:
                return await self.async_step_init()

        if not pending:
            return self.async_show_form(
                step_id="assign_pick",
                data_schema=vol.Schema({}),
                description_placeholders={"pending": "0"},
            )

        choices = {
            record[FIELD_MEASUREMENT_ID]: display_label(record)
            for record in pending
        }
        user_options = _user_choices(self._users_or_default())
        user_options[FIELD_KEEP_UNASSIGNED] = "Keep unassigned"
        return self.async_show_form(
            step_id="assign_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_MEASUREMENT_ID): vol.In(choices),
                    vol.Required(FIELD_USER): vol.In(user_options),
                }
            ),
            errors=errors,
            description_placeholders={"pending": str(len(pending))},
        )

    async def async_step_reassign_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a recent assigned measurement and move it to another user."""
        coordinator = self._coordinator()
        assigned = (
            coordinator.assigned_records(25) if coordinator is not None else []
        )
        errors: dict[str, str] = {}

        if user_input is not None and not assigned:
            return await self.async_step_init()

        if user_input is not None:
            done, message = await self._assign_via_form(
                coordinator,
                user_input[FIELD_MEASUREMENT_ID],
                user_input[FIELD_USER],
            )
            if not done:
                errors["base"] = message
            else:
                return await self.async_step_init()

        if not assigned:
            return self.async_show_form(
                step_id="reassign_pick",
                data_schema=vol.Schema({}),
                description_placeholders={"count": "0"},
            )

        def _label(record: dict[str, Any]) -> str:
            owner = record.get("user_name") or "unknown"
            return f"{display_label(record)} - currently {owner}"

        choices = {
            record[FIELD_MEASUREMENT_ID]: _label(record)
            for record in assigned
        }
        return self.async_show_form(
            step_id="reassign_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(FIELD_MEASUREMENT_ID): vol.In(choices),
                    vol.Required(FIELD_USER): vol.In(
                        _user_choices(self._users_or_default())
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"count": str(len(assigned))},
        )

    # -- commit ------------------------------------------------------------

    async def async_step_save_close(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Commit changes (reloads the entry through the update listener)."""
        users = self._users_or_default()
        coordinator = self._coordinator()
        if coordinator is not None and self._removed_user_ids:
            for user_id in self._removed_user_ids:
                await coordinator.async_drop_user_records(user_id)

        return self.async_create_entry(
            title="",
            data=build_user_options(
                users,
                self._active_user_id,
                self._tolerance_kg,
                self._impedance_tol_ohm,
            ),
        )
