"""IMF PortWatch chokepoint context — daily aggregate for Strait of Hormuz.

Free, no API key. ArcGIS Feature Service used by portwatch.imf.org.
Used for historical baselines + 4-month backfill validation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)

ARCGIS_BASE = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
DAILY_CHOKEPOINTS_URL = f"{ARCGIS_BASE}/Daily_Chokepoints_Data/FeatureServer/0/query"


def _date_where(days: int) -> str:
    """PortWatch daily_chokepoints uses integer year/month/day fields.
    Direct epoch comparisons don't work — we build a compound year/month/day clause.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    y, m, d = cutoff.year, cutoff.month, cutoff.day
    return f"(year>{y} OR (year={y} AND month>{m}) OR (year={y} AND month={m} AND day>={d}))"


async def chokepoint_transits(
    chokepoint: str = "Strait of Hormuz",
    days: int = 30,
) -> list[dict]:
    where = f"portname='{chokepoint}' AND {_date_where(days)}"
    params = {
        "where": where,
        "outFields": "*",
        "orderByFields": "year DESC, month DESC, day DESC",
        "resultRecordCount": str(min(days + 10, 2000)),
        "returnGeometry": "false",
        "f": "json",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(DAILY_CHOKEPOINTS_URL, params=params)
        r.raise_for_status()
        data = r.json()
    return [feat.get("attributes", {}) for feat in data.get("features", [])]


async def baseline_summary(chokepoint: str = "Strait of Hormuz") -> dict:
    """Return 7-day and 30-day average daily transits for the chokepoint."""
    try:
        rows = await chokepoint_transits(chokepoint, days=35)
    except Exception as e:
        log.warning("PortWatch lookup failed: %s", e)
        return {"available": False, "error": str(e)}

    if not rows:
        return {"available": False}

    # Prefer n_total_all (aggregate count) — fall back to first numeric field.
    total_key = "n_total_all" if "n_total_all" in rows[0] else None
    if not total_key:
        for k, v in rows[0].items():
            if isinstance(v, (int, float)) and k.startswith("n_"):
                total_key = k
                break
    if not total_key:
        return {"available": False, "error": "no numeric total field found"}

    vals = [r.get(total_key) or 0 for r in rows]

    def _avg(xs):
        xs = [x for x in xs if x]
        return sum(xs) / len(xs) if xs else 0.0

    return {
        "available": True,
        "field": total_key,
        "avg_daily_7d": _avg(vals[:7]),
        "avg_daily_30d": _avg(vals[:30]),
        "samples": len(rows),
    }
