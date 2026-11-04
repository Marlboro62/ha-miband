"""Rend le paquet `protocol` importable sans Home Assistant."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components" / "miband"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
