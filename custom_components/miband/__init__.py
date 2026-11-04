"""Intégration Home Assistant pour le Xiaomi Mi Band 7 (Bluetooth direct)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .coordinator import MiBandCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

type MiBandConfigEntry = ConfigEntry[MiBandCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MiBandConfigEntry) -> bool:
    """Prépare le coordinateur sans bloquer si le bracelet est absent."""
    coordinator = MiBandCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    # Première synchro en tâche de fond : le bracelet peut être hors de portée.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"miband_first_sync_{entry.entry_id}"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MiBandConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: MiBandConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: MiBandConfigEntry) -> None:
    """Supprime l'historique enregistré du bracelet."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
