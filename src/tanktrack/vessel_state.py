"""Per-vessel state machine + transition detection (SQLite backend).

On every AIS update the pipeline calls `process_position` which:
  - upserts vessels.* metadata if new
  - reads the previous (inside, anchored, stopped_since) state
  - computes the new state
  - writes transition rows for each discrete event that occurred
  - upserts the new vessel_state row

Transitions emitted:
  - 'entered'  — outside → inside
  - 'exited'   — inside → outside
  - 'anchored' — inside + SOG below threshold for >= ANCHOR_MIN_MINUTES
  - 'resumed'  — anchored → moving (SOG above threshold again)
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from tanktrack.config import ANCHOR_MIN_MINUTES, ANCHOR_SOG_KT
from tanktrack.db import conn
from tanktrack.geofence import classify_exit, is_inside

log = logging.getLogger(__name__)


@dataclass
class Position:
    mmsi: int
    lat: float
    lon: float
    sog_kt: Optional[float]
    cog: Optional[float]
    timestamp: datetime


@dataclass
class VesselMeta:
    mmsi: int
    name: Optional[str] = None
    vessel_type: Optional[int] = None
    vessel_type_str: Optional[str] = None
    imo: Optional[int] = None
    callsign: Optional[str] = None
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    draught_m: Optional[float] = None
    destination: Optional[str] = None


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # "YYYY-MM-DDTHH:MM:SS.sssZ"
        s2 = s.rstrip("Z")
        return datetime.fromisoformat(s2).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def upsert_vessel_meta(m: VesselMeta) -> None:
    with conn() as c:
        c.execute(
            """
            INSERT INTO vessels (mmsi, name, vessel_type, vessel_type_str, imo, callsign,
                                 length_m, width_m, draught_m, destination, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mmsi) DO UPDATE SET
                name            = COALESCE(excluded.name,            vessels.name),
                vessel_type     = COALESCE(excluded.vessel_type,     vessels.vessel_type),
                vessel_type_str = COALESCE(excluded.vessel_type_str, vessels.vessel_type_str),
                imo             = COALESCE(excluded.imo,             vessels.imo),
                callsign        = COALESCE(excluded.callsign,        vessels.callsign),
                length_m        = COALESCE(excluded.length_m,        vessels.length_m),
                width_m         = COALESCE(excluded.width_m,         vessels.width_m),
                draught_m       = COALESCE(excluded.draught_m,       vessels.draught_m),
                destination     = COALESCE(excluded.destination,     vessels.destination),
                updated_at      = excluded.updated_at
            """,
            (
                m.mmsi, m.name, m.vessel_type, m.vessel_type_str, m.imo, m.callsign,
                m.length_m, m.width_m, m.draught_m, m.destination,
                _iso(datetime.now(timezone.utc)),
            ),
        )


def process_position(p: Position) -> list[str]:
    """Advance state for one position report. Returns list of event_type strings emitted."""
    ts_iso = _iso(p.timestamp)

    with conn() as c:
        # Make sure vessels row exists (FK)
        c.execute(
            "INSERT OR IGNORE INTO vessels (mmsi, updated_at) VALUES (?, ?)",
            (p.mmsi, ts_iso),
        )

        row = c.execute(
            "SELECT inside, anchored, stopped_since FROM vessel_state WHERE mmsi = ?",
            (p.mmsi,),
        ).fetchone()

        if row is None:
            prev_inside, prev_anchored, prev_stopped_since = False, False, None
        else:
            prev_inside = bool(row["inside"])
            prev_anchored = bool(row["anchored"])
            prev_stopped_since = _parse_iso(row["stopped_since"])

        inside_now = is_inside(p.lat, p.lon)
        sog = p.sog_kt if p.sog_kt is not None else 0.0
        events: list[str] = []

        # --- stop timer (only meaningful while inside) -----------------------
        if inside_now and sog < ANCHOR_SOG_KT:
            stopped_since_new = prev_stopped_since or p.timestamp
        else:
            stopped_since_new = None

        anchored_now = (
            inside_now
            and stopped_since_new is not None
            and (p.timestamp - stopped_since_new) >= timedelta(minutes=ANCHOR_MIN_MINUTES)
        )

        # --- transition detection --------------------------------------------
        if inside_now and not prev_inside:
            events.append("entered")
            c.execute(
                """
                INSERT INTO transitions (id, mmsi, event_type, lat, lon, sog_kt, at)
                VALUES (?, ?, 'entered', ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, p.mmsi, p.lat, p.lon, p.sog_kt, ts_iso),
            )
        elif prev_inside and not inside_now:
            events.append("exited")
            c.execute(
                """
                INSERT INTO transitions (id, mmsi, event_type, direction, lat, lon, sog_kt, at)
                VALUES (?, ?, 'exited', ?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, p.mmsi, classify_exit(p.lat, p.lon),
                 p.lat, p.lon, p.sog_kt, ts_iso),
            )

        if anchored_now and not prev_anchored:
            events.append("anchored")
            c.execute(
                """
                INSERT INTO transitions (id, mmsi, event_type, lat, lon, sog_kt, at)
                VALUES (?, ?, 'anchored', ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, p.mmsi, p.lat, p.lon, p.sog_kt, ts_iso),
            )
        elif prev_anchored and not anchored_now and inside_now:
            events.append("resumed")
            c.execute(
                """
                INSERT INTO transitions (id, mmsi, event_type, lat, lon, sog_kt, at)
                VALUES (?, ?, 'resumed', ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, p.mmsi, p.lat, p.lon, p.sog_kt, ts_iso),
            )

        # --- upsert vessel_state ---------------------------------------------
        c.execute(
            """
            INSERT INTO vessel_state (mmsi, lat, lon, sog_kt, cog, inside, anchored, stopped_since, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mmsi) DO UPDATE SET
                lat           = excluded.lat,
                lon           = excluded.lon,
                sog_kt        = excluded.sog_kt,
                cog           = excluded.cog,
                inside        = excluded.inside,
                anchored      = excluded.anchored,
                stopped_since = excluded.stopped_since,
                last_seen     = excluded.last_seen
            """,
            (
                p.mmsi, p.lat, p.lon, p.sog_kt, p.cog,
                1 if inside_now else 0,
                1 if anchored_now else 0,
                _iso(stopped_since_new) if stopped_since_new else None,
                ts_iso,
            ),
        )

    return events
