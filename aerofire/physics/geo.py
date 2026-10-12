"""Local-tangent-plane geodesy on the WGS84 ellipsoid.

East/north offsets (m) from a reference point are converted with the WGS84
meridional (M) and prime-vertical (N) radii of curvature at the reference
latitude:

    dlat = north / M,   dlon = east / (N cos lat0)

This flat-earth approximation is accurate to well under 0.1 % of the offset
for the distances here (< 200 km).
"""
from __future__ import annotations

import math
from typing import Sequence

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def radii(lat_deg: float) -> tuple[float, float]:
    """(M meridional, N prime vertical) radii of curvature in metres."""
    s = math.sin(math.radians(lat_deg))
    w = math.sqrt(1.0 - WGS84_E2 * s * s)
    return WGS84_A * (1.0 - WGS84_E2) / w**3, WGS84_A / w


def enu_to_latlon(east: float, north: float, lat0: float, lon0: float) -> tuple[float, float]:
    M, N = radii(lat0)
    return lat0 + math.degrees(north / M), lon0 + math.degrees(east / (N * math.cos(math.radians(lat0))))


def latlon_to_enu(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    M, N = radii(lat0)
    return math.radians(lon - lon0) * N * math.cos(math.radians(lat0)), math.radians(lat - lat0) * M


def offset(lat: float, lon: float, azimuth_deg: float, distance_m: float) -> tuple[float, float]:
    """Point at `distance_m` along `azimuth_deg` (clockwise from north)."""
    a = math.radians(azimuth_deg)
    return enu_to_latlon(distance_m * math.sin(a), distance_m * math.cos(a), lat, lon)


def point_in_polygon(lat: float, lon: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Ray casting on (lat, lon) vertices. Fine for small, non-polar polygons."""
    inside = False
    n = len(polygon)
    for i in range(n):
        y1, x1 = polygon[i]
        y2, x2 = polygon[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside
