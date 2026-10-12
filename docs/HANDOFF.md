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

## Run

```bash
.venv/Scripts/python backend.py                    # API 127.0.0.1:8000
cd frontend && npm run dev                         # localhost:5173
.venv/Scripts/python -m pytest -q | tail -5
cd frontend && npx tsc -b --noEmit && npx vitest run
```

## Status

- Step 1: done. pytest 161 passed; vitest 5 passed; tsc and `npm run build` pass.
  The Flight window now attaches to an existing mission (mission picker) instead of
  starting scenarios; Post-flight browser spec kept (it creates missions via the API).
- Next exact step: Step 2.1 write `docs/ICD.md` (ICD v1), then `config/channels.yaml`,
  `sender/` (Laptop A), `aerofire/receiver/` (Laptop B), playback, tests.
