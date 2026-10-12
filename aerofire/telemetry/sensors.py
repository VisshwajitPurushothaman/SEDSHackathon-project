"""Sensor models: the only bridge from truth (aerofire.sim) to telemetry.

Each sensor adds zero-mean Gaussian noise with sigma from config `sensors`
(all ASSUMPTIONS). Faults that corrupt a sensor (GNSS spoofing) are applied
here. Output: plain dicts (sensor_id, event_time, values, units, source); the
onboard recorder assigns sequence numbers.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..physics import geo
from .schema import SENSOR_UNITS


class SensorSuite:
    def __init__(self, cfg, rng: np.random.Generator, faults):
        self.cfg = cfg
        self.s = cfg.sensors
        self.rng = rng
        self.faults = faults
        self.lat0, self.lon0 = cfg.site.lat_deg, cfg.site.lon_deg

    def _n(self, sigma: float) -> float:
        return float(self.rng.normal(0.0, sigma))

    def _rec(self, sensor_id: str, t: float, values: dict[str, Any], source: str) -> dict[str, Any]:
        return {"sensor_id": sensor_id, "event_time": t, "values": values, "units": SENSOR_UNITS[sensor_id], "source": source}

    def sample(self, truth) -> list[dict[str, Any]]:
        """Readings for one truth frame (sensor readings at truth.t)."""
        s, t, out = self.s, truth.t, []
        # Position sensors (gondola). GNSS can be spoofed: the fix drifts away at a constant rate.
        spoof_e = spoof_n = 0.0
        f = self.faults
        if f.gnss_spoof_start_s is not None and t >= f.gnss_spoof_start_s:
            k = t - f.gnss_spoof_start_s
            spoof_e, spoof_n = f.gnss_spoof_drift_m_s[0] * k, f.gnss_spoof_drift_m_s[1] * k
        for sid, sh, sv, de, dn, src in (
            ("gnss", s.gnss_sigma_h_m, s.gnss_sigma_v_m, spoof_e, spoof_n, "gnss.l1"),
            ("navic", s.navic_sigma_h_m, s.navic_sigma_v_m, 0.0, 0.0, "navic.l5"),
        ):
            e = truth.east + de + self._n(sh)
            n = truth.north + dn + self._n(sh)
            lat, lon = geo.enu_to_latlon(e, n, self.lat0, self.lon0)
            out.append(self._rec(sid, t, {"east": e, "north": n, "up": truth.z + self._n(sv), "lat": lat, "lon": lon}, src))
        out.append(self._rec("baro", t, {"p": truth.atmo["p"] + self._n(s.baro_sigma_pa)}, "baro.static"))
        out.append(self._rec("air_temp", t, {"T": truth.atmo["T"] + self._n(s.air_temp_sigma_k)}, "air.probe"))
        sw = truth.swing
        out.append(self._rec("imu", t, {
            "tilt_e": math.degrees(sw["theta_e"]) + self._n(s.imu_tilt_sigma_deg),
            "tilt_n": math.degrees(sw["theta_n"]) + self._n(s.imu_tilt_sigma_deg),
            "rate_e": math.degrees(sw["rate_e"]) + self._n(s.imu_rate_sigma_deg_s),
            "rate_n": math.degrees(sw["rate_n"]) + self._n(s.imu_rate_sigma_deg_s),
        }, "imu.gondola"))
        if truth.rocket_phase == "ABOARD":
            out.append(self._rec("tank", t, {"P": truth.tank["P"] + self._n(s.tank_P_sigma_bar * 1e5),
                                             "T": truth.tank["T"] + self._n(s.tank_T_sigma_k)}, "tank.pt1"))
            out.append(self._rec("motor", t, {"T": truth.motor["T"] + self._n(s.motor_T_sigma_k), "heater_on": truth.motor["heater_on"]}, "motor.tc1"))
        if truth.phase in ("ASCENT", "FLOAT", "IGNITION"):
            b = truth.balloon
            out.append(self._rec("balloon", t, {
                "volume": b["volume"] * (1.0 + self._n(s.balloon_volume_sigma_frac)),
                # Flow sensors have a noise floor: report truth + noise (clipped at 0); the ground thresholds it.
                "vent_rate": max(0.0, b["vent_rate"] + self._n(s.vent_rate_sigma_kg_s)),
                "ballast_drop": b["ballast_drop"],
            }, "balloon.duct"))
        if truth.rocket is not None and truth.rocket_phase in ("BURN", "COAST"):
            r = truth.rocket
            out.append(self._rec("rocket", t, {"downrange": r["downrange"], "up": r["z"], "speed": r["speed"]}, "rocket.model"))
        out.append(self._rec("fc", t, {"phase": truth.phase, "rocket_phase": truth.rocket_phase, "event": "; ".join(truth.events)}, "fc"))
        return out
