"""Gondola descent under parachute after cut-down.

Quasi-steady terminal velocity at every altitude:
    v_t(z) = sqrt(2 m g(z) / (rho(z) Cd A)),   A = pi D^2 / 4
Horizontal motion follows the wind (ASSUMPTION, like the balloon). The canopy
is assumed fully open at cut-down; the free-fall/inflation transient is ignored.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from .atmosphere import AtmoSample, gravity


@dataclass(frozen=True)
class ParachuteParams:
    Cd: float
    diameter: float

    @property
    def area(self) -> float:
        return math.pi * self.diameter**2 / 4.0


def terminal_velocity(p: ParachuteParams, mass: float, a: AtmoSample, z: float) -> float:
    return math.sqrt(2.0 * mass * gravity(z) / (a.rho * p.Cd * p.area))


def step(p: ParachuteParams, mass: float, z: float, east: float, north: float, atmo: Callable[[float], AtmoSample], dt: float) -> tuple[float, float, float, float]:
    """(z, east, north, vz) after dt; vz is negative (down)."""
    a = atmo(z)
    vt = terminal_velocity(p, mass, a, z)
    zm = z - 0.5 * vt * dt  # midpoint for the wind and the velocity
    am = atmo(max(zm, 0.0))
    vt_m = terminal_velocity(p, mass, am, max(zm, 0.0))
    return z - vt_m * dt, east + am.u * dt, north + am.v * dt, -vt_m


def descend(p: ParachuteParams, mass: float, z0: float, east0: float, north0: float, atmo, ground_z: float = 0.0, dt: float = 1.0):
    """Integrate to the ground. Returns (t_land, east, north, path[(t, z, east, north)])."""
    t, z, e, n = 0.0, z0, east0, north0
    path = [(t, z, e, n)]
    while z > ground_z:
        z2, e2, n2, _ = step(p, mass, z, e, n, atmo, dt)
        if z2 <= ground_z:
            f = (z - ground_z) / (z - z2)
            t, e, n, z = t + f * dt, e + f * (e2 - e), n + f * (n2 - n), ground_z
        else:
            t, z, e, n = t + dt, z2, e2, n2
        path.append((t, z, e, n))
    return t, e, n, path
