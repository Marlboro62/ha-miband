"""Coordinateur : connexion périodique au bracelet et calcul des valeurs."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUTH_KEY,
    CONF_SCAN_INTERVAL,
    CONF_SET_TIME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SET_TIME,
    DOMAIN,
    FIRST_SYNC_DEPTH,
    HISTORY_KEEP,
    SAVE_DELAY,
    STORAGE_VERSION,
)
from .protocol.client import MiBandAuthError, MiBandClient, MiBandError, parse_auth_key
from .protocol.parsers import (
    ActivitySample,
    HeartRateStats,
    SleepSummary,
    heart_rate_stats,
    last_sleep_session,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 30
SYNC_TIMEOUT = 180


@dataclass
class MiBandData:
    """Valeurs exposées aux entités."""

    last_sync: datetime | None = None
    firmware: str | None = None
    battery: int | None = None
    charging: bool | None = None
    steps: int | None = None
    distance_m: int | None = None
    calories_kcal: int | None = None
    heart_rate: HeartRateStats | None = None
    sleep: SleepSummary | None = None
    asleep: bool | None = None
    worn: bool | None = None


class MiBandCoordinator(DataUpdateCoordinator[MiBandData]):
    """Se connecte au bracelet à intervalle régulier, puis se déconnecte."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_ADDRESS]}",
            update_interval=timedelta(minutes=minutes),
        )
        self.address: str = entry.data[CONF_ADDRESS]
        self._auth_key = parse_auth_key(entry.data[CONF_AUTH_KEY])
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._samples: dict[int, ActivitySample] = {}
        self._last_sample_ts: int | None = None
        self._lock = asyncio.Lock()
        self._state = MiBandData()

    # ---------------------------------------------------------------- persistance

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        for values in stored.get("samples", []):
            try:
                sample = ActivitySample.from_list(values)
            except TypeError:
                continue
            self._samples[sample.timestamp] = sample
        self._last_sample_ts = stored.get("last_sample_ts")
        state = stored.get("state", {})
        self._state = MiBandData(
            last_sync=dt_util.parse_datetime(state["last_sync"]) if state.get("last_sync") else None,
            firmware=state.get("firmware"),
            battery=state.get("battery"),
            charging=state.get("charging"),
            steps=state.get("steps"),
            distance_m=state.get("distance_m"),
            calories_kcal=state.get("calories_kcal"),
        )
        if self._state.last_sync is not None:
            self._compute(self._state)
            self.async_set_updated_data(self._state)

    def _data_to_store(self) -> dict[str, Any]:
        state = self._state
        return {
            "last_sample_ts": self._last_sample_ts,
            "samples": [s.as_list() for s in sorted(self._samples.values(), key=lambda s: s.timestamp)],
            "state": {
                "last_sync": state.last_sync.isoformat() if state.last_sync else None,
                "firmware": state.firmware,
                "battery": state.battery,
                "charging": state.charging,
                "steps": state.steps,
                "distance_m": state.distance_m,
                "calories_kcal": state.calories_kcal,
            },
        }

    # ------------------------------------------------------------------ synchro

    async def _async_update_data(self) -> MiBandData:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed(
                f"Bracelet {self.address} hors de portée de tout adaptateur ou proxy Bluetooth actif"
            )

        async with self._lock:
            try:
                async with asyncio.timeout(SYNC_TIMEOUT):
                    await self._sync(ble_device)
            except MiBandAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (MiBandError, BleakError, TimeoutError) as err:
                raise UpdateFailed(f"Synchronisation impossible : {err}") from err

        self._store.async_delay_save(self._data_to_store, SAVE_DELAY)
        return self._state

    async def _sync(self, ble_device: Any) -> None:
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.address,
            max_attempts=3,
            timeout=CONNECT_TIMEOUT,
        )
        try:
            band = MiBandClient(client, self._auth_key)
            await band.connect()
            state = self._state

            if state.firmware is None:
                state.firmware = await band.get_firmware()

            now = dt_util.now()
            if self.config_entry.options.get(CONF_SET_TIME, DEFAULT_SET_TIME):
                try:
                    await band.set_time(now)
                except (BleakError, MiBandError) as err:
                    _LOGGER.debug("Mise à l'heure impossible : %s", err)

            battery = await band.get_battery()
            if battery is not None:
                state.battery = battery.level
                state.charging = battery.charging

            steps = await band.get_steps()
            if steps is not None:
                state.steps = steps.steps
                state.distance_m = steps.distance_m
                state.calories_kcal = steps.calories_kcal

            since = now - FIRST_SYNC_DEPTH
            if self._last_sample_ts is not None:
                since = max(since, dt_util.as_local(dt_util.utc_from_timestamp(self._last_sample_ts + 60)))
            if since < now - timedelta(minutes=1):
                samples = await band.fetch_activity(since, now)
                self._merge(samples)
                _LOGGER.debug("%s minutes d'historique récupérées", len(samples))

            state.last_sync = dt_util.utcnow()
            self._compute(state)
        finally:
            try:
                await client.disconnect()
            except BleakError:
                pass

    def _merge(self, samples: list[ActivitySample]) -> None:
        for sample in samples:
            self._samples[sample.timestamp] = sample
        if samples:
            last = max(s.timestamp for s in samples)
            self._last_sample_ts = max(self._last_sample_ts or 0, last)
        cutoff = int((dt_util.utcnow() - HISTORY_KEEP).timestamp())
        for ts in [ts for ts in self._samples if ts < cutoff]:
            del self._samples[ts]

    def _compute(self, state: MiBandData) -> None:
        samples = list(self._samples.values())
        midnight = dt_util.start_of_local_day()
        state.heart_rate = heart_rate_stats(samples, int(midnight.timestamp()))
        state.sleep = last_sleep_session(samples)
        if samples:
            latest = max(samples, key=lambda s: s.timestamp)
            fresh = (dt_util.utcnow().timestamp() - latest.timestamp) < 20 * 60
            state.asleep = latest.is_sleep if fresh else None
            state.worn = latest.is_worn if fresh else None
        else:
            state.asleep = None
            state.worn = None
