"""Strait of Hormuz polygon + AIS bounding box + exit-direction classifier.

Polygon is a hand-digitised quadrilateral covering the navigable strait between
Qeshm Island (Iran) and the Musandam Peninsula (Oman). The narrowest point is
~21 nautical miles (~39 km) across.

Bounding box is a rectangle that fully contains the polygon — we pass it to
AISstream.io as a subscription filter to keep bandwidth small; the polygon is
applied locally as the real geofence.
"""
from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Point, Polygon

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


def hormuz_polygon() -> Polygon:
    """Shapely polygon in (lon, lat) order for point-in-polygon checks."""
    # shapely expects (x, y) = (lon, lat)
    return Polygon([(lon, lat) for (lat, lon) in HORMUZ_POLYGON_LATLON])


def is_inside(lat: float, lon: float) -> bool:
    return hormuz_polygon().contains(Point(lon, lat))


@dataclass(frozen=True)
class ExitDirection:
    direction: str    # 'north' | 'south' | 'east' | 'west'


_CENTROID = None


def _centroid() -> tuple[float, float]:
    global _CENTROID
    if _CENTROID is None:
        c = hormuz_polygon().centroid
        _CENTROID = (c.y, c.x)   # (lat, lon)
    return _CENTROID


def classify_exit(exit_lat: float, exit_lon: float) -> str:
    """Classify the crossing direction relative to the polygon centroid.

    For Hormuz specifically:
      - 'north' / 'west'  ~ Persian Gulf side (inbound to Gulf becomes a south->north crossing)
      - 'south' / 'east'  ~ Gulf of Oman side (outbound)
    """
    clat, clon = _centroid()
    dlat = exit_lat - clat
    dlon = exit_lon - clon
    if abs(dlat) >= abs(dlon):
        return "north" if dlat > 0 else "south"
    return "east" if dlon > 0 else "west"
