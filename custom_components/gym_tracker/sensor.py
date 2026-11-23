"""Sensors of Gym Tracker: one device with its sensors per user with a plan."""

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
from homeassistant.const import UnitOfTime
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
    GymSensorDescription(
        key="cardio_week",
        translation_key="cardio_week",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda s: s.get("cardio_week"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GymTrackerConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Set up the sensors; users who create a plan later get theirs added on the fly."""
    store = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_users() -> None:
        new = [user_id for user_id in store.summaries if user_id not in known]
        known.update(new)
        async_add_entities(GymSensor(entry, user_id, description) for user_id in new for description in SENSORS)

    add_new_users()
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_DATA_UPDATED, add_new_users))


class GymSensor(SensorEntity):
    """A figure calculated from the training history of one user."""

    entity_description: GymSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: GymTrackerConfigEntry, user_id: str, description: GymSensorDescription) -> None:
        """Initialize the sensor."""
        self.entity_description = description
        self._store: GymStore = entry.runtime_data
        self._user_id = user_id
        name = f"{NAME} {self._store.user_names.get(user_id, user_id)}"
        if user_id == self._store.legacy_user:
            # The owner of the data from before 0.3.0 keeps the ids of the former single-user
            # sensors, so their entity ids (dashboards, automations) stay the same.
            self._attr_unique_id = f"{entry.entry_id}_{description.key}"
            identifier = entry.entry_id
        else:
            self._attr_unique_id = f"{entry.entry_id}_{user_id}_{description.key}"
            identifier = f"{entry.entry_id}_{user_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)}, name=name, entry_type=DeviceEntryType.SERVICE
        )

    async def async_added_to_hass(self) -> None:
        """Follow data changes."""
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_DATA_UPDATED, self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """The user still has a plan."""
        return self._user_id in self._store.summaries

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        return self.entity_description.value_fn(self._store.summaries.get(self._user_id, {}))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional figures."""
        fn = self.entity_description.attrs_fn
        return fn(self._store.summaries.get(self._user_id, {})) if fn else None
