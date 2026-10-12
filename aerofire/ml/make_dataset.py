"""Offline dataset for the hybrid physics + ML trajectory forecaster (Laptop A side: uses truth).

    python -m aerofire.ml.make_dataset            # train seeds 10000-10099, test seeds 20000-20029

Each mission: truth simulator with per-mission truth variability (config truth_variability) ->
sensors -> the real ground estimator (impact/footprint products skipped: not features). Every 5 s in
ASCENT and FLOAT the ground-side features and the physics forecast are recorded; the label is
truth(t + h) - physics(t + h) for altitude, east and north, h in {30, 60, 120} s.
Truth is used **only as the offline label**. Output: data/ml/dataset.npz + manifest JSON (gitignored).
"""
from __future__ import annotations

import hashlib
import io
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np

from ..config import load_config, load_raw
from ..ground.forecast import HORIZONS, PhysicsForecaster, state_from_estimates
from ..ground.station import GroundStation
from ..physics import atmosphere as A
from ..sim.mission import Faults, MissionSim, sample_variability
from ..telemetry.sensors import SensorSuite
from . import features as F

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "ml"
TRAIN_SEEDS = list(range(10000, 10100))
TEST_SEEDS = list(range(20000, 20030))
SAMPLE_S = 5.0
FLOAT_KEEP_S = 1200.0  # float samples recorded after float entry


class _LightGround(GroundStation):
    """The real estimator; impact points and landing footprints are skipped (not features, and slow)."""

    def _maybe_update_impact(self, now, E):  # noqa: D401
        return None

    def _maybe_update_footprint(self, now, E):
        return None


class _NoSwingSim(MissionSim):
    """Truth simulator without the gondola pendulum: swing drives only the IMU channels, which are neither
    features nor part of the trajectory. Skipping it keeps data generation within its time budget."""

    def _step_swing(self, dt):
        return None


def profile():
    raw = load_raw()
    return A.load_snapshot(str(ROOT / "config" / "atmosphere_snapshot.json"), raw["atmosphere"]["levels_hpa"], raw["atmosphere"]["blend_m"])


def one_mission(seed: int) -> dict[str, np.ndarray]:
    cfg, prof = load_config(), profile()
    var = sample_variability(cfg, np.random.default_rng(seed))
    sim = _NoSwingSim(cfg, prof, Faults(), seed=seed, variability=var)
    sensors = SensorSuite(cfg, np.random.default_rng(seed + 1), Faults())
    ground = _LightGround(cfg, prof)
    fc = PhysicsForecaster(cfg, prof)
    truth: dict[float, tuple[float, float, float]] = {}
    rows = []
    phase_start: dict[str, float] = {}
    float_at = None
    while sim.t < cfg.sim.max_mission_s:
        tr = sim.step()
        truth[tr.t] = (tr.z, tr.east, tr.north)
        for s in sensors.sample(tr):
            ground.ingest(dict(s, rx={"rssi_dbm": None, "arrival_time": tr.t}))
        if tr.t % SAMPLE_S:
            continue
        fr = ground.frame(tr.t)
        ph = fr["vehicle_phase"]
        phase_start.setdefault(ph, tr.t)
        if ph == "FLOAT" and float_at is None:
            float_at = tr.t
        if float_at is not None and tr.t > float_at + FLOAT_KEEP_S + max(HORIZONS):
            break
        if ph not in F.PHASES:
            continue
        st = state_from_estimates(fr["estimates"])
        if st is None or st["status"] != "FUSED":
            continue
        path = fc.path(ph, st["z"], st["vz"], st["east"], st["north"], horizon_s=max(HORIZONS))
        at_h = fc.at_horizons(path)
        wind = fc.wind(st["z"])
        for h in HORIZONS:
            p = at_h[h]
            rows.append((tr.t, h, 1.0 if ph == "FLOAT" else 0.0, F.build(st, ph, tr.t - phase_start[ph], p, wind, fc.z_float),
                         (p["z"], p["east"], p["north"]), (st["z"], st["east"], st["north"])))
    out = {k: [] for k in ("t", "h", "is_float", "X", "phys", "persist", "truth")}
    for t, h, isf, x, phys, pers in rows:
        tt = truth.get(t + h)
        if tt is None:
            continue
        out["t"].append(t), out["h"].append(h), out["is_float"].append(isf), out["X"].append(x)
        out["phys"].append(phys), out["persist"].append(pers), out["truth"].append(tt)
    res = {k: np.asarray(v, dtype=float) for k, v in out.items()}
    res["seed"] = np.full(len(res["t"]), seed, dtype=float)
    res["variability"] = np.array([var[k] for k in sorted(var)], dtype=float)
    return res


def main(argv: list[str] | None = None) -> int:
    t0 = time.time()
    seeds = TRAIN_SEEDS + TEST_SEEDS
    with mp.Pool(processes=max(1, mp.cpu_count() or 2)) as pool:
        parts = pool.map(one_mission, seeds, chunksize=1)
    data = {k: np.concatenate([p[k] for p in parts]) for k in ("t", "h", "is_float", "X", "phys", "persist", "truth", "seed")}
    data["split_test"] = np.isin(data["seed"], TEST_SEEDS).astype(float)
    OUT.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    np.savez_compressed(buf, **data)
    blob = buf.getvalue()
    (OUT / "dataset.npz").write_bytes(blob)
    cfg_raw = load_raw()
    manifest = {
        "created_utc": time.time(), "seconds": round(time.time() - t0, 1), "rows": int(len(data["t"])),
        "train_seeds": [TRAIN_SEEDS[0], TRAIN_SEEDS[-1]], "test_seeds": [TEST_SEEDS[0], TEST_SEEDS[-1]],
        "rows_train": int((data["split_test"] == 0).sum()), "rows_test": int((data["split_test"] == 1).sum()),
        "features": list(F.FEATURES), "targets": list(F.TARGETS), "horizons_s": list(HORIZONS), "phases": list(F.PHASES),
        "sample_s": SAMPLE_S, "label": "truth(t+h) - physics(t+h), metres (altitude, east, north)",
        "truth_variability": cfg_raw["truth_variability"], "atmosphere": "config/atmosphere_snapshot.json (forecast = truth before variability)",
        "variability_by_seed": {int(s): p["variability"].tolist() for s, p in zip(seeds, parts)},
        "variability_keys": sorted(["wind_scale", "wind_dir_offset_deg", "cd_scale", "superheat_k", "payload_error_frac"]),
        "data_sha256": hashlib.sha256(blob).hexdigest(),
    }
    (OUT / "dataset.manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"dataset: {manifest['rows']} rows ({manifest['rows_train']} train / {manifest['rows_test']} test) "
          f"from {len(seeds)} missions in {manifest['seconds']} s -> {OUT / 'dataset.npz'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
