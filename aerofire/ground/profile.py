"""Pre-flight predicted telemetry profile (ground physics + forecast atmosphere, no telemetry).

Runs the nominal flight plan (pad hold, free-lift ascent, float with the onboard ballast
controller) through the physics models with the *forecast* atmosphere, three times: nominal,
fast (more free lift, less drag) and slow. The band for each predictable channel is the
envelope of the three runs widened by a per-channel model tolerance (config `prediction`,
all ASSUMPTIONS). Values use the channel dictionary's units.
"""
from __future__ import annotations

from typing import Any

from ..physics import atmosphere as A
from ..physics import balloon as Bl
from ..physics import geo
from ..physics import motor_thermal as Mt
from ..physics import tank as Tk
from ..physics.float_control import BallastController

PREDICTED = ("GNSS_ALT", "GNSS_VU", "BARO_P", "AIR_T", "TANK_P", "TANK_T", "MOTOR_T")
K0 = 273.15


def run(cfg, forecast: A.AtmosphereProfile, free_lift_scale: float = 1.0, cd_scale: float = 1.0) -> list[dict[str, Any]]:
    """One deterministic run; a sample every `prediction.sample_s` mission seconds."""
    pc, b, r = cfg.prediction, cfg.balloon, cfg.rocket
    atmo, dt, ground_z = forecast.at, cfg.sim.dt_s, cfg.site.elevation_m
    params = Bl.BalloonParams(b.m_envelope_kg + b.m_gondola_kg + r.m_dry_kg + r.m_prop_kg, b.V_max_m3, b.Cd * cd_scale,
                              R_gas=cfg.constants.R_He, added_mass_coeff=b.added_mass_coeff,
                              leak_dp_pa=b.envelope_leak_dp_pa, leak_cd=b.envelope_leak_cd)
    a0 = atmo(ground_z)
    st = Bl.BalloonState(ground_z, 0.0, Bl.launch_gas_mass(params, a0, b.free_lift_fraction * free_lift_scale, b.m_ballast_kg), b.m_ballast_kg)
    vent = 0.0
    ctl = BallastController.from_config(b)
    tp = Tk.TankParams.from_config(cfg)
    ts = Tk.initial_state(tp, cfg.tank.P0_bar * 1e5, cfg.tank.T0_c + K0)
    mp = Mt.MotorParams.from_config(cfg)
    mT, heater = cfg.motor.T0_c + K0, False
    t, phase, t_float = 0.0, "PAD", None
    out: list[dict[str, Any]] = []
    while True:
        if phase == "PAD" and t >= cfg.sim.pad_hold_s:
            phase = "ASCENT"
        if phase != "PAD":
            a_now = atmo(st.z)
            drop = ctl.command(t, st.z, st.vz, vent > 0, a_now, dt)
            st, bo = Bl.step(params, st, atmo, dt, ground_z=ground_z, ballast_drop_kg_s=drop)
            vent = bo.vent_rate_kg_s
            if phase == "ASCENT" and vent > 0:
                phase, t_float = "FLOAT", t + dt
        a = atmo(max(st.z, 0.0))
        ts, _ = Tk.step(tp, ts, a.T, a.p, a.rho, dt, 0.0)
        heater = Mt.heater_command(mp, mT, heater)
        mT = Mt.step(mp, mT, a.T, a.rho, heater, dt)
        t += dt
        if t % pc.sample_s < 1e-9:
            out.append({"met": t, "phase": phase, "east": st.east, "north": st.north, "ch": {
                "GNSS_ALT": st.z, "GNSS_VU": st.vz if phase != "PAD" else 0.0, "BARO_P": a.p / 100.0, "AIR_T": a.T - K0,
                "TANK_P": Tk.pressure(tp, ts.m, ts.T) / 1e5, "TANK_T": ts.T - K0, "MOTOR_T": mT - K0}})
        if (t_float is not None and t >= t_float + pc.horizon_after_float_s) or t >= cfg.sim.max_mission_s:
            return out


def _tol(cfg, m: str, v: float) -> float:
    tol = cfg.prediction.tolerance.to_dict()[m]
    return (tol.get("abs", 0.0) + tol.get("rel", 0.0) * abs(v)) if isinstance(tol, dict) else float(tol)


def predicted_profile(cfg, forecast: A.AtmosphereProfile) -> dict[str, Any]:
    pc = cfg.prediction
    runs = {
        "nominal": run(cfg, forecast),
        "fast": run(cfg, forecast, 1.0 + pc.free_lift_spread, 1.0 - pc.cd_spread),
        "slow": run(cfg, forecast, 1.0 - pc.free_lift_spread, 1.0 + pc.cd_spread),
    }
    n = min(len(v) for v in runs.values())
    samples = []
    for i in range(n):
        nom = runs["nominal"][i]
        lo, hi = {}, {}
        for m in PREDICTED:
            vals = [runs[k][i]["ch"][m] for k in runs]
            lo[m] = min(vals) - _tol(cfg, m, nom["ch"][m])
            hi[m] = max(vals) + _tol(cfg, m, nom["ch"][m])
        samples.append({"met": nom["met"], "phase": nom["phase"], "ch": nom["ch"], "lo": lo, "hi": hi})
    lat0, lon0 = cfg.site.lat_deg, cfg.site.lon_deg
    track = [{"met": s["met"], "lat": ll[0], "lon": ll[1], "alt": s["ch"]["GNSS_ALT"]}
             for s in runs["nominal"][::12] for ll in [geo.enu_to_latlon(s["east"], s["north"], lat0, lon0)]]
    last = runs["nominal"][n - 1]
    return {"samples": samples, "track": track, "end": {"met": last["met"], "east": last["east"], "north": last["north"],
                                                         "alt": last["ch"]["GNSS_ALT"]},
            "atmo_source": forecast.label, "atmo_note": forecast.note,
            "basis": "ground physics (balloon, tank, motor thermal, ballast controller) with the forecast atmosphere; "
                     "envelope of nominal/fast/slow ascent cases plus per-channel tolerance [ASSUMPTION]"}


def band_at(profile: dict[str, Any] | None, met: float, m: str) -> tuple[float, float, float] | None:
    """(lo, nominal, hi) at `met` by linear interpolation; None outside the profile."""
    if not profile or m not in PREDICTED:
        return None
    s = profile["samples"]
    if not s or met < s[0]["met"] or met > s[-1]["met"]:
        return None
    step = s[1]["met"] - s[0]["met"] if len(s) > 1 else 1.0
    i = min(int((met - s[0]["met"]) / step), len(s) - 2) if len(s) > 1 else 0
    a, b = s[i], s[min(i + 1, len(s) - 1)]
    k = 0.0 if b["met"] == a["met"] else (met - a["met"]) / (b["met"] - a["met"])
    f = lambda key: a[key][m] + (b[key][m] - a[key][m]) * k  # noqa: E731
    return f("lo"), (a["ch"][m] + (b["ch"][m] - a["ch"][m]) * k), f("hi")


# ----------------------------------------------------------------------------- range safety


def _segments_cross(p1, p2, q1, q2) -> bool:
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2, d3, d4 = orient(q1, q2, p1), orient(q1, q2, p2), orient(p1, p2, q1), orient(p1, p2, q2)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def polygons_intersect(a: list[list[float]], b: list[list[float]]) -> bool:
    """Polygons as [[lat, lon], ...]; True if they overlap (vertex inside or edges crossing)."""
    if any(geo.point_in_polygon(p[0], p[1], b) for p in a) or any(geo.point_in_polygon(p[0], p[1], a) for p in b):
        return True
    for i in range(len(a)):
        for j in range(len(b)):
            if _segments_cross(a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)]):
                return True
    return False


def load_range(cfg, path) -> dict[str, Any]:
    """config/range.yaml (zones as offsets from the site) -> {site, approved, zones, label} in absolute lat/lon."""
    import yaml

    with open(path, encoding="utf-8") as fh:
        rc = yaml.safe_load(fh)
    lat0, lon0 = cfg.site.lat_deg, cfg.site.lon_deg
    zones = [{"name": z["name"], "kind": z.get("kind", "restricted"),
              "polygon": [[round(lat0 + a, 6), round(lon0 + b, 6)] for a, b in z["offsets"]]} for z in rc.get("zones", [])]
    return {"site": {"lat": lat0, "lon": lon0, "name": cfg.site.name},
            "approved": {"name": "Approved impact area (config gates.approved_area_offsets_deg)", "polygon": [list(p) for p in cfg.gates.approved_area]},
            "zones": zones, "label": rc.get("label", "")}


def range_check(rng: dict[str, Any], footprint: dict[str, Any] | None, iips: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Footprint regions and IIPs must stay out of every restricted/military/no-fly zone; IIPs inside the approved area."""
    findings = []
    for z in rng["zones"]:
        for reg in (footprint or {}).get("regions", []):
            if polygons_intersect(reg["polygon"], z["polygon"]):
                findings.append({"zone": z["name"], "kind": z["kind"], "what": f"landing footprint {reg['level']} %"})
        for p in iips or []:
            if geo.point_in_polygon(p["lat"], p["lon"], z["polygon"]):
                findings.append({"zone": z["name"], "kind": z["kind"], "what": f"IIP {p['case']}"})
    outside = [p["case"] for p in iips or [] if rng["approved"] and not geo.point_in_polygon(p["lat"], p["lon"], rng["approved"]["polygon"])]
    ok = not findings and not outside
    return {"status": "PASS" if ok else "FAIL", "conflicts": findings, "iip_outside_approved": outside,
            "zones_checked": len(rng["zones"]), "label": rng["label"]}


def wind_drift_landing(predictor, end: dict[str, Any]) -> dict[str, Any]:
    fp = predictor.landing_footprint(end["east"], end["north"], end["alt"], 10.0, rocket_aboard=False)
    fp.pop("points_enu", None)
    fp["basis"] = "predicted cut-down at the end of the nominal profile (after rocket release)"
    return fp

