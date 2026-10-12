# AERO-FIRE: Step 0 audit

Date: 2026-10-12. Scope: the whole repository at commit `65b2ad3` plus the uncommitted `frontend/` (the pre-flight console). Method: read every file, ran the backend (`python backend.py`) and frontend (`npm run dev`), ran the flight engine directly for a sweep of leak sizes with both the live and the fallback atmosphere, probed the Open-Meteo API, and timed the API endpoints. Every claim below has the command or number that backs it.

## 1. What exists

| Path | What it is | State |
|---|---|---|
| `backend.py` | FastAPI app (260 lines): atmosphere fetch, flight engine, `POST /api/simulate`, `GET /api/stream` (SSE), `GET /api/health` | Runs. Physics issues below. |
| `tempCodeRunnerFile.py` | Older CLI copy of the same engine (VS Code "Run Code" scratch file); prints instead of serving | Dead code. Proposed for deletion (needs your OK). |
| `__pycache__/backend.cpython-312.pyc` | Compiled bytecode, **committed to git** | Should be untracked + ignored (needs your OK). |
| `frontend/` | Vite + React 18 + R3F pre-flight console (built earlier in this session) | Works against the current API; 25 unit tests pass; not yet committed. |
| tests, requirements, config, docs | none | `pytest` is not installed; no `requirements.txt`. |

Environment: Python 3.14.6 with fastapi 0.138, uvicorn 0.49, pydantic 2.13, numpy 2.5, scipy 1.18, PyYAML 6.0, httpx 0.28. Node 22.19. GPU: Intel UHD (iGPU).

## 2. Backend physics findings

Engine sweep (`execute_flight_engine`, end state of each run):

| Leak | Fallback atmosphere | Live Open-Meteo |
|---|---|---|
| 0 µm | ARMED at 25.2 km, t = 210 s, 0.00 g lost | ARMED, 0.00 g |
| 20 µm | **ARMED**, 0.74 g lost | **ARMED**, 0.74 g |
| 50 µm | **ARMED**, 4.63 g lost | **ARMED**, 4.64 g |
| 70 µm | **ARMED**, 9.08 g lost | **ARMED**, 9.09 g |
| 100 µm | ABORT at 13.56 km, 10.09 g | ABORT at 13.44 km, 10.00 g |
| 300 µm | ABORT at 1.56 km, 10.49 g | ABORT at 1.56 km |

| # | Finding | Evidence | Fix in |
|---|---|---|---|
| P1 | **Leak detector is a fixed "> 10 g lost" threshold.** Any leak that loses < 10 g before 25 km arms the gate: 50 µm (4.6 g) and even 70 µm (9.1 g) both reach ARMED. The detector reads the simulator's true gas mass (`initial_mass − current_mass`), not a measurement, so it is noise-free and also not a real detector. | sweep above | M1.3 |
| P2 | Leak size is estimated from the *average* loss rate since t = 0 with the *current* tank state; with real noise this needs a regression over a window. | `backend.py:162,174` | M1.3 |
| P3 | **Ascent rate is a constant 120 m/s** (`ascent_rate = 120.0`), about 25× a real zero-pressure balloon (~5 m/s). 25 km takes 208 s instead of about 83 min, which also suppresses every thermal and leak effect by 25×. No balloon model, no float, no venting. | `backend.py:133` | M1.2 |
| P4 | **Fallback atmosphere uses the troposphere-only formula** `z = 44330·(1 − (p/p0)^0.1903)` for every level: 10 hPa lands at **25.9 km** (ISA 1976: about 31.06 km). Its temperature profile is a hand-written piecewise fit, not ISA. | computed: 25 922 m | M1.1 |
| P5 | Altitude interpolation is **linear in p**, not ln p. | `backend.py:103` | M1.1 |
| P6 | Open-Meteo: uses **hour index 0** (00:00 UTC). At audit time that was 15 h stale. No `timezone` param. Wind is requested as `windspeed_*` and then **never used**; wind direction is not requested. | probe: `time[0] = 2026-10-12T00:00`, now 14:57 UTC | M1.1 |
| P7 | Open-Meteo levels: today the API serves all 15 requested levels (1000…10 hPa, no nulls; 10 hPa at 31 087 m). The code never checks, though, and any missing key falls into a bare `except` → **silent** fallback with no flag in the output. | probe + `backend.py:70` | M1.1 (`atmo_source`) |
| P8 | Above the top level the profile is clamped (constant T, p), no blend into a standard atmosphere. | `backend.py:97` | M1.1 |
| P9 | Tank thermal model `dT = −0.002·(T − T_amb)·dt` has no physical basis (no h, A, c_v; no blowdown cooling). Ideal gas at 200 bar overstates He mass by about 10 % (Z ≈ 1.1), unlabelled. Choked-flow equation itself is correct; the choked condition is never checked. | `backend.py:145–158` | M1.3 |
| P10 | Motor temperature has a non-physical floor `max(12.0, …)`. No radiation term, no heater, no qualification limit. | `backend.py:146` | M1.4 |
| P11 | **Tilt is a scripted cosine** `3.2·cos(0.53·t)` (period 11.9 s, no physics, no wind). The gate tests the *instantaneous* angle, so it "passes" at the zero crossing, where the pendulum is moving fastest. | `backend.py:178,184` | M1.5 |
| P12 | Gate = 3 conditions (alt ≥ 25 km, |tilt| ≤ 1.5°, no leak). No float/vertical-speed check, navigation integrity, motor temperature or impact-safety gate. | `backend.py:184` | M1.6 |
| P13 | Verdict string `IGNITION_CONFIRMED` is produced by software with no operator action (rule 6 violation). The run ends at ARMED, so nothing happens after; there is no rocket flight or descent model. | `backend.py:226` | M1.6–1.8 |
| P14 | Sensor model: none. Every output is truth, rounded. No truth/estimate separation. | whole file | M2, rule 3 |
| P15 | Constants are inline literals (V_tank 0.5 m³, Cd 0.62, 200 bar, 26 °C, 120 m/s, 13.72 N / 80.23 E); none are labelled measured/sourced/assumed. | whole file | rule 5 |

## 3. Backend API and telemetry findings

| # | Finding | Evidence | Fix in |
|---|---|---|---|
| A1 | **SSE handler blocks the event loop**: the synchronous Open-Meteo fetch runs inside the `async` generator, so every other request waits. | `/api/health` during stream start: 0.76 s vs 0.21 s idle | M2 |
| A2 | SSE has no `id:` lines and no end event; the server just closes. A plain `EventSource` reconnects and **restarts the simulation** (the pre-flight UI works around this client-side). | 0 `id:`/`event:` lines in the stream | M2 |
| A3 | Every `/api/simulate` call re-downloads the sounding (about 2.1 s per call); no caching. | timed | M1.1 (cache) |
| A4 | One flat frame schema mixing truth and verdicts; no `seq`, timestamps, sensor IDs, units, status or source. No recorder, no link model, no recovery, no estimator. | `backend.py:186` | M2 |
| A5 | No mission resource: no create/commit/hold/postflight/export. | — | M2 |
| A6 | CORS `allow_origins=["*"]` together with `allow_credentials=True` (wildcard plus credentials). Harmless for this demo, but sloppy. | `backend.py:25` | M2 |
| A7 | `uvicorn.run(..., reload=True)` in `__main__`: a dev reloader in the only run path. | `backend.py:261` | M5 |
| A8 | No tests of any kind. | — | all |
| A9 | `localhost` requests take about 210 ms on this machine (likely IPv6 `::1` tried first, then IPv4). Bind/document `127.0.0.1`. | `curl` timings | M5 |

## 4. Frontend (pre-flight) findings

| # | Finding | Fix in |
|---|---|---|
| F1 | Consumes the old flat frame schema; check thresholds are duplicated in `src/lib/checks.ts` (including the broken 10 g rule) instead of coming from backend config. | M3 |
| F2 | Values are shown with "interpolated/derived" labels but **no MEASURED/FUSED/PREDICTED/RECONSTRUCTED/UNKNOWN status**; today they are all simulator truth. | M3 |
| F3 | Operator commit is frontend-only: no backend call, no operator name, no server-side audit trail. | M2/M3 |
| F4 | Uses the `IGNITION_CONFIRMED` verdict in types, logic and tests. | M1.6/M3 |
| F5 | Playback speed hard-codes 40 ms per mission second, sized for a 210 s run. With real ascent physics (about 80 min), 1× would take 3.4 min, so it needs a proper time-scale. | M3 |
| F6 | **Venting visual: the bug as described was not reproduced.** The only gas emitter is `VentJet` (`scene/VentJet.tsx`), attached to the **rocket's pressurant tank** and gated on `leak_detected`; on a sealed run it never fires, and nothing reacts to tank pressure. But two related things *are* wrong: (a) it is driven by the backend's broken 10 g detector (P1); (b) there is **no balloon venting visual at all**, though a zero-pressure balloon vents through its base ducts whenever it is at full volume (on reaching float, on overshoot, and when the gas warms). If you saw gas on a sealed run, tell me where and I'll look again. M3 ties every emitter to an explicit backend field. | M3 |
| F7 | Fixtures (`src/data/fixtures.ts`) are old-physics output (120 m/s); they become invalid after M1 and must be regenerated. | M5 |
| F8 | The 24-check browser test script lives in a scratch folder, not the repo. | M5 |

## 5. Plan and design decisions

Target layout:

```
config/mission.yaml          every constant, each tagged measured | source: … | ASSUMPTION
aerofire/
  config.py                  typed loader for mission.yaml
  physics/                   pure models: atmosphere, balloon, tank, motor_thermal, pendulum, gates, rocket, descent
  sim/                       TRUTH simulator: integrates physics, emits truth state + raw sensor samples
  telemetry/                 schema, sensors (noise/dropouts), recorder (HMAC chain), link emulator
  ground/                    ESTIMATES: leak detector, Kalman filter + integrity, particle predictor, gate evaluation
  api/                       FastAPI app: legacy endpoints + /api/missions/*
backend.py                   thin entry point (keeps `python backend.py` working)
tests/                       pytest; includes an ast-based firewall test: aerofire.ground/api-ground must not import aerofire.sim
docs/                        AUDIT, PHYSICS, API, LIMITS
```

Decisions to flag now:
- **Truth firewall boundary:** `aerofire/ground` may import `aerofire/physics` (models such as the choked-flow equation are needed to invert a slope into a hole size), but never `aerofire/sim` (the truth state). The ast test enforces exactly that.
- **`atmo_source` label:** Open-Meteo's forecast endpoint does not return the model run time, so the label will be `open-meteo@<valid hour UTC>` plus the fetch time, not the run time. This is a documented limitation.
- **Legacy endpoints** (`/api/simulate`, `/api/stream`) keep their URLs and frame fields so the pre-flight UI keeps working, but they will be fed by the new physics and the ground-side detector. Visible consequences: runs become about 80 min of mission time, and `IGNITION_CONFIRMED` becomes `GO_RECOMMENDED` (the pre-flight UI is updated in the same milestone).
- **Python tooling:** a project-local `.venv` with `requirements.txt` / `requirements-dev.txt`, to keep your global Python clean.
- **Git:** work happens on branch `feature/full-mission`, with one commit per milestone. Nothing is pushed without asking.

## 6. Needs your decision

1. Delete `tempCodeRunnerFile.py` (dead CLI copy of the engine)?
2. Remove the committed `__pycache__/backend.cpython-312.pyc` from git and add `__pycache__/` to `.gitignore`?
