"""Save a reproducible Malacca validation artifact.

Connects to AISstream.io for a fixed window, collects every position report
inside the Malacca bounding box, classifies each point with the polygon test,
writes a JSON dump + a scatter-plot PNG showing:
  - bounding box (dashed grey)
  - polygon (solid green)
  - positions classified as inside (blue)
  - positions classified as outside (red)

This is the end-to-end validation: messages come in, polygon fires correctly,
inside/outside classification is visually obvious.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import websockets
from shapely.geometry import Point, Polygon

from tanktrack.config import AISSTREAM_API_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-6s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("malacca-val")

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)

MALACCA_BBOX = [[1.00, 99.50], [3.50, 104.50]]
MALACCA_POLYGON_LATLON = [
    (1.05, 99.70),
    (3.30, 99.90),
    (3.45, 102.50),
    (2.10, 104.30),
    (1.10, 104.30),
    (1.05, 103.00),
]
POLY = Polygon([(lon, lat) for (lat, lon) in MALACCA_POLYGON_LATLON])


async def collect(duration_s: int) -> dict:
    if not AISSTREAM_API_KEY:
        raise RuntimeError("AISSTREAM_API_KEY not set")

    positions: list[dict] = []
    seen: dict[int, int] = {}
    sub = {"APIKey": AISSTREAM_API_KEY, "BoundingBoxes": [MALACCA_BBOX]}

    log.info("connecting, window=%ds", duration_s)
    async with websockets.connect("wss://stream.aisstream.io/v0/stream",
                                  ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps(sub))
        try:
            async with asyncio.timeout(duration_s):
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("MessageType") != "PositionReport":
                        continue
                    body = (msg.get("Message") or {}).get("PositionReport") or {}
                    meta = msg.get("MetaData") or {}
                    mmsi = meta.get("MMSI") or body.get("UserID")
                    lat = body.get("Latitude")
                    lon = body.get("Longitude")
                    if not mmsi or lat is None or lon is None:
                        continue
                    mmsi = int(mmsi)
                    # Dedupe per vessel — keep most recent
                    seen[mmsi] = len(positions)
                    inside = POLY.contains(Point(float(lon), float(lat)))
                    positions.append({
                        "mmsi": mmsi,
                        "lat": float(lat),
                        "lon": float(lon),
                        "sog_kt": body.get("Sog"),
                        "cog": body.get("Cog"),
                        "inside": bool(inside),
                    })
        except TimeoutError:
            pass

    # Keep only the latest position per vessel
    latest = {}
    for p in positions:
        latest[p["mmsi"]] = p
    unique_positions = list(latest.values())

    return {
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_seconds": duration_s,
        "total_raw_positions": len(positions),
        "unique_vessels": len(unique_positions),
        "inside_count": sum(1 for p in unique_positions if p["inside"]),
        "outside_count": sum(1 for p in unique_positions if not p["inside"]),
        "bbox_sw_ne": MALACCA_BBOX,
        "polygon_latlon": MALACCA_POLYGON_LATLON,
        "positions": unique_positions,
    }


def plot(data: dict, out_path: Path) -> None:
    plt.rcParams.update({
        "figure.facecolor": "#0a0a0a",
        "axes.facecolor": "#161616",
        "savefig.facecolor": "#0a0a0a",
        "axes.edgecolor": "#e6e6e6",
        "axes.labelcolor": "#e6e6e6",
        "xtick.color": "#e6e6e6",
        "ytick.color": "#e6e6e6",
        "text.color": "#e6e6e6",
        "axes.titlecolor": "#e6e6e6",
        "grid.color": "#333",
        "grid.alpha": 0.5,
    })

    fig, ax = plt.subplots(figsize=(11, 7), dpi=140)

    # bbox
    [[sw_lat, sw_lon], [ne_lat, ne_lon]] = data["bbox_sw_ne"]
    ax.plot([sw_lon, ne_lon, ne_lon, sw_lon, sw_lon],
            [sw_lat, sw_lat, ne_lat, ne_lat, sw_lat],
            linestyle="--", color="#888", linewidth=1.4, label="AIS bbox (subscription filter)")

    # polygon
    xs = [lon for (_, lon) in data["polygon_latlon"]] + [data["polygon_latlon"][0][1]]
    ys = [lat for (lat, _) in data["polygon_latlon"]] + [data["polygon_latlon"][0][0]]
    ax.plot(xs, ys, color="#2ecc71", linewidth=2.2, label="geofence polygon")
    ax.fill(xs, ys, color="#2ecc71", alpha=0.07)

    # positions
    inside = [(p["lon"], p["lat"]) for p in data["positions"] if p["inside"]]
    outside = [(p["lon"], p["lat"]) for p in data["positions"] if not p["inside"]]
    if inside:
        ax.scatter([lon for lon, _ in inside], [lat for _, lat in inside],
                   s=22, color="#4a90e2", alpha=0.85, edgecolors="white", linewidths=0.4,
                   label=f"inside polygon ({len(inside)})")
    if outside:
        ax.scatter([lon for lon, _ in outside], [lat for _, lat in outside],
                   s=22, color="#e74c3c", alpha=0.85, edgecolors="white", linewidths=0.4,
                   label=f"outside polygon ({len(outside)})")

    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")
    ax.set_title(
        f"Malacca Strait geofence validation — {data['unique_vessels']} unique vessels "
        f"in {data['window_seconds']}s window",
        fontsize=12, pad=14,
    )
    ax.legend(loc="upper left", facecolor="#161616", edgecolor="#e6e6e6", fontsize=9)
    ax.grid(True)
    ax.set_aspect("equal", adjustable="datalim")

    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


async def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    data = await collect(duration)

    json_path = RESULTS / "malacca_validation.json"
    png_path = RESULTS / "malacca_validation.png"
    json_path.write_text(json.dumps(data, indent=2))
    plot(data, png_path)

    print()
    print("=" * 60)
    print("Malacca Strait — geofence validation")
    print("=" * 60)
    print(f"  collected_at:          {data['collected_at']}")
    print(f"  window:                {data['window_seconds']}s")
    print(f"  unique vessels:        {data['unique_vessels']}")
    print(f"  classified as inside:  {data['inside_count']}")
    print(f"  classified as outside: {data['outside_count']}")
    print()
    print(f"  JSON: {json_path}")
    print(f"  PNG:  {png_path}")


if __name__ == "__main__":
    asyncio.run(main())
