"""AISstream.io WebSocket consumer filtered to the Hormuz bounding box.

Message types we care about:
  - PositionReport (AIS types 1, 2, 3, 18, 19) → lat/lon/SOG/COG + MMSI
  - ShipStaticData (AIS types 5, 24) → name, vessel_type, dimensions, destination

Non-blocking reconnect on disconnect; exponential backoff.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import websockets

from tanktrack.config import AISSTREAM_API_KEY
from tanktrack.geofence import HORMUZ_BBOX
from tanktrack.vessel_state import Position, VesselMeta, process_position, upsert_vessel_meta

log = logging.getLogger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
POSITION_TYPES = {"PositionReport"}
METADATA_TYPES = {"ShipStaticData", "StaticDataReport"}


def _ais_ship_type_str(code: int | None) -> str | None:
    """Collapse the AIS ship-type code range into a high-level category.
    See ITU-R M.1371, Table 53."""
    if code is None:
        return None
    if 70 <= code <= 79:
        return "cargo"
    if 80 <= code <= 89:
        return "tanker"
    if 60 <= code <= 69:
        return "passenger"
    if 30 <= code <= 39:
        return "fishing"
    if 40 <= code <= 49:
        return "high_speed"
    if code == 50:
        return "pilot"
    if code == 51:
        return "search_rescue"
    if code == 52:
        return "tug"
    if 20 <= code <= 29:
        return "wing_in_ground"
    if code == 35:
        return "military"
    return "other"


def _parse_timestamp(meta: dict) -> datetime:
    raw = meta.get("time_utc") or meta.get("TimeUtc") or meta.get("timeUtc")
    if not raw:
        return datetime.now(timezone.utc)
    # AISstream.io timestamps look like "2024-01-01 12:00:00.000000000 +0000 UTC"
    try:
        clean = raw.rsplit(" +", 1)[0].rsplit(".", 1)[0]
        return datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _handle_position(msg: dict) -> None:
    meta = msg.get("MetaData") or {}
    body = (msg.get("Message") or {}).get("PositionReport") or {}
    mmsi = meta.get("MMSI") or body.get("UserID")
    if not mmsi:
        return
    lat = body.get("Latitude")
    lon = body.get("Longitude")
    if lat is None or lon is None:
        return
    sog = body.get("Sog")            # speed over ground, knots
    cog = body.get("Cog")            # course over ground, degrees
    timestamp = _parse_timestamp(meta)

    p = Position(mmsi=int(mmsi), lat=float(lat), lon=float(lon),
                 sog_kt=float(sog) if sog is not None else None,
                 cog=float(cog) if cog is not None else None,
                 timestamp=timestamp)
    events = process_position(p)
    if events:
        log.info("mmsi=%s %s @ (%.4f,%.4f) sog=%.1fkt", p.mmsi,
                 ",".join(events), p.lat, p.lon, p.sog_kt or 0.0)


def _handle_static(msg: dict) -> None:
    meta = msg.get("MetaData") or {}
    body = (msg.get("Message") or {}).get("ShipStaticData") \
        or (msg.get("Message") or {}).get("StaticDataReport") or {}
    mmsi = meta.get("MMSI") or body.get("UserID")
    if not mmsi:
        return

    name = body.get("Name") or meta.get("ShipName")
    ship_type = body.get("Type") or body.get("TypeOfShipAndCargo")
    imo = body.get("ImoNumber")
    callsign = body.get("CallSign")
    dim = body.get("Dimension") or {}
    length = None
    width = None
    if dim:
        length = (dim.get("A") or 0) + (dim.get("B") or 0)
        width = (dim.get("C") or 0) + (dim.get("D") or 0)
        length = float(length) if length else None
        width = float(width) if width else None
    draught = body.get("MaximumStaticDraught")
    destination = body.get("Destination")

    upsert_vessel_meta(VesselMeta(
        mmsi=int(mmsi),
        name=(name or "").strip() or None,
        vessel_type=int(ship_type) if ship_type is not None else None,
        vessel_type_str=_ais_ship_type_str(ship_type),
        imo=int(imo) if imo else None,
        callsign=(callsign or "").strip() or None,
        length_m=length,
        width_m=width,
        draught_m=float(draught) if draught else None,
        destination=(destination or "").strip() or None,
    ))


async def _run_once() -> None:
    if not AISSTREAM_API_KEY:
        raise RuntimeError("AISSTREAM_API_KEY not set — register at https://aisstream.io/authenticate")

    subscription = {
        "APIKey": AISSTREAM_API_KEY,
        "BoundingBoxes": [HORMUZ_BBOX],
        "FilterMessageTypes": list(POSITION_TYPES | METADATA_TYPES),
    }
    async with websockets.connect(AISSTREAM_URL, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps(subscription))
        log.info("subscribed to AISstream.io for Hormuz bbox %s", HORMUZ_BBOX)

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("MessageType")
            if mtype in POSITION_TYPES:
                _handle_position(msg)
            elif mtype in METADATA_TYPES:
                _handle_static(msg)


async def run_forever() -> None:
    backoff = 1.0
    while True:
        try:
            await _run_once()
            backoff = 1.0
        except Exception as e:
            log.error("AISstream disconnect / error: %s — reconnecting in %.1fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
