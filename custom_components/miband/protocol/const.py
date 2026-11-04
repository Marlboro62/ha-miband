"""Constantes du protocole BLE Zepp OS (Mi Band 7 et dérivés)."""

from __future__ import annotations


def huami_uuid(short: int) -> str:
    return f"{short:08x}-0000-3512-2118-0009af100700"


def sig_uuid(short: int) -> str:
    return f"{short:08x}-0000-1000-8000-00805f9b34fb"


# Caractéristiques
CHAR_FETCH = huami_uuid(0x0004)  # contrôle de la récupération d'historique
CHAR_ACTIVITY_DATA = huami_uuid(0x0005)  # flux de données d'historique
CHAR_CHUNKED_WRITE = huami_uuid(0x0016)
CHAR_CHUNKED_READ = huami_uuid(0x0017)
CHAR_CURRENT_TIME = sig_uuid(0x2A2B)
CHAR_FIRMWARE_REVISION = sig_uuid(0x2A26)

SERVICE_MIBAND = sig_uuid(0xFEE0)

# Endpoints du transport chunked
ENDPOINT_SERVICES = 0x0000
ENDPOINT_CONNECTION = 0x0015
ENDPOINT_STEPS = 0x0016
ENDPOINT_HEARTRATE = 0x001D
ENDPOINT_BATTERY = 0x0029
ENDPOINT_AUTH = 0x0082

# Réponses génériques
RESPONSE = 0x10
SUCCESS = 0x01
AUTH_FAILED = 0x25

# Services (liste des endpoints et de leur chiffrement)
SERVICES_GET_LIST = 0x03
SERVICES_RET_LIST = 0x04

# Connexion
CONNECTION_MTU_REQUEST = 0x01
CONNECTION_MTU_RESPONSE = 0x02
CONNECTION_PING = 0x03
CONNECTION_PONG = 0x04

# Pas
STEPS_GET = 0x03
STEPS_REPLY = 0x04

# Batterie
BATTERY_REQUEST = 0x03
BATTERY_REPLY = 0x04

# Récupération d'historique
FETCH_START_DATE = 0x01
FETCH_DATA = 0x02
FETCH_ACK = 0x03
FETCH_ACK_KEEP_ON_DEVICE = 0x09
FETCH_TYPE_ACTIVITY = 0x01

ACTIVITY_SAMPLE_SIZE = 8

# Types d'échantillon d'activité
KIND_NOT_WORN = 115
KIND_CHARGING = 118
KIND_SLEEP = 120

# Seuils Gadgetbridge pour distinguer les phases de sommeil
REM_THRESHOLD = 55
DEEP_THRESHOLD = 42
