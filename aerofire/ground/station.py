"""Ground station: turns received telemetry records into status-labelled estimates, gates and alerts.

Inputs: telemetry records only (dicts in the TelemetryRecord shape) and the
public forecast atmosphere. It never sees the simulator (enforced by
tests/test_firewall.py).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..physics import atmosphere as A
from ..physics import gates as G
from ..physics import geo
from ..physics import pendulum as Pd
from ..physics import rocket as Rk
from ..physics import tank as Tk
from ..telemetry.schema import Status
from .leak_detector import LeakDetector

TIMELINE = ["Pad", "Ascent", "Float", "Gates", "Commit", "Rocket burn", "Coast", "Cut-down", "Descent", "Landed"]


def est(value: Any, unit: str, status: Status | str, sigma: float | None = None, source: str = "", age: float | None = None) -> dict[str, Any]:
    return {"value": value, "unit": unit, "status": Status(status).value, "sigma": sigma, "source": source, "age_s": age}


def _slope(ts: list[float], ys: list[float]) -> tuple[float, float] | None:
    if len(ts) < 5:
        return None
    t = np.asarray(ts)
    y = np.asarray(ys)
    tc = t - t.mean()
    sxx = float(tc @ tc)
    if sxx <= 0:
        return None
    b = float(tc @ (y - y.mean()) / sxx)
    r = y - y.mean() - b * tc
    se = math.sqrt(float(r @ r) / (len(t) - 2) / sxx)
    return b, se


@dataclass
class Alert:
    id: int
    t: float
    severity: str  # INFO | CAUTION | WARNING
    key: str
    title: str
    evidence: dict[str, Any]
    acknowledged_by: str | None = None
    acknowledged_at: float | None = None  # wall clock (epoch s)


class GroundStation:
    def __init__(self, cfg, forecast: A.AtmosphereProfile):
        self.cfg = cfg
        self.forecast = forecast
        self.latest: dict[str, dict[str, Any]] = {}
        self.hist: dict[str, deque] = {k: deque(maxlen=120) for k in ("gnss", "imu", "balloon")}
        self.detector = LeakDetector.from_config(cfg)
        self.tank_params = Tk.TankParams.from_config(cfg)
        self.rparams = Rk.RocketParams.from_config(cfg)
        self.pend = Pd.PendulumParams(cfg.pendulum.length_m, cfg.pendulum.damping_ratio)
        self.m_hat0: float | None = None
        self.impact: list[G.ImpactPoint] | None = None
        self._impact_at: tuple[float, float, float, float] | None = None  # t, east, north, z of last evaluation
        self.all_pass_since: float | None = None
        self.recommendation = "NO_GO"
        self.max_phase_idx = 0
        self.operator: dict[str, Any] = {"state": "none"}  # none | committed | held | terminated
        self.alerts: list[Alert] = []
        self._alert_keys: set[str] = set()
        self.records_received = 0
        self.last_record_time: float | None = None
        self.nav_residuals: deque = deque(maxlen=int(cfg.gates.nav_window_epochs))
        self._nav_fail_since: float | None = None
        self._nav_ok_since: float | None = None
        self._go_seen = False

    # ------------------------------------------------------------------ input

    def ingest(self, rec: dict[str, Any]) -> None:
        sid = rec["sensor_id"]
        self.records_received += 1
        self.last_record_time = rec["event_time"] if self.last_record_time is None else max(self.last_record_time, rec["event_time"])
        self.latest[sid] = rec
        if sid in self.hist:
            self.hist[sid].append(rec)
        if sid == "baro":
            self._nav_residual(rec["event_time"])
        if sid == "tank":
            v = rec["values"]
            if self.m_hat0 is None:
                self.m_hat0 = Tk.mass_from_pt(self.tank_params, v["P"], v["T"])
            prev = self.detector.detected
            self.detector.update(rec["event_time"], v["P"], v["T"])
            if self.detector.detected and not prev:
                last = self.detector.last
                self.raise_alert(rec["event_time"], "WARNING", "tank_leak", "Pressurant leak detected",
                                 {"slope_kg_s": last.slope_kg_s, "t_stat": last.t_stat, "d_est_um": last.d_est_um,
                                  "d_ci95_um": last.d_ci_um, "windows": self.cfg.leak_detector.consecutive})

    def _nav_residual(self, t: float) -> None:
        """Per-epoch residuals once GNSS, NavIC and baro of the same epoch are all in."""
        g, n, b = self.latest.get("gnss"), self.latest.get("navic"), self.latest.get("baro")
        if not (g and n and b) or not (g["event_time"] == n["event_time"] == b["event_time"] == t):
            return
        gv, nv = g["values"], n["values"]
        self.nav_residuals.append((gv["east"] - nv["east"], gv["north"] - nv["north"],
                                   gv["up"] - self.forecast.altitude_for_pressure(b["values"]["p"])))

    def raise_alert(self, t: float, severity: str, key: str, title: str, evidence: dict[str, Any]) -> None:
        if key in self._alert_keys:
            return
        self._alert_keys.add(key)
        self.alerts.append(Alert(len(self.alerts) + 1, t, severity, key, title, evidence))

    def clear_alert_key(self, key: str) -> None:
        """Allow an alert to fire again on its next onset (edge-triggered alerts)."""
        self._alert_keys.discard(key)

    # ------------------------------------------------------------------ estimates

    def _age(self, sid: str, now: float) -> float | None:
        r = self.latest.get(sid)
        return None if r is None else now - r["event_time"]

    def estimates(self, now: float) -> dict[str, Any]:
        s = self.cfg.sensors
        E: dict[str, Any] = {}
        g = self.latest.get("gnss")
        if g:
            v = g["values"]
            E["lat"] = est(v["lat"], "deg", Status.MEASURED, None, "gnss", self._age("gnss", now))
            E["lon"] = est(v["lon"], "deg", Status.MEASURED, None, "gnss", self._age("gnss", now))
            E["altitude"] = est(v["up"], "m", Status.MEASURED, s.gnss_sigma_v_m, "gnss", self._age("gnss", now))
            hist = list(self.hist["gnss"])
            recent = [r for r in hist if r["event_time"] >= now - 20.0]
            fit = _slope([r["event_time"] for r in recent], [r["values"]["up"] for r in recent])
            if fit:
                E["vertical_speed"] = est(fit[0], "m/s", Status.FUSED, fit[1], "gnss (20 s regression)", self._age("gnss", now))
            longer = [r for r in hist if r["event_time"] >= now - 60.0]
            fe = _slope([r["event_time"] for r in longer], [r["values"]["east"] for r in longer])
            fn = _slope([r["event_time"] for r in longer], [r["values"]["north"] for r in longer])
            if fe and fn:
                gs = math.hypot(fe[0], fn[0])
                E["ground_speed"] = est(gs, "m/s", Status.FUSED, math.hypot(fe[1], fn[1]), "gnss (60 s regression)")
                E["heading"] = est(math.degrees(math.atan2(fe[0], fn[0])) % 360.0, "deg", Status.FUSED, None, "gnss (60 s regression)")
        baro = self.latest.get("baro")
        air = self.latest.get("air_temp")
        if baro:
            E["pressure"] = est(baro["values"]["p"], "Pa", Status.MEASURED, s.baro_sigma_pa, "baro", self._age("baro", now))
            E["baro_altitude"] = est(self.forecast.altitude_for_pressure(baro["values"]["p"]), "m", Status.FUSED,
                                     s.baro_alt_model_sigma_m, f"baro + {self.forecast.label}")
        if air:
            E["air_temperature"] = est(air["values"]["T"], "K", Status.MEASURED, s.air_temp_sigma_k, "air probe", self._age("air_temp", now))
        if baro and air:
            E["air_density"] = est(baro["values"]["p"] / (A.R_AIR * air["values"]["T"]), "kg/m^3", Status.FUSED, None, "baro + air probe")
        if "altitude" in E:
            fa = self.forecast.at(E["altitude"]["value"])
            E["wind_u"] = est(fa.u, "m/s", Status.PREDICTED, None, fa.source)
            E["wind_v"] = est(fa.v, "m/s", Status.PREDICTED, None, fa.source)
            E["wind_speed"] = est(fa.wind_speed, "m/s", Status.PREDICTED, None, fa.source)
            E["wind_from"] = est(fa.wind_from_deg, "deg", Status.PREDICTED, None, fa.source)
            E["atmo_source"] = est(fa.source, "", Status.PREDICTED, None, "forecast profile at estimated altitude")
        bal = self.latest.get("balloon")
        if bal:
            bv = bal["values"]
            E["balloon_volume"] = est(bv["volume"], "m^3", Status.MEASURED, s.balloon_volume_sigma_frac * bv["volume"], "balloon", self._age("balloon", now))
            E["balloon_vent_rate"] = est(bv["vent_rate"], "kg/s", Status.MEASURED, s.vent_rate_sigma_kg_s, "duct flow", self._age("balloon", now))
            # Venting declared only above 3 sigma of the flow sensor noise floor.
            E["balloon_venting"] = est(bv["vent_rate"] > 3.0 * s.vent_rate_sigma_kg_s, "", Status.FUSED, None, "duct flow > 3 sigma")
            E["ballast_drop"] = est(bv["ballast_drop"], "kg/s", Status.MEASURED, None, "ballast hopper")
        tank = self.latest.get("tank")
        if tank:
            tv = tank["values"]
            m_hat = Tk.mass_from_pt(self.tank_params, tv["P"], tv["T"])
            E["tank_pressure"] = est(tv["P"], "Pa", Status.MEASURED, s.tank_P_sigma_bar * 1e5, "tank PT", self._age("tank", now))
            E["tank_temperature"] = est(tv["T"], "K", Status.MEASURED, s.tank_T_sigma_k, "tank TC", self._age("tank", now))
            E["tank_mass"] = est(m_hat, "kg", Status.FUSED, None, "P V / (Z R T) from measurements")
            if self.m_hat0 is not None:
                E["tank_mass_change"] = est(m_hat - self.m_hat0, "kg", Status.FUSED, None, "m_hat - m_hat(first sample)")
            last = self.detector.last
            if last:
                E["tank_leak_slope"] = est(last.slope_kg_s, "kg/s", Status.FUSED, last.se_kg_s, f"OLS over {self.detector.window_s:.0f} s")
                E["tank_leak_t_stat"] = est(last.t_stat, "", Status.FUSED, None, "slope / se")
            E["tank_leak_detected"] = est(self.detector.detected, "", Status.FUSED, None, "leak detector")
            if self.detector.detected and self.detector.last and self.detector.last.d_est_um:
                E["tank_leak_diameter"] = est(self.detector.last.d_est_um, "um", Status.FUSED, None, "choked-flow inversion",
                                              None)
                E["tank_leak_diameter"]["ci95"] = self.detector.last.d_ci_um
        mot = self.latest.get("motor")
        if mot:
            E["motor_temperature"] = est(mot["values"]["T"], "K", Status.MEASURED, s.motor_T_sigma_k, "motor TC", self._age("motor", now))
            E["motor_heater_on"] = est(mot["values"]["heater_on"], "", Status.MEASURED, None, "heater relay")
        imu_hist = [r for r in self.hist["imu"] if r["event_time"] >= now - self.pend.period]
        if imu_hist:
            amps = []  # degrees: sqrt(theta^2 + (rate / omega)^2) with theta in deg, rate in deg/s
            for r in imu_hist:
                v = r["values"]
                ae = math.hypot(v["tilt_e"], v["rate_e"] / self.pend.omega)
                an = math.hypot(v["tilt_n"], v["rate_n"] / self.pend.omega)
                amps.append(math.hypot(ae, an))
            v = imu_hist[-1]["values"]
            E["tilt"] = est(math.hypot(v["tilt_e"], v["tilt_n"]), "deg", Status.MEASURED, s.imu_tilt_sigma_deg, "imu", self._age("imu", now))
            E["swing_amplitude"] = est(float(np.median(amps)), "deg", Status.FUSED, None, "sqrt(theta^2 + (rate/omega)^2), median over one period")
        rk = self.latest.get("rocket")
        if rk:
            E["rocket_downrange"] = est(rk["values"]["downrange"], "m", Status.PREDICTED, None, Rk.LABEL)
            E["rocket_altitude"] = est(rk["values"]["up"], "m", Status.PREDICTED, None, Rk.LABEL)
            E["rocket_speed"] = est(rk["values"]["speed"], "m/s", Status.PREDICTED, None, Rk.LABEL)
        return E

    # ------------------------------------------------------------------ gates

    def _maybe_update_impact(self, now: float, E: dict[str, Any]) -> None:
        g = self.latest.get("gnss")
        if not g or "altitude" not in E:
            return
        v = g["values"]
        key = (now, v["east"], v["north"], v["up"])
        last = self._impact_at
        if last and now - last[0] < self.cfg.sim.impact_eval_period_s:
            return
        if last and math.hypot(v["east"] - last[1], v["north"] - last[2]) < 200.0 and abs(v["up"] - last[3]) < 100.0 and self.impact:
            self._impact_at = (now, last[1], last[2], last[3])
            return
        self.impact = G.impact_points(self.rparams, v["lat"], v["lon"], v["up"], self.forecast.at,
                                      self.cfg.gates.early_burnout_fractions, self.cfg.gates.approved_area, dt=0.2)
        self._impact_at = key

    def gates(self, now: float, E: dict[str, Any], fc_phase: str) -> tuple[dict[str, G.GateResult], G.GateResult]:
        c = self.cfg
        if fc_phase == "FLOAT":
            self._maybe_update_impact(now, E)
        z = E.get("altitude", {}).get("value")
        vz = E.get("vertical_speed", {}).get("value")
        gn, nv = self.latest.get("gnss"), self.latest.get("navic")
        gates = {
            "float": G.float_gate(z, vz, c.gates.release_band_m, c.gates.float_max_vz_m_s),
            "nav": G.nav_gate(list(self.nav_residuals) if gn and nv else None, c.sensors, c.gates.chi2_2dof_999, c.gates.chi2_1dof_999),
            "motor": G.motor_gate(E["motor_temperature"]["value"] - 273.15 if "motor_temperature" in E else None, c.motor.T_min_qualified_c),
            "swing": G.swing_gate(E.get("swing_amplitude", {}).get("value"), c.gates.swing_max_amplitude_deg),
            "impact": G.impact_gate(self.impact if fc_phase == "FLOAT" else None),
        }
        d = self.detector
        tank = G.tank_criterion(d.detected, d.last.d_est_um if d.last else None, d.last.slope_kg_s if d.last else None, d.last is not None)
        return gates, tank

    # ------------------------------------------------------------------ frame

    def frame(self, now: float) -> dict[str, Any]:
        E = self.estimates(now)
        fc = self.latest.get("fc", {}).get("values", {})
        fc_phase, rocket_phase = fc.get("phase", "PAD"), fc.get("rocket_phase", "ABOARD")
        gates, tank = self.gates(now, E, fc_phase)
        all_pass = fc_phase == "FLOAT" and all(gates[k].passed for k in G.GATE_ORDER)
        if all_pass:
            if self.all_pass_since is None:
                self.all_pass_since = now
        else:
            self.all_pass_since = None
        rec = G.recommend(gates, tank, self.all_pass_since, now, self.cfg.sim.gates_hold_s) if fc_phase == "FLOAT" else (
            "ABORT_RECOMMENDED" if tank.passed is False and fc_phase in ("PAD", "ASCENT") else "NO_GO")
        if rec != self.recommendation:
            if rec == "GO_RECOMMENDED" and not self._go_seen:
                self._go_seen = True
                self.raise_alert(now, "INFO", "go", "GO recommended: operator must commit", {"gates": {k: g.passed for k, g in gates.items()}})
            elif self.recommendation == "GO_RECOMMENDED" and rec == "NO_GO" and self.operator.get("state") == "none":
                failing = [k for k in G.GATE_ORDER if not gates[k].passed]
                self.raise_alert(now, "CAUTION", "go_withdrawn", "GO withdrawn: a gate stopped passing", {"failing": failing})
            elif rec == "ABORT_RECOMMENDED":
                self.raise_alert(now, "WARNING", "abort", "ABORT recommended: do not launch", {"reason": tank.detail})
        self.recommendation = rec
        self._gate_alerts(now, gates, fc_phase)

        phase = self._timeline_phase(fc_phase, rocket_phase, gates)
        return {
            "t": now,
            "phase": phase,
            "phase_index": TIMELINE.index(phase),
            "vehicle_phase": fc_phase,
            "rocket_phase": rocket_phase,
            "estimates": E,
            "gates": {k: g.to_dict() for k, g in gates.items()},
            "tank_criterion": tank.to_dict(),
            "recommendation": rec,
            "operator": dict(self.operator),
            "atmo_source": E.get("atmo_source", {}).get("value", self.forecast.label),
            "atmo_note": self.forecast.note,
            "alerts": [asdict(a) for a in self.alerts],
            "link": {"last_record_age_s": None if self.last_record_time is None else now - self.last_record_time,
                     "records_received": self.records_received},
        }

    def _gate_alerts(self, now: float, gates: dict[str, G.GateResult], fc_phase: str) -> None:
        # Navigation integrity alert: only after a persistent failure; re-armed after a persistent recovery.
        persist = self.cfg.gates.nav_alert_persist_s
        if gates["nav"].passed is False:
            self._nav_ok_since = None
            self._nav_fail_since = self._nav_fail_since if self._nav_fail_since is not None else now
            if now - self._nav_fail_since >= persist:
                self.raise_alert(now, "WARNING", "nav_integrity", "Navigation integrity failure (GNSS/NavIC/baro disagree)",
                                 gates["nav"].evidence | {"value": gates["nav"].value, "failing_for_s": now - self._nav_fail_since})
        elif gates["nav"].passed:
            self._nav_fail_since = None
            self._nav_ok_since = self._nav_ok_since if self._nav_ok_since is not None else now
            if now - self._nav_ok_since >= 30.0:
                self.clear_alert_key("nav_integrity")
        if fc_phase == "FLOAT" and gates["motor"].passed is False:
            self.raise_alert(now, "CAUTION", "motor_cold", "Motor below qualified temperature", {"T_c": gates["motor"].value, "limit_c": gates["motor"].limit})
        if fc_phase == "FLOAT" and gates["impact"].passed is False:
            self.raise_alert(now, "WARNING", "impact_outside", "Predicted impact outside approved area", gates["impact"].evidence)
        if not self.forecast.is_live:
            self.raise_alert(now, "CAUTION", "atmo_fallback", "Atmosphere: ISA fallback in use (no live data)", {"note": self.forecast.note})
        if fc_phase == "FLOAT":
            self.raise_alert(now, "INFO", "float", "Balloon at float (full volume, venting)", {})

    def _timeline_phase(self, fc_phase: str, rocket_phase: str, gates: dict[str, G.GateResult]) -> str:
        if fc_phase == "LANDED":
            p = "Landed"
        elif fc_phase == "DESCENT":
            p = "Descent"
        elif fc_phase == "CUTDOWN":
            p = "Cut-down"
        elif rocket_phase == "COAST":
            p = "Coast"
        elif rocket_phase == "BURN":
            p = "Rocket burn"
        elif self.operator.get("state") == "committed" or fc_phase == "IGNITION":
            p = "Commit"
        elif fc_phase == "FLOAT":
            p = "Gates" if gates["float"].passed else "Float"
        elif fc_phase == "ASCENT":
            p = "Ascent"
        else:
            p = "Pad"
        # The timeline never goes backwards (the rocket can still be coasting during cut-down/descent).
        idx = max(self.max_phase_idx, TIMELINE.index(p))
        self.max_phase_idx = idx
        return TIMELINE[idx]
