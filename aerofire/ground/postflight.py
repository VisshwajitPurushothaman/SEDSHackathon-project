"""Post-flight analysis on recovered telemetry (ground side: no truth used here).

- Reconstruction: RTS smoother over every recovered GNSS/NavIC/baro record (RECONSTRUCTED).
- Leak reprocessing: the leak detector re-run over the complete tank record set,
  plus a hindsight change-point estimate of the leak onset.
- Spoofing onset: change-point on the GNSS-NavIC horizontal residual.
- Requirement verification against config `requirements`:
  PASS | FAIL | NOT TESTED | INCONCLUSIVE, each with evidence.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..physics import atmosphere as A
from ..physics import geo
from ..physics import pendulum as Pd
from ..physics import tank as Tk
from .estimator import rts_smooth
from .leak_detector import LeakDetector


def by_sensor(records: list[dict[str, Any]], sid: str) -> list[dict[str, Any]]:
    return sorted((r for r in records if r["sensor_id"] == sid), key=lambda r: r["event_time"])


def reconstruct(cfg, forecast: A.AtmosphereProfile, records: list[dict[str, Any]], step_s: float = 1.0) -> list[dict[str, Any]]:
    epochs = []
    for r in records:
        v = r["values"]
        if r["sensor_id"] in ("gnss", "navic"):
            epochs.append((r["event_time"], r["sensor_id"], [v["east"], v["north"], v["up"]]))
        elif r["sensor_id"] == "baro":
            epochs.append((r["event_time"], "baro", [forecast.altitude_for_pressure(v["p"])]))
        elif r["sensor_id"] == "fc":
            epochs.append((r["event_time"], "phase", v.get("phase", "")))
    track = rts_smooth(cfg, epochs)
    out = []
    last_t = -1e9
    for p in track:
        if p["t"] - last_t >= step_s - 1e-9:
            lat, lon = geo.enu_to_latlon(p["e"], p["n"], cfg.site.lat_deg, cfg.site.lon_deg)
            out.append(p | {"lat": lat, "lon": lon, "status": "RECONSTRUCTED"})
            last_t = p["t"]
    return out


def changepoint_onset(t: np.ndarray, y: np.ndarray, min_seg: int = 30) -> float | None:
    """Least-squares onset of a ramp: y = c for t < t0, c + r (t - t0) after. Returns t0."""
    n = len(t)
    if n < 2 * min_seg:
        return None
    best, best_sse = None, math.inf
    for k in range(min_seg, n - min_seg, max(1, n // 400)):
        t0 = t[k]
        X = np.column_stack([np.ones(n), np.maximum(t - t0, 0.0)])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        sse = float(np.sum((y - X @ coef) ** 2))
        if sse < best_sse:
            best, best_sse = float(t0), sse
    return best


def leak_reprocess(cfg, records: list[dict[str, Any]]) -> dict[str, Any]:
    tank = by_sensor(records, "tank")
    if not tank:
        return {"available": False}
    det = LeakDetector.from_config(cfg)
    prm = Tk.TankParams.from_config(cfg)
    for r in tank:
        det.update(r["event_time"], r["values"]["P"], r["values"]["T"])
    out = {"available": True, "samples": len(tank), "detected": det.detected, "detected_at": det.detected_at,
           "d_est_um": det.last.d_est_um if det.detected and det.last else None}
    if det.detected:
        t = np.array([r["event_time"] for r in tank])
        m = np.array([Tk.mass_from_pt(prm, r["values"]["P"], r["values"]["T"]) for r in tank])
        stop = np.searchsorted(t, (det.detected_at or t[-1]) + 600.0)
        out["onset_estimate_t"] = changepoint_onset(t[:stop], m[:stop])
    return out


def spoof_onset(records: list[dict[str, Any]]) -> float | None:
    g = {r["event_time"]: r["values"] for r in by_sensor(records, "gnss")}
    n = {r["event_time"]: r["values"] for r in by_sensor(records, "navic")}
    ts = sorted(set(g) & set(n))
    if len(ts) < 100:
        return None
    t = np.array(ts)
    d = np.array([math.hypot(g[x]["east"] - n[x]["east"], g[x]["north"] - n[x]["north"]) for x in ts])
    return changepoint_onset(t, d)


def _req(rid: str, text: str, status: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"id": rid, "text": text, "status": status, "evidence": evidence}


def verify_requirements(cfg, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """ctx: recon (track), leak (reprocess), live (live detection info), recovery (report dict or None),
    prediction_log, footprint_at_cutdown, landing_fix, go_times, records, t_float."""
    texts = {r["id"]: r["text"] for r in cfg.requirements}
    out = []
    recon = ctx["recon"]
    recon_t = np.array([p["t"] for p in recon]) if recon else np.array([])

    def recon_at(t: float) -> dict[str, Any] | None:
        if not len(recon_t):
            return None
        i = int(np.clip(np.searchsorted(recon_t, t), 0, len(recon) - 1))
        return recon[i]

    # REQ-LEAK-1
    rid = "REQ-LEAK-1"
    leak, live = ctx["leak"], ctx["live"]
    if not leak.get("available"):
        out.append(_req(rid, texts[rid], "INCONCLUSIVE", {"reason": "no tank records"}))
    elif not leak["detected"] and not live.get("leak_detected"):
        out.append(_req(rid, texts[rid], "NOT TESTED", {"reason": "no leak in live detection nor in reprocessing of all recovered tank data"}))
    else:
        t_float = ctx.get("t_float")
        t_live = live.get("leak_detected_at")
        ok = t_live is not None and (t_float is None or t_live <= t_float)
        out.append(_req(rid, texts[rid], "PASS" if ok else "FAIL",
                        {"live_detection_t": t_live, "reprocessed_detection_t": leak.get("detected_at"), "float_t": t_float,
                         "hindsight_onset_t": leak.get("onset_estimate_t"), "d_est_um": leak.get("d_est_um")}))

    # REQ-PRED-1
    rid = "REQ-PRED-1"
    snaps = [s for s in ctx["prediction_log"] if "regions" in s]
    if not snaps:
        out.append(_req(rid, texts[rid], "NOT TESTED", {"reason": "no blackout occurred"}))
    elif not recon:
        out.append(_req(rid, texts[rid], "INCONCLUSIVE", {"reason": "no reconstruction (run recovery first)"}))
    else:
        hits = []
        for s in snaps:
            p = recon_at(s["t_snapshot"])
            poly = next(r["polygon"] for r in s["regions"] if r["level"] == 90)
            hits.append(geo.point_in_polygon(p["lat"], p["lon"], poly) if p else False)
        cov = sum(hits) / len(hits)
        status = "PASS" if cov >= 0.9 else ("INCONCLUSIVE" if len(hits) < 10 else "FAIL")
        out.append(_req(rid, texts[rid], status, {"snapshots": len(hits), "coverage": cov, "claimed": 0.9,
                                                  "note": "snapshots within one blackout are correlated"}))

    # REQ-LAND-1
    rid = "REQ-LAND-1"
    fp, land = ctx.get("footprint_at_cutdown"), ctx.get("landing_fix")
    if fp is None:
        out.append(_req(rid, texts[rid], "NOT TESTED", {"reason": "no cut-down"}))
    elif land is None:
        out.append(_req(rid, texts[rid], "INCONCLUSIVE", {"reason": "no landing fix"}))
    else:
        poly = next(r["polygon"] for r in fp["regions"] if r["level"] == 90)
        inside = geo.point_in_polygon(land["lat"], land["lon"], poly)
        e, n = geo.latlon_to_enu(land["lat"], land["lon"], fp["mean"]["lat"], fp["mean"]["lon"])
        out.append(_req(rid, texts[rid], "PASS" if inside else "FAIL",
                        {"landing": land, "footprint_mean": fp["mean"], "miss_distance_m": math.hypot(e, n), "footprint_t": fp.get("t")}))

    # REQ-DATA-1 / REQ-DATA-2
    rec = ctx.get("recovery")
    for rid in ("REQ-DATA-1", "REQ-DATA-2"):
        if rec is None or rec.get("state") != "done":
            out.append(_req(rid, texts[rid], "NOT TESTED", {"reason": "recovery not run yet"}))
        elif rid == "REQ-DATA-1":
            ok = rec["lost_count"] == 0 and rec["have"] == rec["expected"]
            out.append(_req(rid, texts[rid], "PASS" if ok else "FAIL",
                            {"expected": rec["expected"], "have": rec["have"], "lost": rec["lost_count"], "retries": rec["retries"]}))
        else:
            status = "PASS" if rec["chain_ok"] else ("INCONCLUSIVE" if rec["chain_ok"] is None else "FAIL")
            out.append(_req(rid, texts[rid], status, {"chain_ok": rec["chain_ok"], "first_bad_seq": rec["chain_first_bad"],
                                                      "hmac_failures": rec["hmac_failures"][:20]}))

    # REQ-GATE-1
    rid = "REQ-GATE-1"
    go_times = ctx.get("go_times") or []
    if not go_times:
        out.append(_req(rid, texts[rid], "NOT TESTED", {"reason": "GO was never recommended"}))
    elif not recon:
        out.append(_req(rid, texts[rid], "INCONCLUSIVE", {"reason": "no reconstruction"}))
    else:
        lo, hi = cfg.gates.release_band_m
        motor = by_sensor(ctx["records"], "motor")
        imu = by_sensor(ctx["records"], "imu")
        pend = Pd.PendulumParams(cfg.pendulum.length_m, cfg.pendulum.damping_ratio)
        mt = np.array([r["event_time"] for r in motor])
        it = np.array([r["event_time"] for r in imu])
        violations = []
        win = cfg.gates.float_vz_window_s
        for t in go_times:
            p = recon_at(t)
            # Same definition as the live gate: altitude in band and |mean v_z over the window| < limit.
            sel = (recon_t >= t - win) & (recon_t <= t)
            vz_mean = float(np.mean([recon[i]["vu"] for i in np.nonzero(sel)[0]])) if sel.any() else None
            if p and vz_mean is not None and not (lo <= p["u"] <= hi and abs(vz_mean) < cfg.gates.float_max_vz_m_s):
                violations.append({"t": t, "gate": "float", "reconstructed_z": p["u"], "reconstructed_vz_mean": vz_mean})
            if len(mt):
                r = motor[int(np.clip(np.searchsorted(mt, t), 0, len(motor) - 1))]
                if r["values"]["T"] - 273.15 < cfg.motor.T_min_qualified_c:
                    violations.append({"t": t, "gate": "motor", "T_c": r["values"]["T"] - 273.15})
            if len(it):
                i = int(np.clip(np.searchsorted(it, t), 0, len(imu) - 1))
                imu_window = imu[max(0, i - 8): i + 1]
                amps = [math.hypot(math.hypot(w["values"]["tilt_e"], w["values"]["rate_e"] / pend.omega),
                                   math.hypot(w["values"]["tilt_n"], w["values"]["rate_n"] / pend.omega)) for w in imu_window]
                if amps and float(np.median(amps)) > cfg.gates.swing_max_amplitude_deg:
                    violations.append({"t": t, "gate": "swing", "amplitude_deg": float(np.median(amps))})
        out.append(_req(rid, texts[rid], "PASS" if not violations else "FAIL",
                        {"go_epochs_checked": len(go_times), "violations": violations[:20],
                         "note": "nav and impact gates are re-checked only through the live evidence"}))
    return out
