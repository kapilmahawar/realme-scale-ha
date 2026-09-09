"""Sensor platform for the Realme Smart Scale integration.

Each configured user becomes its own HA device whose sensors show that
user's latest attributed measurement.  The root (scale) device additionally
exposes an "unassigned measurements" counter for the unknown queue.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfMass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import metrics
from .const import (
    CONF_ADDRESS,
    DEFAULT_NAME,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import RealmeScaleCoordinator
from .scale_controller import ScaleUser

OHM = "Ω"


def _user_device_identifier(address: str, user_id: str) -> tuple[str, str]:
    """Device-registry identifier for one user's virtual device."""
    return (DOMAIN, f"{address}_{user_id}")


def _scale_device_identifier(address: str) -> tuple[str, str]:
    """Device-registry identifier for the physical scale itself."""
    return (DOMAIN, address)


def _scale_device_info(entry: ConfigEntry) -> DeviceInfo:
    """DeviceInfo for the physical scale (canonical branding, no MAC)."""
    return DeviceInfo(
        identifiers={_scale_device_identifier(entry.data[CONF_ADDRESS])},
        manufacturer=MANUFACTURER,
        name=DEFAULT_NAME,
        model=MODEL,
    )


def _user_device_info(entry: ConfigEntry, user: ScaleUser) -> DeviceInfo:
    """DeviceInfo for one user's virtual device (name = user name only).

    The device hierarchy (via_device = physical scale) already establishes
    that this is a Realme Smart Scale user, so the friendly name is just the
    person's name.
    """
    return DeviceInfo(
        identifiers={
            _user_device_identifier(entry.data[CONF_ADDRESS], user.user_id)
        },
        manufacturer=MANUFACTURER,
        name=user.name or "User",
        model=MODEL,
        via_device=_scale_device_identifier(entry.data[CONF_ADDRESS]),
    )


@dataclass(frozen=True)
class ScaleSensorDescription(SensorEntityDescription):
    """Extend the base description with a ScaleMeasurement attribute name."""

    value_key: str | None = None
    """Name of the ScaleMeasurement attribute holding the sensor value."""


# value_key points at ScaleMeasurement attributes; optional unit overrides.
SENSOR_DESCRIPTIONS: tuple[ScaleSensorDescription, ...] = (
    ScaleSensorDescription(
        key="weight",
        name="Weight",
        value_key="weight_kg",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:scale-bathroom",
    ),
    ScaleSensorDescription(
        key="body_fat",
        name="Body Fat",
        value_key="body_fat",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent",
    ),
    ScaleSensorDescription(
        key="muscle",
        name="Muscle",
        value_key="muscle",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:arm-flex",
    ),
    ScaleSensorDescription(
        key="water",
        name="Body Water",
        value_key="water",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:water-percent",
    ),
    ScaleSensorDescription(
        key="bone_mass",
        name="Bone Mass",
        value_key="bone_kg",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:bone",
    ),
    ScaleSensorDescription(
        key="lean_body_mass",
        name="Lean Body Mass",
        value_key="lean_body_mass_kg",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:human",
    ),
    ScaleSensorDescription(
        key="visceral_fat",
        name="Visceral Fat",
        value_key="visceral_fat",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:human-male-height",
    ),
    ScaleSensorDescription(
        key="impedance",
        name="Impedance",
        value_key="impedance",
        native_unit_of_measurement=OHM,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-outline",
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ScaleSensorDescription(
        key="last_measured",
        name="Last Measured",
        value_key="measured_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


# ---------------------------------------------------------------------------
# Derived metric + interpretation sensors (bodymiscale-style extras)
# ---------------------------------------------------------------------------

# kind -> (name, unit, icon, decimals, is_text, compute(user, measurement))
# Every value is recomputed on the fly from the latest attributed
# measurement and the user profile (nothing extra is persisted).
DerivedCompute = Callable[[Any, Any], object | None]

DERIVED_SENSORS: dict[str, tuple[str, str | None, str, int, bool, DerivedCompute]] = {
    "bmi": (
        "BMI", None, "mdi:human", 2, False,
        lambda u, m: metrics.bmi(m.weight_kg, u.height_cm),
    ),
    "body_fat_mass": (
        "Body Fat Mass", UnitOfMass.KILOGRAMS, "mdi:scale-bathroom", 2, False,
        lambda u, m: metrics.body_fat_mass_kg(m.weight_kg, m.body_fat),
    ),
    "bmr": (
        "BMR", "kcal/day", "mdi:fire", 0, False,
        lambda u, m: metrics.schofield_bmr(u.is_male(), u.age, m.weight_kg),
    ),
    "ideal_weight": (
        "Ideal Weight", UnitOfMass.KILOGRAMS, "mdi:target", 2, False,
        lambda u, m: metrics.devine_ideal_weight(u.is_male(), u.height_cm),
    ),
    "protein": (
        "Protein", PERCENTAGE, "mdi:food-apple-outline", 2, False,
        lambda u, m: metrics.protein_pct(m.body_fat),
    ),
    "bmi_category": (
        "BMI Category", None, "mdi:human-male-height-variant", 0, True,
        lambda u, m: metrics.bmi_category(
            metrics.bmi(m.weight_kg, u.height_cm)
        ),
    ),
    "body_fat_category": (
        "Body Fat Category", None, "mdi:percent-outline", 0, True,
        lambda u, m: metrics.fat_category(u.is_male(), u.age, m.body_fat),
    ),
    "body_type": (
        "Body Type", None, "mdi:account", 0, True,
        lambda u, m: metrics.body_type(
            u.is_male(), u.age, m.body_fat,
            metrics.bmi(m.weight_kg, u.height_cm),
        ),
    ),
    "body_water_category": (
        "Body Water Category", None, "mdi:water-percent", 0, True,
        lambda u, m: metrics.water_category(u.is_male(), m.water),
    ),
    "visceral_fat_category": (
        "Visceral Fat Category", None, "mdi:stomach", 0, True,
        lambda u, m: metrics.visceral_fat_category(m.visceral_fat),
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Realme scale sensors from a config entry."""
    coordinator: RealmeScaleCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        RealmeScaleUnassignedCount(coordinator, entry)
    ]
    for user in coordinator.users:
        entities.extend(
            RealmeScaleUserSensor(coordinator, entry, description, user)
            for description in SENSOR_DESCRIPTIONS
        )
        entities.extend(
            RealmeScaleDerivedSensor(coordinator, entry, kind, user)
            for kind in DERIVED_SENSORS
        )
    async_add_entities(entities)


class RealmeScaleUserSensor(SensorEntity):
    """One metric of one user, fed by that user's latest measurement."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RealmeScaleCoordinator,
        entry: ConfigEntry,
        description: ScaleSensorDescription,
        user: ScaleUser,
    ) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._user_id = user.user_id
        self._attr_unique_id = (
            f"{entry.unique_id}_user_{user.user_id}_{description.key}"
        )
        self._attr_device_info = _user_device_info(entry, user)
        self._remove_listener: Callable[[], None] | None = None

    def _measurement(self):
        return self.coordinator.latest_measurement(self._user_id)

    @property
    def available(self) -> bool:
        """Available whenever we have a (last known) measurement value.

        Intentionally independent of the BLE link state: once a valid
        measurement exists it stays available during disconnects so history
        graphs keep the last known value instead of showing a gap.  Link
        state is reported by the scale-level ``Connected`` binary sensor.
        """
        measurement = self._measurement()
        if measurement is None:
            return False
        return getattr(measurement, self._value_key(), None) is not None

    def _value_key(self) -> str:
        description = self.entity_description
        assert description.value_key is not None
        return description.value_key

    @property
    def native_value(self):
        """Return the current metric value."""
        measurement = self._measurement()
        if measurement is None:
            return None
        value = getattr(measurement, self._value_key(), None)
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        # Present sensible precision without accumulating float noise.
        return round(float(value), 2)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Attach the scale timestamp / user for context."""
        measurement = self._measurement()
        if measurement is None:
            return {}
        user = self.coordinator.get_user(self._user_id)
        attributes: dict[str, object] = {
            "measured_at": measurement.measured_at.isoformat(),
            "user": user.name if user else (measurement.user_name or ""),
            "user_id": self._user_id,
        }
        if user is not None and user.person_entity_id:
            person_state = self.coordinator.hass.states.get(
                user.person_entity_id
            )
            attributes["person"] = (
                person_state.name if person_state else user.person_entity_id
            )
            attributes["person_entity_id"] = user.person_entity_id
        return attributes

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore any cached state and (re)subscribe."""
        self._remove_listener = self.coordinator.async_add_listener(
            self._handle_coordinator_update
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Remove the coordinator listener."""
        if self._remove_listener:
            self._remove_listener()
        await super().async_will_remove_from_hass()


class RealmeScaleDerivedSensor(SensorEntity):
    """A metric recomputed from the latest measurement + user profile.

    Covers both numeric values (BMI, BMR, ...) and interpretation text
    states (BMI category, body fat category, ...); see DERIVED_SENSORS.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RealmeScaleCoordinator,
        entry: ConfigEntry,
        kind: str,
        user: ScaleUser,
    ) -> None:
        self.coordinator = coordinator
        self._user_id = user.user_id
        self._kind = kind
        (
            name,
            unit,
            icon,
            decimals,
            is_text,
            self._compute,
        ) = DERIVED_SENSORS[kind]
        self._decimals = decimals
        self._is_text = is_text
        self._attr_name = name
        self._attr_unique_id = (
            f"{entry.unique_id}_user_{user.user_id}_{kind}"
        )
        self._attr_device_info = _user_device_info(entry, user)
        self._attr_icon = icon
        if unit and not is_text:
            self._attr_native_unit_of_measurement = unit
        if not is_text:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            if unit in (UnitOfMass.KILOGRAMS, PERCENTAGE):
                self._attr_device_class = SensorDeviceClass.WEIGHT if unit == UnitOfMass.KILOGRAMS else None
        self._remove_listener: Callable[[], None] | None = None

    def _measurement(self):
        return self.coordinator.latest_measurement(self._user_id)

    def _computed(self) -> object | None:
        user = self.coordinator.get_user(self._user_id)
        measurement = self._measurement()
        if user is None or measurement is None:
            return None
        try:
            # Derived metrics that depend on age (BMR, categories, body
            # type) must see the user's *current* age derived from their
            # date of birth in the HA-local time zone - never a stale
            # snapshot from when the profile was last saved.
            age_user = replace(user, age=self.coordinator.age_today(user))
            return self._compute(age_user, measurement)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    @property
    def available(self) -> bool:
        """Available whenever a value can be derived from the last reading.

        Independent of the BLE link state so derived metrics keep their last
        known value across disconnects (see RealmeScaleUserSensor).
        """
        return self._computed() is not None

    @property
    def native_value(self):
        """Return the derived value (numeric or interpretation text)."""
        value = self._computed()
        if value is None:
            return None
        if self._is_text:
            return str(value)
        return round(float(value), self._decimals)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Attach the scale timestamp / user for context."""
        measurement = self._measurement()
        if measurement is None:
            return {}
        user = self.coordinator.get_user(self._user_id)
        return {
            "measured_at": measurement.measured_at.isoformat(),
            "user": user.name if user else "",
            "user_id": self._user_id,
        }

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


class RealmeScaleUnassignedCount(SensorEntity):
    """Count of measurements waiting to be assigned to a user."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Unassigned measurements"
    _attr_icon = "mdi:account-question-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: RealmeScaleCoordinator,
        entry: ConfigEntry,
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.unique_id}_unassigned_count"
        self._attr_device_info = _scale_device_info(entry)
        self._remove_listener: Callable[[], None] | None = None

    @property
    def native_value(self) -> int:
        return self.coordinator.unknown_count

    @property
    def available(self) -> bool:
        # The queue is meaningful even while the scale is unreachable.
        return True

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
