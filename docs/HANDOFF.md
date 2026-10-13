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
- `config/range.yaml` (was range.geojson): 4 illustrative zones as offsets from the site (not NOTAM data).
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

## Audit (Phase A, diagnosis only)

Reports in `docs/audit/`: ARCHITECTURE_AS_BUILT, FLUCTUATION_REPORT, REALITY_TABLE, ML_AUDIT,
PHYSICS_CHECK. Scripts in `tools/audit/` (api_tap, ui_tap.mjs, link_probe, fluctuation_report,
test_sender, ml_audit, physics_check). No application code changed in Phase A.

Headline: Laptop A sends TCP (port 65432) and interleaves three streams (live, a frozen packet, and
zero-time packets) over a new connection per packet; the TCP receiver keeps "latest packet wins", so
values flip by up to 18 km. The TCP path bypasses the estimator, forecast and ML, and contains hard-coded
values (TANK_P from time, rate 2.0 Hz, loss 0 %, MEASURED/NOMINAL labels, a fixed post-flight report with
pre-signed closure). The ICD/UDP path is real. Config drift: the site moved to 10.30 N but the approved
area, range zones, snapshot atmosphere and ML data did not (impact gate cannot pass; 1 test fails).

## Phase B progress

- B.0 raw capture (backend stopped 120 s, `tools/audit/link_probe.py`): 942 packets, all from
  172.16.58.215, 926 connections; frozen (MET 1813.295 s) 470, zero-time PRELAUNCH 235, live 237.
  All three arrive ON THE WIRE from Laptop A (each with its own payload size and field set, each at
  its own 2 Hz cadence): nothing inside B produces them. Sender-side cause for the user: several twin
  instances on A ping B (a PRELAUNCH one never started, a stale one at MET 1813 s sent twice per tick,
  and the current one). A's packets declare origin "Chennai Launch Pad (13.0827 N, 80.2707 E)".
- B.1 done: `aerofire/receiver/twin.py` TwinFilter (source lock, lock only on an advancing stream,
  MET-monotonic, rejects zero-time/repeat/backward/foreign, re-lock after a 10 s stall), rejected
  packets logged to `data/sessions/twin_rejected.jsonl`, measured rate/age; removed the merge hack,
  the Point Calimere re-projection, the LEO fixture and the inject/reset endpoints.
- B.2 done: accepted twin packets -> `twin_channels` (A's own WGS-84 position, baro, air T, tilt
  from the accelerometer, gyro rates, attitude, balloon volume/superpressure, raw phase + mapped
  FC_PHASE, rocket status) -> unsigned ICD-shaped frame (seq = MET slot) -> GroundSession: fused
  state, gates, alarms, pre-flight, forecasts/ML, post-flight, session recording. Link loss/gaps from
  MET slots; integrity shown as "none (unsigned twin protocol)"; origin mismatch alarm. Live check:
  Laptop A is currently holding at MET 3544.4 s (SBRL_RELEASE_WINDOW) and not advancing, so B
  correctly shows no lock (all packets rejected as not advancing).
- B.3 done: single site = Laptop A's declared origin (Chennai Launch Pad 13.0827 N, 80.2707 E).
  `aerofire/config.py derive_site()` derives `gates.approved_area` (from `approved_area_offsets_deg`) and
  the ground-station position; `config/range.yaml` holds zones as site offsets (replaces range.geojson);
  `tools/record_atmosphere_snapshot.py` re-recorded the Open-Meteo snapshot for the site (13 Oct);
  demo sessions re-recorded (nominal reaches GO again). Full backend suite: 207 passed.
- B.4 done: frontend reads only the pipeline (`frontend/src/ground/live.ts`: channel/estimate readings with
  status, absent = UNKNOWN). Removed: store.ts synthesis of history rows from the raw packet (TANK_P from MET,
  MOTOR_T -25, rec GO, raw labelled FUSED, Date.now() MET), Overview sticky latch + DEFAULT_TELEMETRY +
  mass/propellant defaults (Overview rebuilt on channels/estimates + twin-stream diagnostics + labelled RAW
  packet), Flight/Trajectory twin branches and the 37.1/-6.733 and Point Calimere constants, MissionLiveMap
  home default, the Post-flight hard-coded debrief (apogee, duration, splashdown, 100 % quality). EVENTS page
  restored (key 6). Post-flight code from 87458be now tested (tests/test_postflight_receiver.py: recovery,
  tamper, report hash, grading isolation, exports, API, twin recovery refused). Backend: 212 passed.

## Status

- Next exact step: B.5 UI performance (profile the 4.7 s stall; fixed 2-5 Hz render from the latest state),
  then raw + fused side by side and empty/error states.
