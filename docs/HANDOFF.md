# AERO-FIRE handoff

Branch `telemetry-console` (from `feature/full-mission`). Tag `pre-telemetry-console`
= commit `9ef0b70` preserves everything removed below. Never push.

## Plan: two-laptop telemetry ground station

Laptop A (vehicle / digital twin: truth sim → sensors → recorder → UDP sender, all
scenarios/faults as CLI flags) → Wi-Fi → Laptop B (this app: receiver → decoder →
limits → Kalman/integrity → predictions → console). Steps 1–7 of the pivot brief.

## Step 1: checkpoint and cleanup

Removed from the ground-station app (recoverable from the tag):

- Legacy Pre-flight 3D console: `frontend/src/scene/*` (R3F balloon/rocket/gondola,
  particles, sky, camera director), `frontend/src/components/*` (checklist, gate,
  HUD, telemetry panel, timeline dock, top bar, hold-to-commit ignition animation,
  link overlay, ticker, charts, sounding), `frontend/src/store/useMission.ts`,
  `frontend/src/hooks/useMissionRuntime.ts`, `frontend/src/lib/{checks,playback}*`,
  `frontend/src/data/fixtures.ts`, `frontend/src/api/types.ts` and the legacy client
  functions in `frontend/src/api/client.ts` (only `API_URL` remains).
- Flight: `FlightScene.tsx` (3D view), scenario/preset picker, time-scale picker,
  "Start mission", camera modes; hold-to-commit/terminate animations.
- Browser specs that covered those: `preflight.spec.mjs`, `flight.spec.mjs`,
  `performance.spec.mjs`. Vitest: `lib/checks.test.ts`, `lib/playback.test.ts`.
- Dependencies: `three`, `@react-three/fiber`, `@react-three/drei`,
  `@react-three/postprocessing`, `postprocessing`, `framer-motion`, `@types/three`.
- `tempCodeRunnerFile.py`, tracked `__pycache__/backend.cpython-312.pyc`.

Kept: all physics, the simulator (moves to the Laptop A role in step 2), estimator,
predictor, recorder/HMAC/recovery and their tests; Flight panels (telemetry, gates,
alerts, map) and Post-flight, attaching to missions that already exist.

## Step 2: telemetry link (done)

- `docs/ICD.md` (ICD v1: UDP JSON frames, HMAC chain with the demo key, recovery
  `/manifest` + `/recover`, grading-only `/truth` on Laptop A port 8100).
- `config/channels.yaml`: dictionary with per-phase yellow/red limits (each with a basis)
  and a per-phase settle time after phase changes.
- `aerofire/telemetry/icd.py`: frames, chain, channel <-> sensor-packet conversion, limits.
- `sender/` (Laptop A): `python -m sender --target <ip> --scenario ... --speed N`; vehicle
  actions (commit/terminate) are Laptop A-side only (automatic per scenario, flags, console).
- `aerofire/receiver/` (Laptop B): verification, gaps/duplicates/reorder, rate/loss/latency,
  AOS/LOS (3 s), session recording, limit alarms + ack, GO/NO-GO poll, feeds GroundStation.
- API: `WS /ws/telemetry`, `/api/ground/*` (docs/API.md). `backend.py` listens on UDP 5600
  by default; `--playback FILE [--playback-speed]`, `--snapshot`, `--no-udp`.
- Demo sessions (snapshot atmosphere, speed 20, seed 11) in `data/demo_sessions/`:
  `nominal` (GO, vehicle commit at MET 5955, landed 9169 s) and `tank_leak_100um`
  (ABORT, terminate at MET 900, landed 1483 s). `*.vehicle.jsonl.gz` + `*.truth.json`
  are the Laptop A side (for `sender --replay` recovery/grading).
- Verified: one-machine two-process UDP run at speed 100 (about 98 frames/s, 0.83 % loss).

## Step 3: console shell and FLIGHT page (done)

- Theme: tokens remapped to the console palette (bg #05080F, panels #0B1220, lines #1A2436,
  nominal/caution/alarm/predicted/stale colours); JetBrains Mono values, Inter labels.
- `frontend/src/ground/`: store (WS 4 Hz + incremental `/api/ground/history?since=`), shell
  (status bar with mission/MET/UTC/phase/AOS-LOS/rate/loss/latency/source/REC/alarms, left nav,
  alarm line with operator name; keys 1-6, A), FLIGHT (telemetry table sortable/filterable with
  limit tint and STALE ages, uPlot strip charts with yellow/red lines and LOS shading, fused state +
  trust ladder + chi-square, gates + recorded GO/NO-GO poll, link panel), OVERVIEW, EVENTS.
  PRE-FLIGHT/TRAJECTORY/POST-FLIGHT show "not implemented yet (Step 4/5/6)".
- Backend: playback runs on a virtual clock in the recording's time base; `websockets` added
  to requirements (uvicorn needs it for `/ws/telemetry`).
- Old Flight view removed; `src/flight/{api,store,ui}.ts(x)` and `src/postflight/*` remain
  unrouted for reuse in Step 6. `postflight.spec.mjs` removed (old missions API UI);
  `console.spec.mjs` added (7 checks).

## Step 4: PRE-FLIGHT (done)

- `aerofire/ground/profile.py`: predicted telemetry profile (balloon/tank/motor/ballast physics with
  the forecast atmosphere; nominal/fast/slow envelope + tolerance from `config/mission.yaml`
  `prediction:`), band lookup, polygon intersection, range check. Ballast controller moved to
  `aerofire/physics/float_control.py` (sim re-exports it) so the ground can use it.
- `config/range.geojson`: site, approved area and 4 illustrative zones (not NOTAM data).
- `aerofire/receiver/preflight.py`: channel health on pad telemetry (present, rate +-50 %,
  pad limits on the median, not frozen), GNSS/NavIC/baro chi-square agreement, profile, range,
  waivers, readiness + SHA-256 report. Session: band fields in history rows, OFF-NOMINAL caution
  (outside band 30 s), in-flight range re-check of live footprint/IIPs.
- Demo results: nominal 32/32 PASS, READY; no OFF-NOMINAL. Leak: OFF-NOMINAL TANK_P at MET 75
  (before the leak detector at about 181 s); its 30 s pad hold is too short for pre-flight to see it.
- UI: PRE-FLIGHT page (readiness, checks + waive, profile table + band mini-charts, range map
  with MapLibre over an always-drawn lat/lon grid); Flight strip charts show the cyan band.

## Step 5: TRAJECTORY + hybrid physics/ML forecast (done)

- No earlier ML existed (Milestone 6 was never started), so the minimal version was built.
- Truth variability (simulator only): `config/mission.yaml` `truth_variability` (wind scale/offset,
  balloon Cd, gas superheat, payload mass; ASSUMPTIONS); `MissionSim(variability=...)`,
  `python -m sender --vary`. Balloon gained an optional `gas_superheat_k` (default 0 = unchanged).
- `aerofire/ground/forecast.py`: deterministic physics path (mean of the particle predictor).
- `aerofire/ml/`: `features.py` + `predictor.py` (runtime, firewalled), offline `make_dataset.py`
  (130 missions, 77 s; pendulum skipped, IMU only), `train.py` (9 HGB residual models, 99 s),
  `evaluate.py` -> `docs/ML_RESULTS.md` (test seeds 20000-20029 only). Model committed in `models/`.
- Result: hybrid beats physics for every target/horizon; altitude @30 s physics 14.5 m -> hybrid
  about 2 m MAE, improvement CI excludes 0 -> hybrid default. Simulator data only.
- Session: forecast every 5 s MET (physics to +600 s; hybrid at 30/60/120 s with fallbacks), forecast
  log for post-flight. Receiver frames are now processed in a worker thread; playback slows its
  virtual clock instead of faking LOS when processing lags.
- UI: TRAJECTORY page (map: fused/predicted track, physics + hybrid forecasts, footprint, LOS
  regions, IIPs, zones; altitude forecast chart; model card + ML toggle + live fallback status).

## Run

```bash
.venv/Scripts/python backend.py                    # API 127.0.0.1:8000
cd frontend && npm run dev                         # localhost:5173
.venv/Scripts/python -m pytest -q | tail -5
cd frontend && npx tsc -b --noEmit && npx vitest run
.venv/Scripts/python -m sender --target 127.0.0.1 --scenario nominal --speed 20   # Laptop A
.venv/Scripts/python backend.py --playback data/demo_sessions/nominal.jsonl.gz --playback-speed 10
```

## Status

- Steps 1-5 done. pytest: 194 passed + 1 fixed (attribute collision) then the two affected files
  re-run green; vitest 5; build passes; console.spec 7 passed.
- Next exact step: Step 6 POST-FLIGHT: progressive recovery from Laptop A `/recover` (every 10th
  first, HMAC verified, retries, lost list) into the session; three histories (live, RTS
  reconstruction via existing `rts_smooth`, truth via `/truth` only when grading enabled); reports
  (limit violations, predicted vs actual, requirements, fault timeline with delays, forecast
  scorecard from `session.forecast_log`, landing error vs footprint); exports (.jsonl, CSV per
  subsystem, report JSON + SHA-256) and closure checklist. Reuse ideas from `frontend/src/postflight`.
