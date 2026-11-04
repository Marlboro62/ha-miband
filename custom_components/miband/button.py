"""Bouton « Synchroniser maintenant »."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import MiBandConfigEntry
from .entity import MiBandEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MiBandConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MiBandSyncButton(entry.runtime_data, "sync")])


class MiBandSyncButton(MiBandEntity, ButtonEntity):
    _attr_translation_key = "sync"

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
