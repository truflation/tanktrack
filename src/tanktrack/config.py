"""Config loader."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

ROOT = _ROOT

DB_PATH = os.environ.get("TANKTRACK_DB_PATH", str(_ROOT / "tanktrack.db"))
AISSTREAM_API_KEY = os.environ.get("AISSTREAM_API_KEY", "")
ANCHOR_SOG_KT = float(os.environ.get("ANCHOR_SOG_KT", "0.5"))
ANCHOR_MIN_MINUTES = int(os.environ.get("ANCHOR_MIN_MINUTES", "15"))
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8088"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
