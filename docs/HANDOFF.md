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

- Step 1: done (`8c1a303`). Step 2: done. pytest 173 passed.
- Next exact step: Step 3, the console UI shell (top status bar, left nav, alarm line,
  keys 1-6/A) over `WS /ws/telemetry`, and the FLIGHT page (telemetry table, uPlot strip
  charts, fused state, gates + GO/NO-GO poll, link panel). Add `uplot`; replace the old
  Flight view; Playwright smoke per page using `backend.py --playback`.
