"""Zero-pressure balloon: ascent, venting at full volume, float, wind drift.

Lifting gas: helium, ideal gas at ambient temperature (ASSUMPTION: no solar or
IR superheat, no gas lag).

    V_free = m_gas R_He T / p                (volume the gas wants)
    V      = min(V_free, V_max)
    If V_free > V_max the excess vents through the base ducts:
        m_gas <- p V_max / (R_He T),  vent rate = removed mass / dt

    B = rho_air V g                          (buoyancy)
    W = (m_sys + m_gas) g                    (weight, gas included)
    D = 1/2 rho_air Cd A |v| v,  A = pi r^2, r = (3V / 4 pi)^(1/3)
    (m_sys + m_gas + c_a rho_air V) dv/dt = B - W - D,   c_a = 0.5 (sphere added mass)

Optional envelope leak (fault), orifice flow at the small zero-pressure
overpressure dp:  mdot = Cd A_hole sqrt(2 rho_gas dp).

Horizontal motion: the balloon moves with the local wind (ASSUMPTION: no lag).
The ascent rate is not prescribed anywhere: it comes out of B - W - D.

Ballast: the system mass includes droppable ballast. Inertia carries the
balloon past its float altitude; it vents a little extra gas there and, with
T_gas = T_air, would then sink indefinitely (lift of a partly full balloon,
m_gas g (R_He/R_air - 1), no longer balances the load). Real flights arrest
this with ballast drops, so `step` accepts a ballast drop rate chosen by an
onboard float controller (aerofire.sim; ASSUMPTION, see config).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from .atmosphere import AtmoSample, gravity

AtmoFn = Callable[[float], AtmoSample]


@dataclass(frozen=True)
class BalloonParams:
    m_fixed: float  # kg: envelope + gondola + rocket (everything except lifting gas and ballast)
    V_max: float  # m^3
    Cd: float
    R_gas: float = 2077.1
    added_mass_coeff: float = 0.5
    leak_dp_pa: float = 5.0
    leak_cd: float = 0.6


@dataclass(frozen=True)
class BalloonState:
    z: float  # m geometric altitude
    vz: float  # m/s up
    m_gas: float  # kg helium in the envelope
    m_ballast: float = 0.0  # kg droppable ballast still aboard
    east: float = 0.0  # m from launch
    north: float = 0.0


@dataclass(frozen=True)
class BalloonOutputs:
    volume_m3: float
    radius_m: float
    vent_rate_kg_s: float
    leak_rate_kg_s: float
    buoyancy_n: float
    weight_n: float
    drag_n: float
    free_lift_n: float  # B - W
    accel_m_s2: float
    ballast_drop_kg_s: float = 0.0


def gas_volume(m_gas: float, T: float, p: float, R_gas: float) -> float:
    return m_gas * R_gas * T / p


def launch_gas_mass(params: BalloonParams, atm0: AtmoSample, free_lift_fraction: float, m_ballast: float) -> float:
    """Helium mass giving free lift = f * gross load * g at launch: (rho_air - rho_He) V = m_load (1 + f)."""
    rho_he = atm0.p / (params.R_gas * atm0.T)
    V0 = (params.m_fixed + m_ballast) * (1.0 + free_lift_fraction) / (atm0.rho - rho_he)
    if V0 > params.V_max:
        raise ValueError("launch volume exceeds V_max")
    return rho_he * V0


def float_altitude(params: BalloonParams, atmo: AtmoFn, m_ballast: float, lo: float = 0.0, hi: float = 45000.0) -> float:
    """Altitude where a full balloon is neutrally buoyant: (rho_air - rho_He) V_max = m_fixed + m_ballast."""

    def net(z: float) -> float:
        a = atmo(z)
        rho_he = a.p / (params.R_gas * a.T)
        return (a.rho - rho_he) * params.V_max - (params.m_fixed + m_ballast)

    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if net(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _forces(params: BalloonParams, z: float, vz: float, m_gas: float, m_ballast: float, atmo: AtmoFn) -> tuple[float, AtmoSample, float, float, float, float, float]:
    a = atmo(z)
    g = gravity(z)
    V = min(gas_volume(m_gas, a.T, a.p, params.R_gas), params.V_max)
    r = (3.0 * V / (4.0 * math.pi)) ** (1.0 / 3.0)
    B = a.rho * V * g
    m_load = params.m_fixed + m_ballast
    W = (m_load + m_gas) * g
    D = 0.5 * a.rho * params.Cd * math.pi * r * r * abs(vz) * vz
    m_eff = m_load + m_gas + params.added_mass_coeff * a.rho * V
    return (B - W - D) / m_eff, a, V, r, B, W, D


def step(
    params: BalloonParams,
    s: BalloonState,
    atmo: AtmoFn,
    dt: float,
    leak_area_m2: float = 0.0,
    ground_z: float = 0.0,
    ballast_drop_kg_s: float = 0.0,
) -> tuple[BalloonState, BalloonOutputs]:
    """Advance one step: RK4 on (z, vz) with gas and ballast frozen, then leak, vent and ballast updates."""

    def deriv(z: float, vz: float) -> tuple[float, float]:
        return vz, _forces(params, z, vz, s.m_gas, s.m_ballast, atmo)[0]

    k1 = deriv(s.z, s.vz)
    k2 = deriv(s.z + 0.5 * dt * k1[0], s.vz + 0.5 * dt * k1[1])
    k3 = deriv(s.z + 0.5 * dt * k2[0], s.vz + 0.5 * dt * k2[1])
    k4 = deriv(s.z + dt * k3[0], s.vz + dt * k3[1])
    z = s.z + dt / 6.0 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
    vz = s.vz + dt / 6.0 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
    if z < ground_z:  # resting on the ground (not enough lift)
        z, vz = ground_z, max(vz, 0.0)

    a = atmo(z)
    # Horizontal drift with the wind (midpoint wind).
    am = atmo(0.5 * (s.z + z))
    east, north = s.east + am.u * dt, s.north + am.v * dt

    # Envelope leak (fault): orifice flow at the zero-pressure balloon's small overpressure.
    m_gas = s.m_gas
    leak = 0.0
    if leak_area_m2 > 0.0 and m_gas > 0.0:
        rho_gas = a.p / (params.R_gas * a.T)
        leak = min(params.leak_cd * leak_area_m2 * math.sqrt(2.0 * rho_gas * params.leak_dp_pa), m_gas / dt)
        m_gas -= leak * dt

    # Venting: gas beyond the full volume leaves through the base ducts.
    vent = 0.0
    m_full = a.p * params.V_max / (params.R_gas * a.T)
    if m_gas > m_full:
        vent = (m_gas - m_full) / dt
        m_gas = m_full

    drop = min(max(ballast_drop_kg_s, 0.0), s.m_ballast / dt)
    m_ballast = s.m_ballast - drop * dt

    acc, _, V, r, B, W, D = _forces(params, z, vz, m_gas, m_ballast, atmo)
    out = BalloonOutputs(V, r, vent, leak, B, W, D, B - W, acc, drop)
    return replace(s, z=z, vz=vz, m_gas=m_gas, m_ballast=m_ballast, east=east, north=north), out
