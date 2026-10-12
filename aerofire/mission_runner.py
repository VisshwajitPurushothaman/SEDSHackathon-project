"""Mission orchestration: world (truth sim, sensors, recorder, link) + ground station, stepped in time.

This module is neither "ground" nor "sim": it wires them together and is the
only place that holds both. The ground station only ever receives what the
link delivers. Truth is kept for the evaluator endpoint and grading only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import time
import uuid
from dataclasses import asdict
from typing import Any

import numpy as np

from .config import Config, deep_merge, load_raw
from .evaluation.scorecard import scorecard
from .ground import postflight as PF
from .ground.recovery import RecoveryClient
from .ground.station import GroundStation
from .physics import atmosphere as A
from .physics import geo
from .sim.mission import Faults, MissionSim
from .telemetry.link import LinkEmulator, fspl_db
from .telemetry.recorder import OnboardRecorder
from .telemetry.sensors import SensorSuite

PREFLIGHT_STATES = ("NOT CHECKED", "PASS", "FAIL", "WAIVED")
CLOSURE_ITEMS = {"payload_recovered": "Payload recovered", "data_archived": "Data archived", "anomalies_filed": "Anomalies filed"}


class CommandError(Exception):
    """An operator command that is not allowed now (maps to HTTP 409)."""


class MissionRun:
    def __init__(self, cfg_raw: dict[str, Any], profile: A.AtmosphereProfile, faults: dict[str, Any] | None = None,
                 seed: int = 0, time_scale: float | None = None, name: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.name = name or f"mission-{self.id[:6]}"
        self.created_wall = time.time()
        self.cfg = Config(cfg_raw)
        self.cfg_raw = cfg_raw
        self.profile = profile
        self.faults_dict = Faults.from_dict(faults).to_dict()
        self.faults = Faults.from_dict(faults)
        self.seed = seed
        self.time_scale = self.cfg.api.default_time_scale if time_scale is None else float(time_scale)
        # Stream at most ~8 frames per wall second whatever the time scale (frames are ~10 kB of JSON).
        self.stream_period = max(self.cfg.api.stream_period_s, self.time_scale / 8.0) if self.time_scale > 0 else self.cfg.api.stream_period_s

        self.sim = MissionSim(self.cfg, profile, self.faults, seed=seed)
        self.sensors = SensorSuite(self.cfg, np.random.default_rng(seed + 1), self.faults)
        self.recorder = OnboardRecorder.from_config(self.cfg)
        self.link = LinkEmulator(self.cfg, self.faults, np.random.default_rng(seed + 2))
        self.ground = GroundStation(self.cfg, profile, reorder_delay_s=1.0)
        self.held: dict[int, dict[str, Any]] = {}  # seq -> record the ground has (live, then recovered)

        self.lock = threading.RLock()
        self.frames: list[dict[str, Any]] = []
        self.track: list[dict[str, Any]] = []  # what the operator saw live (append-only)
        self.truth_track: list[dict[str, Any]] = []
        self.event_log: list[dict[str, Any]] = []
        self.go_times: list[float] = []
        self.footprint_at_cutdown: dict[str, Any] | None = None
        self.landing_fix: dict[str, Any] | None = None
        self.landing_truth: dict[str, Any] | None = None
        self.leak_live: dict[str, Any] = {}
        self.preflight_waivers: dict[str, dict[str, Any]] = {}
        self.closure: dict[str, dict[str, Any] | None] = {k: None for k in CLOSURE_ITEMS}
        self.corrective_actions: dict[str, dict[str, Any]] = {}
        self.recovery: RecoveryClient | None = None
        self.state = "created"  # created | running | finished
        self._done_at: float | None = None
        self.end_reason: str | None = None
        self._last_frame_key: tuple | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.log("system", "mission created", {"faults": self.faults_dict, "seed": seed, "time_scale": self.time_scale,
                                               "atmosphere": profile.label, "atmo_note": profile.note})

    # ------------------------------------------------------------------ lifecycle

    def log(self, actor: str, action: str, detail: dict[str, Any] | None = None) -> None:
        self.event_log.append({"n": len(self.event_log) + 1, "wall": time.time(), "t": self.sim.t, "actor": actor,
                               "action": action, "detail": detail or {}})

    def start(self) -> None:
        self.state = "running"
        self._thread = threading.Thread(target=self._loop, name=f"mission-{self.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        t0_wall = time.time()
        while not self._stop.is_set():
            with self.lock:
                done = self.step()
            if done:
                break
            if self.time_scale > 0:
                lag = self.sim.t / self.time_scale - (time.time() - t0_wall)
                if lag > 0:
                    time.sleep(min(lag, 0.5))

    def run_to_end(self, commit_on_go: str | None = None) -> None:
        """Synchronous run (tests, fixture generation). Optionally commits as `commit_on_go` at the first GO."""
        self.state = "running"
        while True:
            with self.lock:
                if commit_on_go and self.ground.recommendation == "GO_RECOMMENDED" and self.ground.operator["state"] == "none":
                    self.commit(commit_on_go)
                if self.step():
                    break

    # ------------------------------------------------------------------ one step

    def step(self) -> bool:
        """Advance one simulation step. Returns True when the mission has finished."""
        truth = self.sim.step()
        enu = (truth.east, truth.north, truth.z)
        for sample in self.sensors.sample(truth):
            rec = self.recorder.write(sample)  # written onboard BEFORE transmission
            self.link.transmit(rec, enu)
        for rec in self.link.poll(truth.t):
            self.held[rec["seq"]] = rec
            self.ground.ingest(rec)
        fr = self.ground.frame(truth.t)
        E = fr["estimates"]
        if fr["recommendation"] == "GO_RECOMMENDED":
            self.go_times.append(truth.t)
        if self.ground.detector.detected and not self.leak_live:
            self.leak_live = {"leak_detected": True, "leak_detected_at": self.ground.detector.detected_at,
                              "d_est_um": self.ground.detector.last.d_est_um if self.ground.detector.last else None}
        if fr["vehicle_phase"] in ("DESCENT",) and self.footprint_at_cutdown is None and fr["landing_footprint"] \
                and fr["landing_footprint"]["basis"].startswith("descent"):
            self.footprint_at_cutdown = fr["landing_footprint"]
        if "lat" in E:
            self.track.append({"t": truth.t, "lat": E["lat"]["value"], "lon": E["lon"]["value"], "alt": E["altitude"]["value"],
                               "status": E["lat"]["status"]})
        self.truth_track.append({"t": truth.t, "lat": truth.lat, "lon": truth.lon, "z": truth.z, "phase": truth.phase,
                                 "rocket": truth.rocket})
        for ev in truth.events:
            self.log("vehicle", ev)
        key = (fr["phase"], fr["recommendation"], len(fr["alerts"]), fr["predicting"], fr["operator"].get("state"))
        if key != self._last_frame_key or truth.t % self.stream_period < 1e-9:
            self._push_frame(fr)
            self._last_frame_key = key
        finished = False
        if self.sim.done and self._done_at is None:
            self._done_at = truth.t
        # The landed vehicle keeps transmitting, so the link is never empty: finish after a drain margin.
        if self._done_at is not None and truth.t >= self._done_at + 10.0:
            finished, self.end_reason = True, "landed"
            if "lat" in E and E["lat"]["status"] == "FUSED":
                self.landing_fix = {"t": truth.t, "lat": E["lat"]["value"], "lon": E["lon"]["value"]}
            self.landing_truth = {"t": truth.t, "lat": truth.lat, "lon": truth.lon}
        elif self.sim.t >= self.cfg.sim.max_mission_s:
            finished, self.end_reason = True, "simulation horizon reached"
        if finished:
            self.state = "finished"
            self._push_frame(self.ground.frame(truth.t))
            self.log("system", f"mission finished: {self.end_reason}")
        return finished

    def _push_frame(self, fr: dict[str, Any]) -> None:
        f = dict(fr)
        # Slim duplicated payload: impact points are already top-level.
        if f.get("gates", {}).get("impact", {}).get("evidence", {}).get("points") is not None:
            f["gates"] = dict(f["gates"])
            imp = dict(f["gates"]["impact"])
            imp["evidence"] = {k: v for k, v in imp["evidence"].items() if k != "points"}
            f["gates"]["impact"] = imp
        f["id"] = len(self.frames) + 1
        f["mission_id"] = self.id
        f["state"] = self.state
        self.frames.append(f)

    # ------------------------------------------------------------------ operator commands

    @staticmethod
    def _operator(name: str) -> str:
        name = (name or "").strip()
        if not name or len(name) > 64:
            raise ValueError("operator name is required (1-64 characters)")
        return name

    def commit(self, operator: str) -> dict[str, Any]:
        op = self._operator(operator)
        with self.lock:
            g = self.ground
            if g.operator["state"] == "held":
                raise CommandError("operator HOLD is active: release it first")
            if g.operator["state"] in ("committed", "terminated"):
                raise CommandError(f"already {g.operator['state']}")
            if g.recommendation != "GO_RECOMMENDED":
                raise CommandError(f"no GO recommendation (current: {g.recommendation})")
            self.sim.commit()
            g.operator = {"state": "committed", "by": op, "at_t": self.sim.t, "at_wall": time.time()}
            self.log(op, "COMMIT", {"recommendation": g.recommendation, "gates": {k: v.passed for k, v in g.gates(self.sim.t, g.estimates(self.sim.t), g.fc_phase)[0].items()}})
            return dict(g.operator)

    def hold(self, operator: str, release: bool = False) -> dict[str, Any]:
        op = self._operator(operator)
        with self.lock:
            g = self.ground
            if g.operator["state"] in ("committed", "terminated"):
                raise CommandError(f"already {g.operator['state']}")
            if release:
                if g.operator["state"] != "held":
                    raise CommandError("no hold to release")
                g.operator = {"state": "none"}
                self.log(op, "HOLD RELEASED")
            else:
                g.operator = {"state": "held", "by": op, "at_t": self.sim.t, "at_wall": time.time()}
                self.log(op, "HOLD")
            return dict(g.operator)

    def terminate(self, operator: str) -> dict[str, Any]:
        op = self._operator(operator)
        with self.lock:
            g = self.ground
            if g.operator["state"] in ("committed", "terminated"):
                raise CommandError(f"already {g.operator['state']}")
            try:
                self.sim.terminate()
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            g.operator = {"state": "terminated", "by": op, "at_t": self.sim.t, "at_wall": time.time()}
            self.log(op, "TERMINATE (cut-down without launch)", {"recommendation": g.recommendation})
            return dict(g.operator)

    def ack_alert(self, alert_id: int, operator: str) -> dict[str, Any]:
        op = self._operator(operator)
        with self.lock:
            for a in self.ground.alerts:
                if a.id == alert_id:
                    if a.acknowledged_by:
                        raise CommandError("already acknowledged")
                    a.acknowledged_by, a.acknowledged_at = op, time.time()
                    self.log(op, "ALERT ACK", {"alert_id": alert_id, "title": a.title, "severity": a.severity})
                    return asdict(a)
        raise KeyError(alert_id)

    # ------------------------------------------------------------------ preflight

    def preflight(self) -> list[dict[str, Any]]:
        with self.lock:
            g, c = self.ground, self.cfg
            fr = self.frames[-1] if self.frames else None
            E = fr["estimates"] if fr else {}
            items: list[dict[str, Any]] = []

            def item(cid: str, title: str, status: str, evidence: dict[str, Any]) -> None:
                if cid in self.preflight_waivers and status != "PASS":
                    status, evidence = "WAIVED", evidence | {"waiver": self.preflight_waivers[cid]}
                items.append({"id": cid, "title": title, "status": status, "evidence": evidence})

            from .config import DEFAULT_PATH
            assumptions = sum(1 for line in DEFAULT_PATH.read_text(encoding="utf-8").splitlines() if "[ASSUMPTION" in line)
            item("atmosphere_live", "Live atmosphere profile in use", "PASS" if self.profile.is_live else "FAIL",
                 {"source": self.profile.label, "note": self.profile.note})
            item("impact_area", "Approved impact area defined (>= 3 vertices)", "PASS" if len(c.gates.approved_area) >= 3 else "FAIL",
                 {"vertices": len(c.gates.approved_area), "note": "illustrative polygon, not an official range"})
            # Link budget at the edge of the expected flight envelope (150 km slant range).
            rssi_edge = c.link.tx_power_dbm + c.link.antenna_gain_db - fspl_db(150e3, c.link.freq_mhz)
            sens = c.link.get("rx_sensitivity_dbm", -110.0)
            item("link_budget", "Downlink margin at 150 km slant range >= 6 dB", "PASS" if rssi_edge - sens >= 6.0 else "FAIL",
                 {"rssi_dbm": rssi_edge, "sensitivity_dbm": sens, "margin_db": rssi_edge - sens})
            chain_ok = None
            if self.held:
                seqs = sorted(self.held)[:50]
                from .telemetry.recorder import genesis_mac, verify_record
                prev = genesis_mac(c.telemetry.hmac_key_demo.encode(), c.telemetry.hmac_genesis)
                chain_ok = True
                for i, s in enumerate(seqs):
                    if s != i:
                        break
                    if not verify_record(c.telemetry.hmac_key_demo, prev, self.held[s]):
                        chain_ok = False
                        break
                    prev = self.held[s]["mac"]
            item("recorder_chain", "Recorder hash chain verifies from genesis", "NOT CHECKED" if chain_ok is None else ("PASS" if chain_ok else "FAIL"),
                 {"records_checked": min(50, len(self.held)), "key": "demo key, NOT secure"})
            tp = E.get("tank_pressure", {})
            if tp.get("status") == "MEASURED":
                err = abs(tp["value"] / 1e5 - c.tank.P0_bar) / c.tank.P0_bar
                item("tank_fill", "Tank pressure within 2 % of fill", "PASS" if err <= 0.02 else "FAIL", {"P_bar": tp["value"] / 1e5, "fill_bar": c.tank.P0_bar})
            else:
                item("tank_fill", "Tank pressure within 2 % of fill", "NOT CHECKED", {"reason": "no tank telemetry yet"})
            mt = E.get("motor_temperature", {})
            if mt.get("status") == "MEASURED":
                ok = mt["value"] - 273.15 >= c.motor.T_min_qualified_c
                item("motor_temp", "Motor at or above qualified minimum", "PASS" if ok else "FAIL", {"T_c": mt["value"] - 273.15, "limit_c": c.motor.T_min_qualified_c})
            else:
                item("motor_temp", "Motor at or above qualified minimum", "NOT CHECKED", {"reason": "no motor telemetry yet"})
            nav = fr["gates"]["nav"] if fr else None
            item("nav_agreement", "GNSS, NavIC and baro agree (chi-square 99.9 %)",
                 "NOT CHECKED" if not nav or nav["passed"] is None else ("PASS" if nav["passed"] else "FAIL"), nav or {})
            leak = g.detector
            item("leak_check", "No pressurant leak (first detector window)",
                 "NOT CHECKED" if leak.last is None else ("FAIL" if leak.detected else "PASS"),
                 {"windows": len(leak.history), "detected": leak.detected})
            item("truth_endpoint", "Evaluator truth endpoint disabled (operator deployment)",
                 "FAIL" if c.api.truth_enabled else "PASS", {"truth_enabled": c.api.truth_enabled, "note": "enabled for grading"})
            item("assumptions_reviewed", "Unmeasured (ASSUMPTION) parameters reviewed", "NOT CHECKED",
                 {"assumption_tags_in_config": assumptions, "note": "no hardware measurements exist; review and waive"})
            return items

    def waive(self, check_id: str, operator: str, reason: str) -> dict[str, Any]:
        op = self._operator(operator)
        if not reason.strip():
            raise ValueError("a waiver needs a reason")
        with self.lock:
            if check_id not in {i["id"] for i in self.preflight()}:
                raise KeyError(check_id)
            self.preflight_waivers[check_id] = {"by": op, "reason": reason.strip(), "wall": time.time()}
            self.log(op, "PREFLIGHT WAIVER", {"check": check_id, "reason": reason.strip()})
            return self.preflight_waivers[check_id]

    # ------------------------------------------------------------------ recovery & post-flight

    def start_recovery(self, batch_delay_s: float = 0.05) -> dict[str, Any]:
        with self.lock:
            if self.state != "finished":
                raise CommandError("recovery is available after the mission has finished (payload recovered)")
            if self.recovery and self.recovery.report.state == "running":
                raise CommandError("recovery already running")
            rng = np.random.default_rng(self.seed + 3)
            p_fail = self.cfg.recovery.batch_failure_prob
            recorder = self.recorder

            def fetch(seqs: list[int]) -> list[dict[str, Any]] | None:  # vehicle side of the recovery link
                if rng.random() < p_fail:
                    return None
                return recorder.read_many(seqs)

            self.recovery = RecoveryClient(self.cfg, self.cfg.telemetry.hmac_key_demo, self.cfg.telemetry.hmac_genesis,
                                           self.held, len(self.recorder) - 1, fetch, batch_delay_s=batch_delay_s)
            self.log("operator", "RECOVERY started", {"records_onboard": len(self.recorder), "held": len(self.held)})
        threading.Thread(target=self._run_recovery, daemon=True).start()
        return self.recovery_status()

    def _run_recovery(self) -> None:
        rep = self.recovery.run()
        with self.lock:
            self.log("system", "RECOVERY finished", {"recovered": rep.recovered, "lost": len(rep.lost), "chain_ok": rep.chain_ok})

    def recovery_status(self) -> dict[str, Any]:
        return self.recovery.report.to_dict() if self.recovery else {"state": "idle"}

    def _records(self) -> list[dict[str, Any]]:
        return [self.held[s] for s in sorted(self.held)]

    def postflight(self, with_track: bool = True) -> dict[str, Any]:
        with self.lock:
            records = self._records()
            recon = PF.reconstruct(self.cfg, self.profile, records, step_s=5.0)
            leak = PF.leak_reprocess(self.cfg, records)
            rec = self.recovery_status() if self.recovery else None
            ctx = {"recon": recon, "leak": leak, "live": self.leak_live, "recovery": rec,
                   "prediction_log": self.ground.prediction_log, "footprint_at_cutdown": self.footprint_at_cutdown,
                   "landing_fix": self.landing_fix, "go_times": self.go_times[::5], "records": records, "t_float": self._t_float_seen()}
            reqs = PF.verify_requirements(self.cfg, ctx)
            out = {
                "mission_id": self.id,
                "state": self.state,
                "recovery": rec,
                "requirements": reqs,
                "faults": self._fault_timeline(leak, records),
                "operator_track": self.track[::5] if with_track else None,
                "reconstruction": recon if with_track else None,
                "prediction_log": self.ground.prediction_log,
                "footprint_at_cutdown": self.footprint_at_cutdown,
                "landing_fix": self.landing_fix,
                "closure": {k: {"title": CLOSURE_ITEMS[k], "done": v} for k, v in self.closure.items()},
                "event_log": self.event_log,
                "data_note": "computed from records held by the ground (live + recovered); run recovery for a complete picture",
            }
            if self.cfg.api.truth_enabled:
                out["scorecard"] = scorecard(self.faults_dict, self.truth_track, self.ground.prediction_log,
                                             [asdict(a) for a in self.ground.alerts], self.footprint_at_cutdown, self.landing_truth,
                                             self.leak_live, self.cfg.site.lat_deg, self.cfg.site.lon_deg)
                out["truth_track"] = self.truth_track[::5] if with_track else None
            return out

    def _t_float_seen(self) -> float | None:
        for f in self.frames:
            if f["vehicle_phase"] == "FLOAT":
                return f["t"]
        return None

    def _fault_timeline(self, leak: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        f = self.faults_dict
        alerts = {a.key.split("@")[0]: a for a in self.ground.alerts}
        out = []

        def entry(fid: str, kind: str, injected: dict[str, Any] | None, detected_key: str | None, extra: dict[str, Any]) -> None:
            a = alerts.get(detected_key) if detected_key else None
            onset = (injected or {}).get("onset_s")
            out.append({"id": fid, "kind": kind, "injected": injected, "detected_at": a.t if a else None,
                        "detected_by": a.title if a else None,
                        "detection_delay_s": (a.t - onset) if (a and onset is not None) else None,
                        "corrective_action": self.corrective_actions.get(fid)} | extra)

        if f["tank_leak_um"]:
            entry("tank_leak", "Pressurant tank leak", {"diameter_um": f["tank_leak_um"], "onset_s": f["tank_leak_onset_s"]}, "tank_leak",
                  {"estimated_diameter_um": self.leak_live.get("d_est_um"), "hindsight_onset_t": leak.get("onset_estimate_t")})
        if f["gnss_spoof_start_s"] is not None:
            entry("gnss_spoof", "GNSS spoofing", {"onset_s": f["gnss_spoof_start_s"], "drift_m_s": f["gnss_spoof_drift_m_s"]}, "nav_integrity",
                  {"hindsight_onset_t": PF.spoof_onset(records)})
        if f["motor_heater_failed"]:
            entry("motor_heater", "Motor heater failure", {"onset_s": 0.0}, "motor_cold", {})
        for i, (a, b) in enumerate(f["blackouts"]):
            entry(f"blackout_{i + 1}", "Telemetry blackout", {"onset_s": a, "end_s": b}, "blackout", {})
        if f["balloon_leak_mm"]:
            entry("balloon_leak", "Balloon envelope leak", {"diameter_mm": f["balloon_leak_mm"], "onset_s": f["balloon_leak_onset_s"]}, None, {})
        # Detected faults with no injected counterpart.
        injected_keys = {"tank_leak", "nav_integrity", "motor_cold", "blackout"}
        for key, a in alerts.items():
            if a.severity == "WARNING" and key not in injected_keys and key not in ("abort", "impact_outside"):
                out.append({"id": key, "kind": "detected, not injected", "injected": None, "detected_at": a.t,
                            "detected_by": a.title, "detection_delay_s": None, "corrective_action": self.corrective_actions.get(key)})
        return out

    def set_corrective_action(self, fault_id: str, operator: str, text: str) -> dict[str, Any]:
        op = self._operator(operator)
        with self.lock:
            self.corrective_actions[fault_id] = {"by": op, "text": text.strip(), "wall": time.time()}
            self.log(op, "CORRECTIVE ACTION", {"fault": fault_id, "text": text.strip()})
            return self.corrective_actions[fault_id]

    def close_item(self, item: str, operator: str) -> dict[str, Any]:
        op = self._operator(operator)
        if item not in CLOSURE_ITEMS:
            raise KeyError(item)
        with self.lock:
            self.closure[item] = {"by": op, "wall": time.time()}
            self.log(op, "CLOSURE", {"item": item})
            return self.closure[item]

    # ------------------------------------------------------------------ export & truth

    def export_json(self) -> dict[str, Any]:
        report = self.postflight(with_track=True)
        report.pop("scorecard", None)
        report.pop("truth_track", None)
        canon = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
        return {"mission": self.summary(), "records": self._records(), "report": report,
                "report_sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest()}

    def export_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["seq", "event_time", "sensor_id", "source", "status", "received_live", "field", "value", "unit"])
        live = {s for s, r in self.held.items() if "rx" in r}
        for r in self._records():
            for k, v in r["values"].items():
                w.writerow([r["seq"], r["event_time"], r["sensor_id"], r["source"], r["status"], r["seq"] in live, k, v, r["units"].get(k, "")])
        return buf.getvalue()

    def truth(self) -> dict[str, Any]:
        return {"faults": self.faults_dict, "track": self.truth_track, "events": self.sim.event_log,
                "rocket": None if self.sim.rocket_result is None else {k: v for k, v in asdict(self.sim.rocket_result).items() if k != "samples"},
                "landing": self.landing_truth}

    def summary(self) -> dict[str, Any]:
        last = self.frames[-1] if self.frames else None
        return {"id": self.id, "name": self.name, "state": self.state, "end_reason": self.end_reason, "t": self.sim.t,
                "phase": last["phase"] if last else "Pad", "recommendation": last["recommendation"] if last else "NO_GO",
                "operator": dict(self.ground.operator), "faults": self.faults_dict, "time_scale": self.time_scale,
                "atmo_source": self.profile.label, "frames": len(self.frames), "created_wall": self.created_wall,
                "records_onboard": len(self.recorder), "records_held": len(self.held), "link": self.link.stats()}


def build_mission(base_raw: dict[str, Any] | None, profile: A.AtmosphereProfile, overrides: dict[str, Any] | None = None,
                  faults: dict[str, Any] | None = None, seed: int = 0, time_scale: float | None = None, name: str = "") -> MissionRun:
    raw = deep_merge(base_raw or load_raw(), overrides or {})
    return MissionRun(raw, profile, faults=faults, seed=seed, time_scale=time_scale, name=name)
