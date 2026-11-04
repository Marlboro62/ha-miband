"""Récupération de la clé d'authentification BLE depuis le compte Zepp.

Reprend la séquence de connexion de huami-token (licence MIT,
https://codeberg.org/argrento/huami-token). Le mot de passe n'est utilisé
que le temps de l'appel et n'est jamais enregistré.
"""

from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import uuid
from dataclasses import dataclass

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

_KEY = b"xeNtBVqzDc6tuNTh"
_IV = b"MAAAYAAAAAAAAABg"
_CHANNEL = "a100900101016"
_APP_VERSION = "9.12.5"
_CV = f"151689_{_APP_VERSION}"
_USER_AGENT = f"Zepp/{_APP_VERSION} (Pixel 4; Android 12; Density/2.75)"

URL_TOKENS = "https://api-user-us2.zepp.com/v2/registrations/tokens"
URL_LOGIN = "https://api-mifit-us2.zepp.com/v2/client/login"
URL_LOGOUT = "https://api-mifit-us2.zepp.com/v1/client/logout"
URL_DEVICES = "https://api-mifit.zepp.com/users/{user_id}/devices"

TIMEOUT = aiohttp.ClientTimeout(total=30)


class ZeppError(Exception):
    """Erreur générique du cloud Zepp."""


class ZeppAuthError(ZeppError):
    """Identifiants refusés."""


@dataclass(frozen=True)
class ZeppDevice:
    mac: str
    active: bool
    auth_key: str | None


def _encrypt(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def normalize_mac(mac: str) -> str:
    return mac.replace("-", ":").upper()


async def async_fetch_devices(
    session: aiohttp.ClientSession, email: str, password: str
) -> list[ZeppDevice]:
    """Se connecte au compte Zepp et renvoie les appareils liés avec leur clé."""
    payload = urllib.parse.urlencode(
        {
            "emailOrPhone": email,
            "state": "REDIRECTION",
            "client_id": "HuaMi",
            "password": password,
            "redirect_uri": "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html",
            "region": "us-west-2",
            "token": ["access", "refresh"],
            "country_code": "US",
        },
        doseq=True,
    ).encode()
    headers = {
        "app_name": "com.huami.midong",
        "appname": "com.huami.midong",
        "cv": _CV,
        "v": "2.0",
        "appplatform": "android_phone",
        "vb": "202509151347",
        "vn": _APP_VERSION,
        "user-agent": _USER_AGENT,
        "x-hm-ekv": "1",
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    try:
        async with session.post(
            URL_TOKENS,
            data=_encrypt(payload),
            headers=headers,
            allow_redirects=False,
            timeout=TIMEOUT,
        ) as resp:
            if resp.status != 303:
                raise ZeppAuthError(f"réponse {resp.status} au lieu de 303")
            location = resp.headers.get("Location", "")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        access = query.get("access", [None])[0]
        if not access:
            raise ZeppAuthError("pas de jeton d'accès (identifiants refusés ?)")

        async with session.post(
            URL_LOGIN,
            data={
                "code": access,
                "device_id": str(uuid.uuid4()),
                "device_model": "android_phone",
                "app_version": _APP_VERSION,
                "dn": "api-mifit.zepp.com,api-user.zepp.com,api-mifit.zepp.com,"
                "api-watch.zepp.com,app-analytics.zepp.com,auth.zepp.com,api-analytics.zepp.com",
                "third_name": "huami",
                "source": f"com.huami.watch.hmwatchmanager:{_APP_VERSION}:151689",
                "app_name": "com.huami.midong",
                "country_code": "US",
                "grant_type": "access_token",
                "allow_registration": "false",
                "lang": "en",
                "countryState": "US-NY",
            },
            headers={
                "app_name": "com.huami.webapp",
                "appname": "com.huami.webapp",
                "origin": "https://user.zepp.com",
                "referer": "https://user.zepp.com/",
                "accept": "application/json, text/plain, */*",
            },
            timeout=TIMEOUT,
        ) as resp:
            if resp.status != 200:
                raise ZeppAuthError(f"connexion refusée ({resp.status})")
            login = await resp.json(content_type=None)
        token_info = login.get("token_info", {})
        app_token = token_info.get("app_token")
        login_token = token_info.get("login_token")
        user_id = token_info.get("user_id")
        if not app_token or not user_id:
            raise ZeppAuthError("jetons de session absents")

        try:
            params = [
                ("r", str(uuid.uuid4())),
                ("r", str(uuid.uuid4())),
                ("enableMultiDeviceOnMultiType", "true"),
                ("enableMultiDeviceOnMultiType", "true"),
                ("userid", str(user_id)),
                ("appid", str(secrets.randbits(64))),
                ("channel", _CHANNEL),
                ("country", "US"),
                ("cv", _CV),
                ("device", "android_32"),
                ("device_type", "android_phone"),
                ("enableMultiDevice", "true"),
                ("lang", "en_US"),
                ("timezone", "Europe/Paris"),
                ("v", "2.0"),
            ]
            async with session.get(
                URL_DEVICES.format(user_id=user_id),
                params=params,
                headers={
                    "country": "US",
                    "appplatform": "android_phone",
                    "x-request-id": str(uuid.uuid4()),
                    "timezone": "Europe/Paris",
                    "channel": _CHANNEL,
                    "cv": _CV,
                    "appname": "com.huami.midong",
                    "v": "2.0",
                    "vn": _APP_VERSION,
                    "apptoken": app_token,
                    "lang": "en_US",
                    "user-agent": _USER_AGENT,
                },
                timeout=TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    raise ZeppError(f"liste des appareils indisponible ({resp.status})")
                body = await resp.json(content_type=None)
        finally:
            if login_token:
                try:
                    async with session.post(
                        URL_LOGOUT,
                        data={"login_token": login_token, "os_verison": "vnull"},
                        headers={"appname": "com.huami.midong", "user-agent": _USER_AGENT},
                        timeout=TIMEOUT,
                    ):
                        pass
                except aiohttp.ClientError:
                    _LOGGER.debug("Déconnexion Zepp impossible (sans conséquence)")
    except aiohttp.ClientError as err:
        raise ZeppError(f"cloud Zepp injoignable : {err}") from err

    devices: list[ZeppDevice] = []
    for item in body.get("items", []):
        info_raw = item.get("additionalInfo") or "{}"
        try:
            info = json.loads(info_raw)
        except ValueError:
            info = {}
        devices.append(
            ZeppDevice(
                mac=normalize_mac(item.get("macAddress", "")),
                active=bool(item.get("activeStatus", 0)),
                auth_key=info.get("auth_key") or None,
            )
        )
    return devices
