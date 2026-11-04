"""Tests du protocole Zepp OS (sans Home Assistant ni matériel)."""

from __future__ import annotations

import asyncio
import struct
from datetime import datetime, timedelta, timezone

import pytest
from fake_band import FakeBand

from protocol import ecdh
from protocol.chunked import ChunkedDecoder, ChunkedEncoder
from protocol.client import MiBandAuthError, MiBandClient, parse_auth_key
from protocol.const import ENDPOINT_BATTERY, KIND_SLEEP
from protocol.parsers import (
    ActivitySample,
    decode_band_time,
    encode_current_time,
    encode_fetch_start,
    heart_rate_stats,
    last_sleep_session,
    parse_activity_samples,
    parse_battery,
    parse_steps,
)

AUTH_KEY = bytes.fromhex("a3c10e34e5c14637eea6b9efc0610601")
PARIS_WINTER = timezone(timedelta(hours=1))


# --------------------------------------------------------------------- ECDH B-163


def test_generator_and_order():
    assert ecdh.on_curve(ecdh.GENERATOR)
    assert ecdh.scalar_mult(ecdh.ORDER, ecdh.GENERATOR) is None


def test_real_band_public_key_is_on_curve():
    """Clé publique d'un vrai bracelet (vecteur de test de Gadgetbridge) : valide l'ordre des octets."""
    remote = bytes.fromhex(
        "e6016aba1de7ac0f0c7f0ff7e2243e6662b5e03b01000000"
        "ad8a4bedc76a1efde7725cc662b54835513e3d5705000000"
    )
    assert ecdh.on_curve(ecdh.decode_point(remote))
    private = bytes.fromhex("0b42b9e61c23340e35c16e2e7de433f4b5859a72ec114027")
    shared = ecdh.shared_secret(private, remote)
    assert ecdh.on_curve(ecdh.decode_point(shared))


def test_shared_secret_is_symmetric():
    a = ecdh.generate_private_key()
    b = ecdh.generate_private_key()
    assert ecdh.shared_secret(a, ecdh.public_key(b)) == ecdh.shared_secret(b, ecdh.public_key(a))


def test_invalid_remote_key_rejected():
    with pytest.raises(ValueError):
        ecdh.shared_secret(ecdh.generate_private_key(), bytes(48))


# ------------------------------------------------------------------- transport


@pytest.mark.parametrize("encrypt", [False, True])
@pytest.mark.parametrize("size", [1, 12, 13, 100, 700])
def test_chunked_roundtrip(encrypt, size):
    key = bytes(range(16))
    encoder = ChunkedEncoder(mtu=23)
    encoder.set_encryption(0x12345678, key)
    decoder = ChunkedDecoder(session_key=key)
    payload = bytes((i * 7) & 0xFF for i in range(size))
    chunks = encoder.encode(0x0029, payload, encrypt)
    assert all(len(c) <= 20 for c in chunks)
    message = None
    for i, chunk in enumerate(chunks):
        message, needs_ack = decoder.decode(chunk)
        assert needs_ack == (i == len(chunks) - 1)
    assert message is not None
    assert message.endpoint == 0x0029
    assert message.payload == payload


def test_chunk_header_layout():
    chunks = ChunkedEncoder(mtu=247).encode(0x0082, b"\x01\x02", False)
    assert chunks == [bytes([0x03, 0x07, 0x00, 0x01, 0x00, 2, 0, 0, 0, 0x82, 0x00, 0x01, 0x02])]


def test_ack_format():
    decoder = ChunkedDecoder()
    decoder.decode(bytes([0x03, 0x07, 0x00, 0x05, 0x00, 1, 0, 0, 0, 0x29, 0x00, 0xAA]))
    assert decoder.ack() == bytes([0x04, 0x00, 0x05, 0x01, 0x00])


# --------------------------------------------------------------------- parseurs


def test_parse_auth_key():
    assert parse_auth_key("0xA3C10E34E5C14637EEA6B9EFC0610601") == AUTH_KEY
    assert parse_auth_key(" a3c10e34e5c14637eea6b9efc0610601 ") == AUTH_KEY
    with pytest.raises(ValueError):
        parse_auth_key("0x1234")


def test_parse_battery_and_steps():
    battery = parse_battery(bytes([0x04, 0x0F, 64, 0x00]) + bytes(17))
    assert battery.level == 64 and battery.charging is False
    steps = parse_steps(bytes([0x04, 0x01, 0x0C]) + struct.pack("<III", 8000, 5600, 320))
    assert (steps.steps, steps.distance_m, steps.calories_kcal) == (8000, 5600, 320)
    assert parse_steps(b"\x04\x01") is None


def test_time_encodings():
    dt = datetime(2026, 11, 4, 1, 53, 20, tzinfo=PARIS_WINTER)
    assert encode_fetch_start(dt) == bytes([0xEA, 0x07, 11, 4, 1, 53, 0x00, 4])
    # mercredi -> 3 (dimanche = 0)
    assert encode_current_time(dt) == bytes([0xEA, 0x07, 11, 4, 1, 53, 20, 3, 0x00, 0x08, 4])
    decoded = decode_band_time(bytes([0xEA, 0x07, 11, 4, 1, 53, 0, 4]))
    assert decoded == datetime(2026, 11, 4, 1, 53, tzinfo=PARIS_WINTER)


def _sample(ts, kind=80, hr=70, deep=0, rem=0):
    return ActivitySample(ts, kind, 10, 0, hr, 0, deep, rem)


def test_sleep_session_and_phases():
    base = int(datetime(2026, 11, 3, 23, 0, tzinfo=PARIS_WINTER).timestamp())
    samples = [_sample(base - 3600 + 60 * i) for i in range(60)]  # éveillé
    for i in range(420):  # 7 h de sommeil
        deep = 60 if 60 <= i < 150 else 0
        rem = 80 if 300 <= i < 360 else 0
        if 200 <= i < 210:  # réveil de 10 min
            samples.append(_sample(base + 60 * i, kind=80))
            continue
        samples.append(_sample(base + 60 * i, kind=KIND_SLEEP, deep=deep, rem=rem))
    samples.append(_sample(base + 60 * 600, kind=KIND_SLEEP))  # minute isolée ignorée

    summary = last_sleep_session(samples)
    assert summary is not None
    assert summary.total_min == 410
    assert summary.deep_min == 90
    assert summary.rem_min == 60
    assert summary.light_min == 260
    assert summary.awake_min == 10
    assert summary.start == datetime(2026, 11, 3, 22, 0, tzinfo=timezone.utc)
    assert summary.end == datetime(2026, 11, 4, 5, 0, tzinfo=timezone.utc)


def test_heart_rate_stats_ignores_invalid():
    samples = [_sample(1000, hr=255), _sample(1060, hr=58), _sample(1120, hr=0), _sample(1180, hr=90)]
    stats = heart_rate_stats(samples, since_ts=1050)
    assert (stats.last, stats.minimum, stats.maximum, stats.average) == (90, 58, 90, 74)


def test_parse_activity_samples():
    start = datetime(2026, 11, 4, 0, 0, tzinfo=PARIS_WINTER)
    data = bytes([80, 20, 5, 72, 0, 0, 0, 0, KIND_SLEEP, 3, 0, 55, 0, 9, 50, 2, 1, 2])
    samples = parse_activity_samples(start, data)
    assert len(samples) == 2  # octets en trop ignorés
    assert samples[1].timestamp - samples[0].timestamp == 60
    assert samples[1].is_sleep and samples[1].sleep_phase() == "deep"


# ------------------------------------------------------------ dialogue complet


def _run(coro):
    return asyncio.run(coro)


def test_full_session_with_fake_band():
    start = datetime(2026, 11, 3, 22, 0, tzinfo=PARIS_WINTER)
    raw = b"".join(bytes([KIND_SLEEP, 5, 0, 55 + i % 5, 0, 9, 0, 0]) for i in range(150))
    band = FakeBand(AUTH_KEY, samples=raw, sample_start=start)

    async def scenario():
        client = MiBandClient(band, AUTH_KEY)
        await client.connect()
        battery = await client.get_battery()
        steps = await client.get_steps()
        await client.set_time(datetime(2026, 11, 4, 1, 53, tzinfo=PARIS_WINTER))
        samples = await client.fetch_activity(start, datetime(2026, 11, 4, 1, 53, tzinfo=PARIS_WINTER))
        return client, battery, steps, samples

    client, battery, steps, samples = _run(scenario())
    assert client.authenticated
    assert client._encoder.mtu == 185  # min(MTU bracelet 247, MTU lien 185)
    assert battery.level == 87 and battery.charging
    assert steps.steps == 5432 and steps.distance_m == 3900 and steps.calories_kcal == 210
    assert band.time_written is not None

    # la requête batterie est partie chiffrée, avec un CRC valide
    battery_requests = [r for r in band.received if r[0] == ENDPOINT_BATTERY]
    assert battery_requests == [(ENDPOINT_BATTERY, b"\x03", True)]
    assert band.check_encrypted_message(band.raw_encrypted_chunks[0])
    assert band.acks_received > 0

    # 150 minutes en 3 manches (60 + 60 + 30), horodatées minute par minute
    assert len(samples) == 150
    assert samples[0].timestamp == int(start.timestamp())
    assert samples[-1].timestamp == int(start.timestamp()) + 149 * 60
    assert all(ack == b"\x03\x09" for ack in band.fetch_acks)


def test_wrong_key_is_rejected():
    band = FakeBand(AUTH_KEY)

    async def scenario():
        await MiBandClient(band, bytes(16)).connect()

    with pytest.raises(MiBandAuthError):
        _run(scenario())
