"""Live geofence validation against the Malacca Strait (currently ~150-200 transits/day).

Connects to AISstream.io with a Malacca bounding box, runs incoming messages
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
log = logging.getLogger("malacca-test")

# --- Malacca Strait bbox (SW, NE) -------------------------------------------
# Covers the whole channel from the NW entrance (north of Sumatra)
# down through Singapore Strait.
MALACCA_BBOX = [
    [1.00, 99.50],
    [3.50, 104.50],
]

# Polygon (counter-clockwise). Slightly tighter than the bbox to exercise the
# point-in-polygon path the same way Hormuz does.
MALACCA_POLYGON_LATLON = [
    (1.05, 99.70),
    (3.30, 99.90),
    (3.45, 102.50),
    (2.10, 104.30),
    (1.10, 104.30),
    (1.05, 103.00),
]


def polygon() -> Polygon:
    # Shapely wants (x, y) = (lon, lat)
    return Polygon([(lon, lat) for (lat, lon) in MALACCA_POLYGON_LATLON])


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
        "BoundingBoxes": [MALACCA_BBOX],
    }
    state: dict[int, State] = {}
    counters = Counters()

    log.info("connecting to AISstream.io, bbox=%s, duration=%ds", MALACCA_BBOX, duration_s)
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
    print("Malacca geofence validation — results")
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
