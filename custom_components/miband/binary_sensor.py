"""Capteurs binaires du Mi Band : en charge, endormi, porté."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiBandConfigEntry
from .coordinator import MiBandData
from .entity import MiBandEntity


@dataclass(frozen=True, kw_only=True)
class MiBandBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[MiBandData], bool | None]


BINARY_SENSORS: tuple[MiBandBinaryDescription, ...] = (
    MiBandBinaryDescription(
        key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda d: d.charging,
    ),
    MiBandBinaryDescription(
        key="asleep",
        translation_key="asleep",
        value_fn=lambda d: d.asleep,
    ),
    MiBandBinaryDescription(
        key="worn",
        translation_key="worn",
        value_fn=lambda d: d.worn,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiBandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(MiBandBinarySensor(coordinator, d) for d in BINARY_SENSORS)


class MiBandBinarySensor(MiBandEntity, BinarySensorEntity):
    entity_description: MiBandBinaryDescription

    def __init__(self, coordinator, description: MiBandBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return self.entity_description.value_fn(data) if data else None
