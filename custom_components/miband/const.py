"""Constantes de l'intégration Mi Band."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "miband"
MANUFACTURER = "Xiaomi"
MODEL = "Smart Band 7"

CONF_AUTH_KEY = "auth_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SET_TIME = "set_time"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"

DEFAULT_SCAN_INTERVAL = 10  # minutes
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 180
DEFAULT_SET_TIME = True

# Historique minute conservé localement (sommeil, statistiques FC)
HISTORY_KEEP = timedelta(hours=48)
# Profondeur maximale demandée au bracelet lors de la première synchro
FIRST_SYNC_DEPTH = timedelta(hours=36)

STORAGE_VERSION = 1
SAVE_DELAY = 10  # secondes

LOCAL_NAMES = ("Xiaomi Smart Band 7", "Mi Smart Band 7", "Amazfit Band 7")
