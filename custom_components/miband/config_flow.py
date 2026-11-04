"""Assistant de configuration : choix du bracelet puis de la clé d'authentification."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_AUTH_KEY,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_SET_TIME,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SET_TIME,
    DOMAIN,
    LOCAL_NAMES,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .protocol.client import parse_auth_key
from .zepp_cloud import ZeppAuthError, ZeppError, async_fetch_devices, normalize_mac

_LOGGER = logging.getLogger(__name__)

MANUAL_ENTRY = "manual"


def _is_band(info: BluetoothServiceInfoBleak) -> bool:
    name = info.name or ""
    return any(name.startswith(prefix) for prefix in LOCAL_NAMES)


def _valid_mac(value: str) -> bool:
    parts = value.split(":")
    return len(parts) == 6 and all(len(p) == 2 and all(c in "0123456789ABCDEF" for c in p) for p in parts)


class MiBandConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flux de configuration du Mi Band."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None
        self._discovered: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MiBandOptionsFlow:
        return MiBandOptionsFlow()

    # ---------------------------------------------------------------- découverte

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address.upper())
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address.upper()
        self._name = discovery_info.name or "Mi Band 7"
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_key()
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._name or "", "address": self._address or ""},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            choice = user_input[CONF_ADDRESS]
            if choice == MANUAL_ENTRY:
                return await self.async_step_manual_address()
            return await self._async_select(choice, self._discovered.get(choice))

        current = self._async_current_ids(include_ignore=False)
        self._discovered = {
            info.address.upper(): f"{info.name} ({info.address.upper()})"
            for info in async_discovered_service_info(self.hass, connectable=True)
            if _is_band(info) and info.address.upper() not in current
        }
        if not self._discovered:
            return await self.async_step_manual_address()

        options = [
            SelectOptionDict(value=address, label=label)
            for address, label in self._discovered.items()
        ]
        options.append(SelectOptionDict(value=MANUAL_ENTRY, label=MANUAL_ENTRY))
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(options=options, translation_key="address")
                    )
                }
            ),
        )

    async def async_step_manual_address(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = normalize_mac(user_input[CONF_ADDRESS].strip())
            if _valid_mac(address):
                return await self._async_select(address, None)
            errors[CONF_ADDRESS] = "invalid_mac"
        return self.async_show_form(
            step_id="manual_address",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
        )

    async def _async_select(self, address: str, label: str | None) -> ConfigFlowResult:
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._address = address
        self._name = label.split(" (")[0] if label else "Mi Band 7"
        return await self.async_step_key()

    # ----------------------------------------------------------------------- clé

    async def async_step_key(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="key", menu_options=["zepp", "manual_key"])

    async def async_step_manual_key(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                key = parse_auth_key(user_input[CONF_AUTH_KEY])
            except ValueError:
                errors[CONF_AUTH_KEY] = "invalid_key"
            else:
                return self._async_finish(key)
        return self.async_show_form(
            step_id="manual_key",
            data_schema=vol.Schema({vol.Required(CONF_AUTH_KEY): str}),
            errors=errors,
            description_placeholders={"address": self._address or ""},
        )

    async def async_step_zepp(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                devices = await async_fetch_devices(
                    async_get_clientsession(self.hass),
                    user_input[CONF_EMAIL].strip(),
                    user_input[CONF_PASSWORD],
                )
            except ZeppAuthError:
                errors["base"] = "invalid_auth"
            except ZeppError:
                _LOGGER.exception("Erreur du cloud Zepp")
                errors["base"] = "cannot_connect"
            else:
                match = next((d for d in devices if d.mac == self._address and d.auth_key), None)
                if match is None:
                    _LOGGER.debug(
                        "Appareils du compte : %s", [(d.mac, d.active, bool(d.auth_key)) for d in devices]
                    )
                    errors["base"] = "device_not_found"
                else:
                    try:
                        return self._async_finish(parse_auth_key(match.auth_key or ""))
                    except ValueError:
                        errors["base"] = "invalid_key"

        return self.async_show_form(
            step_id="zepp",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
                    ),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD, autocomplete="current-password"
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"address": self._address or ""},
        )

    def _async_finish(self, key: bytes) -> ConfigFlowResult:
        key_hex = "0x" + key.hex()
        if self.source == "reauth":
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates={CONF_AUTH_KEY: key_hex}
            )
        return self.async_create_entry(
            title=self._name or "Mi Band 7",
            data={CONF_ADDRESS: self._address, CONF_AUTH_KEY: key_hex},
        )

    # ------------------------------------------------------------------- reauth

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._address = entry_data[CONF_ADDRESS]
        self._name = self._get_reauth_entry().title
        return await self.async_step_key()


class MiBandOptionsFlow(OptionsFlow):
    """Intervalle de synchronisation et mise à l'heure du bracelet."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
                    vol.Required(
                        CONF_SET_TIME, default=options.get(CONF_SET_TIME, DEFAULT_SET_TIME)
                    ): bool,
                }
            ),
        )
