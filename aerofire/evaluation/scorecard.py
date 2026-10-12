"""Grading scorecard: ground station performance against TRUTH (evaluator only, `api.truth_enabled`).

Never shown to the operator in the live views; the API serves it only when
grading is enabled in config.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..physics import geo

# Alerts a given injected fault is expected to cause; anything else at CAUTION/WARNING is a false alarm.
EXPECTED_ALERTS = {
    "tank_leak_um": {"tank_leak", "abort"},
    "gnss_spoof_start_s": {"nav_integrity", "excluded_gnss", "excluded_navic", "go_withdrawn"},
    "motor_heater_failed": {"motor_cold"},
    "blackouts": {"blackout", "go_withdrawn"},
    "balloon_leak_mm": {"go_withdrawn"},
}
OPERATIONAL = {"go", "float", "go_withdrawn", "atmo_fallback"}


def _truth_at(truth: list[dict[str, Any]], t: float) -> dict[str, Any]:
    ts = np.array([p["t"] for p in truth])
    i = int(np.clip(np.searchsorted(ts, t), 0, len(truth) - 1))
    return truth[i]


def scorecard(faults: dict[str, Any], truth: list[dict[str, Any]], prediction_log: list[dict[str, Any]], alerts: list[dict[str, Any]],
              footprint_at_cutdown: dict[str, Any] | None, landing_truth: dict[str, Any] | None, live_leak: dict[str, Any],
              lat0: float, lon0: float) -> dict[str, Any]:
    out: dict[str, Any] = {"mode": "grading (uses simulator truth)"}
    # Position error at reconnect.
    rec = [p for p in prediction_log if "t_reconnect" in p and p.get("predicted_mean")]
    errs = []
    for p in rec:
        tr = _truth_at(truth, p["t_reconnect"])
        e, n = geo.latlon_to_enu(tr["lat"], tr["lon"], lat0, lon0)
        errs.append({"t": p["t_reconnect"], "blackout_s": p["blackout_s"],
                     "error_vs_truth_m": math.hypot(e - p["predicted_mean"]["east"], n - p["predicted_mean"]["north"])})
    out["reconnect_errors"] = errs
    # Region coverage vs claimed level.
    snaps = [s for s in prediction_log if "regions" in s]
    cov = {}
    for lvl in (50, 90, 99):
        hits = []
        for s in snaps:
            tr = _truth_at(truth, s["t_snapshot"])
            poly = next(r["polygon"] for r in s["regions"] if r["level"] == lvl)
            hits.append(geo.point_in_polygon(tr["lat"], tr["lon"], poly))
        cov[str(lvl)] = {"claimed": lvl / 100, "achieved": (sum(hits) / len(hits)) if hits else None, "snapshots": len(hits)}
    out["region_coverage"] = cov
    # False alarms.
    expected = set()
    for k, keys in EXPECTED_ALERTS.items():
        v = faults.get(k)
        if v not in (None, 0, 0.0, False, [], ()):
            expected |= keys
    false_alarms = []
    for a in alerts:
        base = a["key"].split("@")[0]
        if a["severity"] in ("CAUTION", "WARNING") and base not in expected and base not in OPERATIONAL:
            false_alarms.append({"t": a["t"], "key": a["key"], "title": a["title"]})
    out["false_alarms"] = false_alarms
    # Landing error.
    if footprint_at_cutdown and landing_truth:
        e, n = geo.latlon_to_enu(landing_truth["lat"], landing_truth["lon"], footprint_at_cutdown["mean"]["lat"], footprint_at_cutdown["mean"]["lon"])
        poly90 = next(r["polygon"] for r in footprint_at_cutdown["regions"] if r["level"] == 90)
        out["landing"] = {"truth": landing_truth, "predicted_mean": footprint_at_cutdown["mean"], "error_m": math.hypot(e, n),
                          "inside_90": geo.point_in_polygon(landing_truth["lat"], landing_truth["lon"], poly90)}
    # Leak sizing.
    if faults.get("tank_leak_um"):
        out["leak"] = {"injected_um": faults["tank_leak_um"], "onset_s": faults.get("tank_leak_onset_s", 0.0),
                       "estimated_um": live_leak.get("d_est_um"), "detected_at": live_leak.get("leak_detected_at")}
    return out
