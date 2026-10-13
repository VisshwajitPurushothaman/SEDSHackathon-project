"""One received mission on the ground station (Laptop B).

Frames arrive over UDP (or from a recorded session file in PLAYBACK). This module
verifies each frame's HMAC, tracks sequence gaps, duplicates and reordering,
measures the link (rate, loss, latency, AOS/LOS), records every frame to
`data/sessions/<mission_id>.jsonl`, evaluates the channel dictionary limits, and
feeds the existing ground estimator (aerofire.ground.station). It never sees truth.

All times are passed in by the caller (`recv_utc`, `now_utc`) so playback is
deterministic.
"""
from __future__ import annotations

import json
import threading
import statistics
from collections import deque
from pathlib import Path
from typing import Any

from ..ground import profile as PF
from ..ground.forecast import HORIZONS, state_from_estimates
from ..ml import features as MLF
from ..physics import geo
from ..ground.station import GroundStation
from ..physics import atmosphere as A
from ..telemetry import icd
from .preflight import PadEvidence

LOS_AFTER_S = 3.0  # silence before declaring loss of signal
STALE_AFTER_S = 3.0  # channel value older than this (wall s) is STALE
RATE_WINDOW_S = 10.0
HISTORY_KEYS = ("GNSS_ALT", "GNSS_LAT", "GNSS_LON", "GNSS_VU", "TANK_P", "TANK_T", "MOTOR_T", "IMU_TILT", "BARO_P", "AIR_T", "RSSI", "BATT_V",
                "POS_DOWNRANGE", "POS_ALT_PAD", "POS_CROSSRANGE", "IMU_ROLL", "IMU_PITCH", "IMU_YAW")


class GroundSession:
    def __init__(self, cfg, forecast: A.AtmosphereProfile, mission_id: str, source: str = "LIVE UDP",
                 sessions_dir: Path | None = None, record: bool = True, preflight_ctx: dict[str, Any] | None = None,
                 lock: Any = None):
        self.lock = lock or threading.RLock()  # shared with the receiver (recovery runs in its own thread)
        self.cfg, self.mission_id, self.source, self.forecast = cfg, mission_id, source, forecast
        self.preflight_ctx = preflight_ctx or {}
        self.pad = PadEvidence()
        self.waivers: dict[str, dict[str, Any]] = {}
        self._off_since: dict[str, float] = {}
        self._off_flagged: set[str] = set()
        self._range_keys: set[str] = set()
        self._range_src: tuple | None = None
        self.forecaster = self.preflight_ctx.get("forecaster")
        self.hybrid = self.preflight_ctx.get("hybrid")
        self.traj_forecast: dict[str, Any] | None = None  # (self.forecast is the forecast atmosphere)
        self.forecast_log: list[dict[str, Any]] = []
        self._forecast_met = -1e9
        # Evidence kept for post-flight (ground-side only).
        self.prediction_log: list[dict[str, Any]] = []
        self.footprint_at_cutdown: dict[str, Any] | None = None
        self.landing_fix: dict[str, Any] | None = None
        self.go_times: list[float] = []
        self.t_float: float | None = None
        self.sender_addr: str | None = None
        self.recovered_seqs: set[int] = set()
        self.recovery: Any = None
        self.truth: dict[str, Any] | None = None
        self.closure: dict[str, dict[str, Any] | None] = {"payload_recovered": None, "data_archived": None, "anomalies_filed": None}
        self._pf_cache: tuple | None = None
        self.key, self.genesis = cfg.telemetry.hmac_key_demo, cfg.telemetry.hmac_genesis
        self.dictionary = icd.load_channels()
        self.ground = GroundStation(cfg, forecast, reorder_delay_s=1.0)
        self.lat0, self.lon0 = cfg.site.lat_deg, cfg.site.lon_deg
        self.frames: dict[int, dict[str, Any]] = {}  # seq -> frame held (live; recovered frames are added post-flight)
        self.max_seq = -1
        self.duplicates = self.out_of_order = 0
        self.unsigned = 0
        self.min_seq: int | None = None
        self.hmac_failures: list[int] = []
        self.chain_breaks: list[int] = []
        self.arrivals: deque[float] = deque()
        self.deltas: deque[float] = deque(maxlen=200)  # recv - sent, wall s
        self.first_rx_utc: float | None = None
        self.last_rx_utc: float | None = None
        self.last_met = 0.0
        self.time_scale = 1.0
        self.ingested_met = -1.0
        self.los_periods: list[dict[str, Any]] = []
        self.aos = False
        self.channels: dict[str, dict[str, Any]] = {}  # mnemonic -> {value, met, rx_utc, seq}
        self.limit_states: dict[str, str] = {}
        self.alarms: list[dict[str, Any]] = []
        self.event_log: list[dict[str, Any]] = []
        self.polls: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.track: list[dict[str, Any]] = []
        self.gframe: dict[str, Any] | None = None
        self._seen_alerts: set[int] = set()
        self._phase, self._phase_since = "PAD", 0.0
        self.path: Path | None = None
        self._fh = None
        if record:
            d = sessions_dir or Path(__file__).resolve().parents[2] / "data" / "sessions"
            d.mkdir(parents=True, exist_ok=True)
            self.path = d / f"{mission_id}.jsonl"
            self._fh = self.path.open("a", encoding="utf-8")
        self.log(None, "system", "session opened", {"source": source, "file": str(self.path) if self.path else None})

    # ------------------------------------------------------------------ log

    def log(self, utc: float | None, actor: str, action: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        e = {"n": len(self.event_log) + 1, "utc": utc, "met": self.last_met, "actor": actor, "action": action, "detail": detail or {}}
        self.event_log.append(e)
        return e

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    # ------------------------------------------------------------------ frames

    def ingest(self, frame: dict[str, Any], recv_utc: float) -> str:
        """Accept one decoded frame. Returns 'ok', 'duplicate' or 'hmac_fail'."""
        seq = frame["seq"]
        unsigned = frame.get("integrity") == "none"  # the twin protocol carries no HMAC: never shown as verified
        ok = None if unsigned else icd.verify(self.key, frame)
        if self._fh:
            self._fh.write(json.dumps({**{k: v for k, v in frame.items() if k != "rx"}, "rx": {"utc": recv_utc, "hmac_ok": ok}},
                                      separators=(",", ":")) + "\n")
            self._fh.flush()
        if unsigned:
            self.unsigned += 1
        elif not ok:
            self.hmac_failures.append(seq)
            self.log(recv_utc, "system", "HMAC FAIL", {"seq": seq})
            self._raise(recv_utc, "WARNING", f"hmac:{seq}", f"Frame {seq} failed HMAC: discarded", {"seq": seq})
            return "hmac_fail"
        if seq in self.frames:
            self.duplicates += 1
            return "duplicate"
        if seq < self.max_seq:
            self.out_of_order += 1
        # Chain link to neighbours already held (signed frames only).
        prev = self.frames.get(seq - 1)
        nxt = self.frames.get(seq + 1)
        if not unsigned and ((prev and frame["prev_hash"] != prev["hash"]) or (nxt and nxt["prev_hash"] != frame["hash"])):
            self.chain_breaks.append(seq)
            self._raise(recv_utc, "WARNING", f"chain:{seq}", f"Frame {seq} does not chain to its neighbour", {"seq": seq})
        self.frames[seq] = frame
        self.max_seq = max(self.max_seq, seq)
        self.min_seq = seq if self.min_seq is None else min(self.min_seq, seq)
        self.time_scale = float(frame.get("time_scale") or 1.0)
        if frame.get("utc_sent") is not None:
            self.deltas.append(recv_utc - frame["utc_sent"])
        self.arrivals.append(recv_utc)
        if self.first_rx_utc is None:
            self.first_rx_utc = recv_utc
        if not self.aos:
            if self.los_periods and self.los_periods[-1]["end_utc"] is None:
                self.los_periods[-1].update(end_utc=recv_utc, end_met=frame["met_s"])
                self.log(recv_utc, "system", "AOS", {"seq": seq, "met": frame["met_s"]})
            self.aos = True
        self.last_rx_utc = recv_utc
        for k, v in frame["ch"].items():
            c = self.channels.get(k)
            if c is None or frame["met_s"] >= c["met"]:
                self.channels[k] = {"value": v, "met": frame["met_s"], "rx_utc": recv_utc, "seq": seq}
        if frame["met_s"] > self.ingested_met:
            self.ingested_met = float(frame["met_s"])
            self.last_met = self.ingested_met
            self.pad.add(self.ingested_met, frame["ch"], str(frame["ch"].get("FC_PHASE", self.phase())))
            arrival = frame["met_s"] + max(0.0, self.latency_s() or 0.0) * self.time_scale
            for pkt in icd.packets_from_frame(frame, self.lat0, self.lon0, arrival_time=arrival):
                self.ground.ingest(pkt)
            self._step_ground(recv_utc, self.ingested_met)
        return "ok"

    def tick(self, now_utc: float) -> None:
        """Periodic update without a new frame: LOS detection and predictions on the extrapolated clock."""
        if self.last_rx_utc is None:
            return
        silent = now_utc - self.last_rx_utc
        if self.aos and silent > LOS_AFTER_S:
            self.aos = False
            self.los_periods.append({"start_utc": self.last_rx_utc, "end_utc": None, "start_met": self.last_met, "end_met": None})
            self.log(now_utc, "system", "LOS", {"last_seq": self.max_seq, "silence_s": silent})
            self._raise(now_utc, "CAUTION", f"los:{len(self.los_periods)}", "Loss of signal: values are STALE, position PREDICTED",
                        {"last_seq": self.max_seq, "last_met": self.last_met})
        if not self.aos:
            met = self.last_met + silent * self.time_scale
            self._step_ground(now_utc, met, store=False)

    def met_now(self, now_utc: float) -> float:
        if self.last_rx_utc is None:
            return 0.0
        return self.last_met if self.aos else self.last_met + (now_utc - self.last_rx_utc) * self.time_scale

    # ------------------------------------------------------------------ ground estimator

    def _step_ground(self, utc: float, met: float, store: bool = True) -> None:
        fr = self.ground.frame(met)
        self.gframe = fr
        for a in fr["alerts"]:
            if a["id"] not in self._seen_alerts:
                self._seen_alerts.add(a["id"])
                self._raise(utc, a["severity"], f"ground:{a['id']}", a["title"], a.get("evidence") or {}, source="estimator")
        self._check_limits(utc)
        self._check_range(utc, fr)
        self._maybe_forecast(met, fr)
        self._keep_evidence(met, fr)
        E = fr["estimates"]
        if store:
            self._check_off_nominal(utc, met)
            row = {"met": met, "phase": fr["phase"], "rec": fr["recommendation"]}
            prof = self.preflight_ctx.get("profile")
            for m in PF.PREDICTED:
                b = PF.band_at(prof, met, m)
                if b:
                    row[f"band_{m}_lo"], row[f"band_{m}_hi"] = round(b[0], 4), round(b[2], 4)
            for k in HISTORY_KEYS:
                c = self.channels.get(k)
                if c is not None and c["met"] == met:
                    row[k] = c["value"]
            for k in ("altitude", "vertical_speed"):
                if k in E and isinstance(E[k].get("value"), (int, float)):
                    row[f"fused_{k}"] = E[k]["value"]
                    row[f"fused_{k}_sigma"] = E[k].get("sigma")
            self.history.append(row)
        if "lat" in E and isinstance(E["lat"].get("value"), (int, float)):
            self.track.append({"met": met, "lat": E["lat"]["value"], "lon": E["lon"]["value"],
                               "alt": E.get("altitude", {}).get("value"), "status": E["lat"]["status"]})

    # ------------------------------------------------------------------ limits and alarms

    def phase(self) -> str:
        return str(self.channels.get("FC_PHASE", {}).get("value") or "PAD")

    def _check_limits(self, utc: float) -> None:
        ph = self.phase()
        if ph != self._phase:
            self._phase, self._phase_since = ph, self.last_met
        settle = self.dictionary.get("settle_s", {})
        if self.last_met - self._phase_since < settle.get(ph, settle.get("default", 0)):
            return
        for k, c in self.channels.items():
            st = icd.limit_state(k, c["value"], ph, self.dictionary)
            prev = self.limit_states.get(k, "NOMINAL")
            self.limit_states[k] = st
            if st in ("CAUTION", "ALARM") and st != prev:
                lim = icd.limits_for(k, ph, self.dictionary) or {}
                self._raise(utc, "WARNING" if st == "ALARM" else "CAUTION", f"limit:{k}:{st}:{c['met']}",
                            f"{k} {'RED' if st == 'ALARM' else 'YELLOW'} limit ({ph})",
                            {"value": c["value"], "yellow": lim.get("yellow"), "red": lim.get("red"), "basis": lim.get("basis")},
                            source="limits")
            elif st == "NOMINAL" and prev in ("CAUTION", "ALARM"):
                self.log(utc, "system", "limit cleared", {"channel": k, "phase": ph, "value": c["value"]})

    def _check_off_nominal(self, utc: float, met: float) -> None:
        """OFF-NOMINAL caution when a predictable channel stays outside its pre-flight band."""
        prof = self.preflight_ctx.get("profile")
        if not prof or self.phase() not in ("PAD", "ASCENT", "FLOAT"):
            return
        persist = self.cfg.prediction.off_nominal_persist_s
        for m in PF.PREDICTED:
            c = self.channels.get(m)
            b = PF.band_at(prof, met, m)
            if c is None or b is None or c["met"] != met or not isinstance(c["value"], (int, float)):
                continue
            if b[0] <= c["value"] <= b[2]:
                self._off_since.pop(m, None)
                if m in self._off_flagged:
                    self._off_flagged.discard(m)
                    self.log(utc, "system", "back inside predicted band", {"channel": m, "value": c["value"]})
                continue
            since = self._off_since.setdefault(m, met)
            if met - since >= persist and m not in self._off_flagged:
                self._off_flagged.add(m)
                self._raise(utc, "CAUTION", f"offnom:{m}:{met}", f"OFF-NOMINAL: {m} outside predicted band",
                            {"value": c["value"], "band": [b[0], b[2]], "nominal": b[1], "since_met": since}, source="prediction")

    def _check_range(self, utc: float, fr: dict[str, Any]) -> None:
        """In flight: re-check the live landing footprint and IIPs against the range zones."""
        rng = self.preflight_ctx.get("range")
        fp, iips = fr.get("landing_footprint"), fr.get("impact_points")
        src = ((fp or {}).get("t"), tuple((p["lat"], p["lon"]) for p in iips or []))
        if not rng or src == self._range_src or (not fp and not iips):
            return
        self._range_src = src
        res = PF.range_check(rng, fp, iips)
        keys = {f"{c['zone']}|{c['what']}" for c in res["conflicts"]}
        for c in res["conflicts"]:
            k = f"{c['zone']}|{c['what']}"
            if k not in self._range_keys:
                self._raise(utc, "WARNING", f"range:{k}:{self.last_met}", f"Predicted entry into {c['zone']} ({c['what']})", c, source="range")
        for k in self._range_keys - keys:
            self.log(utc, "system", "range conflict cleared", {"conflict": k})
        self._range_keys = keys

    def _keep_evidence(self, met: float, fr: dict[str, Any]) -> None:
        E, ph = fr["estimates"], fr["vehicle_phase"]
        if fr["recommendation"] == "GO_RECOMMENDED":
            self.go_times.append(met)
        if ph == "FLOAT" and self.t_float is None:
            self.t_float = met
        pred = E.get("prediction")
        if fr["predicting"] and isinstance(pred, dict) and pred.get("regions") and \
                (not self.prediction_log or met - self.prediction_log[-1]["t_snapshot"] >= 10.0):
            self.prediction_log.append({"t_snapshot": met, "regions": pred["regions"]})
        fp = fr.get("landing_footprint")
        if self.footprint_at_cutdown is None and ph == "DESCENT" and fp and str(fp.get("basis", "")).startswith("descent"):
            self.footprint_at_cutdown = fp
        if ph == "LANDED" and E.get("lat", {}).get("status") == "FUSED":
            self.landing_fix = {"t": met, "lat": E["lat"]["value"], "lon": E["lon"]["value"]}

    def _maybe_forecast(self, met: float, fr: dict[str, Any]) -> None:
        """Physics trajectory forecast every 5 mission seconds, plus the ML residual correction (hybrid)."""
        if self.forecaster is None or met - self._forecast_met < 5.0:
            return
        phase = fr["vehicle_phase"]
        st = state_from_estimates(fr["estimates"])
        if st is None or phase in ("PAD", "LANDED"):
            return
        self._forecast_met = met
        fc = self.forecaster
        rocket_aboard = fr["rocket_phase"] == "ABOARD"
        path = fc.path(phase, st["z"], st["vz"], st["east"], st["north"], rocket_aboard, horizon_s=600.0)
        at_h = fc.at_horizons(path)
        wind = fc.wind(st["z"])
        ll = lambda e, n: geo.enu_to_latlon(e, n, self.lat0, self.lon0)  # noqa: E731
        horizons = {}
        for h in HORIZONS:
            p = at_h[h]
            phys = {"alt": p["z"], "east": p["east"], "north": p["north"]}
            if st["status"] != "FUSED":
                res = {"status": "STATE_NOT_FUSED", "delta": None}
            elif self.hybrid is None:
                res = {"status": "MISSING_MODEL", "delta": None}
            else:
                x = MLF.build(st, phase, met - self._phase_since, p, wind, fc.z_float)
                res = self.hybrid.correct(phase, x, h)
            hyb = None
            if res["delta"]:
                d = res["delta"]
                hyb = {"alt": p["z"] + d["z"], "east": p["east"] + d["east"], "north": p["north"] + d["north"]}
            for v in (phys, hyb):
                if v:
                    v["lat"], v["lon"] = ll(v["east"], v["north"])
            horizons[str(h)] = {"physics": phys, "hybrid": hyb, "status": res["status"],
                                "out_of_range": res.get("out_of_range"),
                                "default": bool(self.hybrid and self.hybrid.default_enabled("z", h))}
            self.forecast_log.append({"met": met, "h": h, "phase": phase, "state": st["status"],
                                      "persist": [st["z"], st["east"], st["north"]],
                                      "physics": [phys["alt"], phys["east"], phys["north"]],
                                      "hybrid": [hyb["alt"], hyb["east"], hyb["north"]] if hyb else None, "status": res["status"]})
        self.traj_forecast = {"met": met, "phase": phase, "state_status": st["status"], "now": {"alt": st["z"], **dict(zip(("lat", "lon"), ll(st["east"], st["north"])))},
                         "physics_path": [{"h": q["h"], "alt": q["z"], **dict(zip(("lat", "lon"), ll(q["east"], q["north"])))} for q in path[5::6]],
                         "horizons": horizons, "label": "PREDICTED"}

    def waive(self, check_id: str, operator: str, reason: str, utc: float) -> dict[str, Any]:
        w = {"by": operator, "reason": reason, "utc": utc, "met": self.last_met}
        self.waivers[check_id] = w
        self.log(utc, operator, "PREFLIGHT WAIVER", {"check": check_id, "reason": reason})
        return w

    def _raise(self, utc: float, severity: str, key: str, title: str, evidence: dict[str, Any], source: str = "link") -> None:
        a = {"id": len(self.alarms) + 1, "key": key, "severity": severity, "title": title, "evidence": evidence,
             "source": source, "utc": utc, "met": self.last_met, "acknowledged_by": None, "acknowledged_utc": None}
        self.alarms.append(a)
        self.log(utc, "system", f"ALARM {severity}", {"id": a["id"], "title": title})

    def ack(self, alarm_id: int, operator: str, utc: float) -> dict[str, Any]:
        for a in self.alarms:
            if a["id"] == alarm_id:
                if a["acknowledged_by"]:
                    raise ValueError("already acknowledged")
                a["acknowledged_by"], a["acknowledged_utc"] = operator, utc
                self.log(utc, operator, "ALARM ACK", {"id": alarm_id, "title": a["title"]})
                return a
        raise KeyError(alarm_id)

    def poll(self, operator: str, vote: str, note: str, utc: float) -> dict[str, Any]:
        """Record one GO / NO-GO poll entry. It commands nothing: the ground station has no uplink."""
        rec = self.gframe["recommendation"] if self.gframe else "NO_GO"
        p = {"n": len(self.polls) + 1, "operator": operator, "vote": vote, "note": note, "utc": utc, "met": self.last_met,
             "recommendation": rec, "gates": {k: g["passed"] for k, g in (self.gframe or {}).get("gates", {}).items()}}
        self.polls.append(p)
        self.log(utc, operator, f"POLL {vote}", {"recommendation": rec, "note": note})
        return p

    # ------------------------------------------------------------------ link stats

    def latency_s(self) -> float | None:
        """Median of (receive - send) minus its minimum: latency above the fastest frame (clock offset removed)."""
        if not self.deltas:
            return None
        return statistics.median(self.deltas) - min(self.deltas)

    def link(self, now_utc: float) -> dict[str, Any]:
        while self.arrivals and self.arrivals[0] < now_utc - RATE_WINDOW_S:
            self.arrivals.popleft()
        lo = self.min_seq or 0
        expected = self.max_seq - lo + 1 if self.max_seq >= 0 else 0
        missing = expected - len(self.frames)
        return {
            "aos": self.aos and self.last_rx_utc is not None and now_utc - self.last_rx_utc <= LOS_AFTER_S,
            "last_frame_age_s": None if self.last_rx_utc is None else now_utc - self.last_rx_utc,
            "rate_hz": len(self.arrivals) / RATE_WINDOW_S,
            "frames_received": len(self.frames), "max_seq": self.max_seq,
            "loss_pct": 100.0 * missing / expected if expected > 0 else 0.0,
            "latency_s": self.latency_s(),
            "clock_offset_s": min(self.deltas) if self.deltas else None,
            "latency_note": "median(recv - sent) - min(recv - sent): the fixed clock offset between laptops cannot be separated from the minimum delay",
            "duplicates": self.duplicates, "out_of_order": self.out_of_order,
            "hmac_failures": len(self.hmac_failures), "chain_breaks": len(self.chain_breaks),
            "integrity": "none (unsigned twin protocol)" if self.unsigned else "HMAC-SHA256 chain (demo key)",
            "unsigned_frames": self.unsigned, "gaps": self.gaps(), "los_periods": self.los_periods, "time_scale": self.time_scale,
        }

    def gaps(self, limit: int = 200) -> list[list[int]]:
        out: list[list[int]] = []
        start = None
        for s in range(self.min_seq or 0, self.max_seq + 1):
            if s not in self.frames:
                start = s if start is None else start
            elif start is not None:
                out.append([start, s - 1])
                start = None
        return out[-limit:]

    # ------------------------------------------------------------------ snapshot

    def channel_table(self, now_utc: float) -> list[dict[str, Any]]:
        ph = self.phase()
        rows = []
        names = list(self.dictionary["channels"]) + sorted(k for k in self.channels if k not in self.dictionary["channels"])
        for k in names:
            d = self.dictionary["channels"].get(k)
            c = self.channels.get(k)
            age = None if c is None else now_utc - c["rx_utc"]
            status = "UNKNOWN" if c is None else ("STALE" if age is not None and age > STALE_AFTER_S else "MEASURED")
            rows.append({"mnemonic": k, "value": None if c is None else c["value"], "units": d["units"] if d else "",
                         "subsystem": d["subsystem"] if d else "RAW", "description": d["description"] if d else "not in dictionary",
                         "decimals": d.get("decimals") if d else None, "status": status, "age_s": age,
                         "met": None if c is None else c["met"], "limit": self.limit_states.get(k, "NO_LIMIT") if c else "NO_LIMIT",
                         "limits": icd.limits_for(k, ph, self.dictionary), "known": d is not None})
        return rows

    def snapshot(self, now_utc: float) -> dict[str, Any]:
        link = self.link(now_utc)
        return {
            "mission_id": self.mission_id, "source": self.source, "recording": self._fh is not None,
            "session_file": str(self.path) if self.path else None,
            "utc": now_utc, "met": self.met_now(now_utc), "met_status": "MEASURED" if link["aos"] else "PREDICTED",
            "fc_phase": self.phase(), "link": link, "channels": self.channel_table(now_utc), "ground": self.gframe,
            "alarms": self.alarms[-100:], "active_alarms": sum(1 for a in self.alarms if not a["acknowledged_by"]),
            "polls": self.polls, "forecast": self.traj_forecast,
        }
