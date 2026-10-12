"""Rocket flight after release: 2-D point mass. ILLUSTRATIVE MODEL, NOT VALIDATED.

State in the vertical plane along the rail azimuth: downrange x, altitude z, (vx, vz), mass m.
    thrust  F = mdot Isp g0,  mdot = m_prop / t_burn (constant)    while burning
    dm/dt   = -F / (Isp g0)
    drag    D = 1/2 rho |v|^2 Cd A_ref   opposite to velocity       (Cd constant: no Mach dependence)
    gravity g(z) = g0 (r0 / (r0 + z))^2, flat Earth otherwise
On the rail, thrust acts along the rail (elevation from config); after leaving
it, along the velocity vector (gravity turn). The flight is computed relative to
the air mass the gondola drifts with; the ground track then adds the wind at
the release altitude times the flight time (`impact_latlon`), i.e. the wind is
treated as uniform with height. Earth curvature and rotation are ignored. Every output of this module must be
labelled "illustrative model, not validated".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from . import geo
from .atmosphere import AtmoSample, gravity

G0 = 9.80665
LABEL = "illustrative model, not validated"


@dataclass(frozen=True)
class RocketParams:
    m_dry: float
    m_prop: float
    isp: float
    burn_time: float
    diameter: float
    Cd: float
    elevation_deg: float
    azimuth_deg: float
    rail_length: float

    @classmethod
    def from_config(cls, cfg) -> "RocketParams":
        r = cfg.rocket
        return cls(r.m_dry_kg, r.m_prop_kg, r.isp_s, r.burn_time_s, r.diameter_m, r.Cd, r.rail_elevation_deg, r.rail_azimuth_deg, r.rail_length_m)

    @property
    def area(self) -> float:
        return math.pi * self.diameter**2 / 4.0


@dataclass
class RocketResult:
    case: str
    ignited: bool
    burn_fraction: float
    apogee_m: float
    apogee_t_s: float
    range_m: float  # downrange at impact
    impact_t_s: float
    max_q_pa: float  # ascent max-Q (up to apogee)
    max_q_t_s: float
    reentry_max_q_pa: float  # largest dynamic pressure after apogee (descent to impact)
    impact_speed_m_s: float
    burnout_v_m_s: float
    burnout_z_m: float
    samples: list[tuple[float, float, float, float]] = field(default_factory=list)  # (t, x, z, speed) every ~0.5 s
    label: str = LABEL


def impact_latlon(p: "RocketParams", r: "RocketResult", lat: float, lon: float, u: float, v: float) -> tuple[float, float]:
    """Ground impact point: downrange along the rail azimuth plus uniform wind drift (u east, v north) over the flight."""
    return position_latlon(p, r.range_m, r.impact_t_s, lat, lon, u, v)


def position_latlon(p: "RocketParams", x: float, t: float, lat: float, lon: float, u: float, v: float) -> tuple[float, float]:
    az = math.radians(p.azimuth_deg)
    return geo.enu_to_latlon(x * math.sin(az) + u * t, x * math.cos(az) + v * t, lat, lon)


def simulate(
    p: RocketParams,
    z0: float,
    atmo: Callable[[float], AtmoSample],
    ignite: bool = True,
    burn_fraction: float = 1.0,
    v_h0: float = 0.0,
    dt: float = 0.05,
    t_max: float = 3600.0,
    case: str = "nominal",
) -> RocketResult:
    """Fly from release altitude z0 until impact (z = 0). burn_fraction < 1 models early burnout."""
    mdot = p.m_prop / p.burn_time if ignite else 0.0
    t_burn = p.burn_time * burn_fraction if ignite else 0.0
    thrust = mdot * p.isp * G0
    el = math.radians(p.elevation_deg)
    x, z, vx, vz, m, t = 0.0, z0, v_h0, 0.0, p.m_dry + p.m_prop, 0.0
    rail = 0.0
    apogee, apogee_t = z0, 0.0
    max_q, max_q_t = 0.0, 0.0
    reentry_q = 0.0
    past_apogee = not ignite
    bo_v = bo_z = 0.0
    samples = [(0.0, 0.0, z0, 0.0)]
    next_sample = 0.5

    def deriv(x_, z_, vx_, vz_, m_, burning, on_rail):
        a = atmo(max(z_, 0.0))
        v = math.hypot(vx_, vz_)
        if burning:
            if on_rail or v < 1e-6:
                ux, uz = math.cos(el), math.sin(el)
            else:
                ux, uz = vx_ / v, vz_ / v
            fx, fz = thrust * ux, thrust * uz
        else:
            fx = fz = 0.0
        q = 0.5 * a.rho * v * v
        dx = -q * p.Cd * p.area * (vx_ / v) if v > 1e-9 else 0.0
        dz = -q * p.Cd * p.area * (vz_ / v) if v > 1e-9 else 0.0
        ax = (fx + dx) / m_
        az = (fz + dz) / m_ - gravity(max(z_, 0.0))
        if on_rail and burning:  # constrained to the rail: keep only the along-rail component
            along = ax * math.cos(el) + az * math.sin(el)
            ax, az = along * math.cos(el), along * math.sin(el)
        return vx_, vz_, ax, az, (-mdot if burning else 0.0), q

    while t < t_max:
        burning = t < t_burn
        on_rail = rail < p.rail_length and ignite
        k1 = deriv(x, z, vx, vz, m, burning, on_rail)
        k2 = deriv(x + 0.5 * dt * k1[0], z + 0.5 * dt * k1[1], vx + 0.5 * dt * k1[2], vz + 0.5 * dt * k1[3], m + 0.5 * dt * k1[4], burning, on_rail)
        k3 = deriv(x + 0.5 * dt * k2[0], z + 0.5 * dt * k2[1], vx + 0.5 * dt * k2[2], vz + 0.5 * dt * k2[3], m + 0.5 * dt * k2[4], burning, on_rail)
        k4 = deriv(x + dt * k3[0], z + dt * k3[1], vx + dt * k3[2], vz + dt * k3[3], m + dt * k3[4], burning, on_rail)
        nx = x + dt / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        nz = z + dt / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        nvx = vx + dt / 6 * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2])
        nvz = vz + dt / 6 * (k1[3] + 2 * k2[3] + 2 * k3[3] + k4[3])
        m = m + dt / 6 * (k1[4] + 2 * k2[4] + 2 * k3[4] + k4[4])
        if on_rail:
            rail += math.hypot(nx - x, nz - z)
        if past_apogee:
            reentry_q = max(reentry_q, k1[5])
        elif k1[5] > max_q:
            max_q, max_q_t = k1[5], t
        if burning and t + dt >= t_burn:
            bo_v, bo_z = math.hypot(nvx, nvz), nz
        if nz <= 0.0:  # impact: interpolate to z = 0
            f = z / (z - nz) if z != nz else 1.0
            x = x + f * (nx - x)
            t = t + f * dt
            samples.append((t, x, 0.0, math.hypot(nvx, nvz)))
            return RocketResult(case, ignite, burn_fraction, apogee, apogee_t, x, t, max_q, max_q_t, reentry_q,
                                math.hypot(nvx, nvz), bo_v, bo_z, samples)
        if not past_apogee and nvz < 0.0 and t > 0.0:
            past_apogee = True
        x, z, vx, vz, t = nx, nz, nvx, nvz, t + dt
        if z > apogee:
            apogee, apogee_t = z, t
        if t >= next_sample:
            samples.append((t, x, z, math.hypot(vx, vz)))
            next_sample += 0.5
    raise RuntimeError("rocket did not land within t_max")
