"""Sensor platform for the Realme Smart Scale integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

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

from .const import (
    CONF_ADDRESS,
    DOMAIN,
    MANUFACTURER,
    MODEL,
)
from .coordinator import RealmeScaleCoordinator

OHM = "Ω"


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
        icon="mdi:dumbbell",
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
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Realme scale sensors from a config entry."""
    coordinator: RealmeScaleCoordinator = entry.runtime_data
    async_add_entities(
        RealmeScaleSensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class RealmeScaleSensor(SensorEntity):
    """A single metric exposed by the Realme Smart Scale."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RealmeScaleCoordinator,
        entry: ConfigEntry,
        description: ScaleSensorDescription,
    ) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_ADDRESS])},
            manufacturer=MANUFACTURER,
            name=entry.title or "Realme Smart Scale",
            model=MODEL,
        )
        self._remove_listener: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        """Only available while the scale link is up and we have a value."""
        if not self.coordinator.connected:
            return False
        measurement = self.coordinator.latest
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
        measurement = self.coordinator.latest
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
        measurement = self.coordinator.latest
        if measurement is None:
            return {}
        return {
            "measured_at": measurement.measured_at.isoformat(),
            "user": self.coordinator.user.name,
        }

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
