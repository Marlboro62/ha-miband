"""Transport « chunked 2021 » des bracelets Zepp OS.

Les messages applicatifs (endpoint 16 bits + charge utile) sont découpés en
fragments écrits sur la caractéristique 0x0016 ; les réponses arrivent en
notifications sur 0x0017. Les messages peuvent être chiffrés en AES-128-ECB
avec la clé de session (XOR le numéro de message).

Format d'un fragment (en-tête étendu, toujours utilisé par Zepp OS) :
    0x03 | flags | 0x00 | handle | count | [longueur u32 | type u16] | données
    flags : 0x01 premier, 0x02 dernier, 0x04 accusé demandé, 0x08 chiffré
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

FLAG_FIRST = 0x01
FLAG_LAST = 0x02
FLAG_NEEDS_ACK = 0x04
FLAG_ENCRYPTED = 0x08

CHUNK_DATA = 0x03
CHUNK_ACK = 0x04


def aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()  # noqa: S305
    return encryptor.update(data) + encryptor.finalize()


def aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305
    return decryptor.update(data) + decryptor.finalize()


def _message_key(session_key: bytes, handle: int) -> bytes:
    return bytes(b ^ (handle & 0xFF) for b in session_key)


def _padded_length(length: int) -> int:
    total = length + 8
    if total % 16:
        total += 16 - total % 16
    return total


@dataclass
class ChunkedEncoder:
    """Découpe (et chiffre si besoin) les messages sortants."""

    mtu: int = 23
    session_key: bytes | None = None
    sequence: int = 0
    _handle: int = 0

    def set_encryption(self, sequence: int, session_key: bytes) -> None:
        self.sequence = sequence & 0xFFFFFFFF
        self.session_key = session_key

    def encode(self, endpoint: int, payload: bytes, encrypt: bool = False) -> list[bytes]:
        if encrypt and self.session_key is None:
            raise RuntimeError("chiffrement demandé sans clé de session")

        self._handle = (self._handle + 1) & 0xFF
        handle = self._handle
        length = len(payload)
        data = payload

        if encrypt:
            assert self.session_key is not None
            plain = bytearray(_padded_length(length))
            plain[:length] = payload
            plain[length : length + 4] = struct.pack("<I", self.sequence)
            self.sequence = (self.sequence + 1) & 0xFFFFFFFF
            crc = zlib.crc32(bytes(plain[: length + 4])) & 0xFFFFFFFF
            plain[length + 4 : length + 8] = struct.pack("<I", crc)
            data = aes_ecb_encrypt(bytes(plain), _message_key(self.session_key, handle))

        chunks: list[bytes] = []
        remaining = len(data)
        offset = 0
        count = 0
        header_size = 11  # en-tête étendu du premier fragment
        while remaining > 0:
            max_len = self.mtu - 3 - header_size
            if max_len <= 0:
                raise ValueError(f"MTU {self.mtu} trop petit")
            size = min(remaining, max_len)
            flags = FLAG_ENCRYPTED if encrypt else 0
            header = bytearray([CHUNK_DATA, 0, 0, handle, count & 0xFF])
            if count == 0:
                flags |= FLAG_FIRST
                header += struct.pack("<IH", length, endpoint & 0xFFFF)
            if remaining <= max_len:
                flags |= FLAG_LAST | FLAG_NEEDS_ACK
            header[1] = flags
            chunks.append(bytes(header) + data[offset : offset + size])
            offset += size
            remaining -= size
            count += 1
            header_size = 5
        return chunks


@dataclass
class DecodedMessage:
    endpoint: int
    payload: bytes


@dataclass
class ChunkedDecoder:
    """Réassemble (et déchiffre si besoin) les messages entrants."""

    session_key: bytes | None = None
    last_handle: int = 0
    last_count: int = 0
    _handle: int | None = None
    _endpoint: int = 0
    _length: int = 0
    _buffer: bytearray = field(default_factory=bytearray)

    def decode(self, data: bytes) -> tuple[DecodedMessage | None, bool]:
        """Traite un fragment. Retourne (message complet éventuel, accusé requis)."""
        if len(data) < 5 or data[0] != CHUNK_DATA:
            return None, False
        flags = data[1]
        encrypted = bool(flags & FLAG_ENCRYPTED)
        first = bool(flags & FLAG_FIRST)
        last = bool(flags & FLAG_LAST)
        needs_ack = bool(flags & FLAG_NEEDS_ACK)
        handle = data[3]
        count = data[4]
        idx = 5

        if self._handle is not None and handle != self._handle and not first:
            return None, False
        self.last_handle = handle
        self.last_count = count

        if first:
            self._length, self._endpoint = struct.unpack_from("<IH", data, idx)
            idx += 6
            self._buffer = bytearray()
            self._handle = handle
        elif self._handle is None:
            return None, needs_ack

        self._buffer += data[idx:]

        if not last:
            return None, needs_ack

        buf = bytes(self._buffer)
        endpoint = self._endpoint
        length = self._length
        self._handle = None
        self._buffer = bytearray()

        if encrypted:
            if self.session_key is None:
                return None, needs_ack
            buf = buf[: _padded_length(length)]
            if len(buf) % 16:
                return None, needs_ack
            buf = aes_ecb_decrypt(buf, _message_key(self.session_key, handle))[:length]

        return DecodedMessage(endpoint, buf), needs_ack

    def ack(self) -> bytes:
        return bytes([CHUNK_ACK, 0x00, self.last_handle, 0x01, self.last_count])
