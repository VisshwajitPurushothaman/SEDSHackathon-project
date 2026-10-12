"""Onboard float keeping: drag-matched ballast drops (the onboard flight software, modelled; used by the truth simulator and by ground predictions).

At float, a zero-pressure balloon that vented slightly too much gas on its
overshoot sinks slowly at a speed where drag balances the lift deficit:
deficit = 1/2 rho Cd A vbar^2 / g. The controller drops that much ballast
(times a margin), then waits for the response. It only acts after the balloon
has first reached full volume. Flight control, not a launch action: it never
releases, fires or ignites anything on the rocket.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .atmosphere import AtmoSample, gravity


@dataclass
class BallastController:
    V_max: float
    Cd: float
    tau_s: float
    trigger_vz: float
    margin: float
    lockout_s: float
    max_rate: float
    armed: bool = False  # becomes True once the balloon first vents (full volume)
    vbar: float = 0.0
    pending_kg: float = 0.0
    next_ok_t: float = 0.0
    drops: list[tuple[float, float]] = field(default_factory=list)  # (t, kg) commanded

    @classmethod
    def from_config(cls, bcfg) -> "BallastController":
        c = bcfg.ballast_controller
        return cls(bcfg.V_max_m3, bcfg.Cd, c.vz_filter_tau_s, c.trigger_vz_m_s, c.margin, c.lockout_s, c.max_rate_kg_s)

    def command(self, t: float, z: float, vz: float, venting: bool, atm: AtmoSample, dt: float) -> float:
        """Ballast drop rate (kg/s) for the next step."""
        self.vbar += (vz - self.vbar) * min(1.0, dt / self.tau_s)
        if venting:
            self.armed = True
        if self.armed and t >= self.next_ok_t and self.vbar < self.trigger_vz:
            r = (3.0 * self.V_max / (4.0 * math.pi)) ** (1.0 / 3.0)
            kg = self.margin * 0.5 * atm.rho * self.Cd * math.pi * r * r * self.vbar**2 / gravity(z)
            self.pending_kg += kg
            self.next_ok_t = t + self.lockout_s
            self.drops.append((t, kg))
        rate = min(self.pending_kg / dt, self.max_rate)
        self.pending_kg -= rate * dt
        return rate
