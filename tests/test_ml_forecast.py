"""Hybrid physics + ML trajectory forecast: features, fallbacks, leakage, determinism, runtime, API."""
import json
import os
from pathlib import Path

import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from aerofire.config import load_config, load_raw
from aerofire.ground.forecast import HORIZONS, PhysicsForecaster
from aerofire.ml import features as F
from aerofire.ml.predictor import HybridPredictor
from aerofire.physics import atmosphere as A
from aerofire.receiver.service import Receiver, load_session

CFG = load_config()
RAW = load_raw()
PROFILE = A.load_snapshot("config/atmosphere_snapshot.json", RAW["atmosphere"]["levels_hpa"], RAW["atmosphere"]["blend_m"])
ROOT = Path(__file__).resolve().parent.parent
STATE = {"z": 12000.0, "east": -2000.0, "north": -1500.0, "vz": 4.8, "vz_mean": 4.8, "sigma_z": 2.0, "gs": 8.0, "heading": 220.0, "status": "FUSED"}


@pytest.fixture(scope="module")
def fc():
    return PhysicsForecaster(CFG, PROFILE)


def x_for(fc, phase="ASCENT", h=30, **kw):
    st = STATE | kw
    u, v = fc.wind(st["z"])  # a plausible state drifts with the (forecast) wind
    st["gs"], st["heading"] = float(np.hypot(u, v)), float(np.degrees(np.arctan2(u, v)) % 360.0)
    at = fc.at_horizons(fc.path(phase, st["z"], st["vz"], st["east"], st["north"], horizon_s=120))[h]
    return F.build(st, phase, 1000.0, at, fc.wind(st["z"]), fc.z_float)


def test_physics_forecast_phases(fc):
    asc = fc.path("ASCENT", fc.z_float - 100.0, 5.0, 0.0, 0.0, horizon_s=120)
    assert asc[0]["z"] == pytest.approx(fc.z_float - 75.0) and max(p["z"] for p in asc) == pytest.approx(fc.z_float)
    flt = fc.path("FLOAT", 27000.0, 0.8, 0.0, 0.0, horizon_s=60)
    assert all(p["z"] == 27000.0 for p in flt) and flt[-1]["east"] != 0.0  # drifts with the forecast wind
    des = fc.path("DESCENT", 20000.0, -40.0, 0.0, 0.0, horizon_s=120)
    assert des[-1]["z"] < des[0]["z"] < 20000.0
    assert set(fc.at_horizons(asc)) == set(HORIZONS)


def test_features_are_ground_side_and_fixed_length(fc):
    x = x_for(fc)
    assert len(x) == len(F.FEATURES) and all(isinstance(v, float) for v in x)
    assert x == x_for(fc)
    i = F.FEATURES.index("dve")
    assert x[i] == pytest.approx(x[F.FEATURES.index("ve")] - x[F.FEATURES.index("u_fc")])


def test_predictor_fallbacks(fc, tmp_path):
    missing = HybridPredictor(tmp_path)
    assert not missing.available and missing.correct("ASCENT", x_for(fc), 30)["status"] == "MISSING_MODEL"
    hp = HybridPredictor()
    assert hp.available, "models/traj_forecaster_v1.joblib must be committed"
    ok = hp.correct("ASCENT", x_for(fc), 30)
    assert ok["status"] == "OK" and set(ok["delta"]) == set(F.TARGETS)
    assert hp.correct("DESCENT", x_for(fc), 30)["status"] == "PHASE_NOT_TRAINED"
    far = x_for(fc, vz=40.0)
    res = hp.correct("ASCENT", far, 30)
    assert res["status"] == "OUT_OF_RANGE" and "vz" in res["out_of_range"]

    class Broken:
        def predict(self, X):
            raise RuntimeError("boom")

    hp.models = dict(hp.models, **{"z@30": Broken()})
    assert hp.correct("ASCENT", x_for(fc), 30)["status"] == "EXCEPTION"


def test_no_leakage_between_train_and_test_missions():
    card = json.loads((ROOT / "models" / "traj_forecaster_v1.json").read_text(encoding="utf-8"))
    tr, te = range(card["train_seeds"][0], card["train_seeds"][1] + 1), range(card["test_seeds"][0], card["test_seeds"][1] + 1)
    assert not set(tr) & set(te)
    assert card["features"] == list(F.FEATURES) and "truth" not in " ".join(card["features"])
    ev = card["evaluation"]
    assert ev and ev["test_missions"] == len(te) and ev["primary"] == "z@30"
    data = ROOT / "data" / "ml" / "dataset.npz"
    if data.exists():  # regenerated locally (gitignored)
        with np.load(data) as z:
            seeds, test = z["seed"], z["split_test"]
        assert set(seeds[test == 1]) <= set(te) and set(seeds[test == 0]) <= set(tr)


def test_training_is_deterministic():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 5))
    y = X[:, 0] * 3 + np.sin(X[:, 1]) + rng.normal(0, 0.1, 2000)
    from aerofire.ml.train import COMMON

    a = HistGradientBoostingRegressor(**COMMON, learning_rate=0.1, max_leaf_nodes=15).fit(X, y).predict(X[:50])
    b = HistGradientBoostingRegressor(**COMMON, learning_rate=0.1, max_leaf_nodes=15).fit(X, y).predict(X[:50])
    assert np.array_equal(a, b)


def test_live_session_forecasts_are_predicted_and_fall_back_when_not_fused():
    r = Receiver(CFG, lambda: PROFILE)
    r.mode = "PLAYBACK"
    for row in load_session("data/demo_sessions/nominal.jsonl.gz")[:900]:
        r.on_frame(row, row["rx"]["utc"])
    s = r.current
    f = s.traj_forecast
    assert f["label"] == "PREDICTED" and f["phase"] == "ASCENT" and set(f["horizons"]) == {"30", "60", "120"}
    assert all(v["status"] == "OK" and v["hybrid"] for v in f["horizons"].values())
    # LOS: the state becomes PREDICTED, the forecast falls back to physics.
    s.tick(row["rx"]["utc"] + 60.0)
    assert s.traj_forecast["state_status"] == "PREDICTED"
    assert all(v["status"] == "STATE_NOT_FUSED" and v["hybrid"] is None for v in s.traj_forecast["horizons"].values())


def test_ml_api_contract():
    os.environ["AEROFIRE_ATMOSPHERE"] = "snapshot"
    from fastapi.testclient import TestClient

    from aerofire.api.app import app

    c = TestClient(app)
    m = c.get("/api/ground/ml/model").json()
    assert m["available"] and m["evaluation"]["primary"] == "z@30" and "experimental" in m["label"]
    rep = c.get("/api/ground/ml/report")
    assert rep.status_code == 200 and rep.text.startswith("# Hybrid physics + ML trajectory forecast")
