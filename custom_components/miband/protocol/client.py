"""Client BLE Zepp OS : authentification, requêtes et récupération d'historique.

Indépendant de Home Assistant : il travaille sur n'importe quel objet qui
expose l'API de `bleak.BleakClient` (start_notify, write_gatt_char, ...).
"""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from . import ecdh
from .chunked import ChunkedDecoder, ChunkedEncoder, DecodedMessage, aes_ecb_encrypt
from .const import (
    AUTH_FAILED,
    BATTERY_REQUEST,
    CHAR_ACTIVITY_DATA,
    CHAR_CHUNKED_READ,
    CHAR_CHUNKED_WRITE,
    CHAR_CURRENT_TIME,
    CHAR_FETCH,
    CHAR_FIRMWARE_REVISION,
    CONNECTION_MTU_REQUEST,
    CONNECTION_MTU_RESPONSE,
    CONNECTION_PING,
    CONNECTION_PONG,
    ENDPOINT_AUTH,
    ENDPOINT_BATTERY,
    ENDPOINT_CONNECTION,
    ENDPOINT_SERVICES,
    ENDPOINT_STEPS,
    FETCH_ACK,
    FETCH_ACK_KEEP_ON_DEVICE,
    FETCH_DATA,
    FETCH_START_DATE,
    FETCH_TYPE_ACTIVITY,
    RESPONSE,
    SERVICES_GET_LIST,
    SERVICES_RET_LIST,
    STEPS_GET,
    SUCCESS,
)
from .parsers import (
    ActivitySample,
    BatteryInfo,
    StepsInfo,
    decode_band_time,
    encode_current_time,
    encode_fetch_start,
    parse_activity_samples,
    parse_battery,
    parse_steps,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0
MAX_FETCH_ROUNDS = 6


class MiBandError(Exception):
    """Erreur de dialogue avec le bracelet."""


class MiBandAuthError(MiBandError):
    """Clé d'authentification refusée par le bracelet."""


class BleakLike(Protocol):
    """Sous-ensemble de BleakClient utilisé ici."""

    mtu_size: int
    services: Any

    async def start_notify(self, char: str, callback: Callable[[Any, bytearray], None]) -> None: ...
    async def stop_notify(self, char: str) -> None: ...
    async def write_gatt_char(self, char: str, data: bytes, response: bool = ...) -> None: ...
    async def read_gatt_char(self, char: str) -> bytearray: ...


def parse_auth_key(value: str) -> bytes:
    """Clé saisie « 0x…» ou hexadécimale nue → 16 octets."""
    cleaned = value.strip().lower().replace(" ", "").replace(":", "")
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    key = bytes.fromhex(cleaned)
    if len(key) != 16:
        raise ValueError("la clé doit faire 16 octets (32 caractères hexadécimaux)")
    return key


class MiBandClient:
    """Session authentifiée avec un bracelet Zepp OS."""

    def __init__(self, client: BleakLike, auth_key: bytes) -> None:
        self._client = client
        self._auth_key = auth_key
        self._encoder = ChunkedEncoder(mtu=23)
        self._decoder = ChunkedDecoder()
        self._encrypted_endpoints: set[int] = set()
        self._waiters: list[tuple[int, Callable[[bytes], bool], asyncio.Future[bytes]]] = []
        self._write_lock = asyncio.Lock()
        self._fetch_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._fetch_buffer = bytearray()
        self._fetch_counter = -1
        self._fetch_error = False
        self._background: set[asyncio.Task[None]] = set()
        self.authenticated = False

    # ------------------------------------------------------------------ bas niveau

    def _write_response(self, char: str) -> bool:
        """Écriture avec réponse sauf si la caractéristique ne gère que sans."""
        try:
            characteristic = self._client.services.get_characteristic(char)
        except Exception:  # noqa: BLE001
            characteristic = None
        if characteristic is None:
            return True
        props = set(characteristic.properties)
        return "write" in props or "write-without-response" not in props

    async def _write(self, char: str, data: bytes) -> None:
        async with self._write_lock:
            await self._client.write_gatt_char(char, data, response=self._write_response(char))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _on_chunked(self, _sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        if not raw:
            return
        if raw[0] != 0x03:
            return  # 0x04 = accusé de réception du bracelet pour nos écritures
        message, needs_ack = self._decoder.decode(raw)
        if needs_ack:
            self._spawn(self._write(CHAR_CHUNKED_READ, self._decoder.ack()))
        if message is not None:
            self._dispatch(message)

    def _dispatch(self, message: DecodedMessage) -> None:
        payload = message.payload
        _LOGGER.debug("Reçu endpoint 0x%04x : %s", message.endpoint, payload.hex())
        if (
            message.endpoint == ENDPOINT_CONNECTION
            and payload
            and payload[0] == CONNECTION_PING
        ):
            self._spawn(self.send(ENDPOINT_CONNECTION, bytes([CONNECTION_PONG])))
            return
        for waiter in list(self._waiters):
            endpoint, predicate, future = waiter
            if endpoint == message.endpoint and not future.done() and predicate(payload):
                self._waiters.remove(waiter)
                future.set_result(payload)
                return

    async def send(self, endpoint: int, payload: bytes, encrypt: bool | None = None) -> None:
        if encrypt is None:
            encrypt = self.authenticated and endpoint in self._encrypted_endpoints
        for chunk in self._encoder.encode(endpoint, payload, encrypt):
            await self._write(CHAR_CHUNKED_WRITE, chunk)

    async def request(
        self,
        endpoint: int,
        payload: bytes,
        predicate: Callable[[bytes], bool] = lambda _p: True,
        timeout: float = DEFAULT_TIMEOUT,
        encrypt: bool | None = None,
    ) -> bytes:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        waiter = (endpoint, predicate, future)
        self._waiters.append(waiter)
        try:
            await self.send(endpoint, payload, encrypt)
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as err:
            raise MiBandError(f"pas de réponse sur l'endpoint 0x{endpoint:04x}") from err
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

    # ------------------------------------------------------------- authentification

    async def connect(self) -> None:
        """Active les notifications puis authentifie la session."""
        await self._client.start_notify(CHAR_CHUNKED_READ, self._on_chunked)
        await self.authenticate()
        await self._negotiate_mtu()
        await self._discover_services()

    async def authenticate(self) -> None:
        private = ecdh.generate_private_key()
        public = ecdh.public_key(private)
        reply = await self.request(
            ENDPOINT_AUTH,
            bytes([0x04, 0x02, 0x00, 0x02]) + public,
            predicate=lambda p: len(p) >= 3 and p[0] == RESPONSE and p[1] == 0x04,
            encrypt=False,
        )
        if reply[2] != SUCCESS or len(reply) < 3 + 16 + 48:
            raise MiBandError(f"échange de clé refusé ({reply[:3].hex()})")

        remote_random = reply[3:19]
        remote_public = reply[19:67]
        shared = await asyncio.get_running_loop().run_in_executor(
            None, ecdh.shared_secret, private, remote_public
        )
        sequence = struct.unpack_from("<I", shared, 0)[0]
        session_key = bytes(shared[8 + i] ^ self._auth_key[i] for i in range(16))
        self._encoder.set_encryption(sequence, session_key)
        self._decoder.session_key = session_key

        proof = (
            bytes([0x05])
            + aes_ecb_encrypt(remote_random, self._auth_key)
            + aes_ecb_encrypt(remote_random, session_key)
        )
        result = await self.request(
            ENDPOINT_AUTH,
            proof,
            predicate=lambda p: len(p) >= 3 and p[0] == RESPONSE and p[1] == 0x05,
            encrypt=False,
        )
        if result[2] == AUTH_FAILED:
            raise MiBandAuthError("clé d'authentification refusée par le bracelet")
        if result[2] != SUCCESS:
            raise MiBandError(f"authentification échouée ({result[:3].hex()})")
        self.authenticated = True
        _LOGGER.debug("Authentification réussie")

    async def _negotiate_mtu(self) -> None:
        try:
            reply = await self.request(
                ENDPOINT_CONNECTION,
                bytes([CONNECTION_MTU_REQUEST]),
                predicate=lambda p: len(p) >= 3 and p[0] == CONNECTION_MTU_RESPONSE,
                timeout=5,
            )
        except MiBandError:
            return
        band_mtu = struct.unpack_from("<H", reply, 1)[0] + 3
        link_mtu = getattr(self._client, "mtu_size", 23) or 23
        self._encoder.mtu = max(23, min(band_mtu, link_mtu))
        _LOGGER.debug("MTU : bracelet %s, lien %s", band_mtu, link_mtu)

    async def _discover_services(self) -> None:
        reply = await self.request(
            ENDPOINT_SERVICES,
            bytes([SERVICES_GET_LIST]),
            predicate=lambda p: len(p) >= 3 and p[0] == SERVICES_RET_LIST,
            encrypt=False,
        )
        count = struct.unpack_from("<H", reply, 1)[0]
        offset = 3
        for _ in range(count):
            if offset + 3 > len(reply):
                break
            endpoint, flag = struct.unpack_from("<HB", reply, offset)
            if flag == 1:
                self._encrypted_endpoints.add(endpoint)
            offset += 3
        _LOGGER.debug(
            "%s services, chiffrés : %s",
            count,
            sorted(f"0x{e:04x}" for e in self._encrypted_endpoints),
        )

    # --------------------------------------------------------------------- données

    async def get_battery(self) -> BatteryInfo | None:
        payload = await self.request(
            ENDPOINT_BATTERY, bytes([BATTERY_REQUEST]), predicate=lambda p: p[:1] == b"\x04"
        )
        return parse_battery(payload)

    async def get_steps(self) -> StepsInfo | None:
        payload = await self.request(
            ENDPOINT_STEPS, bytes([STEPS_GET]), predicate=lambda p: p[:1] == b"\x04"
        )
        return parse_steps(payload)

    async def get_firmware(self) -> str | None:
        try:
            raw = await self._client.read_gatt_char(CHAR_FIRMWARE_REVISION)
        except Exception:  # noqa: BLE001
            return None
        return bytes(raw).decode("ascii", "ignore").strip("\x00 ") or None

    async def set_time(self, now: datetime) -> None:
        await self._write(CHAR_CURRENT_TIME, encode_current_time(now))

    # ---------------------------------------------------------- historique minute

    def _on_fetch(self, _sender: Any, data: bytearray) -> None:
        self._fetch_queue.put_nowait(bytes(data))

    def _on_activity(self, _sender: Any, data: bytearray) -> None:
        raw = bytes(data)
        if not raw:
            return
        expected = (self._fetch_counter + 1) & 0xFF
        if raw[0] != expected:
            self._fetch_error = True
            return
        self._fetch_counter = expected
        self._fetch_buffer += raw[1:]

    async def _fetch_response(self, command: int, timeout: float) -> bytes:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise MiBandError(f"pas de réponse à la commande d'historique 0x{command:02x}")
            try:
                value = await asyncio.wait_for(self._fetch_queue.get(), remaining)
            except TimeoutError as err:
                raise MiBandError(
                    f"pas de réponse à la commande d'historique 0x{command:02x}"
                ) from err
            if len(value) >= 3 and value[0] == RESPONSE and value[1] == command:
                return value

    async def _fetch_ack(self) -> None:
        await self._write(CHAR_FETCH, bytes([FETCH_ACK, FETCH_ACK_KEEP_ON_DEVICE]))
        try:
            await self._fetch_response(FETCH_ACK, 5)
        except MiBandError:
            _LOGGER.debug("Pas de confirmation de l'accusé d'historique")

    async def fetch_activity(self, since: datetime, now: datetime) -> list[ActivitySample]:
        """Récupère les échantillons minute depuis `since` (datetime avec fuseau).

        Les données sont laissées sur le bracelet (accusé « garder »), pour ne
        rien faire perdre à l'appli d'origine si elle est réutilisée plus tard.
        """
        samples: list[ActivitySample] = []
        await self._client.start_notify(CHAR_FETCH, self._on_fetch)
        try:
            for _round in range(MAX_FETCH_ROUNDS):
                while not self._fetch_queue.empty():
                    self._fetch_queue.get_nowait()
                await self._write(
                    CHAR_FETCH,
                    bytes([FETCH_START_DATE, FETCH_TYPE_ACTIVITY]) + encode_fetch_start(since),
                )
                start = await self._fetch_response(FETCH_START_DATE, DEFAULT_TIMEOUT)
                if start[2] != SUCCESS or len(start) < 15:
                    _LOGGER.debug("Début d'historique refusé : %s", start.hex())
                    break
                expected = struct.unpack_from("<I", start, 3)[0]
                if expected == 0:
                    await self._fetch_ack()
                    break
                try:
                    start_time = decode_band_time(start[7:15])
                except ValueError as err:
                    await self._fetch_ack()
                    raise MiBandError(f"horodatage d'historique invalide : {start.hex()}") from err

                self._fetch_buffer = bytearray()
                self._fetch_counter = -1
                self._fetch_error = False
                await self._client.start_notify(CHAR_ACTIVITY_DATA, self._on_activity)
                try:
                    await self._write(CHAR_FETCH, bytes([FETCH_DATA]))
                    end = await self._fetch_response(FETCH_DATA, 30 + expected / 200)
                finally:
                    await self._client.stop_notify(CHAR_ACTIVITY_DATA)

                if end[2] != SUCCESS or self._fetch_error:
                    _LOGGER.warning("Transfert d'historique incomplet, il sera repris")
                    await self._fetch_ack()
                    break

                chunk = parse_activity_samples(start_time, bytes(self._fetch_buffer))
                await self._fetch_ack()
                if not chunk:
                    break
                samples.extend(chunk)
                next_since = datetime.fromtimestamp(chunk[-1].timestamp + 60, since.tzinfo)
                if next_since <= since or next_since >= now - timedelta(minutes=1):
                    break
                since = next_since
        finally:
            try:
                await self._client.stop_notify(CHAR_FETCH)
            except Exception:  # noqa: BLE001
                pass
        return samples

