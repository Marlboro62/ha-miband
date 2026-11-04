"""Bracelet Zepp OS simulé, côté périphérique, pour tester le client sans matériel."""

from __future__ import annotations

import asyncio
import struct
import zlib
from datetime import datetime
from types import SimpleNamespace

from protocol import ecdh
from protocol.chunked import (
    ChunkedDecoder,
    ChunkedEncoder,
    _message_key,
    _padded_length,
    aes_ecb_decrypt,
    aes_ecb_encrypt,
)
from protocol.const import (
    CHAR_ACTIVITY_DATA,
    CHAR_CHUNKED_READ,
    CHAR_CHUNKED_WRITE,
    CHAR_CURRENT_TIME,
    CHAR_FETCH,
    ENDPOINT_AUTH,
    ENDPOINT_BATTERY,
    ENDPOINT_CONNECTION,
    ENDPOINT_SERVICES,
    ENDPOINT_STEPS,
)


class _Characteristic(SimpleNamespace):
    pass


class _Services:
    def get_characteristic(self, uuid: str):
        if uuid == CHAR_CHUNKED_WRITE:
            return _Characteristic(properties=["write-without-response"])
        return _Characteristic(properties=["write", "notify"])


class FakeBand:
    """Implémente l'API BleakClient utilisée par MiBandClient."""

    def __init__(
        self,
        auth_key: bytes,
        samples: bytes = b"",
        sample_start: datetime | None = None,
        encrypted_battery: bool = True,
    ) -> None:
        self.auth_key = auth_key
        self.mtu_size = 185
        self.services = _Services()
        self.samples = samples
        self.sample_start = sample_start
        self.encrypted_battery = encrypted_battery
        self.callbacks: dict[str, object] = {}
        self.decoder = ChunkedDecoder()
        self.encoder = ChunkedEncoder(mtu=23)
        self.private = ecdh.generate_private_key()
        self.random = bytes(range(16))
        self.session_key: bytes | None = None
        self.expected_sequence: int | None = None
        self.received: list[tuple[int, bytes, bool]] = []
        self.time_written: bytes | None = None
        self.acks_received = 0
        self.fetch_acks: list[bytes] = []
        self.sample_offset = 0
        self.raw_encrypted_chunks: list[bytes] = []
        self._round = b""

    # --- API BleakClient ------------------------------------------------------
    async def start_notify(self, char, callback):
        self.callbacks[char] = callback

    async def stop_notify(self, char):
        self.callbacks.pop(char, None)

    async def read_gatt_char(self, char):
        return bytearray(b"2.1.0.13\x00")

    async def write_gatt_char(self, char, data, response=True):
        data = bytes(data)
        if char == CHAR_CHUNKED_WRITE:
            self._on_chunk(data)
        elif char == CHAR_CHUNKED_READ:
            self.acks_received += 1
        elif char == CHAR_CURRENT_TIME:
            self.time_written = data
        elif char == CHAR_FETCH:
            self._on_fetch(data)

    # --- simulation -----------------------------------------------------------
    def _notify(self, char: str, value: bytes) -> None:
        callback = self.callbacks.get(char)
        if callback is not None:
            asyncio.get_running_loop().call_soon(callback, None, bytearray(value))

    def _send(self, endpoint: int, payload: bytes, encrypt: bool = False) -> None:
        for chunk in self.encoder.encode(endpoint, payload, encrypt):
            self._notify(CHAR_CHUNKED_READ, chunk)

    def _on_chunk(self, data: bytes) -> None:
        encrypted = bool(data[1] & 0x08)
        if encrypted:
            self.raw_encrypted_chunks.append(data)
        message, _ = self.decoder.decode(data)
        if message is None:
            return
        self.received.append((message.endpoint, message.payload, encrypted))
        self._handle(message.endpoint, message.payload)

    def _handle(self, endpoint: int, payload: bytes) -> None:
        if endpoint == ENDPOINT_AUTH and payload[0] == 0x04:
            host_public = payload[4:52]
            shared = ecdh.shared_secret(self.private, host_public)
            self.expected_sequence = struct.unpack_from("<I", shared, 0)[0]
            self.session_key = bytes(shared[8 + i] ^ self.auth_key[i] for i in range(16))
            self._send(
                ENDPOINT_AUTH,
                bytes([0x10, 0x04, 0x01]) + self.random + ecdh.public_key(self.private),
            )
        elif endpoint == ENDPOINT_AUTH and payload[0] == 0x05:
            ok = payload[1:17] == aes_ecb_encrypt(self.random, self.auth_key) and payload[
                17:33
            ] == aes_ecb_encrypt(self.random, self.session_key)
            if ok:
                self.decoder.session_key = self.session_key
                self.encoder.set_encryption(0, self.session_key)
            self._send(ENDPOINT_AUTH, bytes([0x10, 0x05, 0x01 if ok else 0x25]))
        elif endpoint == ENDPOINT_CONNECTION and payload[0] == 0x01:
            self._send(ENDPOINT_CONNECTION, bytes([0x02]) + struct.pack("<H", 244))
        elif endpoint == ENDPOINT_SERVICES:
            entries = [(ENDPOINT_BATTERY, 1 if self.encrypted_battery else 0), (ENDPOINT_STEPS, 0)]
            body = struct.pack("<H", len(entries)) + b"".join(
                struct.pack("<HB", e, f) for e, f in entries
            )
            self._send(ENDPOINT_SERVICES, bytes([0x04]) + body)
        elif endpoint == ENDPOINT_BATTERY:
            self._send(
                ENDPOINT_BATTERY,
                bytes([0x04, 0x0F, 87, 0x01]) + bytes(17),
                encrypt=self.encrypted_battery,
            )
        elif endpoint == ENDPOINT_STEPS:
            self._send(
                ENDPOINT_STEPS,
                bytes([0x04, 0x01, 0x0C]) + struct.pack("<III", 5432, 3900, 210),
            )

    def check_encrypted_message(self, chunk_data: bytes) -> bool:
        """Utilitaire de test : vérifie séquence + CRC d'un message chiffré complet."""
        length = struct.unpack_from("<I", chunk_data, 5)[0]
        body = chunk_data[11:]
        plain = aes_ecb_decrypt(body[: _padded_length(length)], _message_key(self.session_key, chunk_data[3]))
        crc = struct.unpack_from("<I", plain, length + 4)[0]
        return crc == zlib.crc32(plain[: length + 4]) & 0xFFFFFFFF

    def _on_fetch(self, data: bytes) -> None:
        if data[0] == 0x01:
            remaining = self.samples[self.sample_offset :]
            if not remaining or self.sample_start is None:
                self._notify(CHAR_FETCH, bytes([0x10, 0x01, 0x01]) + bytes(12))
                return
            start = self.sample_start.timestamp() + 60 * (self.sample_offset // 8)
            start_dt = datetime.fromtimestamp(start, self.sample_start.tzinfo)
            quarters = int(start_dt.utcoffset().total_seconds() // 900)
            time_bytes = struct.pack(
                "<HBBBBBb",
                start_dt.year,
                start_dt.month,
                start_dt.day,
                start_dt.hour,
                start_dt.minute,
                start_dt.second,
                quarters,
            )
            # une manche ne transfère que 60 minutes, pour tester les reprises
            self._round = remaining[: 60 * 8]
            self._notify(
                CHAR_FETCH,
                bytes([0x10, 0x01, 0x01]) + struct.pack("<I", len(self._round)) + time_bytes + b"\x00",
            )
        elif data[0] == 0x02:
            payload = self._round
            counter = 0
            for i in range(0, len(payload), 160):
                self._notify(CHAR_ACTIVITY_DATA, bytes([counter & 0xFF]) + payload[i : i + 160])
                counter += 1
            crc = zlib.crc32(payload) & 0xFFFFFFFF
            self._notify(CHAR_FETCH, bytes([0x10, 0x02, 0x01]) + struct.pack("<I", crc))
            self.sample_offset += len(payload)
        elif data[0] == 0x03:
            self.fetch_acks.append(data)
            self._notify(CHAR_FETCH, bytes([0x10, 0x03, 0x01]))
