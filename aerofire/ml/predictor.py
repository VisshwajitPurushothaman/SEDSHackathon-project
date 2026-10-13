"""Runtime hybrid forecast (ground side): physics forecast + learned residual correction.

Never raises into the caller. Falls back to the physics forecast when the model file is missing
(MISSING_MODEL), a feature lies outside its training range (OUT_OF_RANGE: beyond p1/p99 widened by
RANGE_MARGIN of the span), the phase was not trained (PHASE_NOT_TRAINED) or prediction fails
(EXCEPTION). All outputs are PREDICTED. Imports no simulator code (tests/test_firewall.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import features as F

MODEL_DIR = Path(__file__).resolve().parents[2] / "models"
NAME = "traj_forecaster_v1"
RANGE_MARGIN = 0.25


class HybridPredictor:
    def __init__(self, model_dir: Path | str = MODEL_DIR, name: str = NAME):
        self.card: dict[str, Any] | None = None
        self.models: dict[str, Any] | None = None
        self.load_error: str | None = None
        try:
            import joblib

            self.card = json.loads((Path(model_dir) / f"{name}.json").read_text(encoding="utf-8"))
            self.models = joblib.load(Path(model_dir) / f"{name}.joblib")
            p1, p99 = np.asarray(self.card["feature_p1"]), np.asarray(self.card["feature_p99"])
            span = np.maximum(p99 - p1, 1e-9)
            self.lo, self.hi = p1 - RANGE_MARGIN * span, p99 + RANGE_MARGIN * span
        except Exception as exc:  # missing file, version mismatch, corrupt pickle
            self.card, self.models, self.load_error = None, None, f"{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self.models is not None

    def default_enabled(self, target: str, h: int) -> bool:
        """Hybrid is the default display only where the test-mission CI of the improvement excludes 0."""
        ev = (self.card or {}).get("evaluation") or {}
        return bool(ev.get("default_enabled", {}).get(f"{target}@{h}", False))

    def out_of_range(self, x: list[float]) -> list[str]:
        a = np.asarray(x)
        bad = (a < self.lo) | (a > self.hi)
        return [F.FEATURES[i] for i in np.flatnonzero(bad)]

    def correct(self, phase: str, x: list[float], h: int) -> dict[str, Any]:
        """{'status': 'OK'|fallback reason, 'delta': {target: metres} | None, 'out_of_range': [...]}"""
        if not self.available:
            return {"status": "MISSING_MODEL", "delta": None, "detail": self.load_error}
        if phase not in F.PHASES:
            return {"status": "PHASE_NOT_TRAINED", "delta": None}
        bad = self.out_of_range(x)
        if bad:
            return {"status": "OUT_OF_RANGE", "delta": None, "out_of_range": bad}
        try:
            X = np.asarray([x])
            return {"status": "OK", "delta": {t: float(self.models[f"{t}@{h}"].predict(X)[0]) for t in F.TARGETS}}
        except Exception as exc:
            return {"status": "EXCEPTION", "delta": None, "detail": f"{type(exc).__name__}: {exc}"}

    def summary(self) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "name": NAME, "error": self.load_error}
        c = self.card
        return {"available": True, "name": c["name"], "sklearn": c["sklearn"], "train_seeds": c["train_seeds"],
                "test_seeds": c["test_seeds"], "data_sha256": c["data_sha256"][:16], "evaluation": c.get("evaluation"),
                "label": (((c.get("evaluation") or {}).get("label")) or "experimental, marginal gain") + " · simulator data only"}
