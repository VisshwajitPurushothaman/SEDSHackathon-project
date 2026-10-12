"""Pre-flight checks on pad telemetry (Laptop B): channel health, pad sensor agreement, predicted
profile, range/airspace safety, readiness and a hashed readiness report.

Pad samples are the frames received while FC_PHASE == PAD. Results freeze at release.
Statuses: NOT CHECKED / PASS / FAIL / WAIVED (a waiver needs an operator name and a reason).
"""
from __future__ import annotations

import hashlib
import json
import statistics
from typing import Any

from ..physics import geo
from ..telemetry import icd

MIN_PAD_SAMPLES = 10  # [ASSUMPTION] distinct pad epochs needed before a channel can be judged
RATE_TOLERANCE = 0.5  # [ASSUMPTION] measured rate within +-50 % of the dictionary rate


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


class PadEvidence:
    """Accumulates pad-phase samples per channel."""

    def __init__(self) -> None:
        self.samples: dict[str, list[tuple[float, Any]]] = {}
        self.frozen = False  # True once the vehicle has left the pad

    def add(self, met: float, ch: dict[str, Any], phase: str) -> None:
        if self.frozen:
            return
        if phase != "PAD":
            self.frozen = bool(self.samples)
            return
        for k, v in ch.items():
            s = self.samples.setdefault(k, [])
            if not s or s[-1][0] != met:
                s.append((met, v))


def channel_health(dictionary: dict[str, Any], ev: PadEvidence) -> list[dict[str, Any]]:
    items = []
    for m, d in dictionary["channels"].items():
        if d.get("pad", True) is False:
            continue
        s = ev.samples.get(m, [])
        evidence: dict[str, Any] = {"samples": len(s), "expected_rate_hz": d.get("rate_hz")}
        if len(s) < MIN_PAD_SAMPLES:
            status = "NOT CHECKED" if not ev.frozen else ("FAIL" if not s else "NOT CHECKED")
            evidence["reason"] = "absent from pad telemetry" if not s else f"fewer than {MIN_PAD_SAMPLES} pad epochs"
            items.append({"id": f"ch:{m}", "title": f"{m} healthy on the pad", "status": status, "evidence": evidence})
            continue
        span = s[-1][0] - s[0][0] + 1.0
        rate = len(s) / span
        evidence["rate_hz"] = rate
        fails = []
        if d.get("rate_hz") and not (1 - RATE_TOLERANCE) * d["rate_hz"] <= rate <= (1 + RATE_TOLERANCE) * d["rate_hz"]:
            fails.append("rate")
        nums = [v for _, v in s if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums:
            med = statistics.median(nums)
            evidence["median"] = med
            st = icd.limit_state(m, med, "PAD", dictionary)
            evidence["pad_limit"] = st
            if st in ("CAUTION", "ALARM"):
                fails.append(f"outside pad {'yellow' if st == 'CAUTION' else 'red'} limit")
            if (d.get("decimals") or 0) >= 1:
                var = statistics.pvariance(nums)
                evidence["variance"] = var
                if var <= 0.0:
                    fails.append("frozen (zero variance)")
        evidence["failures"] = fails
        items.append({"id": f"ch:{m}", "title": f"{m} present, at rate, within pad limits, not frozen",
                      "status": "FAIL" if fails else "PASS", "evidence": evidence})
    return items


def pad_agreement(cfg, forecast, ev: PadEvidence) -> dict[str, Any]:
    """GNSS vs NavIC vs barometer on the pad: chi-square of the mean residual against config sensor noise."""
    s = {m: dict(ev.samples.get(m, [])) for m in ("GNSS_LAT", "GNSS_LON", "GNSS_ALT", "NAVIC_LAT", "NAVIC_LON", "NAVIC_ALT", "BARO_P")}
    mets = sorted(set(s["GNSS_LAT"]) & set(s["NAVIC_LAT"]) & set(s["BARO_P"]) & set(s["GNSS_ALT"]) & set(s["NAVIC_ALT"]))
    if len(mets) < MIN_PAD_SAMPLES:
        return {"id": "agreement", "title": "GNSS / NavIC / baro agree on the pad", "status": "NOT CHECKED",
                "evidence": {"epochs": len(mets)}}
    se, sn = cfg.sensors, cfg.site
    de, dn, dz_gn, dz_gb = [], [], [], []
    for t in mets:
        eg, ng = geo.latlon_to_enu(s["GNSS_LAT"][t], s["GNSS_LON"][t], sn.lat_deg, sn.lon_deg)
        en, nn = geo.latlon_to_enu(s["NAVIC_LAT"][t], s["NAVIC_LON"][t], sn.lat_deg, sn.lon_deg)
        de.append(eg - en)
        dn.append(ng - nn)
        dz_gn.append(s["GNSS_ALT"][t] - s["NAVIC_ALT"][t])
        dz_gb.append(s["GNSS_ALT"][t] - forecast.altitude_for_pressure(s["BARO_P"][t] * 100.0))
    N = len(mets)
    var_h = (se.gnss_sigma_h_m ** 2 + se.navic_sigma_h_m ** 2) / N
    var_v = (se.gnss_sigma_v_m ** 2 + se.navic_sigma_v_m ** 2) / N
    var_b = se.gnss_sigma_v_m ** 2 / N + se.baro_alt_model_sigma_m ** 2  # forecast inversion error is a bias, not noise
    chi_h = (statistics.fmean(de) ** 2 + statistics.fmean(dn) ** 2) / var_h
    chi_v = statistics.fmean(dz_gn) ** 2 / var_v
    chi_b = statistics.fmean(dz_gb) ** 2 / var_b
    lim2, lim1 = cfg.gates.chi2_2dof_999, cfg.gates.chi2_1dof_999
    ok = chi_h <= lim2 and chi_v <= lim1 and chi_b <= lim1
    return {"id": "agreement", "title": "GNSS / NavIC / baro agree on the pad (chi-square of mean residuals, p = 0.999)",
            "status": "PASS" if ok else "FAIL",
            "evidence": {"epochs": N, "chi2_gnss_navic_h": chi_h, "chi2_gnss_navic_v": chi_v, "chi2_gnss_baro_v": chi_b,
                         "limit_2dof": lim2, "limit_1dof": lim1, "mean_dh_m": [statistics.fmean(de), statistics.fmean(dn)],
                         "mean_dz_gnss_baro_m": statistics.fmean(dz_gb)}}


def evaluate(session) -> dict[str, Any]:
    """Full pre-flight picture for a session (or for the receiver before any telemetry)."""
    cfg, pf = session.cfg, session.preflight_ctx
    items = channel_health(session.dictionary, session.pad) + [pad_agreement(cfg, session.forecast, session.pad)]
    prof = pf.get("profile")
    items.append({"id": "profile", "title": "Predicted telemetry profile computed (forecast atmosphere)",
                  "status": "PASS" if prof else "NOT CHECKED",
                  "evidence": {"samples": len(prof["samples"]) if prof else 0, "atmo_source": prof["atmo_source"] if prof else None}})
    rng = pf.get("range_check")
    items.append({"id": "range", "title": "Predicted landing footprint and IIPs clear of restricted zones",
                  "status": rng["status"] if rng else "NOT CHECKED", "evidence": rng or {}})
    for it in items:
        w = session.waivers.get(it["id"])
        if w and it["status"] != "PASS":
            it["status"], it["evidence"] = "WAIVED", it["evidence"] | {"waiver": w}
    counts = {k: sum(1 for i in items if i["status"] == k) for k in ("PASS", "FAIL", "NOT CHECKED", "WAIVED")}
    ready = counts["FAIL"] == 0 and counts["NOT CHECKED"] == 0
    readiness = "READY FOR DIRECTOR REVIEW" if ready else "HOLD"
    report = {"mission_id": session.mission_id, "met": session.last_met, "pad_frozen": session.pad.frozen, "readiness": readiness,
              "counts": counts, "items": items, "range": rng, "atmo_source": prof["atmo_source"] if prof else None,
              "note": "software recommendation for the director's review; it commands nothing"}
    return {"readiness": readiness, "counts": counts, "items": items, "report": report, "report_sha256": canonical_hash(report)}
