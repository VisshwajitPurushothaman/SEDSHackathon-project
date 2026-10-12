# AERO-FIRE Flight Director frontend

The existing Vite + React 18 + TypeScript console has three hash-routed windows:
Pre-flight (`#/`), Flight (`#/flight`) and Post-flight (`#/postflight`). Three.js,
React Three Fiber, MapLibre and Recharts retain the procedural scene and dark
mission-control design. Colour and font tokens remain in `src/theme/tokens.ts`.

See the [root README](../README.md) for installation, the one-command Windows/
Linux runner, all five demo presets and the complete mission workflow.

```bash
npm ci
npm run dev          # localhost:5173; API defaults to 127.0.0.1:8000
npm test
npm run typecheck
npm run build
```

Set `VITE_API_URL` when using a different API port. The example environment file
uses the same IPv4 address as the backend. No credentials are required by this
local simulator.

Pre-flight replay interpolates numeric fields by mission time, with 2 ms per
mission second at 1× (500× real time). Discrete fields switch at their recorded
epoch. The backend sends about one legacy frame every 60 mission seconds and
on recommendation changes. Limits are the 26–30 km release band, swing amplitude
≤ 1.5°, motor ≥ −10 °C, navigation/impact gates and the ground leak detector;
the 5 g caution colour is display-only. The local hold-to-commit animation is
kept separate from the Flight mission API.

Flight receives status-labelled ground frames over resumable SSE and keeps an
append-only live history. Commands act only in live mode and require an operator
name. The balloon radius follows measured volume; the base-duct plume follows
ground-confirmed venting above 3σ; the tank jet follows the leak detector. No
envelope-leak jet is shown because that truth field is absent from operator
telemetry. The rocket path follows the existing illustrative simulation.

Post-flight uses recovered records for reconstruction and requirements. Grading
adds a third history and scorecard only when enabled. JSON downloads preserve
the server response text so the report's canonical SHA-256 remains verifiable.

Backend-down fixtures support Pre-flight Sealed and 100 µm runs. Flight and
Post-flight require backend mission records; use the root runner's `--snapshot`
option for deterministic demonstrations without weather network access.

Keyboard in Pre-flight: Space play/pause, arrows scrub 5 s (Shift 25 s), R restart.
The scene caps DPR at 1.75. Hidden scenes stop rendering; camera guidance respects
reduced motion. Essential motion representing simulation state remains visible.

For browser regression, start the isolated snapshot server on 8001/5174 as
described in the root README, then run `npm run test:browser`. Reports, screenshots
and traces are written to ignored `test-results/`. [Verification results](../docs/VERIFICATION.md)
and [limitations](../docs/LIMITS.md) distinguish checks run from untested conditions.
