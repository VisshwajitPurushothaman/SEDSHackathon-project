"""Helium pressurant tank: real-gas state, orifice (puncture) flow, lumped thermal model.

Equation of state (virial, truncated after the second coefficient):
    Z = 1 + B2 P / (R_u T),   P = Z rho R T
    =>  P = rho R T / (1 - rho B2 / M)       (closed form for given density)
    m = P V / (Z R T)

Orifice flow through a puncture of area A (Cd from config):
    critical ratio  r* = (2/(g+1))^(g/(g-1))  ~ 0.487 for helium (g = 5/3)
    choked (p_amb/P < r*):
        mdot = Cd A P sqrt(g / (Z R T)) (2/(g+1))^((g+1)/(2(g-1)))
    subsonic otherwise:
        mdot = Cd A P sqrt(2g/((g-1) Z R T) [ r^(2/g) - r^((g+1)/g) ]),  r = p_amb / P
    (Z enters as a first-order real-gas correction to the stagnation density.)

Lumped thermal model, gas and wall at one temperature T:
    (m c_v + C_wall) dT/dt = UA(rho_air) (T_amb - T) - mdot R T
    UA(rho) = max(UA_floor, UA_sl (rho / 1.225)^n)
The -mdot R T term is the adiabatic-blowdown cooling: for C_wall = 0, UA = 0
it reduces to dT/dt = -(g - 1) T mdot / m.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

R_U = 8.314462618  # J/(mol K) [CODATA 2018]
RHO_SL = 1.225  # kg/m^3 ISA sea level


@dataclass(frozen=True)
class TankParams:
    V: float  # m^3
    R: float = 2077.1  # J/(kg K)
    gamma: float = 1.667
    B2: float = 11.8e-6  # m^3/mol; 0 -> ideal gas
    Cd: float = 0.62
    C_wall: float = 3600.0  # J/K
    UA_sl: float = 1.5  # W/K
    UA_exp: float = 0.5
    UA_floor: float = 0.05

    @property
    def molar_mass(self) -> float:
        return R_U / self.R

    @property
    def cv(self) -> float:
        return self.R / (self.gamma - 1.0)

    @classmethod
    def from_config(cls, cfg) -> "TankParams":
        t = cfg.tank
        return cls(
            V=t.V_m3,
            R=cfg.constants.R_He,
            gamma=cfg.constants.gamma_He,
            B2=cfg.constants.B2_He_m3_per_mol if t.compressibility == "virial" else 0.0,
            Cd=t.orifice_cd,
            C_wall=t.wall_heat_capacity_j_per_k,
            UA_sl=t.UA_sea_level_w_per_k,
            UA_exp=t.UA_density_exponent,
            UA_floor=t.UA_floor_w_per_k,
        )


@dataclass(frozen=True)
class TankState:
    m: float  # kg helium
    T: float  # K


def compressibility(P: float, T: float, B2: float) -> float:
    return 1.0 + B2 * P / (R_U * T)


def pressure(params: TankParams, m: float, T: float) -> float:
    rho = m / params.V
    return rho * params.R * T / (1.0 - rho * params.B2 / params.molar_mass)


def mass_from_pt(params: TankParams, P: float, T: float) -> float:
    """Gas mass from pressure and temperature (used by the ground estimator on measurements)."""
    return P * params.V / (compressibility(P, T, params.B2) * params.R * T)


def initial_state(params: TankParams, P0: float, T0: float) -> TankState:
    return TankState(mass_from_pt(params, P0, T0), T0)


def critical_pressure_ratio(gamma: float) -> float:
    return (2.0 / (gamma + 1.0)) ** (gamma / (gamma - 1.0))


def choked_flux_coeff(gamma: float) -> float:
    return (2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0)))


def is_choked(P: float, p_amb: float, gamma: float) -> bool:
    return p_amb / P < critical_pressure_ratio(gamma)


def orifice_mass_flow(params: TankParams, P: float, T: float, p_amb: float, area: float) -> float:
    """Mass flow (kg/s) out through a hole of `area` m^2; choked or subsonic as appropriate."""
    if area <= 0.0 or P <= p_amb:
        return 0.0
    g, R = params.gamma, params.R
    Z = compressibility(P, T, params.B2)
    if is_choked(P, p_amb, g):
        return params.Cd * area * P * math.sqrt(g / (Z * R * T)) * choked_flux_coeff(g)
    r = p_amb / P
    term = r ** (2.0 / g) - r ** ((g + 1.0) / g)
    return params.Cd * area * P * math.sqrt(2.0 * g / ((g - 1.0) * Z * R * T) * max(term, 0.0))


def hole_area_from_flow(params: TankParams, mdot: float, P: float, T: float) -> float:
    """Invert the choked-flow law: hole area that gives `mdot` at (P, T). Ground-side leak sizing."""
    Z = compressibility(P, T, params.B2)
    return mdot / (params.Cd * P * math.sqrt(params.gamma / (Z * params.R * T)) * choked_flux_coeff(params.gamma))


def diameter_to_area(d_m: float) -> float:
    return math.pi / 4.0 * d_m * d_m


def area_to_diameter(a_m2: float) -> float:
    return math.sqrt(4.0 * max(a_m2, 0.0) / math.pi)


def conductance(params: TankParams, rho_air: float) -> float:
    return max(params.UA_floor, params.UA_sl * (max(rho_air, 0.0) / RHO_SL) ** params.UA_exp)


def step(params: TankParams, s: TankState, T_amb: float, p_amb: float, rho_air: float, dt: float, hole_area: float = 0.0) -> tuple[TankState, float]:
    """Advance one step (explicit; thermal time constant >> dt). Returns (state, mdot out)."""
    P = pressure(params, s.m, s.T)
    mdot = min(orifice_mass_flow(params, P, s.T, p_amb, hole_area), s.m / dt)
    UA = conductance(params, rho_air)
    heat_cap = s.m * params.cv + params.C_wall
    dTdt = (UA * (T_amb - s.T) - mdot * params.R * s.T) / heat_cap
    return replace(s, m=s.m - mdot * dt, T=s.T + dTdt * dt), mdot
