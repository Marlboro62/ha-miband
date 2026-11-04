"""Capteurs du Mi Band : batterie, activité, fréquence cardiaque, sommeil."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiBandConfigEntry
from .coordinator import MiBandData
from .entity import MiBandEntity

BPM = "bpm"


@dataclass(frozen=True, kw_only=True)
class MiBandSensorDescription(SensorEntityDescription):
    value_fn: Callable[[MiBandData], Any]
    attrs_fn: Callable[[MiBandData], dict[str, Any] | None] = lambda _d: None


def _hr(attr: str) -> Callable[[MiBandData], Any]:
    return lambda d: getattr(d.heart_rate, attr) if d.heart_rate else None


def _sleep(attr: str) -> Callable[[MiBandData], Any]:
    return lambda d: getattr(d.sleep, attr) if d.sleep else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


SLEEP_DURATION = {
    "device_class": SensorDeviceClass.DURATION,
    "native_unit_of_measurement": UnitOfTime.MINUTES,
    "suggested_unit_of_measurement": UnitOfTime.HOURS,
    "suggested_display_precision": 1,
}

SENSORS: tuple[MiBandSensorDescription, ...] = (
    MiBandSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.battery,
    ),
    MiBandSensorDescription(
        key="steps",
        translation_key="steps",
        native_unit_of_measurement="steps",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.steps,
    ),
    MiBandSensorDescription(
        key="distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.distance_m,
    ),
    MiBandSensorDescription(
        key="calories",
        translation_key="calories",
        native_unit_of_measurement="kcal",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: d.calories_kcal,
    ),
    MiBandSensorDescription(
        key="heart_rate",
        translation_key="heart_rate",
        native_unit_of_measurement=BPM,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_hr("last"),
        attrs_fn=lambda d: {"measured_at": _iso(d.heart_rate.last_time)} if d.heart_rate else None,
    ),
    MiBandSensorDescription(
        key="heart_rate_min",
        translation_key="heart_rate_min",
        native_unit_of_measurement=BPM,
        value_fn=_hr("minimum"),
    ),
    MiBandSensorDescription(
        key="heart_rate_max",
        translation_key="heart_rate_max",
        native_unit_of_measurement=BPM,
        value_fn=_hr("maximum"),
    ),
    MiBandSensorDescription(
        key="heart_rate_avg",
        translation_key="heart_rate_avg",
        native_unit_of_measurement=BPM,
        value_fn=_hr("average"),
    ),
    MiBandSensorDescription(key="sleep_total", translation_key="sleep_total", value_fn=_sleep("total_min"), **SLEEP_DURATION),
    MiBandSensorDescription(key="sleep_deep", translation_key="sleep_deep", value_fn=_sleep("deep_min"), **SLEEP_DURATION),
    MiBandSensorDescription(key="sleep_light", translation_key="sleep_light", value_fn=_sleep("light_min"), **SLEEP_DURATION),
    MiBandSensorDescription(key="sleep_rem", translation_key="sleep_rem", value_fn=_sleep("rem_min"), **SLEEP_DURATION),
    MiBandSensorDescription(key="sleep_awake", translation_key="sleep_awake", value_fn=_sleep("awake_min"), **SLEEP_DURATION),
    MiBandSensorDescription(
        key="sleep_start",
        translation_key="sleep_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_sleep("start"),
    ),
    MiBandSensorDescription(
        key="sleep_end",
        translation_key="sleep_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_sleep("end"),
    ),
    MiBandSensorDescription(
        key="last_sync",
        translation_key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_sync,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiBandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(MiBandSensor(coordinator, description) for description in SENSORS)


class MiBandSensor(MiBandEntity, SensorEntity):
    entity_description: MiBandSensorDescription

    def __init__(self, coordinator, description: MiBandSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        return self.entity_description.value_fn(data) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        return self.entity_description.attrs_fn(data) if data else None
