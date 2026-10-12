"""Ground station: turns received telemetry records into status-labelled estimates, gates and alerts.

Inputs: telemetry records only (TelemetryRecord-shaped dicts, optionally with
receiver metadata `rx`) and the public forecast atmosphere. It never sees the
simulator (enforced by tests/test_firewall.py).

Status rules (never show stale numbers as live):
  MEASURED      latest sensor reading, younger than `stale_after_s`
  FUSED         Kalman / regression / model combination of fresh measurements
  PREDICTED     position, altitude, vertical speed from the particle ensemble once fixes stop
  UNKNOWN       anything else whose last reading is stale (the last value and its age are still reported)
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from ..physics import atmosphere as A
from ..physics import gates as G
from ..physics import geo
from ..physics import pendulum as Pd
from ..physics import rocket as Rk
from ..physics import tank as Tk
from ..telemetry.schema import Status
from .estimator import PositionFilter
from .leak_detector import LeakDetector
from .predictor import Predictor, inside_ellipse

TIMELINE = ["Pad", "Ascent", "Float", "Gates", "Commit", "Rocket burn", "Coast", "Cut-down", "Descent", "Landed"]


def est(value: Any, unit: str, status: Status | str, sigma: float | None = None, source: str = "", age: float | None = None) -> dict[str, Any]:
    return {"value": value, "unit": unit, "status": Status(status).value, "sigma": sigma, "source": source, "age_s": age}


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
    def __init__(self, cfg, forecast: A.AtmosphereProfile, reorder_delay_s: float = 0.0):
        self.cfg = cfg
        self.forecast = forecast
        self.reorder_delay_s = reorder_delay_s
        self.stale_s = cfg.estimator.stale_after_s
        self._inbox: list[tuple[float, int, dict[str, Any]]] = []
        self._inbox_n = 0
        self.latest: dict[str, dict[str, Any]] = {}
        self.hist: dict[str, deque] = {k: deque(maxlen=120) for k in ("imu",)}
        self.detector = LeakDetector.from_config(cfg)
        self.tank_params = Tk.TankParams.from_config(cfg)
        self.rparams = Rk.RocketParams.from_config(cfg)
        self.pend = Pd.PendulumParams(cfg.pendulum.length_m, cfg.pendulum.damping_ratio)
        self.kf = PositionFilter(cfg)
        self.predictor = Predictor(cfg, forecast)
        self.predicting = False
        self.last_fix_t: float | None = None
        self.footprint: dict[str, Any] | None = None
        self._footprint_t: float | None = None
        self.prediction_log: list[dict[str, Any]] = []  # snapshots of predicted regions during blackouts (for post-flight checks)
        self.m_hat0: float | None = None
        self.impact: list[G.ImpactPoint] | None = None
        self._impact_at: tuple[float, float, float, float] | None = None
        self.all_pass_since: float | None = None
        self.recommendation = "NO_GO"
        self.max_phase_idx = 0
        self.operator: dict[str, Any] = {"state": "none"}  # none | committed | held | terminated
        self.alerts: list[Alert] = []
        self._alert_keys: set[str] = set()
        self.records_received = 0
        self.received_seqs: set[int] = set()
        self.max_seq = -1
        self.last_rx_wall: float | None = None  # arrival time of the newest packet (mission clock)
        self.latencies: deque = deque(maxlen=50)
        self.rssi: deque = deque(maxlen=20)
        self.nav_residuals: deque = deque(maxlen=int(cfg.gates.nav_window_epochs))
        self._nav_fail_since: float | None = None
        self._nav_ok_since: float | None = None
        self._go_seen = False
        self.fc_phase = "PAD"
        self.rocket_phase = "ABOARD"
        self._vz_hist: deque = deque()  # (t, fused vz) for the float test
        self._epoch_t: float | None = None
        self._epoch_fixes: dict[str, tuple[float, float, float]] = {}

    # ------------------------------------------------------------------ input

    def ingest(self, rec: dict[str, Any]) -> None:
        """Queue a received record; it is processed in event-time order after `reorder_delay_s`."""
        self._inbox_n += 1
        heapq.heappush(self._inbox, (rec["event_time"], self._inbox_n, rec))
        self.records_received += 1
        if "seq" in rec:
            self.received_seqs.add(rec["seq"])
            self.max_seq = max(self.max_seq, rec["seq"])
        rx = rec.get("rx")
        if rx:
            self.last_rx_wall = rx["arrival_time"] if self.last_rx_wall is None else max(self.last_rx_wall, rx["arrival_time"])
            self.latencies.append(rx["arrival_time"] - rec["event_time"])
            self.rssi.append(rx["rssi_dbm"])

    def _drain(self, now: float) -> None:
        while self._inbox and self._inbox[0][0] <= now - self.reorder_delay_s:
            _, _, rec = heapq.heappop(self._inbox)
            if self._epoch_t is not None and rec["event_time"] != self._epoch_t:
                self._flush_epoch()
            self._process(rec)
        self._flush_epoch()  # everything up to now - delay has been drained: the epoch is complete

    def _flush_epoch(self) -> None:
        if self._epoch_fixes:
            self.kf.update_epoch(self._epoch_t, self._epoch_fixes)
            self._on_fix(self._epoch_t)
            pending_baro = getattr(self, "_pending_baro", None)
            if pending_baro is not None:
                t, z = pending_baro
                self.kf.update("baro", t, (z,))
                self._pending_baro = None
        self._epoch_fixes = {}
        self._epoch_t = None

    def _process(self, rec: dict[str, Any]) -> None:
        sid, t, v = rec["sensor_id"], rec["event_time"], rec["values"]
        prev = self.latest.get(sid)
        if prev is None or t >= prev["event_time"]:
            self.latest[sid] = rec
        if sid in self.hist:
            self.hist[sid].append(rec)
        if sid in ("gnss", "navic"):
            self._epoch_t = t
            self._epoch_fixes[sid] = (v["east"], v["north"], v["up"])
        elif sid == "baro":
            z_baro = self.forecast.altitude_for_pressure(v["p"])
            if self._epoch_t == t:
                self._pending_baro = (t, z_baro)  # fuse after the position fixes of the same epoch
            else:
                self.kf.update("baro", t, (z_baro,))
            self._nav_residual(t)
        elif sid == "fc":
            self.fc_phase, self.rocket_phase = v.get("phase", self.fc_phase), v.get("rocket_phase", self.rocket_phase)
            self.kf.set_phase(self.fc_phase)
        elif sid == "tank":
            if self.m_hat0 is None:
                self.m_hat0 = Tk.mass_from_pt(self.tank_params, v["P"], v["T"])
            was = self.detector.detected
            self.detector.update(t, v["P"], v["T"])
            if self.detector.detected and not was:
                last = self.detector.last
                self.raise_alert(t, "WARNING", "tank_leak", "Pressurant leak detected",
                                 {"slope_kg_s": last.slope_kg_s, "t_stat": last.t_stat, "d_est_um": last.d_est_um,
                                  "d_ci95_um": last.d_ci_um, "windows": self.cfg.leak_detector.consecutive})

    def _on_fix(self, t: float) -> None:
        """A position fix arrived. If we were predicting, close the blackout and score the prediction."""
        if self.predicting and self.kf.last_update_t is not None and self.kf.last_update_t >= t - 1e-9:
            self.predictor.advance(t)
            s = self.predictor.summary()
            x = self.kf.x
            fix = (float(x[0]), float(x[1]))
            err = math.hypot(fix[0] - s["mean"]["east"], fix[1] - s["mean"]["north"]) if s else None
            inside = {lvl: inside_ellipse(self.predictor.p[:, :2], fix, lvl) for lvl in (50, 90, 99)} if s else {}
            self.prediction_log.append({"t_reconnect": t, "t_last_fix": self.last_fix_t, "blackout_s": t - (self.last_fix_t or t),
                                        "error_m": err, "inside": inside, "predicted_mean": s["mean"] if s else None,
                                        "fix_enu": fix})
            self.raise_alert(t, "INFO", f"reconnect@{t:.0f}", "Telemetry restored",
                             {"blackout_s": t - (self.last_fix_t or t), "prediction_error_m": err, "inside_regions": inside})
            self.clear_alert_key("blackout")
            self.predicting = False
        if self.kf.last_update_t is not None:
            self.last_fix_t = self.kf.last_update_t

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

    def _fresh(self, sid: str, now: float) -> bool:
        a = self._age(sid, now)
        return a is not None and a <= self.stale_s

    def _measured(self, E: dict[str, Any], key: str, sid: str, field: str, unit: str, sigma: float | None, source: str, now: float, scale: float = 1.0) -> None:
        r = self.latest.get(sid)
        if r is None:
            return
        val = r["values"][field]
        val = val * scale if isinstance(val, (int, float)) and not isinstance(val, bool) else val
        age = now - r["event_time"]
        if age <= self.stale_s:
            E[key] = est(val, unit, Status.MEASURED, sigma, source, age)
        else:
            E[key] = est(None, unit, Status.UNKNOWN, None, source, age) | {"last_value": val}

    def _position(self, now: float, E: dict[str, Any]) -> None:
        kf = self.kf
        if kf.x is None:
            return
        fix_age = now - kf.last_update_t
        if fix_age <= self.stale_s:
            if self.predicting:
                self.predicting = False
            peek = kf.peek(now)
            x, P = peek if peek else (kf.x, kf.P)
            status, src = Status.FUSED, "Kalman filter (GNSS + NavIC + baro, chi-square gated)"
            e, n, u, ve, vn, vu = (float(v) for v in x)
            sig_h = math.sqrt(max(P[0, 0] + P[1, 1], 0.0))
            sig_v = math.sqrt(max(P[2, 2], 0.0))
            sig_vz = math.sqrt(max(P[5, 5], 0.0))
        else:
            if not self.predicting:
                self.predicting = True
                self.predictor.start(kf.last_update_t, kf.x, kf.P, self.fc_phase, rocket_aboard=self.rocket_phase == "ABOARD")
                self.raise_alert(now, "CAUTION", "blackout", "Telemetry lost: position is PREDICTED",
                                 {"last_fix_t": kf.last_update_t, "predictor": self.predictor.pc.particles})
            self.predictor.advance(now)
            s = self.predictor.summary()
            if s and (not self.prediction_log or self.prediction_log[-1].get("t_snapshot", -1e9) < now - 30.0):
                self.prediction_log.append({"t_snapshot": now, "t_last_fix": self.last_fix_t, "regions": s["regions"], "mean": s["mean"]})
            status, src = Status.PREDICTED, "particle ensemble of the balloon model"
            e, n, u = s["mean"]["east"], s["mean"]["north"], s["mean"]["up"]
            ve = vn = 0.0
            vu = float(self.predictor.p[:, 3].mean())
            sig_h, sig_v, sig_vz = s["sigma_h_m"], s["sigma_v_m"], self.predictor.pc.vz_sigma_m_s
            E["prediction"] = s
            a = self.forecast.at(max(u, 0.0))
            ve, vn = a.u, a.v
        lat, lon = geo.enu_to_latlon(e, n, self.cfg.site.lat_deg, self.cfg.site.lon_deg)
        E["lat"] = est(lat, "deg", status, sig_h, src, fix_age)
        E["lon"] = est(lon, "deg", status, sig_h, src, fix_age)
        E["east"] = est(e, "m", status, sig_h, src, fix_age)
        E["north"] = est(n, "m", status, sig_h, src, fix_age)
        E["altitude"] = est(u, "m", status, sig_v, src, fix_age)
        E["vertical_speed"] = est(vu, "m/s", status, sig_vz, src, fix_age)
        E["ground_speed"] = est(math.hypot(ve, vn), "m/s", status, None, src if status != Status.PREDICTED else "forecast wind", fix_age)
        E["heading"] = est(math.degrees(math.atan2(ve, vn)) % 360.0, "deg", status, None, src if status != Status.PREDICTED else "forecast wind", fix_age)
        E["last_fix_t"] = est(self.last_fix_t, "s", Status.MEASURED, None, "newest fused position fix")
        if status == Status.FUSED:
            win = self.cfg.gates.float_vz_window_s
            self._vz_hist.append((now, vu))
            while self._vz_hist and self._vz_hist[0][0] < now - win:
                self._vz_hist.popleft()
            if self._vz_hist and now - self._vz_hist[0][0] >= 0.8 * win:
                E["vertical_speed_mean"] = est(float(np.mean([v for _, v in self._vz_hist])), "m/s", Status.FUSED, None,
                                               f"mean of fused v_z over {win:.0f} s")

    def estimates(self, now: float) -> dict[str, Any]:
        s = self.cfg.sensors
        E: dict[str, Any] = {}
        self._position(now, E)
        self._measured(E, "pressure", "baro", "p", "Pa", s.baro_sigma_pa, "baro", now)
        self._measured(E, "air_temperature", "air_temp", "T", "K", s.air_temp_sigma_k, "air probe", now)
        if E.get("pressure", {}).get("status") == "MEASURED":
            E["baro_altitude"] = est(self.forecast.altitude_for_pressure(E["pressure"]["value"]), "m", Status.FUSED,
                                     s.baro_alt_model_sigma_m, f"baro + {self.forecast.label}")
            if E.get("air_temperature", {}).get("status") == "MEASURED":
                E["air_density"] = est(E["pressure"]["value"] / (A.R_AIR * E["air_temperature"]["value"]), "kg/m^3", Status.FUSED, None, "baro + air probe")
        if "altitude" in E:
            fa = self.forecast.at(max(E["altitude"]["value"], 0.0))
            E["wind_u"] = est(fa.u, "m/s", Status.PREDICTED, None, fa.source)
            E["wind_v"] = est(fa.v, "m/s", Status.PREDICTED, None, fa.source)
            E["wind_speed"] = est(fa.wind_speed, "m/s", Status.PREDICTED, None, fa.source)
            E["wind_from"] = est(fa.wind_from_deg, "deg", Status.PREDICTED, None, fa.source)
            E["atmo_source"] = est(fa.source, "", Status.PREDICTED, None, "forecast profile at estimated altitude")
        self._measured(E, "balloon_volume", "balloon", "volume", "m^3", None, "balloon", now)
        self._measured(E, "balloon_vent_rate", "balloon", "vent_rate", "kg/s", s.vent_rate_sigma_kg_s, "duct flow", now)
        self._measured(E, "ballast_drop", "balloon", "ballast_drop", "kg/s", None, "ballast hopper", now)
        vr = E.get("balloon_vent_rate")
        if vr and vr["status"] == "MEASURED":
            # Venting is declared only above 3 sigma of the flow sensor's noise floor.
            E["balloon_venting"] = est(vr["value"] > 3.0 * s.vent_rate_sigma_kg_s, "", Status.FUSED, None, "duct flow > 3 sigma")
            vol = E.get("balloon_volume", {}).get("value")
            if vol and E.get("air_density"):
                rho_a = E["air_density"]["value"]
                rho_he = E["pressure"]["value"] / (self.cfg.constants.R_He * E["air_temperature"]["value"])
                b, r = self.cfg.balloon, self.cfg.rocket
                load = b.m_envelope_kg + b.m_gondola_kg + r.m_dry_kg + r.m_prop_kg
                E["free_lift"] = est((rho_a - rho_he) * vol * A.gravity(E["altitude"]["value"]) - load * A.gravity(E["altitude"]["value"]),
                                     "N", Status.FUSED, None, "(rho_air - rho_He) V g - load g; ballast aboard unknown (excluded)")
        self._measured(E, "tank_pressure", "tank", "P", "Pa", s.tank_P_sigma_bar * 1e5, "tank PT", now)
        self._measured(E, "tank_temperature", "tank", "T", "K", s.tank_T_sigma_k, "tank TC", now)
        if E.get("tank_pressure", {}).get("status") == "MEASURED" and E.get("tank_temperature", {}).get("status") == "MEASURED":
            m_hat = Tk.mass_from_pt(self.tank_params, E["tank_pressure"]["value"], E["tank_temperature"]["value"])
            E["tank_mass"] = est(m_hat, "kg", Status.FUSED, None, "P V / (Z R T) from measurements")
            if self.m_hat0 is not None:
                E["tank_mass_change"] = est(m_hat - self.m_hat0, "kg", Status.FUSED, None, "m_hat - m_hat(first sample)")
        last = self.detector.last
        if last:
            E["tank_leak_slope"] = est(last.slope_kg_s, "kg/s", Status.FUSED, last.se_kg_s, f"OLS over {self.detector.window_s:.0f} s", now - last.t_end)
            E["tank_leak_t_stat"] = est(last.t_stat, "", Status.FUSED, None, "slope / se", now - last.t_end)
        if "tank" in self.latest:
            E["tank_leak_detected"] = est(self.detector.detected, "", Status.FUSED, None, "leak detector")
        if self.detector.detected and last and last.d_est_um:
            E["tank_leak_diameter"] = est(last.d_est_um, "um", Status.FUSED, None, "choked-flow inversion") | {"ci95": last.d_ci_um}
        self._measured(E, "motor_temperature", "motor", "T", "K", s.motor_T_sigma_k, "motor TC", now)
        self._measured(E, "motor_heater_on", "motor", "heater_on", "", None, "heater relay", now)
        imu_hist = [r for r in self.hist["imu"] if r["event_time"] >= now - self.pend.period and now - r["event_time"] <= self.stale_s + self.pend.period]
        if imu_hist and self._fresh("imu", now):
            amps = []
            for r in imu_hist:
                v = r["values"]
                amps.append(math.hypot(math.hypot(v["tilt_e"], v["rate_e"] / self.pend.omega), math.hypot(v["tilt_n"], v["rate_n"] / self.pend.omega)))
            v = imu_hist[-1]["values"]
            E["tilt"] = est(math.hypot(v["tilt_e"], v["tilt_n"]), "deg", Status.MEASURED, s.imu_tilt_sigma_deg, "imu", self._age("imu", now))
            E["swing_amplitude"] = est(float(np.median(amps)), "deg", Status.FUSED, None, "sqrt(theta^2 + (rate/omega)^2), median over one period")
        elif "imu" in self.latest:
            E["swing_amplitude"] = est(None, "deg", Status.UNKNOWN, None, "imu stale", self._age("imu", now))
        rk = self.latest.get("rocket")
        if rk:
            E["rocket_azimuth"] = est(self.rparams.azimuth_deg, "deg", Status.PREDICTED, None, "rail azimuth (config)")
            for key, f, unit in (("rocket_downrange", "downrange", "m"), ("rocket_altitude", "up", "m"), ("rocket_speed", "speed", "m/s")):
                E[key] = est(rk["values"][f], unit, Status.PREDICTED, None, Rk.LABEL, now - rk["event_time"])
        self._link_estimates(now, E)
        return E

    def _link_estimates(self, now: float, E: dict[str, Any]) -> None:
        fresh = self.last_rx_wall is not None and now - self.last_rx_wall <= self.stale_s
        if self.rssi:
            E["link_rssi"] = est(float(np.mean(self.rssi)) if fresh else None, "dBm", Status.MEASURED if fresh else Status.UNKNOWN,
                                 None, "receiver, mean of last 20 packets")
        if self.latencies:
            latency = float(np.mean(self.latencies))
            E["link_latency"] = est(latency if fresh else None, "s", Status.MEASURED if fresh else Status.UNKNOWN,
                                     None, "arrival - event time, last 50 packets", now - self.last_rx_wall)
            if not fresh:
                E["link_latency"]["last_value"] = latency
        if self.max_seq >= 0:
            lo = max(0, self.max_seq - 999)
            window = self.max_seq - lo + 1
            got = sum(1 for s_ in range(lo, self.max_seq + 1) if s_ in self.received_seqs)
            E["link_packet_loss"] = est(1.0 - got / window, "", Status.FUSED, None, "missing seq in the last 1000 records")
        if self.last_rx_wall is not None:
            E["link_last_packet_age"] = est(now - self.last_rx_wall, "s", Status.MEASURED, None, "time since newest arrival")

    # ------------------------------------------------------------------ gates

    def _maybe_update_impact(self, now: float, E: dict[str, Any]) -> None:
        if "lat" not in E or E["lat"]["status"] != "FUSED":
            return
        e, n, u = E["east"]["value"], E["north"]["value"], E["altitude"]["value"]
        last = self._impact_at
        if last and now - last[0] < self.cfg.sim.impact_eval_period_s:
            return
        if last and math.hypot(e - last[1], n - last[2]) < 200.0 and abs(u - last[3]) < 100.0 and self.impact:
            self._impact_at = (now, last[1], last[2], last[3])
            return
        self.impact = G.impact_points(self.rparams, E["lat"]["value"], E["lon"]["value"], u, self.forecast.at,
                                      self.cfg.gates.early_burnout_fractions, self.cfg.gates.approved_area, dt=0.2)
        self._impact_at = (now, e, n, u)

    def _maybe_update_footprint(self, now: float, E: dict[str, Any]) -> None:
        if "east" not in E or self.fc_phase in ("PAD", "LANDED"):
            return
        if self._footprint_t is not None and now - self._footprint_t < 3 * self.cfg.predictor.update_period_s:
            return
        self._footprint_t = now
        fp = self.predictor.landing_footprint(E["east"]["value"], E["north"]["value"], E["altitude"]["value"],
                                              E["east"]["sigma"] or 10.0, rocket_aboard=self.rocket_phase == "ABOARD")
        fp["t"] = now
        fp["basis"] = "descent from the current position" if self.fc_phase in ("DESCENT", "CUTDOWN") else "if cut down now"
        fp.pop("points_enu", None)
        self.footprint = fp

    def gates(self, now: float, E: dict[str, Any], fc_phase: str) -> tuple[dict[str, G.GateResult], G.GateResult]:
        c = self.cfg
        if fc_phase == "FLOAT":
            self._maybe_update_impact(now, E)
        fused = E.get("altitude", {}).get("status") == "FUSED"
        z = E["altitude"]["value"] if fused else None
        vz = E["vertical_speed_mean"]["value"] if fused and "vertical_speed_mean" in E else None
        fresh_nav = self._fresh("gnss", now) and self._fresh("navic", now)
        gates = {
            "float": G.float_gate(z, vz, c.gates.release_band_m, c.gates.float_max_vz_m_s),
            "nav": G.nav_gate(list(self.nav_residuals) if fresh_nav else None, c.sensors, c.gates.chi2_2dof_999, c.gates.chi2_1dof_999),
            "motor": G.motor_gate(E["motor_temperature"]["value"] - 273.15 if E.get("motor_temperature", {}).get("status") == "MEASURED" else None,
                                  c.motor.T_min_qualified_c),
            "swing": G.swing_gate(E.get("swing_amplitude", {}).get("value"), c.gates.swing_max_amplitude_deg),
            "impact": G.impact_gate(self.impact if fc_phase == "FLOAT" and fused else None),
        }
        d = self.detector
        tank = G.tank_criterion(d.detected, d.last.d_est_um if d.last else None, d.last.slope_kg_s if d.last else None, d.last is not None)
        return gates, tank

    # ------------------------------------------------------------------ frame

    def frame(self, now: float) -> dict[str, Any]:
        self._drain(now)
        E = self.estimates(now)
        fc_phase, rocket_phase = self.fc_phase, self.rocket_phase
        gates, tank = self.gates(now, E, fc_phase)
        self._maybe_update_footprint(now, E)
        all_pass = fc_phase == "FLOAT" and all(gates[k].passed for k in G.GATE_ORDER)
        if all_pass:
            if self.all_pass_since is None:
                self.all_pass_since = now
        else:
            self.all_pass_since = None
        if fc_phase == "FLOAT":
            rec = G.recommend(gates, tank, self.all_pass_since, now, self.cfg.sim.gates_hold_s)
        else:
            rec = "ABORT_RECOMMENDED" if tank.passed is False and fc_phase in ("PAD", "ASCENT") else "NO_GO"
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
            "predicting": self.predicting,
            "last_fix_t": self.last_fix_t,
            "trust": {k: tr.to_dict() for k, tr in self.kf.trust.items()},
            "landing_footprint": self.footprint,
            "impact_points": [asdict(p) for p in self.impact] if self.impact else None,
            "alerts": [asdict(a) for a in self.alerts],
            "link": {"records_received": self.records_received, "max_seq": self.max_seq},
        }

    def _gate_alerts(self, now: float, gates: dict[str, G.GateResult], fc_phase: str) -> None:
        # Navigation integrity alert: only after a persistent failure; re-armed after a persistent recovery.
        persist = self.cfg.gates.nav_alert_persist_s
        if gates["nav"].passed is False:
            self._nav_ok_since = None
            self._nav_fail_since = self._nav_fail_since if self._nav_fail_since is not None else now
            if now - self._nav_fail_since >= persist:
                self.raise_alert(now, "WARNING", "nav_integrity", "Navigation integrity failure (GNSS/NavIC/baro disagree)",
                                 gates["nav"].evidence | {"value": gates["nav"].value, "failing_for_s": now - self._nav_fail_since,
                                                          "trust": {k: tr.state for k, tr in self.kf.trust.items()}})
        elif gates["nav"].passed:
            self._nav_fail_since = None
            self._nav_ok_since = self._nav_ok_since if self._nav_ok_since is not None else now
            if now - self._nav_ok_since >= 30.0:
                self.clear_alert_key("nav_integrity")
        for name, tr in self.kf.trust.items():
            if tr.state == "EXCLUDED":
                self.raise_alert(now, "WARNING", f"excluded_{name}", f"{name.upper()} excluded by integrity monitor",
                                 {"history": tr.history[-5:], "last_nis": self.kf.last_nis.get(name)})
            elif tr.state == "ACCEPTED" and tr.retrusted_at is not None:
                self.clear_alert_key(f"excluded_{name}")
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
