"""Deterministic physics trajectory forecast from the current fused state (ground side).

The mean path of the existing particle predictor (aerofire.ground.predictor): ascent at the
current climb rate up to the design float altitude, float holds altitude, parachute descent at
terminal velocity; horizontal drift with the forecast wind at each altitude, corrected by the measured
drift (wind-error estimate at the current altitude, decaying with height difference). Persistence is the
naive baseline (position held).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..physics import atmosphere as A
from .predictor import Predictor

HORIZONS = (30, 60, 120)
STEP_S = 5.0


class PhysicsForecaster:
    def __init__(self, cfg, forecast: A.AtmosphereProfile):
        self.cfg = cfg
        self.pred = Predictor(cfg, forecast, np.random.default_rng(0))  # tables, float altitude, terminal velocity
        self.z_float = self.pred.z_float

    def wind(self, z: float) -> tuple[float, float]:
        zc = min(max(z, self.pred._zg[0]), self.pred._zg[-1])
        return float(np.interp(zc, self.pred._zg, self.pred._u)), float(np.interp(zc, self.pred._zg, self.pred._v))

    def path(self, phase: str, z: float, vz: float, east: float, north: float, rocket_aboard: bool = True,
             horizon_s: float = 600.0, step_s: float = STEP_S, drift: tuple[float, float] | None = None) -> list[dict[str, float]]:
        """[{h, z, east, north}] every step_s up to horizon_s.

        `drift` = measured horizontal velocity (east, north) at the current altitude. Its difference from the
        forecast wind there is a wind-error estimate, applied along the path and decaying with height
        difference (scale `prediction.drift_decay_m`, an ASSUMPTION)."""
        out = []
        ground = float(self.cfg.site.elevation_m)
        z0 = z
        bu = bv = 0.0
        if drift is not None and phase in ("ASCENT", "FLOAT", "IGNITION", "DESCENT", "CUTDOWN"):
            u0, v0 = self.wind(z0)
            bu, bv = drift[0] - u0, drift[1] - v0
        decay = float(self.cfg.prediction.drift_decay_m)
        h = 0.0
        while h < horizon_s - 1e-9:
            u, v = self.wind(z)
            w = math.exp(-abs(z - z0) / decay) if decay > 0 else 0.0
            u, v = u + bu * w, v + bv * w
            if phase in ("DESCENT", "CUTDOWN"):
                mass = self.pred.m_descent_rocket if rocket_aboard else self.pred.m_descent
                z = max(z - float(self.pred._vt(np.array([z]), mass)[0]) * step_s, ground)
            elif phase == "ASCENT":
                z = min(z + max(vz, 0.0) * step_s, max(self.z_float, z))
            elif phase in ("PAD", "LANDED"):
                u = v = 0.0
            east, north = east + u * step_s, north + v * step_s
            h += step_s
            out.append({"h": h, "z": z, "east": east, "north": north})
        return out

    def at_horizons(self, path: list[dict[str, float]], horizons=HORIZONS) -> dict[int, dict[str, float]]:
        by_h = {int(round(p["h"])): p for p in path}
        return {h: by_h[h] for h in horizons if h in by_h}


def state_from_estimates(E: dict[str, Any]) -> dict[str, float] | None:
    """Fused state needed by the forecaster and the ML features; None if position is not available."""
    need = ("altitude", "east", "north")
    if any(not isinstance(E.get(k, {}).get("value"), (int, float)) for k in need):
        return None
    vz = E.get("vertical_speed", {}).get("value")
    return {"z": float(E["altitude"]["value"]), "east": float(E["east"]["value"]), "north": float(E["north"]["value"]),
            "vz": float(vz) if isinstance(vz, (int, float)) else 0.0,
            "vz_mean": float(E.get("vertical_speed_mean", {}).get("value") or (vz or 0.0)),
            "sigma_z": float(E["altitude"].get("sigma") or 0.0),
            "gs": float(E.get("ground_speed", {}).get("value") or 0.0),
            "heading": float(E.get("heading", {}).get("value") or 0.0),
            "status": E["altitude"]["status"]}


def drift_of(st: dict[str, float]) -> tuple[float, float]:
    """Measured horizontal velocity (east, north) from the fused ground speed and heading."""
    hd = math.radians(st["heading"])
    return st["gs"] * math.sin(hd), st["gs"] * math.cos(hd)
