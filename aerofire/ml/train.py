"""Train the residual models (offline): truth - physics for altitude, east, north at 30/60/120 s.

    python -m aerofire.ml.train

HistGradientBoostingRegressor per (target, horizon); hyper-parameters from a 4-combination grid
scored by GroupKFold (3 folds, grouped by mission) on every 4th training row (time budget), final
fit on all training rows; fixed random_state. Writes models/traj_forecaster_v1.joblib and its JSON card (features, seeds, data
hash, p1/p99 training ranges for the runtime range check, sklearn version).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold

from . import features as F

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "ml"
MODELS = ROOT / "models"
NAME = "traj_forecaster_v1"
SEED = 7
GRID = [
    {"learning_rate": 0.1, "max_leaf_nodes": 15},
    {"learning_rate": 0.1, "max_leaf_nodes": 31},
    {"learning_rate": 0.05, "max_leaf_nodes": 31},
    {"learning_rate": 0.05, "max_leaf_nodes": 63},
]
CV_STRIDE = 4
COMMON = {"max_iter": 300, "early_stopping": True, "validation_fraction": 0.1, "n_iter_no_change": 15, "random_state": SEED}


def load() -> dict[str, np.ndarray]:
    with np.load(DATA / "dataset.npz") as z:
        return {k: z[k] for k in z.files}


def main(argv: list[str] | None = None) -> int:
    t0 = time.time()
    d = load()
    manifest = json.loads((DATA / "dataset.manifest.json").read_text(encoding="utf-8"))
    train = d["split_test"] == 0
    models, card_models = {}, {}
    for h in manifest["horizons_s"]:
        m_h = train & (d["h"] == h)
        X, groups = d["X"][m_h], d["seed"][m_h]
        for k, tgt in enumerate(F.TARGETS):
            y = d["truth"][m_h, k] - d["phys"][m_h, k]
            scores = []
            Xc, yc, gc = X[::CV_STRIDE], y[::CV_STRIDE], groups[::CV_STRIDE]
            for params in GRID:
                fold = []
                for tr, va in GroupKFold(n_splits=3).split(Xc, yc, gc):
                    mdl = HistGradientBoostingRegressor(**COMMON, **params).fit(Xc[tr], yc[tr])
                    fold.append(float(np.mean(np.abs(yc[va] - mdl.predict(Xc[va])))))
                scores.append(float(np.mean(fold)))
            best = GRID[int(np.argmin(scores))]
            mdl = HistGradientBoostingRegressor(**COMMON, **best).fit(X, y)
            models[f"{tgt}@{h}"] = mdl
            card_models[f"{tgt}@{h}"] = {"params": best, "cv_mae_m": dict(zip([json.dumps(g) for g in GRID], scores)),
                                         "cv_physics_mae_m": float(np.mean(np.abs(y))), "iterations": int(mdl.n_iter_)}
            print(f"{tgt}@{h}s: best {best} CV MAE {min(scores):.1f} m (physics {np.mean(np.abs(y)):.1f} m)", flush=True)
    Xtr = d["X"][train]
    card = {
        "name": NAME, "created_utc": time.time(), "train_seconds": round(time.time() - t0, 1),
        "sklearn": sklearn.__version__, "numpy": np.__version__, "random_state": SEED,
        "features": list(F.FEATURES), "targets": list(F.TARGETS), "horizons_s": manifest["horizons_s"], "phases": list(F.PHASES),
        "label": manifest["label"], "train_seeds": manifest["train_seeds"], "test_seeds": manifest["test_seeds"],
        "data_sha256": manifest["data_sha256"], "rows_train": int(train.sum()),
        "feature_p1": np.percentile(Xtr, 1, axis=0).tolist(), "feature_p99": np.percentile(Xtr, 99, axis=0).tolist(),
        "grid": GRID, "cv": f"GroupKFold(3) by mission on every {CV_STRIDE}th training row", "models": card_models,
        "evaluation": None,  # filled by aerofire.ml.evaluate (test missions only)
    }
    MODELS.mkdir(exist_ok=True)
    joblib.dump(models, MODELS / f"{NAME}.joblib", compress=3)
    (MODELS / f"{NAME}.json").write_text(json.dumps(card, indent=1), encoding="utf-8")
    print(f"trained {len(models)} models in {card['train_seconds']} s -> {MODELS / (NAME + '.joblib')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
