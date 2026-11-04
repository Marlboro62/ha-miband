"""Décodage des réponses du bracelet et calculs dérivés (sommeil, FC)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .const import (
    ACTIVITY_SAMPLE_SIZE,
    BATTERY_REPLY,
    DEEP_THRESHOLD,
    KIND_CHARGING,
    KIND_NOT_WORN,
    KIND_SLEEP,
    REM_THRESHOLD,
    STEPS_REPLY,
)


@dataclass(frozen=True)
class BatteryInfo:
    level: int
    charging: bool


@dataclass(frozen=True)
class StepsInfo:
    steps: int
    distance_m: int
    calories_kcal: int


@dataclass(frozen=True)
class ActivitySample:
    """Un échantillon minute de l'historique d'activité."""

    timestamp: int  # epoch UTC (secondes)
    kind: int
    intensity: int
    steps: int
    heart_rate: int
    sleep: int
    deep_sleep: int
    rem_sleep: int

    @property
    def valid_heart_rate(self) -> int | None:
        return self.heart_rate if 20 <= self.heart_rate <= 250 else None

    @property
    def is_sleep(self) -> bool:
        return self.kind == KIND_SLEEP

    @property
    def is_worn(self) -> bool:
        return self.kind not in (KIND_NOT_WORN, KIND_CHARGING)

    def sleep_phase(self) -> str:
        """« rem », « deep » ou « light » (mêmes seuils que Gadgetbridge)."""
        if (self.rem_sleep & 0x7F) > REM_THRESHOLD:
            return "rem"
        if (self.deep_sleep & 0x7F) > DEEP_THRESHOLD:
            return "deep"
        return "light"

    def as_list(self) -> list[int]:
        return [
            self.timestamp,
            self.kind,
            self.intensity,
            self.steps,
            self.heart_rate,
            self.sleep,
            self.deep_sleep,
            self.rem_sleep,
        ]

    @classmethod
    def from_list(cls, values: list[int]) -> ActivitySample:
        return cls(*values)


@dataclass(frozen=True)
class SleepSummary:
    start: datetime
    end: datetime
    total_min: int
    deep_min: int
    light_min: int
    rem_min: int
    awake_min: int


def parse_battery(payload: bytes) -> BatteryInfo | None:
    """Réponse endpoint batterie : 04 ?? niveau état ..."""
    if len(payload) < 4 or payload[0] != BATTERY_REPLY:
        return None
    level = payload[2]
    if level > 100:
        return None
    return BatteryInfo(level=level, charging=payload[3] == 1)


def parse_steps(payload: bytes) -> StepsInfo | None:
    """Réponse endpoint pas : 04 statut puis 13 octets (flag, pas, distance, calories)."""
    if len(payload) != 15 or payload[0] != STEPS_REPLY:
        return None
    steps, distance, calories = struct.unpack_from("<III", payload, 3)
    return StepsInfo(steps=steps, distance_m=distance, calories_kcal=calories)


def tz_quarters(dt: datetime) -> int:
    """Décalage UTC (DST inclus) en quarts d'heure, sur un octet signé."""
    offset = dt.utcoffset() or timedelta(0)
    return int(offset.total_seconds() // 900) & 0xFF


def encode_fetch_start(dt: datetime) -> bytes:
    """Date de début de récupération (précision minute) + fuseau."""
    return struct.pack("<HBBBB", dt.year, dt.month, dt.day, dt.hour, dt.minute) + bytes(
        [0x00, tz_quarters(dt)]
    )


def encode_current_time(dt: datetime) -> bytes:
    """Format de la caractéristique Current Time attendu par Zepp OS (11 octets)."""
    weekday = (dt.weekday() + 1) % 7  # dimanche = 0
    return struct.pack(
        "<HBBBBBB", dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, weekday
    ) + bytes([0x00, 0x08, tz_quarters(dt)])


def decode_band_time(data: bytes) -> datetime:
    """Horodatage du bracelet : année u16, mois, jour, h, min, s [, fuseau en quarts d'heure]."""
    year, month, day, hour, minute, second = struct.unpack_from("<HBBBBB", data, 0)
    quarters = struct.unpack_from("<b", data, 7)[0] if len(data) >= 8 else 0
    tz = timezone(timedelta(minutes=15 * quarters))
    return datetime(year, month, day, hour, minute, second, tzinfo=tz)


def parse_activity_samples(start: datetime, data: bytes) -> list[ActivitySample]:
    """Échantillons de 8 octets, un par minute à partir de `start`."""
    usable = len(data) - len(data) % ACTIVITY_SAMPLE_SIZE
    base = int(start.timestamp())
    samples: list[ActivitySample] = []
    for minute, offset in enumerate(range(0, usable, ACTIVITY_SAMPLE_SIZE)):
        kind, intensity, steps, hr, _unk, sleep, deep, rem = data[offset : offset + 8]
        samples.append(
            ActivitySample(
                timestamp=base + 60 * minute,
                kind=kind,
                intensity=intensity,
                steps=steps,
                heart_rate=hr,
                sleep=sleep,
                deep_sleep=deep,
                rem_sleep=rem,
            )
        )
    return samples


def last_sleep_session(
    samples: list[ActivitySample],
    max_gap_min: int = 30,
    min_duration_min: int = 60,
) -> SleepSummary | None:
    """Dernière nuit (ou sieste longue) détectée dans les échantillons.

    Les minutes de sommeil séparées de moins de `max_gap_min` minutes sont
    regroupées ; les minutes d'écart à l'intérieur d'une session comptent
    comme « éveillé ». Les sessions de moins de `min_duration_min` minutes de
    sommeil sont ignorées.
    """
    sleep_samples = sorted((s for s in samples if s.is_sleep), key=lambda s: s.timestamp)
    if not sleep_samples:
        return None

    sessions: list[list[ActivitySample]] = [[sleep_samples[0]]]
    for sample in sleep_samples[1:]:
        if sample.timestamp - sessions[-1][-1].timestamp <= (max_gap_min + 1) * 60:
            sessions[-1].append(sample)
        else:
            sessions.append([sample])

    for session in reversed(sessions):
        if len(session) < min_duration_min:
            continue
        phases = {"deep": 0, "light": 0, "rem": 0}
        for sample in session:
            phases[sample.sleep_phase()] += 1
        start_ts = session[0].timestamp
        end_ts = session[-1].timestamp + 60
        span = (end_ts - start_ts) // 60
        return SleepSummary(
            start=datetime.fromtimestamp(start_ts, timezone.utc),
            end=datetime.fromtimestamp(end_ts, timezone.utc),
            total_min=len(session),
            deep_min=phases["deep"],
            light_min=phases["light"],
            rem_min=phases["rem"],
            awake_min=max(0, span - len(session)),
        )
    return None


@dataclass(frozen=True)
class HeartRateStats:
    last: int | None
    last_time: datetime | None
    minimum: int | None
    maximum: int | None
    average: int | None


def heart_rate_stats(samples: list[ActivitySample], since_ts: int) -> HeartRateStats:
    """Dernière FC connue et min/max/moyenne depuis `since_ts`."""
    last: ActivitySample | None = None
    values: list[int] = []
    for sample in sorted(samples, key=lambda s: s.timestamp):
        hr = sample.valid_heart_rate
        if hr is None:
            continue
        last = sample
        if sample.timestamp >= since_ts:
            values.append(hr)
    return HeartRateStats(
        last=last.heart_rate if last else None,
        last_time=datetime.fromtimestamp(last.timestamp, timezone.utc) if last else None,
        minimum=min(values) if values else None,
        maximum=max(values) if values else None,
        average=round(sum(values) / len(values)) if values else None,
    )
