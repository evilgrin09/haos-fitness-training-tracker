"""Sensors of Gym Tracker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GymTrackerConfigEntry
from .const import DOMAIN, NAME, SIGNAL_DATA_UPDATED
from .store import GymStore

Summary = dict[str, Any]


@dataclass(frozen=True, kw_only=True)
class GymSensorDescription(SensorEntityDescription):
    """Describes a Gym Tracker sensor."""

    value_fn: Callable[[Summary], Any]
    attrs_fn: Callable[[Summary], dict[str, Any]] | None = None


def _target(summary: Summary) -> dict[str, Any]:
    return {"target_min": summary.get("freq_min"), "target_max": summary.get("freq_max")}


SENSORS: tuple[GymSensorDescription, ...] = (
    GymSensorDescription(
        key="last_workout",
        translation_key="last_workout",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda s: date.fromisoformat(s["last_date"]) if s.get("last_date") else None,
    ),
    GymSensorDescription(
        key="workouts_week",
        translation_key="workouts_week",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.get("week"),
        attrs_fn=_target,
    ),
    GymSensorDescription(
        key="workouts_month",
        translation_key="workouts_month",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.get("month"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GymTrackerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the sensors."""
    async_add_entities(GymSensor(entry, description) for description in SENSORS)


class GymSensor(SensorEntity):
    """A figure calculated from the training history."""

    entity_description: GymSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: GymTrackerConfigEntry, description: GymSensorDescription) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._store: GymStore = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=NAME, entry_type=DeviceEntryType.SERVICE
        )

    async def async_added_to_hass(self) -> None:
        """Follow data changes."""
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_DATA_UPDATED, self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self._store.summary)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional figures."""
        fn = self.entity_description.attrs_fn
        return fn(self._store.summary) if fn else None
