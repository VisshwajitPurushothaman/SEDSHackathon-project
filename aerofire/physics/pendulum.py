"""Gondola swing: damped planar pendulum, one per horizontal axis (east, north).

    theta'' + 2 zeta w theta' + w^2 sin(theta) = -a_s(t) cos(theta) / L,   w = sqrt(g / L)

a_s is the horizontal acceleration of the suspension point (balloon): wind
shear seen while climbing (dU/dz * v_z) plus small turbulent gusts. Small-angle
period: 2 pi sqrt(L / g).

The launch gate uses the swing AMPLITUDE, not the instantaneous angle:
    A = sqrt(theta^2 + (theta' / w)^2)
A pendulum crosses theta = 0 at its maximum angular rate, so "fire when the
angle reads zero" is exactly the wrong moment; A is constant over a cycle of a
free oscillation and reads the true swing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class PendulumParams:
    L: float
    zeta: float
    g: float = 9.80665

    @property
    def omega(self) -> float:
        return math.sqrt(self.g / self.L)

    @property
    def period(self) -> float:
        return 2.0 * math.pi * math.sqrt(self.L / self.g)


def _acc(p: PendulumParams, th: float, om: float, a_s: float) -> float:
    w = p.omega
    return -2.0 * p.zeta * w * om - w * w * math.sin(th) - a_s * math.cos(th) / p.L


def step(p: PendulumParams, theta: float, rate: float, a_s: float, dt: float, substeps: int = 10) -> tuple[float, float]:
    """RK4 with `substeps` substeps; a_s held constant over dt."""
    h = dt / substeps
    for _ in range(substeps):
        k1t, k1w = rate, _acc(p, theta, rate, a_s)
        k2t, k2w = rate + 0.5 * h * k1w, _acc(p, theta + 0.5 * h * k1t, rate + 0.5 * h * k1w, a_s)
        k3t, k3w = rate + 0.5 * h * k2w, _acc(p, theta + 0.5 * h * k2t, rate + 0.5 * h * k2w, a_s)
        k4t, k4w = rate + h * k3w, _acc(p, theta + h * k3t, rate + h * k3w, a_s)
        theta += h / 6.0 * (k1t + 2 * k2t + 2 * k3t + k4t)
        rate += h / 6.0 * (k1w + 2 * k2w + 2 * k3w + k4w)
    return theta, rate


def amplitude(p: PendulumParams, theta: float, rate: float) -> float:
    return math.hypot(theta, rate / p.omega)
