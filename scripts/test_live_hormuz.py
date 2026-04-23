"""Live geofence validation against the Malacca Strait (currently ~150-200 transits/day).

Connects to AISstream.io with a Hormuz bounding box, runs incoming messages
through the same is_inside() + state-machine logic the production pipeline
uses, and reports entered/exited/inside-now counts over a fixed window.

Does NOT touch hormuz.db — all state is in-memory, so Hormuz data stays clean.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import websockets
from shapely.geometry import Point, Polygon

from tanktrack.config import AISSTREAM_API_KEY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-6s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hormuz-test")

# Hormuz polygon vertices — (lat, lon). Counter-clockwise.
# Adjusted to include:
#   - NW entrance from the Persian Gulf (Larak / Qeshm area)
#   - NE corner running along the Iran coast
#   - SE exit toward the Gulf of Oman (south of Musandam)
#   - SW corner near the Strait's southern mouth
HORMUZ_POLYGON_LATLON: list[tuple[float, float]] = [
    (26.80, 55.80),
    (27.00, 56.50),
    (26.70, 56.95),
    (26.15, 57.05),
    (25.75, 56.60),
    (25.85, 56.10),
]

# Bounding box for the AIS subscription filter: [SW, NE] as lat/lon pairs.
# Slightly looser than the polygon so we don't miss messages near the edge.
HORMUZ_BBOX = [
    [25.60, 55.70],   # SW
    [27.10, 57.20],   # NE
]


def polygon() -> Polygon:
    # Shapely wants (x, y) = (lon, lat)
    return Polygon([(lon, lat) for (lat, lon) in HORMUZ_POLYGON_LATLON])


POLY = polygon()


def is_inside(lat: float, lon: float) -> bool:
    return POLY.contains(Point(lon, lat))


# --- In-memory state machine (mirror of hormuz.vessel_state, no DB) ---------

@dataclass
class State:
    inside: bool = False
    last_seen: datetime | None = None


@dataclass
class Counters:
    messages_total: int = 0
    positions: int = 0
    static: int = 0
    entered: int = 0
    exited: int = 0
    inside_now: int = 0
    unique_mmsi_seen: set[int] = field(default_factory=set)


def process(mmsi: int, lat: float, lon: float, state: dict[int, State], counters: Counters) -> None:
    counters.positions += 1
    counters.unique_mmsi_seen.add(mmsi)

    inside_now = is_inside(lat, lon)
    prev = state.get(mmsi, State())
    if inside_now and not prev.inside:
        counters.entered += 1
        log.info("  ENTER  mmsi=%s @ (%.4f,%.4f)", mmsi, lat, lon)
    elif prev.inside and not inside_now:
        counters.exited += 1
        log.info("  EXIT   mmsi=%s @ (%.4f,%.4f)", mmsi, lat, lon)

    state[mmsi] = State(inside=inside_now, last_seen=datetime.now(timezone.utc))
    counters.inside_now = sum(1 for s in state.values() if s.inside)


async def run(duration_s: int = 60) -> None:
    if not AISSTREAM_API_KEY:
        log.error("AISSTREAM_API_KEY not set"); return

    sub = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": [HORMUZ_BBOX],
    }
    state: dict[int, State] = {}
    counters = Counters()

    log.info("connecting to AISstream.io, bbox=%s, duration=%ds", HORMUZ_BBOX, duration_s)
    async with websockets.connect("wss://stream.aisstream.io/v0/stream",
                                  ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps(sub))
        log.info("subscribed; reading for %ds…", duration_s)

        try:
            async with asyncio.timeout(duration_s):
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    counters.messages_total += 1
                    mtype = msg.get("MessageType")
                    if mtype == "PositionReport":
                        meta = msg.get("MetaData") or {}
                        body = (msg.get("Message") or {}).get("PositionReport") or {}
                        mmsi = meta.get("MMSI") or body.get("UserID")
                        lat = body.get("Latitude")
                        lon = body.get("Longitude")
                        if mmsi and lat is not None and lon is not None:
                            process(int(mmsi), float(lat), float(lon), state, counters)
                    elif mtype in ("ShipStaticData", "StaticDataReport"):
                        counters.static += 1
        except TimeoutError:
            pass

    print()
    print("=" * 60)
    print("Hormuz geofence validation — results")
    print("=" * 60)
    print(f"  messages received:        {counters.messages_total}")
    print(f"  position reports:         {counters.positions}")
    print(f"  static-data messages:     {counters.static}")
    print(f"  unique MMSIs seen:        {len(counters.unique_mmsi_seen)}")
    print(f"  entered polygon:          {counters.entered}")
    print(f"  exited polygon:           {counters.exited}")
    print(f"  inside polygon right now: {counters.inside_now}")
    print()
    if counters.positions == 0:
        print("  ⚠ zero position reports — bbox or subscription issue")
    elif counters.inside_now == 0 and counters.entered == 0:
        print("  ⚠ messages received but 0 inside polygon — polygon may be badly positioned")
    else:
        print("  ✓ geofence is firing correctly")


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    asyncio.run(run(duration_s=duration))
