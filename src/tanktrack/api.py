"""FastAPI exposing the live tanktrack state."""
from __future__ import annotations

from fastapi import FastAPI

from tanktrack.config import setup_logging
from tanktrack.counters import (
    by_vessel_type_inside,
    count_anchored,
    count_inside,
    counts_window,
    current_inside,
    recent_transitions,
)
from tanktrack.portwatch import baseline_summary

setup_logging()
app = FastAPI(title="tanktrack — Strait of Hormuz Monitor", version="0.1.0")


@app.get("/")
def root() -> dict:
    return {
        "service": "tanktrack",
        "version": "0.1.0",
        "endpoints": ["/state", "/inside", "/transitions", "/baseline"],
    }


@app.get("/state")
def state() -> dict:
    """Headline snapshot: entered/exited/anchored over 24h + current counts."""
    return {
        "in_transit_now": count_inside(),
        "anchored_now": count_anchored(),
        "last_24h": counts_window(hours=24),
        "last_1h": counts_window(hours=1),
        "by_vessel_type": by_vessel_type_inside(),
    }


@app.get("/inside")
def inside(limit: int = 500) -> list[dict]:
    """Full list of vessels currently inside the polygon."""
    return current_inside(limit=limit)


@app.get("/transitions")
def transitions(limit: int = 50) -> list[dict]:
    """Most recent discrete events."""
    return recent_transitions(limit=limit)


@app.get("/baseline")
async def baseline() -> dict:
    """Historical context from IMF PortWatch (7d and 30d average daily transits)."""
    return await baseline_summary()
