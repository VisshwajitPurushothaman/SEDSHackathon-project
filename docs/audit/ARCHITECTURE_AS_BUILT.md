# AERO-FIRE: architecture as built (audit A0, 13 Oct 2026)

Branch `telemetry-console`, HEAD `87458be` (teammate commit on top of `738fcbd`). Measured on
Laptop B (this machine, Wi-Fi `172.16.59.95`, network profile **Public**).

## Processes and ports seen running

| Process | Bind | Purpose |
|---|---|---|
| `backend.py --tcp-port 65432` (PID 44520, started 06:43) | TCP `0.0.0.0:65432`, UDP `0.0.0.0:5600`, HTTP `127.0.0.1:8000` | ground-station API + both receivers |
| Vite dev server | `localhost:5173` (and a stale one on 5174) | console UI, API `127.0.0.1:8000` |
| Laptop A `172.16.58.215` | client of TCP 65432 | digital twin "client_node_01"; **3 concurrent TCP connections** observed (source ports 51657-51659), new connections during capture (49681, 49683, 49684, 54362) |

## Two independent telemetry paths

```
PATH 1 (ICD v1, built in steps 2-6; used by sender/ in this repo)
  sender/ (python -m sender) --UDP 5600 JSON frames, seq + HMAC chain-->
  aerofire/receiver/service.py Receiver (worker thread)  ->  receiver/session.py GroundSession
    -> telemetry/icd.py packets -> ground/station.py GroundStation (Kalman, integrity, gates, leak, predictor)
    -> ground/forecast.py + ml/predictor.py (hybrid forecast)  -> receiver/preflight.py, receiver/postflight.py
  -> api/ground.py snapshot() / WS /ws/telemetry / REST  -> frontend/src/ground/* (store.ts)

PATH 2 (TCP digital twin, added in 87458be; what Laptop A actually sends)
  Laptop A twin (code NOT in this repo) --TCP 65432, 4-byte length + JSON, ACK per packet-->
  aerofire/receiver/tcp_receiver.py TcpReceiver (thread per connection; one shared latest_telemetry)
    -> api/ground.py snapshot(): when no ICD session exists, channels built by _generate_channels_from_tcp()
       and the raw packet attached as digital_twin_telemetry (also when ICD is active)
    -> api/ground.py history/postflight TCP branches (_tcp_postflight_analysis)
  -> frontend store.ts builds its own history rows from digital_twin_telemetry;
     OverviewPage/FlightPage/TrajectoryPage read digital_twin_telemetry directly.
  No seq, no HMAC, no estimator, no gates, no forecast/ML, no pre-flight evidence on this path.
```

## Files per stage

| Stage | Path 1 (ICD/UDP) | Path 2 (TCP twin) |
|---|---|---|
| Sender | `sender/__main__.py`, `sender/vehicle.py` | Laptop A code (not in repo); local emulator `tools/send_tcp_telemetry.py` |
| Wire format | `docs/ICD.md`, `aerofire/telemetry/icd.py`, `config/channels.yaml` | `tcp_receiver.py` docstring + `SAMPLE_TELEMETRY` fixture |
| Receiver | `aerofire/receiver/service.py`, `session.py` | `aerofire/receiver/tcp_receiver.py` |
| Estimation | `aerofire/ground/station.py`, `estimator.py`, `predictor.py`, `leak_detector.py` | none |
| Prediction | `ground/profile.py`, `ground/forecast.py`, `ml/predictor.py` | none |
| API | `aerofire/api/ground.py` (session branches) | `aerofire/api/ground.py` (TCP branches, inject endpoints) |
| UI | `frontend/src/ground/{Shell,FlightPage,PreflightPage,TrajectoryPage,PostflightPage}.tsx`, `store.ts` | `OverviewPage.tsx`, `ArtificialHorizon.tsx`, `MissionLiveMap.tsx`, `TrajectoryXYZGraph.tsx`, TCP branches in `store.ts`, `FlightPage.tsx`, `TrajectoryPage.tsx` |

## Configuration drift

`config/mission.yaml` site moved to Point Calimere (10.3015 N, 79.8512 E) in `87458be`, but
`gates.approved_area` (13.0-14.6 N), `config/range.geojson` zones, `config/atmosphere_snapshot.json`,
the demo sessions and the ML training data still use the old site near 13.72 N. Consequence: the impact
gate can never pass (test `test_impact_points_and_gate` now fails), so GO can never be recommended on path 1.
