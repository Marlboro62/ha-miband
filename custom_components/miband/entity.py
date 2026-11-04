"""Entité de base du Mi Band."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import MiBandCoordinator


class MiBandEntity(CoordinatorEntity[MiBandCoordinator]):
    """Garde la dernière valeur connue quand le bracelet est hors de portée.

    Un bracelet s'éloigne souvent des proxys Bluetooth : rendre toutes les
    entités indisponibles à chaque absence ferait perdre l'information. Le
    capteur « Dernière synchronisation » indique la fraîcheur des données.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: MiBandCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.config_entry.title,
            sw_version=coordinator.data.firmware if coordinator.data else None,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None
