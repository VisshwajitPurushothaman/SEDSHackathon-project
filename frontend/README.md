# AERO-FIRE Flight Director (frontend)

Mission-control console for the AERO-FIRE rockoon backend (`../backend.py`). It plays the backend's telemetry and shows every pre-flight / launch-commit check as motion on a procedural 3D vehicle.

## Run

```bash
# terminal 1: backend (port 8000)
python backend.py

# terminal 2: frontend
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Set `VITE_API_URL` (see `.env.example`) if the backend is not on `http://localhost:8000`. If `/api/health` does not answer, the console shows **Backend offline · demo data** and plays the recorded fixtures (Sealed and 100 µm only).

```bash
npm test             # Vitest: interpolation, check derivation, events, verdicts
npm run build        # type-check + production build
```

## How it works

- **Playback, not frame-to-frame.** The backend emits ~1 frame per 3 km. `lib/playback.ts` runs a mission clock (1 mission s = 40 ms at 1×) and interpolates numeric fields linearly by `time_s`. Booleans and strings switch at the frame where they change.
- **Everything is derived from `(frames, playhead)`.** `lib/checks.ts` turns the frame at the playhead into check states, using only backend thresholds (10 g leak flag, 25 km, |tilt| ≤ 1.5°). Scrubbing in either direction is therefore exact. The 5 g caution colour is display-only and labelled as such.
- **Live stream** (`api/client.ts`) closes the `EventSource` itself after a terminal frame, or on the first error after any frame, so the browser never reconnects and restarts the run.
- **Operator commit is frontend-only.** The backend can only recommend GO (gate ARMED). Ignition needs a 1.5 s hold. Nothing is sent to the backend.
- On screen, `≈` marks interpolated values, and **derived** marks values computed in the frontend (ascent rate, the 25 km crossing time).

## Keyboard

`Space` play/pause · `←`/`→` scrub 5 s (`Shift` = 25 s) · `R` restart

## Layout

```
src/
  api/        client.ts (health, simulate, SSE stream), types.ts
  data/       fixtures.ts (real backend output for offline mode)
  lib/        playback.ts, checks.ts (+ *.test.ts)
  store/      useMission.ts (zustand)
  scene/      procedural three.js vehicle, sky, rail, particles, camera director
  components/ panels, gate, hold-to-commit, timeline dock, event log
  theme/      tokens.ts (single colour source for Tailwind and the scene)
```
