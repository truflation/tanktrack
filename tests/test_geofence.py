"""Smoke tests for the geofence polygon."""
from __future__ import annotations

from tanktrack.geofence import classify_exit, is_inside


def test_inside_centroid_region():
    # Somewhere in the middle of the strait
    assert is_inside(26.5, 56.4) is True


def test_outside_persian_gulf():
    # Well inside the Persian Gulf, west of the strait
    assert is_inside(26.5, 54.0) is False


def test_outside_gulf_of_oman():
    # East of the strait, Arabian Sea
    assert is_inside(25.0, 58.5) is False


def test_classify_exit_directions():
    # Crossing toward the north from the centroid's perspective
    assert classify_exit(27.1, 56.4) == "north"
    # Crossing to the south
    assert classify_exit(25.5, 56.4) == "south"
    # Crossing to the east
    assert classify_exit(26.4, 57.3) == "east"
