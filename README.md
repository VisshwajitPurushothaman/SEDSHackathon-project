# AERO-FIRE

A two-laptop telemetry ground station for a simulated rockoon (balloon-launched
rocket). **Laptop A** runs the vehicle digital twin (truth simulator, sensors,
onboard recorder) and sends ICD v1 telemetry frames over UDP
([docs/ICD.md](docs/ICD.md)). **Laptop B** runs this ground station: receiver,
limit checks against the [channel dictionary](config/channels.yaml), Kalman
estimator, predictions and the console. Laptop B never sees truth during a
mission and has no uplink. No real ignition, release or deployment hardware is
connected.

## Install once

Python 3.11+ and Node.js 20+ with npm are required. This continuation was tested
on Windows with Python 3.14.6, Node 22.19 and Chrome.

PowerShell, from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm --prefix frontend ci
```

Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
```

## Run

One command after installation (PowerShell or Linux):

```bash
python tools/dev.py
```

Use `python3` if that is your Linux interpreter name. Open
**http://localhost:5173**; the API listens at **http://127.0.0.1:8000**.
Ctrl+C stops the processes started by the runner. Existing servers are never
killed by it. `python tools/dev.py --snapshot` uses the recorded weather profile
and labels it as recorded in the console.

The existing separate-terminal commands remain available:
`.venv/Scripts/python.exe backend.py` (Linux: `.venv/bin/python backend.py`) and
`npm --prefix frontend run dev`. `AEROFIRE_HOST`/`AEROFIRE_PORT` configure the
backend; `VITE_API_URL` configures the frontend. Its default is
`http://127.0.0.1:8000`.

## Two-laptop setup

1. Put both laptops on the same Wi-Fi (a phone hotspot works). On Laptop B run
   `ipconfig` and note the Wi-Fi adapter's IPv4 address (e.g. `192.168.43.20`);
   do the same on Laptop A for recovery.
2. Allow the traffic in Windows Firewall (PowerShell **as Administrator**; the
   Wi-Fi network profile must be Private, or use `-Profile Any`):

   ```powershell
   # Laptop B (ground station): telemetry in
   New-NetFirewallRule -DisplayName "AERO-FIRE telemetry UDP 5600" -Direction Inbound -Protocol UDP -LocalPort 5600 -Action Allow -Profile Private
   # Laptop A (vehicle): post-flight recovery requests in
   New-NetFirewallRule -DisplayName "AERO-FIRE recovery TCP 8100" -Direction Inbound -Protocol TCP -LocalPort 8100 -Action Allow -Profile Private
   ```
3. Laptop B: `.venv/Scripts/python backend.py` (listens on UDP 0.0.0.0:5600;
   API/UI on 127.0.0.1:8000) and `npm --prefix frontend run dev`.
4. Laptop A: `.venv/Scripts/python -m sender --target <B-ip> --scenario nominal --speed 20`.
   Scenarios: `nominal`, `tank_leak_100um`, `gnss_spoof`, `blackout_600s`,
   `cold_motor`. The vehicle commit/terminate happens on Laptop A (automatic per
   scenario, `--commit-at`/`--terminate-at`, or type `commit`/`terminate`).
   `--replay data/demo_sessions/nominal.vehicle.jsonl.gz` resends a recorded flight.

**One laptop:** run both on the same machine with `--target 127.0.0.1`.

**No sender at all (PLAYBACK):**
`.venv/Scripts/python backend.py --playback data/demo_sessions/nominal.jsonl.gz --playback-speed 10`
replays a recorded ground session (also `tank_leak_100um.jsonl.gz`); the console
shows PLAYBACK. Re-record with `python tools/record_demo_session.py <scenario>`.

Ground-station API: `ws://127.0.0.1:8000/ws/telemetry`, `/api/ground/*`
(see [docs/API.md](docs/API.md)). Received frames are written to
`data/sessions/<mission_id>.jsonl`.

## Demo flow

- **Pre-flight:** Sealed (0 µm) or a tank puncture. Replay/scrub the received
  telemetry, inspect the seven checks and five gates, and use the existing
  1.5 s hold for the illustrative local ignition animation. This preview does
  not launch a Flight mission.
- **Flight:** choose one of the existing five presets, enter an operator name,
  choose a simulation time scale and press Start mission. GO is a recommendation;
  an explicit operator commit calls the simulated mission API. Hold and terminate
  retain their existing semantics. Commands require live mode. Blackouts change
  position to PREDICTED and stale sensor readings to UNKNOWN.
- **Post-flight:** select a finished mission, run progressive recovery, inspect
  live/reconstructed histories and requirement evidence, save corrective actions,
  export CSV/JSON, and complete the three closure items. Reveal truth / grade
  exposes the evaluator scorecard only when `api.truth_enabled` is enabled.

| Flight preset | Expected evidence / operator completion |
|---|---|
| Nominal | Five gates can recommend GO; operator commits; rocket/coast/cut-down/descent/landing |
| 100 µm tank leak | Ground detector declares leak and ABORT; operator may terminate without launch |
| GNSS spoofed (ascent) | Navigation disagreement and widened uncertainty; no GO; operator may terminate |
| 10-minute blackout (float) | Link loss T+6000–6600 s, PREDICTED position and regions; wait to commit until after reconnect to keep the blackout at float |
| Motor too cold | Motor falls below the configured qualified minimum; gate holds; operator may terminate |

At high time scales a GO window may close during the 1.5 s hold. The backend
rechecks the current recommendation and refuses an invalid commit. Nothing
automatically launches or terminates a mission; an uncommitted mission can reach
the six-hour simulation horizon. All outcomes depend on the configured physics
and atmosphere, rather than being forced by a preset.

If the backend is unavailable, **Pre-flight** uses the existing recorded Sealed
and 100 µm fixtures and displays “Backend offline · demo data”. Flight and
Post-flight need the backend; they do not invent full mission records. Weather
failure with the backend running produces a labelled ISA fallback; `--snapshot`
is the deterministic full-mission demonstration option. Internet loss can also
remove map tiles/fonts/HDR; the telemetry and procedural scene remain local.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -ra
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Use `.venv/bin/python` on Linux. Browser checks use the installed Chrome by
default. Start an isolated deterministic test instance in one terminal:

```bash
python tools/dev.py --snapshot --api-port 8001 --web-port 5174
```

Then run `npm --prefix frontend run test:browser` in another terminal. For an
installed Playwright Chromium, use `TEST_BROWSER_CHANNEL=chromium`; for other
ports set `TEST_API_URL` and `TEST_BASE_URL`. Tests use the Vite development
module graph for renderer/store inspection, so this command targets the dev
server. It creates simulated missions and downloads reports into ignored
`frontend/test-results/`; failures retain screenshots and traces. Never point
these tests at a real system.

## Engineering documents

- [Physics, equations and assumptions](docs/PHYSICS.md)
- [API contract and examples](docs/API.md)
- [Limitations and remaining untested conditions](docs/LIMITS.md)
- [Executed verification results](docs/VERIFICATION.md)
- [Original audit and development decisions](docs/AUDIT.md)

The physics truth simulator feeds noisy sensors, then the onboard recorder and
link emulator, then the ground estimator. Ground code receives telemetry only;
the import firewall remains tested. Mission data is in memory and is lost on
server restart: export completed missions before stopping it. The old
`tempCodeRunnerFile.py` and tracked bytecode remain preserved historical files;
`backend.py` is the current entry point.
