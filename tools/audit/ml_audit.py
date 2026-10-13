"""Audit A4: re-check the committed trajectory model on the TEST missions with an extra baseline.

    python tools/audit/ml_audit.py      (needs data/ml/dataset.npz: python -m aerofire.ml.make_dataset)

Baselines: hold (position held), constant velocity x + v*h (fused v at t), physics, hybrid (runtime fallback rule).
Mission-level bootstrap 95 % CI (1000) of MAE(baseline) - MAE(hybrid). Prints a markdown table.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aerofire.ml import features as F  # noqa: E402
from aerofire.ml.predictor import HybridPredictor  # noqa: E402


def ci(a_base, a_hyb, groups, rng):
    ids = np.unique(groups)
    idx = [np.flatnonzero(groups == g) for g in ids]
    sb, sh, n = (np.array([a[i].sum() for i in idx]) for a in (a_base, a_hyb)), None, None
    sb, sh = list(sb)
    n = np.array([len(i) for i in idx])
    d = rng.integers(0, len(ids), (1000, len(ids)))
    diff = (sb[d].sum(1) - sh[d].sum(1)) / n[d].sum(1)
    return float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))


def main() -> int:
    with np.load(ROOT / "data" / "ml" / "dataset.npz") as z:
        d = {k: z[k] for k in z.files}
    man = json.loads((ROOT / "data" / "ml" / "dataset.manifest.json").read_text(encoding="utf-8"))
    hp = HybridPredictor()
    card = hp.card
    print(f"model {card['name']} sklearn {card['sklearn']} data hash card {card['data_sha256'][:16]} / regenerated {man['data_sha256'][:16]} "
          f"-> {'REPRODUCED' if card['data_sha256'] == man['data_sha256'] else 'DIFFERENT DATA'}")
    test = d["split_test"] == 1
    assert not set(d["seed"][test]) & set(d["seed"][~test]), "train/test missions overlap"
    rng = np.random.default_rng(3)
    fi = {n: i for i, n in enumerate(F.FEATURES)}
    print("| target @ h | hold MAE | x+v*h MAE | physics MAE | hybrid MAE | CI (x+v*h - hybrid) m | CI (physics - hybrid) m |")
    print("|---|---|---|---|---|---|---|")
    for h in card["horizons_s"]:
        m = test & (d["h"] == h)
        X, tr, ph, pe, g = d["X"][m], d["truth"][m], d["phys"][m], d["persist"][m], d["seed"][m]
        oor = np.array([bool(hp.out_of_range(x)) for x in X])
        vel = np.column_stack([X[:, fi["vz"]], X[:, fi["ve"]], X[:, fi["vn"]]])
        cv = pe + vel * h
        for k, t in enumerate(F.TARGETS):
            hyb = ph[:, k] + np.where(oor, 0.0, hp.models[f"{t}@{h}"].predict(X))
            e = {n: np.abs(tr[:, k] - v) for n, v in (("hold", pe[:, k]), ("cv", cv[:, k]), ("phys", ph[:, k]), ("hyb", hyb))}
            c1, c2 = ci(e["cv"], e["hyb"], g, rng), ci(e["phys"], e["hyb"], g, rng)
            print(f"| {t}@{h} s | {e['hold'].mean():.1f} | {e['cv'].mean():.1f} | {e['phys'].mean():.1f} | {e['hyb'].mean():.1f} | "
                  f"{c1[0]:.1f} .. {c1[1]:.1f} | {c2[0]:.1f} .. {c2[1]:.1f} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
