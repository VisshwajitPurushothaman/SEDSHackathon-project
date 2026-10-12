"""Monte Carlo evaluation of the ground leak detector (simulator side: it needs truth).

The true tank trajectory depends only on the leak size and the flight (no
randomness), so each leak size is simulated once along a nominal balloon
flight; sensor noise is then drawn per seed and the detector's window test is
run vectorised over all seeds (`window_tstats`, same rule as the streaming
`LeakDetector`).

Run:  python -m aerofire.sim.leak_mc
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

from ..config import load_config
from ..ground.leak_detector import window_tstats
from ..physics import atmosphere as A
from ..physics import balloon as Bl
from ..physics import tank as tk
from .float_control import BallastController

SNAPSHOT = Path(__file__).resolve().parents[2] / "config" / "atmosphere_snapshot.json"


@dataclass
class Flight:
    t: np.ndarray
    T_amb: np.ndarray
    p_amb: np.ndarray
    rho: np.ndarray
    t_float: float  # first time the balloon reached full volume


def nominal_flight(cfg, duration_s: float, atmo=None) -> Flight:
    atmo = atmo or A.load_snapshot(str(SNAPSHOT), cfg.atmosphere.levels_hpa).at
    b, r = cfg.balloon, cfg.rocket
    params = Bl.BalloonParams(b.m_envelope_kg + b.m_gondola_kg + r.m_dry_kg + r.m_prop_kg, b.V_max_m3, b.Cd, R_gas=cfg.constants.R_He)
    s = Bl.BalloonState(cfg.site.elevation_m, 0.0, Bl.launch_gas_mass(params, atmo(cfg.site.elevation_m), b.free_lift_fraction, b.m_ballast_kg), b.m_ballast_kg)
    ctl = BallastController.from_config(b)
    dt = cfg.sim.dt_s
    n = int(duration_s / dt) + 1
    T, p, rho = np.empty(n), np.empty(n), np.empty(n)
    t_float = math.inf
    venting = False
    for i in range(n):
        a = atmo(s.z)
        T[i], p[i], rho[i] = a.T, a.p, a.rho
        drop = ctl.command(i * dt, s.z, s.vz, venting, a, dt)
        s, o = Bl.step(params, s, atmo, dt, ballast_drop_kg_s=drop)
        venting = o.vent_rate_kg_s > 0
        if venting and t_float == math.inf:
            t_float = (i + 1) * dt
    return Flight(np.arange(n) * dt, T, p, rho, t_float)


def tank_truth(cfg, flight: Flight, d_um: float) -> tuple[np.ndarray, np.ndarray]:
    """True (P, T) of the tank along the flight for a puncture of d_um (present from launch)."""
    prm = tk.TankParams.from_config(cfg)
    s = tk.initial_state(prm, cfg.tank.P0_bar * 1e5, cfg.tank.T0_c + 273.15)
    area = tk.diameter_to_area(d_um * 1e-6)
    dt = cfg.sim.dt_s
    P = np.empty(len(flight.t))
    T = np.empty(len(flight.t))
    for i in range(len(flight.t)):
        P[i], T[i] = tk.pressure(prm, s.m, s.T), s.T
        s, _ = tk.step(prm, s, flight.T_amb[i], flight.p_amb[i], flight.rho[i], dt, area)
    return P, T


@dataclass
class LeakResult:
    d_um: float
    seeds: int
    detection_rate_before_float: float
    detection_rate_mission: float
    median_detect_time_s: float | None
    median_size_error_pct: float | None
    false_windows: float  # mean count of significant windows per seed (sealed only meaningful)


def evaluate(cfg, flight: Flight, d_um: float, seeds: int, rng_seed: int = 1, batch: int = 250) -> LeakResult:
    P, T = tank_truth(cfg, flight, d_um)
    sc, dc = cfg.sensors, cfg.leak_detector
    prm = tk.TankParams.from_config(cfg)
    rng = np.random.default_rng(rng_seed)
    parts_b, parts_t = [], []
    for start in range(0, seeds, batch):
        k = min(batch, seeds - start)
        P_meas = P[None, :] + rng.normal(0.0, sc.tank_P_sigma_bar * 1e5, (k, len(P)))
        T_meas = T[None, :] + rng.normal(0.0, sc.tank_T_sigma_k, (k, len(T)))
        Z = 1.0 + prm.B2 * P_meas / (tk.R_U * T_meas)
        m_hat = P_meas * prm.V / (Z * prm.R * T_meas)
        t_ends, b_k, t_k = window_tstats(flight.t, m_hat, dc.window_s, dc.stride_s)
        parts_b.append(b_k)
        parts_t.append(t_k)
    slopes, tstats = np.vstack(parts_b), np.vstack(parts_t)
    n_win = int(round(dc.window_s / cfg.sim.dt_s)) + 1
    crit = stats.t.ppf(1.0 - dc.alpha, n_win - 2)
    sig = tstats < -crit
    # N consecutive significant windows
    run = np.zeros(seeds, dtype=int)
    det_t = np.full(seeds, np.inf)
    det_slope = np.full(seeds, np.nan)
    for j, te in enumerate(t_ends):
        run = np.where(sig[:, j], run + 1, 0)
        newly = (run >= dc.consecutive) & np.isinf(det_t)
        det_t[newly] = te
        det_slope[newly] = slopes[newly, j]
    detected = np.isfinite(det_t)
    before_float = detected & (det_t <= flight.t_float)
    med_t = float(np.median(det_t[detected])) if detected.any() else None
    size_err = None
    if detected.any() and d_um > 0:
        idx = np.clip((det_t[detected] / cfg.sim.dt_s).astype(int), 0, len(P) - 1)
        d_est = [
            tk.area_to_diameter(tk.hole_area_from_flow(prm, -b, P[i], T[i])) * 1e6
            for b, i in zip(det_slope[detected], idx)
        ]
        size_err = float(np.median(np.abs(np.asarray(d_est) / d_um - 1.0)) * 100.0)
    return LeakResult(
        d_um,
        seeds,
        float(before_float.mean()),
        float(detected.mean()),
        med_t,
        size_err,
        float(sig.sum(axis=1).mean()),
    )


def min_detectable_before_float(cfg, flight: Flight, seeds: int = 100, target: float = 0.9) -> tuple[float, list[tuple[float, float]]]:
    """Smallest hole (um, 1 um resolution in the search) detected before float in >= target of seeds."""
    curve = []
    lo, hi = 1.0, 100.0
    while hi - lo > 1.0:
        mid = round(0.5 * (lo + hi))
        rate = evaluate(cfg, flight, mid, seeds, rng_seed=7).detection_rate_before_float
        curve.append((mid, rate))
        if rate >= target:
            hi = mid
        else:
            lo = mid
    return hi, sorted(curve)


def main() -> None:
    cfg = load_config()
    flight = nominal_flight(cfg, 5600 + 3600)
    print(f"Nominal flight: float (full volume) reached at t = {flight.t_float:.0f} s; analysis span {flight.t[-1]:.0f} s")
    for n in (200, 2000):
        sealed = evaluate(cfg, flight, 0.0, n)
        k = round(sealed.detection_rate_mission * n)
        upper = stats.beta.ppf(0.95, k + 1, n - k) * 100  # one-sided 95 % Clopper-Pearson upper bound
        print(f"Sealed, {n} seeds: false alarms {k}/{n} = {sealed.detection_rate_mission * 100:.2f} % "
              f"(95 % upper bound {upper:.2f} %); mean significant windows per mission = {sealed.false_windows:.2f}")
    print(f"{'hole um':>8} {'det. before float':>18} {'det. in mission':>16} {'median t det (s)':>17} {'median |size err|':>18}")
    for d in (20, 50, 100, 300):
        r = evaluate(cfg, flight, d, 200)
        print(f"{d:8.0f} {r.detection_rate_before_float * 100:17.1f}% {r.detection_rate_mission * 100:15.1f}% "
              f"{r.median_detect_time_s if r.median_detect_time_s is not None else float('nan'):17.0f} "
              f"{r.median_size_error_pct if r.median_size_error_pct is not None else float('nan'):17.1f}%")
    dmin, curve = min_detectable_before_float(cfg, flight)
    print(f"Minimum leak detected before float in >= 90 % of 100 seeds: {dmin:.0f} um   (search: {curve})")


if __name__ == "__main__":
    main()
