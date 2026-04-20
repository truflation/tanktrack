"""Read-side queries: counts, current-inside listing, aggregates by vessel type."""
from __future__ import annotations

from tanktrack.db import conn


def count_inside() -> int:
    with conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM vessel_state WHERE inside = 1").fetchone()
        return int(row["n"])


def count_anchored() -> int:
    with conn() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM vessel_state WHERE anchored = 1").fetchone()
        return int(row["n"])


def counts_window(hours: int = 24) -> dict:
    with conn() as c:
        row = c.execute(
            """
            SELECT
                SUM(CASE WHEN event_type='entered'  THEN 1 ELSE 0 END) AS entered,
                SUM(CASE WHEN event_type='exited'   THEN 1 ELSE 0 END) AS exited,
                SUM(CASE WHEN event_type='anchored' THEN 1 ELSE 0 END) AS anchored,
                SUM(CASE WHEN event_type='resumed'  THEN 1 ELSE 0 END) AS resumed
            FROM transitions
            WHERE at > datetime('now', ?)
            """,
            (f"-{hours} hours",),
        ).fetchone()
    return {
        "entered":  int(row["entered"]  or 0),
        "exited":   int(row["exited"]   or 0),
        "anchored": int(row["anchored"] or 0),
        "resumed":  int(row["resumed"]  or 0),
    }


def by_vessel_type_inside() -> dict[str, int]:
    with conn() as c:
        rows = c.execute(
            """
            SELECT COALESCE(v.vessel_type_str, 'unknown') AS kind, COUNT(*) AS n
            FROM vessel_state s
            JOIN vessels v ON v.mmsi = s.mmsi
            WHERE s.inside = 1
            GROUP BY 1
            ORDER BY 2 DESC
            """
        ).fetchall()
        return {r["kind"]: int(r["n"]) for r in rows}


def current_inside(limit: int = 500) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """
            SELECT v.mmsi, v.name, v.vessel_type_str, s.lat, s.lon, s.sog_kt,
                   s.anchored, s.stopped_since, s.last_seen
            FROM vessel_state s
            JOIN vessels v ON v.mmsi = s.mmsi
            WHERE s.inside = 1
            ORDER BY s.last_seen DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_transitions(limit: int = 50) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """
            SELECT t.at, t.event_type, t.direction, t.mmsi,
                   v.name, v.vessel_type_str, t.lat, t.lon, t.sog_kt
            FROM transitions t
            LEFT JOIN vessels v ON v.mmsi = t.mmsi
            ORDER BY t.at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
