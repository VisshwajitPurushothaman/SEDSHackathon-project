# ML audit (audit A4, 13 Oct 2026)

Search: `rg -n "sklearn|joblib|pickle|torch|tensorflow|keras|xgboost|lightgbm|\.pkl|\.joblib|\.h5|\.pt"` finds one model.

## `models/traj_forecaster_v1` (hybrid trajectory forecast residuals)

| Question | Answer | Evidence |
|---|---|---|
| File exists, loads | yes: 9 `HistGradientBoostingRegressor` (altitude/east/north × 30/60/120 s), 5.39 MB, created 13 Oct 04:36, sklearn 1.7.2; loads with `HybridPredictor` | `models/traj_forecaster_v1.{joblib,json}` |
| Training code | `aerofire/ml/make_dataset.py`, `train.py`, `evaluate.py` (in repo) | |
| Reproducible from HEAD | **No, not exactly**: regenerated dataset hash `95abd104…` ≠ card `91f81060…`. Cause: `87458be` moved the launch site and pad elevation in `config/mission.yaml` after training. Data generation took 83.9 s, training 119.8 s (within the 5 min / 2 min budgets) | `tools/audit/ml_audit.py` |
| Split | by mission seed: train 10000-10099, test 20000-20029; overlap asserted empty | ml_audit.py, tests/test_ml_forecast.py |
| Runtime features | ground-side only (fused state, physics forecast, forecast wind); no future data, no truth | `aerofire/ml/features.py` |
| Firewall | `aerofire.ml.features`, `aerofire.ml.predictor`, `aerofire.ground.forecast` may not import sim/sender/evaluation/mission_runner; test passes | tests/test_firewall.py |
| Fallbacks | MISSING_MODEL, OUT_OF_RANGE, PHASE_NOT_TRAINED, EXCEPTION, STATE_NOT_FUSED; output always PREDICTED | tests/test_ml_forecast.py (7 pass) |
| Responds to inputs | yes on the ICD path (perturbation ramp: physics forecast changes; hybrid falls back OUT_OF_RANGE for the unphysical synthetic ramp) | data/audit/perturbation.json |
| Used at runtime in the live demo | **No.** Laptop A sends TCP; the TCP path never reaches the estimator or the forecaster. The model only runs for ICD/UDP senders and PLAYBACK | ARCHITECTURE_AS_BUILT.md |

### Held-out test missions, data regenerated with the current config (30 missions, 121 950 rows)

Baselines: hold (position held), constant velocity x + v·h (fused velocity), physics, hybrid. CI = mission
bootstrap 95 %, 1000 resamples, of MAE(baseline) − MAE(hybrid).

| target @ h | hold MAE | x+v·h MAE | physics MAE | hybrid MAE | CI vs x+v·h (m) | CI vs physics (m) |
|---|---|---|---|---|---|---|
| alt @ 30 s | 129.5 | 8.5 | 14.7 | 2.0 | 6.4 .. 6.6 | 12.4 .. 13.1 |
| east @ 30 s | 61.9 | 7.7 | 21.2 | 6.2 | 1.0 .. 1.8 | 10.7 .. 19.5 |
| north @ 30 s | 111.2 | 8.1 | 14.8 | 5.8 | 2.1 .. 2.5 | 6.8 .. 11.4 |
| alt @ 60 s | 259.4 | 17.1 | 27.5 | 3.3 | 13.5 .. 14.0 | 23.5 .. 24.9 |
| east @ 60 s | 123.4 | 17.6 | 42.2 | 11.6 | 5.0 .. 6.8 | 21.9 .. 40.3 |
| north @ 60 s | 221.9 | 19.8 | 29.6 | 10.7 | 8.4 .. 9.6 | 13.8 .. 23.4 |
| alt @ 120 s | 520.1 | 39.0 | 51.2 | 6.4 | 31.9 .. 33.2 | 43.4 .. 46.2 |
| east @ 120 s | 244.6 | 49.3 | 84.0 | 22.6 | 23.8 .. 29.2 | 43.9 .. 81.2 |
| north @ 120 s | 441.6 | 59.8 | 59.2 | 20.9 | 37.0 .. 41.0 | 29.6 .. 47.5 |

Findings:
1. The hybrid beats every baseline on held-out simulated missions; all CIs exclude 0.
2. **`docs/ML_RESULTS.md` overstates the gain**: it compares only against "persistence" (position held) and
   the physics forecast. The constant-velocity baseline x + v·h is much stronger than the physics forecast
   (e.g. east @ 30 s: 7.7 m vs 21.2 m) because the physics path drifts with forecast wind and ignores the
   measured drift. Against x + v·h the margins are smaller (east @ 30 s: 1.0-1.8 m).
3. On the zero-variability nominal demo flight (one mission, not a test set), hybrid horizontal error was
   worse than physics (10.6 m vs 7.9 m vs truth at 30 s), altitude better (13.0 vs 17.6 m).
4. Everything is simulator data with assumed variability; nothing here is evidence about real flights.

**Verdict: VALID** (on simulator data, held-out missions, with stronger margins against the weaker
baselines than against x + v·h). Caveats to fix: not reachable from the live TCP path; model trained before
the site/elevation change; ML_RESULTS must add the x + v·h baseline; the physics forecast itself should
use the measured drift.
