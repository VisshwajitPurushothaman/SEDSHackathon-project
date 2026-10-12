"""Motor (propellant/casing) lumped thermal model.

    C dT/dt = -UA(rho_air) (T - T_air) - (eps A) sigma (T^4 - T_sky^4) + Q_heater
    UA(rho) = UA_sl (rho / 1.225)^n            (convection through insulation)

All parameters are ASSUMPTIONS (config `motor`). There is no temperature floor:
the old model's `max(12.0, ...)` clamp was non-physical and is gone. No solar
input (night flight, ASSUMPTION). The heater is a thermostat: on below the
setpoint, off 1 K above it (hysteresis).
"""
from __future__ import annotations

from dataclasses import dataclass

SIGMA_SB = 5.670374419e-8
RHO_SL = 1.225


@dataclass(frozen=True)
class MotorParams:
    C: float  # J/K
    UA_sl: float  # W/K
    UA_exp: float
    eps_area: float  # m^2 (emissivity x area)
    T_sky: float  # K
    heater_w: float
    setpoint_k: float
    hysteresis_k: float = 1.0

    @classmethod
    def from_config(cls, cfg, heater_failed: bool = False) -> "MotorParams":
        m = cfg.motor
        return cls(
            C=m.heat_capacity_j_per_k,
            UA_sl=m.UA_sea_level_w_per_k,
            UA_exp=m.UA_density_exponent,
            eps_area=m.emissivity_area_m2,
            T_sky=m.T_sky_k,
            heater_w=0.0 if heater_failed else m.heater_w,
            setpoint_k=m.heater_setpoint_c + 273.15,
        )


def heater_command(p: MotorParams, T: float, was_on: bool) -> bool:
    if T < p.setpoint_k:
        return True
    if T > p.setpoint_k + p.hysteresis_k:
        return False
    return was_on


def dTdt(p: MotorParams, T: float, T_air: float, rho_air: float, heater_on: bool) -> float:
    UA = p.UA_sl * (max(rho_air, 0.0) / RHO_SL) ** p.UA_exp
    q = -UA * (T - T_air) - p.eps_area * SIGMA_SB * (T**4 - p.T_sky**4) + (p.heater_w if heater_on else 0.0)
    return q / p.C


def step(p: MotorParams, T: float, T_air: float, rho_air: float, heater_on: bool, dt: float) -> float:
    """RK2 (midpoint) step."""
    k1 = dTdt(p, T, T_air, rho_air, heater_on)
    k2 = dTdt(p, T + 0.5 * dt * k1, T_air, rho_air, heater_on)
    return T + dt * k2
