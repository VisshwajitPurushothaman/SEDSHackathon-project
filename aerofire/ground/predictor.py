"""Particle-ensemble prediction of the balloon when telemetry stops (blackout).

Initial particles: drawn from the Kalman state and covariance at the last fix.
Each particle carries a persistent forecast-wind error (sigma per component)
and a vertical-speed error (both ASSUMPTIONS from config `predictor`).
Propagation by phase (what the ground last knew):
    ASCENT  : v_z = v_z(last fix) + error, until the model float altitude
    FLOAT   : v_z = 0 (+ error decaying), held in the release band
    DESCENT : parachute terminal velocity from the descent model
    horizontal: forecast wind at the particle's altitude + its wind error
Regions: 2-D Gaussian fit of the particle cloud; ellipses at 50/90/99 %
(chi-square 2 dof: 1.386, 4.605, 9.210). Landing footprint: each footprint
particle descends under the parachute from its current state.
Every output is PREDICTED.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..physics import atmosphere as A
from ..physics import balloon as Bl
from ..physics import descent as Ds
from ..physics import geo

LEVELS = {50: 1.386294, 90: 4.605170, 99: 9.210340}


def ellipse(points: np.ndarray, lat0: float, lon0: float, level: int, n_vertices: int = 32) -> dict[str, Any]:
    """Gaussian-fit ellipse (lat/lon polygon) containing `level` % of a 2-D cloud of (east, north) points."""
    mu = points.mean(axis=0)
    cov = np.cov(points.T) + np.eye(2) * 1e-6
    vals, vecs = np.linalg.eigh(cov)
    k = math.sqrt(LEVELS[level])
    poly = []
    for i in range(n_vertices):
        a = 2 * math.pi * i / n_vertices
        p = mu + vecs @ (k * np.sqrt(np.maximum(vals, 0.0)) * np.array([math.cos(a), math.sin(a)]))
        poly.append(list(geo.enu_to_latlon(float(p[0]), float(p[1]), lat0, lon0)))
    return {"level": level, "polygon": poly, "semi_axes_m": [float(k * math.sqrt(max(v, 0.0))) for v in vals[::-1]]}


def inside_ellipse(points: np.ndarray, point: tuple[float, float], level: int) -> bool:
    mu = points.mean(axis=0)
    cov = np.cov(points.T) + np.eye(2) * 1e-6
    d = np.asarray(point) - mu
    return float(d @ np.linalg.solve(cov, d)) <= LEVELS[level]


class Predictor:
    def __init__(self, cfg, forecast: A.AtmosphereProfile, rng: np.random.Generator | None = None):
        self.cfg = cfg
        self.pc = cfg.predictor
        self.forecast = forecast
        self.rng = rng or np.random.default_rng(12345)
        self.lat0, self.lon0 = cfg.site.lat_deg, cfg.site.lon_deg
        b, r = cfg.balloon, cfg.rocket
        params = Bl.BalloonParams(b.m_envelope_kg + b.m_gondola_kg + r.m_dry_kg + r.m_prop_kg, b.V_max_m3, b.Cd, R_gas=cfg.constants.R_He)
        self.z_float = Bl.float_altitude(params, forecast.at, b.m_ballast_kg)  # ground knows the vehicle design, not its state
        self.chute = Ds.ParachuteParams(cfg.descent.parachute_cd, cfg.descent.parachute_diameter_m)
        # Forecast tabulated every 50 m so the ensemble propagates with numpy (no per-particle model calls).
        self._zg = np.arange(0.0, 45000.0 + 1.0, 50.0)
        samples = [forecast.at(float(z)) for z in self._zg]
        self._rho = np.array([a.rho for a in samples])
        self._u = np.array([a.u for a in samples])
        self._v = np.array([a.v for a in samples])
        self._g = np.array([A.gravity(float(z)) for z in self._zg])
        self.m_descent = b.m_gondola_kg + 0.75 * b.m_ballast_kg  # nominal: rocket gone, most ballast left (ASSUMPTION)
        self.m_descent_rocket = self.m_descent + r.m_dry_kg + r.m_prop_kg
        self.p: np.ndarray | None = None  # (N, 4): e, n, u, vz
        self.wind_err: np.ndarray | None = None
        self.vz_err: np.ndarray | None = None
        self.t: float | None = None
        self.phase = "ASCENT"
        self.started_at: float | None = None

    def start(self, t: float, x: np.ndarray, P: np.ndarray, phase: str, rocket_aboard: bool = True) -> None:
        n = self.pc.particles
        idx = [0, 1, 2]
        Pp = P[np.ix_(idx, idx)] + np.eye(3) * 1e-6
        pos = self.rng.multivariate_normal(x[:3], Pp, n)
        vz = np.full(n, float(x[5]))
        self.p = np.column_stack([pos, vz])
        self.wind_err = self.rng.normal(0.0, self.pc.wind_sigma_m_s, (n, 2))
        self.vz_err = self.rng.normal(0.0, self.pc.vz_sigma_m_s, n)
        self.t, self.started_at, self.phase = t, t, phase
        self.rocket_aboard = rocket_aboard

    def _tab(self, z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        zc = np.clip(z, self._zg[0], self._zg[-1])
        return (np.interp(zc, self._zg, self._rho), np.interp(zc, self._zg, self._u),
                np.interp(zc, self._zg, self._v), np.interp(zc, self._zg, self._g))

    def _vt(self, z: np.ndarray, mass: float) -> np.ndarray:
        rho, _, _, g = self._tab(z)
        return np.sqrt(2.0 * mass * g / (rho * self.chute.Cd * self.chute.area))

    def advance(self, t: float, dt_max: float = 10.0) -> None:
        if self.p is None or self.t is None:
            return
        while self.t < t - 1e-9:
            dt = min(dt_max, t - self.t)
            e, n_, u, vz = self.p.T
            _, wu, wv, _ = self._tab(u)
            if self.phase in ("DESCENT", "CUTDOWN"):
                mass = self.m_descent_rocket if self.rocket_aboard else self.m_descent
                vz = -self._vt(u, mass)
                u_new = np.maximum(u + vz * dt, float(self.cfg.site.elevation_m))
            elif self.phase == "ASCENT":
                rate = np.where(u >= self.z_float, 0.0, vz + self.vz_err)
                u_new = np.minimum(np.maximum(u + rate * dt, 0.0), self.z_float)
            else:  # FLOAT and anything before cut-down
                vz = np.zeros_like(vz)
                u_new = u
            self.p = np.column_stack([e + (wu + self.wind_err[:, 0]) * dt, n_ + (wv + self.wind_err[:, 1]) * dt, u_new, vz])
            self.t += dt

    def summary(self) -> dict[str, Any] | None:
        if self.p is None:
            return None
        en = self.p[:, :2]
        me, mn = en.mean(axis=0)
        lat, lon = geo.enu_to_latlon(float(me), float(mn), self.lat0, self.lon0)
        return {
            "t": self.t,
            "since_last_fix_s": None if self.started_at is None else self.t - self.started_at,
            "mean": {"east": float(me), "north": float(mn), "up": float(self.p[:, 2].mean()), "lat": lat, "lon": lon},
            "sigma_h_m": float(math.sqrt(np.trace(np.cov(en.T)))),
            "sigma_v_m": float(self.p[:, 2].std()),
            "regions": [ellipse(en, self.lat0, self.lon0, lvl) for lvl in (50, 90, 99)],
            "status": "PREDICTED",
            "model": "particle ensemble of the balloon model (forecast wind + persistent errors)",
        }

    def landing_footprint(self, east: float, north: float, up: float, sigma_h: float, rocket_aboard: bool, n: int | None = None) -> dict[str, Any]:
        """Footprint if the gondola descends from (east, north, up) now. Uses per-particle wind errors."""
        n = n or self.pc.footprint_particles
        dt = self.pc.footprint_dt_s
        mass = self.m_descent_rocket if rocket_aboard else self.m_descent
        ground = float(self.cfg.site.elevation_m)
        werr = self.rng.normal(0.0, self.pc.wind_sigma_m_s, (n, 2))
        e = east + self.rng.normal(0.0, sigma_h / math.sqrt(2), n)
        nn = north + self.rng.normal(0.0, sigma_h / math.sqrt(2), n)
        z = np.full(n, float(up))
        for _ in range(100000):
            alive = z > ground
            if not alive.any():
                break
            _, wu, wv, _ = self._tab(z)
            vt = self._vt(z, mass)
            step = np.where(alive, np.minimum(dt, (z - ground) / vt), 0.0)
            e = e + (wu + werr[:, 0]) * step
            nn = nn + (wv + werr[:, 1]) * step
            z = z - vt * step
        arr = np.column_stack([e, nn])
        me, mn = arr.mean(axis=0)
        lat, lon = geo.enu_to_latlon(float(me), float(mn), self.lat0, self.lon0)
        return {"mean": {"lat": lat, "lon": lon, "east": float(me), "north": float(mn)},
                "regions": [ellipse(arr, self.lat0, self.lon0, lvl) for lvl in (50, 90, 99)],
                "points_enu": arr.tolist(), "status": "PREDICTED", "rocket_aboard": rocket_aboard}
