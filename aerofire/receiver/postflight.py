"""Post-flight on the ground station (Laptop B): recovery, reconstruction, reports, grading, exports.

Recovery pulls the frames missed live from Laptop A's onboard recorder (`GET /manifest`,
`GET /recover?from_seq&to_seq`, docs/ICD.md) or from a recorder file handed over after landing
(`file:<path>`), progressively: every 10th missing frame first, then the rest in batches, each frame
HMAC-checked, failed batches retried, lost frames listed. Truth is fetched only when the operator
enables grading after the mission (`/truth` or `<recording>.truth.json`) and stays out of the report.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..ground import postflight as GP
from ..ground import profile as PF
from ..physics import geo
from ..telemetry import icd
from .preflight import canonical_hash

Fetch = Callable[[int, int], list[dict[str, Any]] | None]


# ----------------------------------------------------------------------------- sources


class Source:
    """Laptop A's recorder over HTTP (`http://host:8100`) or a recorder file (`file:path.jsonl[.gz]`)."""

    def __init__(self, spec: str, timeout_s: float = 5.0):
        self.spec, self.timeout = spec, timeout_s
        self.kind = "file" if spec.startswith("file:") else "http"
        self._frames: list[dict[str, Any]] | None = None

    def label(self) -> str:
        return f"recorder file {self.spec[5:]}" if self.kind == "file" else f"Laptop A recorder {self.spec}"

    def _file_frames(self) -> list[dict[str, Any]]:
        if self._frames is None:
            p = Path(self.spec[5:])
            data = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
            self._frames = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
        return self._frames

    def _get(self, path: str) -> Any:
        with urllib.request.urlopen(self.spec.rstrip("/") + path, timeout=self.timeout) as r:  # noqa: S310 (local link)
            return json.loads(r.read().decode("utf-8"))

    def manifest(self) -> dict[str, Any]:
        if self.kind == "file":
            fr = self._file_frames()
            return {"v": 1, "mission_id": fr[0]["mission_id"] if fr else None, "frames": len(fr), "first_seq": 0,
                    "last_seq": len(fr) - 1, "finished": True}
        return self._get("/manifest")

    def recover(self, a: int, b: int) -> list[dict[str, Any]] | None:
        try:
            if self.kind == "file":
                return [dict(f) for f in self._file_frames()[a: b + 1]]
            return self._get(f"/recover?from_seq={a}&to_seq={b}")["frames"]
        except Exception:
            return None

    def truth(self) -> dict[str, Any]:
        if self.kind == "file":
            p = self.spec[5:]
            t = Path(p.removesuffix(".gz").removesuffix(".jsonl").removesuffix(".vehicle") + ".truth.json")
            return json.loads(t.read_text(encoding="utf-8"))
        return self._get("/truth")


def default_source(session, playback_file: str | None) -> str | None:
    if playback_file:
        p = Path(playback_file)
        stem = p.name.removesuffix(".gz").removesuffix(".jsonl")
        cand = p.with_name(f"{stem}.vehicle.jsonl.gz")
        return f"file:{cand}" if cand.exists() else None
    return f"http://{session.sender_addr}:8100" if session.sender_addr else None


# ----------------------------------------------------------------------------- recovery


class FrameRecovery:
    def __init__(self, session, source: Source):
        self.s, self.src = session, source
        rc = session.cfg.recovery
        self.batch, self.stride, self.max_retries = int(rc.batch_size), int(rc.sparse_stride), int(rc.max_retries)
        self.r: dict[str, Any] = {"state": "idle", "stage": "", "source": source.label(), "expected": None, "have": len(session.frames),
                                  "missing_at_start": None, "recovered": 0, "retries": 0, "lost": [], "lost_count": 0,
                                  "hmac_failures": [], "chain_ok": None, "chain_first_bad": None, "progress": [], "error": None}

    def _transfer(self, seqs: list[int]) -> None:
        for i in range(0, len(seqs), self.batch):
            batch = seqs[i: i + self.batch]
            got = None
            for attempt in range(self.max_retries + 1):
                if attempt:
                    self.r["retries"] += 1
                    time.sleep(min(0.2 * attempt, 1.0))
                # One request per contiguous range inside the batch.
                frames, ok = [], True
                for a, b in _ranges(batch):
                    part = self.src.recover(a, b)
                    if part is None:
                        ok = False
                        break
                    frames += part
                if ok:
                    got = frames
                    break
            if got is None:
                self.r["lost"] += batch
                continue
            want = set(batch)
            with self.s_lock():
                for f in got:
                    f.pop("rx", None)
                    if f.get("seq") not in want or f.get("mission_id") != self.s.mission_id:
                        continue
                    if not icd.verify(self.s.key, f):
                        self.r["hmac_failures"].append(f["seq"])
                        continue
                    self.s.frames[f["seq"]] = f
                    self.s.recovered_seqs.add(f["seq"])
                    self.r["recovered"] += 1
            self.r["have"] = len(self.s.frames)
            self.r["progress"].append({"stage": self.r["stage"], "have": self.r["have"], "expected": self.r["expected"], "utc": time.time()})

    def s_lock(self):
        return self.s.lock

    def run(self) -> dict[str, Any]:
        r = self.r
        r["state"] = "running"
        try:
            man = self.src.manifest()
            if man.get("mission_id") not in (None, self.s.mission_id):
                raise ValueError(f"recorder holds mission {man.get('mission_id')}, not {self.s.mission_id}")
            if not man.get("finished"):
                raise ValueError("the vehicle reports the mission is not finished: recover after landing")
            r["expected"] = int(man["last_seq"]) + 1
            missing = [q for q in range(r["expected"]) if q not in self.s.frames]
            r["missing_at_start"] = len(missing)
            r["stage"] = f"rough picture (every {self.stride}th missing frame)"
            self._transfer(missing[:: self.stride])
            rest = [q for q in missing if q not in self.s.frames and q not in r["lost"]]
            r["stage"] = f"full detail (batches of {self.batch})"
            self._transfer(rest)
            with self.s.lock:
                held = [self.s.frames[q] for q in sorted(self.s.frames)]
            if len(held) == r["expected"]:
                r["chain_ok"], r["chain_first_bad"] = icd.verify_chain(self.s.key, self.s.genesis, held)
            else:
                r["chain_ok"] = False if r["hmac_failures"] else None
            r["lost"] = sorted(set(r["lost"]))
            r["lost_count"] = len(r["lost"])
            r["state"], r["stage"] = "done", "complete"
        except Exception as exc:
            r["state"], r["error"] = "failed", f"{type(exc).__name__}: {exc}"
        r["have"] = len(self.s.frames)
        self.s.log(time.time(), "system", "RECOVERY " + r["state"].upper(),
                   {k: r[k] for k in ("recovered", "lost_count", "retries", "chain_ok", "error")})
        return r


def _ranges(seqs: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for q in sorted(seqs):
        if out and q == out[-1][1] + 1:
            out[-1] = (out[-1][0], q)
        else:
            out.append((q, q))
    return out


# ----------------------------------------------------------------------------- analysis


def held_frames(session) -> list[dict[str, Any]]:
    return [session.frames[q] for q in sorted(session.frames)]


def records(session, frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for f in frames:
        out += icd.packets_from_frame(f, session.lat0, session.lon0)
    out.sort(key=lambda r: r["event_time"])
    return out


def limit_violations(frames: list[dict[str, Any]], dictionary: dict[str, Any]) -> list[dict[str, Any]]:
    """Contiguous CAUTION/ALARM intervals per channel (worst level, peak value), on all held frames
    (live + recovered), with the dictionary's phase-change settle time as in flight."""
    open_: dict[str, dict[str, Any]] = {}
    out = []
    last_met = None
    settle = dictionary.get("settle_s", {})
    phase, since = None, 0.0
    for f in frames:
        met, ph = f["met_s"], str(f["ch"].get("FC_PHASE", "PAD"))
        if met == last_met:
            continue
        last_met = met
        if ph != phase:
            phase, since = ph, met
        if met - since < settle.get(ph, settle.get("default", 0)):
            continue  # same phase-change settle time as the live limit alarms
        for m, v in f["ch"].items():
            st = icd.limit_state(m, v, ph, dictionary)
            cur = open_.get(m)
            if st in ("CAUTION", "ALARM"):
                lim = icd.limits_for(m, ph, dictionary) or {}
                if cur is None:
                    cur = open_[m] = {"channel": m, "phase": ph, "start_met": met, "end_met": met, "level": st, "peak": v,
                                      "yellow": lim.get("yellow"), "red": lim.get("red"), "samples": 0}
                cur["end_met"], cur["samples"] = met, cur["samples"] + 1
                if st == "ALARM":
                    cur["level"] = "ALARM"
                ref = (lim.get("red") or lim.get("yellow") or [None, None])
                lo_side = ref[0] is not None and v < ref[0]
                cur["peak"] = min(cur["peak"], v) if lo_side else max(cur["peak"], v)
            elif cur is not None:
                out.append(open_.pop(m))
    out += open_.values()
    return sorted(out, key=lambda x: x["start_met"])


def predicted_vs_actual(frames: list[dict[str, Any]], profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for m in PF.PREDICTED:
        inside = n = 0
        worst = 0.0
        for f in frames:
            v = f["ch"].get(m)
            if not isinstance(v, (int, float)) or str(f["ch"].get("FC_PHASE")) not in ("PAD", "ASCENT", "FLOAT"):
                continue
            b = PF.band_at(profile, f["met_s"], m)
            if b is None:
                continue
            n += 1
            if b[0] <= v <= b[2]:
                inside += 1
            else:
                worst = max(worst, b[0] - v if v < b[0] else v - b[2])
        rows.append({"channel": m, "samples": n, "inside_pct": 100.0 * inside / n if n else None, "worst_outside": worst if n else None})
    return rows


def _ref_lookup(track: list[dict[str, Any]], key_t: str, key_z: str, key_e: str, key_n: str):
    t = np.array([p[key_t] for p in track]) if track else np.array([])

    def at(x: float):
        if not len(t) or x < t[0] or x > t[-1]:
            return None
        i = int(np.clip(np.searchsorted(t, x), 0, len(track) - 1))
        if abs(t[i] - x) > 5.0:
            return None
        p = track[i]
        return p[key_z], p[key_e], p[key_n]

    return at


def forecast_scorecard(log: list[dict[str, Any]], recon: list[dict[str, Any]], truth_track: list[dict[str, Any]] | None, lat0: float, lon0: float):
    refs = {"reconstruction": _ref_lookup(recon, "t", "u", "e", "n")}
    if truth_track:
        tt = []
        for p in truth_track:
            e, n = geo.latlon_to_enu(p["lat"], p["lon"], lat0, lon0)
            tt.append({"t": p["t"], "z": p["z"], "e": e, "n": n})
        refs["truth (grading)"] = _ref_lookup(tt, "t", "z", "e", "n")
    out = []
    for ref_name, at in refs.items():
        for h in sorted({e["h"] for e in log}):
            errs: dict[str, list[float]] = {"persistence": [], "physics": [], "hybrid": []}
            herr: dict[str, list[float]] = {"persistence": [], "physics": [], "hybrid": []}
            used_hybrid = 0
            for e in log:
                if e["h"] != h:
                    continue
                ref = at(e["met"] + h)
                if ref is None:
                    continue
                for k in ("persistence", "physics", "hybrid"):
                    p = e["persist"] if k == "persistence" else e["physics"] if k == "physics" else (e["hybrid"] or e["physics"])
                    errs[k].append(abs(p[0] - ref[0]))
                    herr[k].append(math.hypot(p[1] - ref[1], p[2] - ref[2]))
                used_hybrid += e["hybrid"] is not None
            if errs["physics"]:
                out.append({"reference": ref_name, "h": h, "n": len(errs["physics"]), "hybrid_used": used_hybrid,
                            **{f"{k}_alt_mae": float(np.mean(v)) for k, v in errs.items()},
                            **{f"{k}_horiz_mae": float(np.mean(v)) for k, v in herr.items()}})
    return out


def landing_report(session) -> dict[str, Any] | None:
    fp, land = session.footprint_at_cutdown, session.landing_fix
    if fp is None or land is None:
        return None
    e, n = geo.latlon_to_enu(land["lat"], land["lon"], fp["mean"]["lat"], fp["mean"]["lon"])
    inside = {r["level"]: geo.point_in_polygon(land["lat"], land["lon"], r["polygon"]) for r in fp["regions"]}
    return {"landing_fix": land, "footprint_mean": fp["mean"], "miss_distance_m": math.hypot(e, n), "inside": inside, "footprint_t": fp.get("t")}


def fault_timeline(session, truth: dict[str, Any] | None) -> list[dict[str, Any]]:
    detected = [{"met": a["met"], "severity": a["severity"], "title": a["title"], "source": a["source"]}
                for a in session.alarms if a["source"] in ("estimator", "prediction", "range") or a["title"].startswith("Loss of signal")]
    rows = [{"kind": "detected", **d} for d in detected]
    if truth:
        f = truth.get("faults") or {}

        def first(pred) -> float | None:
            return next((d["met"] for d in detected if pred(d["title"])), None)

        injected = []
        if f.get("tank_leak_um"):
            injected.append(("tank leak %.0f um" % f["tank_leak_um"], f.get("tank_leak_onset_s", 0.0), first(lambda t: "leak" in t.lower())))
        if f.get("gnss_spoof_start_s") is not None:
            injected.append(("GNSS spoofing", f["gnss_spoof_start_s"], first(lambda t: "integrity" in t.lower() or "nav" in t.lower())))
        if f.get("motor_heater_failed"):
            injected.append(("motor heater failed", 0.0, first(lambda t: "motor" in t.lower())))
        for a, b in f.get("blackouts") or []:
            injected.append((f"link blackout {a:.0f}-{b:.0f} s", a, next((d["met"] for d in detected if d["title"].startswith("Loss of signal") and d["met"] >= a - 5), None)))
        for name, onset, det in injected:
            rows.append({"kind": "injected (truth)", "met": onset, "title": name, "detected_met": det,
                         "detection_delay_s": None if det is None else det - onset})
    return sorted(rows, key=lambda r: r["met"])


def analyse(session, grading: bool) -> dict[str, Any]:
    with session.lock:
        frames = held_frames(session)
        rec_state = session.recovery.r if session.recovery else None
        truth = session.truth if grading else None
    recs = records(session, frames)
    recon = GP.reconstruct(session.cfg, session.forecast, recs, step_s=5.0)
    det = session.ground.detector
    ctx = {"recon": recon, "leak": GP.leak_reprocess(session.cfg, recs),
           "live": {"leak_detected": det.detected, "leak_detected_at": det.detected_at,
                    "d_est_um": det.last.d_est_um if det.last else None},
           "recovery": rec_state, "prediction_log": session.prediction_log, "footprint_at_cutdown": session.footprint_at_cutdown,
           "landing_fix": session.landing_fix, "go_times": session.go_times[::5], "records": recs, "t_float": session.t_float}
    reqs = GP.verify_requirements(session.cfg, ctx)
    profile = session.preflight_ctx.get("profile")
    report = {
        "mission_id": session.mission_id, "frames_held": len(frames), "frames_recovered": len(session.recovered_seqs),
        "recovery": {k: v for k, v in (rec_state or {}).items() if k != "progress"} or None,
        "requirements": reqs, "limit_violations": limit_violations(frames, session.dictionary),
        "predicted_vs_actual": predicted_vs_actual(frames, profile), "fault_timeline": fault_timeline(session, None),
        "forecast_scorecard": forecast_scorecard(session.forecast_log, recon, None, session.lat0, session.lon0),
        "landing": landing_report(session), "polls": session.polls, "closure": session.closure,
        "event_log": session.event_log,
        "note": "ground-side evidence only (live + recovered telemetry); evaluator truth is never part of this report",
    }
    out = {"report": report, "report_sha256": canonical_hash(report), "grading": bool(truth),
           "histories": {"live": session.track[::5], "reconstructed": [{"t": p["t"], "lat": p["lat"], "lon": p["lon"], "alt": p["u"],
                                                                       "status": "RECONSTRUCTED"} for p in recon]}}
    if truth:
        out["grading_view"] = {"fault_timeline": fault_timeline(session, truth),
                               "forecast_scorecard": forecast_scorecard(session.forecast_log, recon, truth.get("track"), session.lat0, session.lon0),
                               "landing_truth": truth.get("landing"), "variability": truth.get("variability"), "scenario": truth.get("scenario")}
        out["histories"]["truth"] = [{"t": p["t"], "lat": p["lat"], "lon": p["lon"], "alt": p["z"]} for p in truth.get("track", [])]
    return out


# ----------------------------------------------------------------------------- exports


def frames_jsonl(session) -> str:
    with session.lock:
        frames = held_frames(session)
        rec = set(session.recovered_seqs)
    return "".join(json.dumps(f | ({"recovered": True} if f["seq"] in rec else {}), separators=(",", ":")) + "\n" for f in frames)


def subsystem_csv(session, subsystem: str) -> str:
    chans = [m for m, d in session.dictionary["channels"].items() if d.get("subsystem") == subsystem]
    if not chans:
        raise KeyError(subsystem)
    with session.lock:
        frames = held_frames(session)
        rec = set(session.recovered_seqs)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["seq", "met_s", "received", *chans])
    last = None
    for f in frames:
        if f["met_s"] == last:
            continue  # heartbeat repeats
        last = f["met_s"]
        w.writerow([f["seq"], f["met_s"], "recovered" if f["seq"] in rec else "live", *[f["ch"].get(m, "") for m in chans]])
    return buf.getvalue()
