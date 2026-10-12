"""Launch-commit gates. Evaluated by the ground station on ESTIMATES; the result is a recommendation.

Five commit gates (all must pass, continuously for `gates_hold_s`):
  1. FLOAT      altitude inside the release band and |v_z| < float_max_vz
  2. NAV        GNSS, NavIC and barometric altitude agree (chi-square, 99.9 %), tested on the MEAN
                residual r_bar over the last N epochs (a single-epoch 99.9 % test fires on ~0.2 % of
                epochs by construction, i.e. several false alarms per flight):
                  horizontal  d2 = N sum_axes r_bar^2 / (s_g^2 + s_n^2)        <= 13.816 (2 dof)
                  vertical    d2 = N r_bar_v^2 / (s_gv^2 + s_baro^2)           <= 10.828 (1 dof)
  3. MOTOR      motor temperature >= qualified minimum
  4. SWING      swing AMPLITUDE A = sqrt(theta^2 + (theta'/w)^2) <= limit (both axes combined)
  5. IMPACT     instantaneous impact points of the nominal, early-burnout and no-ignition cases
                all lie inside the approved area polygon (ballistic with drag, illustrative rocket model)
Abort criterion (separate from the five, from the original backend):
  TANK          no pressurant leak declared by the ground leak detector.

Recommendation: GO_RECOMMENDED | NO_GO | ABORT_RECOMMENDED. The software never
confirms ignition; only the operator's commit does (POST /api/missions/{id}/commit).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence

from . import geo
from . import rocket as Rk
from .atmosphere import AtmoSample

GATE_ORDER = ["float", "nav", "motor", "swing", "impact"]
GATE_NAMES = {
    "float": "Float in release band",
    "nav": "Navigation integrity",
    "motor": "Motor temperature",
    "swing": "Swing amplitude",
    "impact": "Impact safety",
    "tank": "Pressurant integrity (abort criterion)",
}


@dataclass
class GateResult:
    id: str
    name: str
    passed: bool | None  # None = NOT EVALUATED (inputs unavailable)
    value: Any
    limit: Any
    margin: float | None
    unit: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unknown(gid: str, why: str) -> GateResult:
    return GateResult(gid, GATE_NAMES[gid], None, None, None, None, "", f"not evaluated: {why}")


def float_gate(z: float | None, vz: float | None, band: Sequence[float], vz_max: float) -> GateResult:
    if z is None or vz is None:
        return _unknown("float", "no altitude / vertical-speed estimate")
    lo, hi = band
    in_band = lo <= z <= hi
    still = abs(vz) < vz_max
    return GateResult("float", GATE_NAMES["float"], in_band and still, {"z_m": z, "vz_m_s": vz},
                      {"band_m": list(band), "vz_max_m_s": vz_max}, min(z - lo, hi - z), "m",
                      ("in band" if in_band else "outside release band") + ("; stable" if still else f"; |vz| {abs(vz):.2f} m/s too high"),
                      {"vz_margin_m_s": vz_max - abs(vz)})


def nav_gate(residuals: Sequence[tuple[float, float, float]] | None, s, chi2_2: float, chi2_1: float) -> GateResult:
    """residuals: per-epoch (gnss_e - navic_e, gnss_n - navic_n, gnss_up - baro_alt), most recent last."""
    if not residuals:
        return _unknown("nav", "missing GNSS, NavIC or barometer")
    n = len(residuals)
    me = sum(r[0] for r in residuals) / n
    mn = sum(r[1] for r in residuals) / n
    mv = sum(r[2] for r in residuals) / n
    var_h = s.gnss_sigma_h_m**2 + s.navic_sigma_h_m**2
    d2_h = n * (me**2 + mn**2) / var_h
    var_v = s.gnss_sigma_v_m**2 + s.baro_alt_model_sigma_m**2
    d2_v = n * mv**2 / var_v
    ok = d2_h <= chi2_2 and d2_v <= chi2_1
    detail = "GNSS, NavIC and baro agree" if ok else ("GNSS/NavIC horizontal disagreement" if d2_h > chi2_2 else "GNSS/baro vertical disagreement")
    return GateResult("nav", GATE_NAMES["nav"], ok, {"chi2_h": d2_h, "chi2_v": d2_v}, {"chi2_h": chi2_2, "chi2_v": chi2_1},
                      min(chi2_2 - d2_h, chi2_1 - d2_v), "chi2", detail,
                      {"mean_gnss_minus_navic_m": [me, mn], "mean_gnss_minus_baro_m": mv, "epochs": n})


def motor_gate(T_c: float | None, limit_c: float) -> GateResult:
    if T_c is None:
        return _unknown("motor", "no motor temperature")
    return GateResult("motor", GATE_NAMES["motor"], T_c >= limit_c, T_c, limit_c, T_c - limit_c, "degC",
                      "qualified" if T_c >= limit_c else "below qualified minimum")


def swing_gate(amp_deg: float | None, limit_deg: float) -> GateResult:
    if amp_deg is None:
        return _unknown("swing", "no IMU data")
    return GateResult("swing", GATE_NAMES["swing"], amp_deg <= limit_deg, amp_deg, limit_deg, limit_deg - amp_deg, "deg",
                      "amplitude within limit" if amp_deg <= limit_deg else "swinging too much")


@dataclass
class ImpactPoint:
    case: str
    lat: float
    lon: float
    inside: bool
    range_m: float
    apogee_m: float


def impact_points(rparams: Rk.RocketParams, lat: float, lon: float, z: float, atmo: Callable[[float], AtmoSample],
                  early_fractions: Sequence[float], polygon, dt: float = 0.1) -> list[ImpactPoint]:
    """IIPs for nominal, early-burnout and no-ignition cases from a release at (lat, lon, z). Illustrative model."""
    a = atmo(z)
    cases = [("nominal", True, 1.0)] + [(f"early burnout {int(f * 100)} %", True, f) for f in early_fractions] + [("no ignition", False, 1.0)]
    out = []
    for name, ign, frac in cases:
        r = Rk.simulate(rparams, z, atmo, ignite=ign, burn_fraction=frac, dt=dt, case=name)
        ilat, ilon = Rk.impact_latlon(rparams, r, lat, lon, a.u, a.v)
        out.append(ImpactPoint(name, ilat, ilon, geo.point_in_polygon(ilat, ilon, polygon), r.range_m, r.apogee_m))
    return out


def impact_gate(points: list[ImpactPoint] | None) -> GateResult:
    if not points:
        return _unknown("impact", "no impact prediction yet")
    ok = all(p.inside for p in points)
    bad = [p.case for p in points if not p.inside]
    return GateResult("impact", GATE_NAMES["impact"], ok, len(points) - len(bad), len(points), None, "cases inside",
                      "all impact points inside approved area" if ok else "outside approved area: " + ", ".join(bad),
                      {"points": [asdict(p) for p in points], "model": Rk.LABEL})


def tank_criterion(detected: bool, d_est_um: float | None, slope: float | None, evaluated: bool) -> GateResult:
    if not evaluated:
        return _unknown("tank", "leak detector has not completed a window yet")
    if detected:
        return GateResult("tank", GATE_NAMES["tank"], False, d_est_um, 0.0, None, "um",
                          f"LEAK declared, est. {d_est_um:.0f} um" if d_est_um else "LEAK declared",
                          {"slope_kg_s": slope})
    return GateResult("tank", GATE_NAMES["tank"], True, 0.0, 0.0, None, "um", "no significant mass loss", {"slope_kg_s": slope})


def recommend(gates: dict[str, GateResult], tank: GateResult, all_pass_since: float | None, t: float, hold_s: float) -> str:
    if tank.passed is False:
        return "ABORT_RECOMMENDED"
    if all(gates[g].passed for g in GATE_ORDER) and all_pass_since is not None and t - all_pass_since >= hold_s:
        return "GO_RECOMMENDED"
    return "NO_GO"
