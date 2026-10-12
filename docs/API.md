# AERO-FIRE API

Base URL: `http://127.0.0.1:8000` (`python backend.py`). JSON in, JSON out. CORS is open; no credentials.

Every operator command needs `"operator": "<name>"` (1–64 characters) and is written to the mission's append-only event log. **No endpoint fires, releases or ignites without an explicit operator call.**

Status codes: `404` unknown mission or item, `409` command not allowed now (with the reason), `422` invalid input (for example a missing operator name).

Interactive schemas/examples: `GET /docs`; machine-readable schema: `GET /openapi.json`.
Missions are in memory in this single backend process; export before restarting.

PowerShell example (recorded weather, no weather network request):
```powershell
$mission = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/missions -ContentType application/json -Body '{"name":"nominal demo","seed":11,"atmosphere":"snapshot","time_scale":60}'
$missionId = $mission.mission_id
Invoke-RestMethod "http://127.0.0.1:8000/api/missions/$missionId/frame"
```

---

## Ground station (receiver)

Telemetry arrives as ICD v1 UDP frames ([ICD.md](ICD.md)); `python backend.py` listens on
UDP 5600 (`--udp-port`, `--no-udp`, `--playback FILE`, `--playback-speed`, `--snapshot`).
Nothing here sends to the vehicle.

| Endpoint | Meaning |
|---|---|
| `WS /ws/telemetry?hz=4` | ground-side snapshots at 2–10 Hz: `{receiver, mission_id, source (LIVE UDP / PLAYBACK), recording, utc, met, met_status, fc_phase, link, channels, ground, alarms, active_alarms, polls}` |
| `GET /api/ground/state` | one snapshot (same object) |
| `GET /api/ground/status` | receiver mode, listen address, rejected datagrams, sessions |
| `GET /api/ground/history?max_points=3000` | decimated per-step rows (`met`, dictionary channels, `fused_altitude`, `fused_vertical_speed` with sigma, phase, recommendation), fused track, LOS periods |
| `GET /api/ground/dictionary` | `config/channels.yaml` |
| `GET /api/ground/events` | append-only event log |
| `POST /api/ground/alarms/{id}/ack` | `{"operator"}`; `409` if already acknowledged |
| `POST /api/ground/poll` | `{"operator", "vote": "GO"\|"NO_GO", "note"}`: recorded GO/NO-GO poll; commands nothing |

| `GET /api/ground/preflight` | checks (`ch:<MNEMONIC>` channel health, `agreement`, `profile`, `range`) with NOT CHECKED / PASS / FAIL / WAIVED and evidence, `readiness` (HOLD / READY FOR DIRECTOR REVIEW), `report` and its `report_sha256` (canonical JSON) |
| `GET /api/ground/preflight/profile?step=4` | predicted telemetry profile (nominal + band per channel), predicted track, landing footprint, IIPs (illustrative rocket model), range zones and the range check |
| `POST /api/ground/preflight/{check_id}/waive` | `{"operator", "reason"}` (both required) |

| `GET /api/ground/ml/model` | trajectory forecast model card: version, sklearn, seeds, data hash, test-mission evaluation and decision |
| `GET /api/ground/ml/report` | `docs/ML_RESULTS.md` (text/markdown) |

Snapshots carry `forecast` (every 5 s MET in flight, status PREDICTED):
`{met, phase, state_status, now {alt, lat, lon}, physics_path [{h, alt, lat, lon}] to +600 s,
horizons {"30"|"60"|"120": {physics {alt, lat, lon, east, north}, hybrid {...} | null, status, out_of_range, default}}}`.
`status` is `OK` (hybrid available) or the fallback reason: `MISSING_MODEL`, `OUT_OF_RANGE`,
`PHASE_NOT_TRAINED`, `EXCEPTION`, `STATE_NOT_FUSED` (e.g. during LOS); then only physics is shown.

Pre-flight evidence is the pad telemetry (`FC_PHASE == PAD`); it freezes at release. The
profile is ground physics with the forecast atmosphere (no telemetry). During flight,
history rows carry `band_<MNEMONIC>_lo/hi`; a channel outside its band for
`prediction.off_nominal_persist_s` raises an OFF-NOMINAL caution, and the live footprint/IIPs
are re-checked against `config/range.geojson` (illustrative zones, not NOTAM data).

`link`: `aos`, `last_frame_age_s`, `rate_hz` (10 s window), `loss_pct` (missing seq),
`latency_s` (median minus minimum of receive−send: the laptops' clock offset cannot be
separated from the minimum delay), `clock_offset_s`, `duplicates`, `out_of_order`,
`hmac_failures`, `chain_breaks`, `gaps` (`[[first, last], ...]`), `los_periods` (LOS after
3 s of silence).

`channels[]`: `mnemonic, value, units, subsystem, status (MEASURED / STALE / UNKNOWN), age_s,
met, limit (NOMINAL / CAUTION / ALARM / NO_LIMIT), limits (current phase), known`. Mnemonics
missing from the dictionary are shown raw with `known: false`.

`ground`: the estimator frame (status-labelled estimates, gates, recommendation, trust,
footprint, prediction) as documented under `/frame` below.

The `/api/missions/*` endpoints below remain for the in-process simulator used by tests;
the ground-station console does not create missions or inject faults.

---

## Health and legacy pre-flight endpoints

These are kept for the pre-flight console. They now run on the new physics and report ground-side estimates.

### `GET /api/health`
```json
{"status": "AERO-FIRE Backend Operational"}
```

### `GET /api/config`
Public site, gate limits/approved area, qualified motor minimum, balloon volume,
tank fill, rail geometry/model label, link station, API defaults and vent sensor
noise. This contains no state trajectory or hidden physical truth.

### `POST /api/simulate`
Runs pad → ascent → float until the first GO or ABORT recommendation and returns everything at once.
```bash
curl -X POST 127.0.0.1:8000/api/simulate -H 'content-type: application/json' -d '{"puncture_um": 0}'
```
```json
{"summary": {"verdict": "GO_RECOMMENDED", "abort_reason": null, "puncture_input_um": 0, "final_altitude_m": 28123.9,
             "total_frames": 99, "atmo_source": "open-meteo@2026-10-12T16:00Z", "atmo_note": "15 levels used"},
 "telemetry": [{"time_s": 0.0, "altitude_m": 4.8, "...": "...", "gate_status": "NO-GO", "is_ignition": false,
                "recommendation": "NO_GO", "swing_amplitude_deg": 0.02, "atmo_source": "open-meteo@2026-10-12T16:00Z"}]}
```
- `verdict` is one of `GO_RECOMMENDED`, `ABORT_RECOMMENDED` or `NO_GO`.
- `is_ignition` is always `false`: the software never confirms ignition.

### `GET /api/stream?puncture_um=0&pace_s=0.1`
SSE of the same frames, with `id:` lines, then `event: end`.

---

## Missions

### `POST /api/missions` → `201`
```bash
curl -X POST 127.0.0.1:8000/api/missions -H 'content-type: application/json' -d '{
  "name": "demo leak", "seed": 1, "time_scale": 60, "atmosphere": "live",
  "faults": {"tank_leak_um": 100, "blackouts": [[6200, 6800]], "gnss_spoof_start_s": null, "motor_heater_failed": false},
  "config_overrides": {"link": {"packet_loss": 0.02}}
}'
```
```json
{"mission_id": "3f9c1a2b7d4e", "summary": {"state": "running", "time_scale": 60.0, "atmo_source": "open-meteo@...", "...": "..."}}
```

| Field | Meaning |
|---|---|
| `time_scale` | mission seconds per wall second (default 60; `0` = as fast as possible) |
| `atmosphere` | `live` (Open-Meteo, labelled fallback to ISA) or `snapshot` (recorded profile) |
| `faults.tank_leak_um`, `faults.tank_leak_onset_s` | pressurant tank puncture |
| `faults.balloon_leak_mm`, `faults.balloon_leak_onset_s` | envelope tear |
| `faults.gnss_spoof_start_s`, `faults.gnss_spoof_drift_m_s` | spoofed fix drifting at `[east, north]` m/s |
| `faults.motor_heater_failed` | heater fault |
| `faults.blackouts` | `[[start_s, end_s], ...]` link blackouts |
| `faults.packet_loss`, `faults.latency_s` | link overrides |
| `config_overrides` | deep-merged into `config/mission.yaml` |
| `autostart` | `false` creates the mission paused (tests and scripted demos step it) |

### `GET /api/missions`, `GET /api/missions/{id}`
Mission summaries: state, phase, recommendation, operator state, link counters, record counts.

### `GET /api/missions/{id}/frame`
The latest ground frame. This is the same object the stream carries:
```json
{"id": 812, "t": 5829.0, "phase": "Gates", "vehicle_phase": "FLOAT", "rocket_phase": "ABOARD",
 "recommendation": "GO_RECOMMENDED", "operator": {"state": "none"},
 "estimates": {"altitude": {"value": 28131.0, "unit": "m", "status": "FUSED", "sigma": 1.9, "source": "Kalman filter ...", "age_s": 1.0},
               "tank_pressure": {"value": 17624000.0, "unit": "Pa", "status": "MEASURED", "...": "..."},
               "wind_speed": {"value": 6.7, "status": "PREDICTED", "source": "open-meteo@2026-10-12T15:00Z"}},
 "gates": {"float": {"passed": true, "value": {"z_m": 28131, "vz_m_s": 0.2}, "limit": {"band_m": [26000, 30000]}, "margin": 2131, "detail": "in band; stable"},
           "nav": {}, "motor": {}, "swing": {}, "impact": {"evidence": {"points": [], "model": "illustrative model, not validated"}}},
 "tank_criterion": {"passed": true, "detail": "no significant mass loss"},
 "predicting": false, "last_fix_t": 5828.0, "trust": {"gnss": {"state": "ACCEPTED"}, "navic": {}, "baro": {}},
 "landing_footprint": {"basis": "if cut down now", "regions": [{"level": 90, "polygon": [[13.7, 80.1]]}]},
 "alerts": [{"id": 2, "severity": "INFO", "title": "GO recommended: operator must commit", "acknowledged_by": null}],
 "atmo_source": "open-meteo@2026-10-12T15:00Z"}
```
- **Status** on every estimate: `MEASURED`, `FUSED`, `PREDICTED`, `RECONSTRUCTED` or `UNKNOWN`.
- Stale values are `UNKNOWN`, with `last_value` and `age_s`.
- During a blackout, position is `PREDICTED` and `estimates.prediction` carries the 50/90/99 % regions.

### `GET /api/missions/{id}/stream`
SSE of ground frames (period `max(5, time_scale / 8)` mission seconds for positive
time scales, plus every change of phase, recommendation, alert count, prediction
or operator state). This limits ordinary updates to about eight per wall second
at high time scales. Phase events can add frames above that ordinary rate.
- Each event has `id: <frame id>`.
- Reconnect with the `Last-Event-ID` header (or `?from=<id>`) to resume after that frame.
- When the mission finishes, the server sends `event: end`; the client must close then, or `EventSource` would reconnect.

### `GET /api/missions/{id}/track?step=5`
What the operator saw live (append-only): `{t, lat, lon, alt, status}`.

### Operator commands
| Endpoint | Body | Effect / refusal |
|---|---|---|
| `POST /{id}/commit` | `{"operator"}` | Ignition countdown (5 s) → release → cut-down 60 s later. `409` unless the recommendation is `GO_RECOMMENDED` and no hold is active. |
| `POST /{id}/hold` | `{"operator", "release": false}` | Operator hold (blocks commit); `release: true` lifts it. |
| `POST /{id}/terminate` | `{"operator"}` | Cut-down without launch (rocket stays aboard). |
| `POST /{id}/alerts/{alert_id}/ack` | `{"operator"}` | Acknowledge an alert; `409` if already acknowledged. |
| `POST /{id}/preflight/{check_id}/waive` | `{"operator", "reason"}` | Mark a pre-flight check WAIVED. |
| `POST /{id}/faults/{fault_id}/action` | `{"operator", "text"}` | Corrective action for a fault. |
| `POST /{id}/closure/{item}` | `{"operator"}` | Mission closure: `payload_recovered`, `data_archived`, `anomalies_filed`. |

### `GET /api/missions/{id}/events`
Append-only event log: `[{n, wall, t, actor, action, detail}]`. Actors are `system`, `vehicle` or the operator's name.

### `GET /api/missions/{id}/preflight`
Checks are `NOT CHECKED`, `PASS`, `FAIL` or `WAIVED`, with evidence:
- `atmosphere_live`
- `impact_area`
- `link_budget`
- `recorder_chain`
- `tank_fill`
- `motor_temp`
- `nav_agreement`
- `leak_check`
- `truth_endpoint`: FAIL while grading mode is on
- `assumptions_reviewed`

### Recovery: `POST /api/missions/{id}/recover`, `GET /api/missions/{id}/recover`
Available after the mission has finished (`409` before). The POST starts the progressive download; the GET reports progress:
```json
{"state": "done", "stage": "complete", "expected": 72084, "have": 72084, "missing_at_start": 5000, "recovered": 5000,
 "retries": 3, "lost_count": 0, "hmac_failures": [], "chain_ok": true,
 "progress": [{"stage": "rough picture (every 10th missing record)", "have": 67584, "expected": 72084}]}
```

### `GET /api/missions/{id}/postflight`
Available after the mission has finished. Returns:
- `requirements`: PASS / FAIL / NOT TESTED / INCONCLUSIVE with evidence;
- `faults`: injected vs detected, detection delay, estimated leak size, hindsight onset, corrective action;
- `operator_track`, `reconstruction` (RTS smoother, `RECONSTRUCTED`), `prediction_log`, `footprint_at_cutdown`, `landing_fix`, `closure`, `event_log`;
- in grading mode only: `scorecard` and `truth_track`.

`?tracks=false` omits the three large history arrays while still computing
requirements. `grading` indicates whether evaluator data is enabled for that
mission. The operator view hides injected-only rows until reveal; disabled
grading serves detected faults only. A failed requirement is a mission outcome,
not an API error. Run recovery for complete reconstruction evidence.

### `GET /api/missions/{id}/export?format=json|csv`
- **json:** `{mission, records, report, report_sha256}`. `report_sha256` = SHA-256 of the report's canonical JSON (sorted keys, compact). The export never contains evaluator truth: grading histories/scorecard, injected-only faults, injection settings and creation-event injection details are redacted; the internal event log is preserved. `mission.faults` is an empty object in exports.
- **csv:** long format, `seq,event_time,sensor_id,source,status,received_live,field,value,unit`.

### `GET /api/missions/{id}/truth`
**Evaluator only.** Returns simulator truth (track, events, rocket result, landing). `403` unless `api.truth_enabled` is true in config.

The configuration gate authorizes local grading; it is not authentication. The
Post-flight reveal toggle is a display control. Set `api.truth_enabled: false`
for an operator-only mission configuration. No API call actuates real hardware.

### Verify an exported report

Use the untouched downloaded JSON; do not parse/stringify it in a browser before
saving, because doing so changes floating-point number representations.

```python
import hashlib, json
with open("mission.json", encoding="utf-8") as stream:
    export = json.load(stream)
canonical = json.dumps(export["report"], sort_keys=True, separators=(",", ":"), default=str)
assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == export["report_sha256"]
```
