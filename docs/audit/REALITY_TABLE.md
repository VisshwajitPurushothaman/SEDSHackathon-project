# Reality table (audit A3, 13 Oct 2026)

Classes: REAL (computed from received telemetry by documented physics/estimator), DERIVED (correct
transformation of real data), SIMULATED-LABELLED (model/assumption, labelled so), PLACEHOLDER (constant,
fixture, default), FAKE (made to look live/correct without being computed).

Evidence sources: static reading (file:line), the perturbation test `tools/audit/test_sender.py`
(isolated backend, results `data/audit/perturbation.json`), and the live capture (FLUCTUATION_REPORT.md).
**Which path the UI uses depends on the data source:** Laptop A sends TCP (path 2), so in the live demo
the UI shows the TCP rows below. The ICD/UDP rows (path 1) are what `python -m sender` and PLAYBACK feed.

## Path 2: TCP digital twin (what the live demo shows)

| Page | Item | Source chain | Class | Evidence | Fix needed |
|---|---|---|---|---|---|
| Top bar | MET | `timestamp_ms` of the latest packet (any stream) | DERIVED, but wrong | jumps 0 / 1813 / 2900 s with the interleaved streams | one stream, monotonic MET |
| Top bar | mission ID | `"SBRL-MISSION-LIVE-01"` constant (`api/ground.py` snapshot) | PLACEHOLDER | constant in code | take from packets or label |
| Top bar / Link | packet rate | `rate_hz: 2.0` constant | FAKE | 2.0 in all 1767 polls, also in the perturbation test | measure |
| Top bar / Link | loss % | `loss_pct: 0.0` constant | FAKE | 0.0 while ≤ 13 % of the live stream was missing | measure (needs a sequence number) |
| Top bar | AOS | `packets_received > 0` (never returns to LOS) | FAKE | logic in `snapshot()` TCP branch | age-based AOS/LOS |
| Top bar | last-frame age | receiver wall clock | REAL | `get_status()` | none |
| Telemetry table | GNSS_ALT, POS_X/Y/Z, SLANT_R | packet `gnss_coordinates` (+2 m pad) | REAL / DERIVED | ramp test: output = input + 2 m | stream separation |
| Telemetry table | LAT_DEG / LON_DEG | flat-earth from x (east) and z (north), fixed metres/degree, origin hard-coded twice | DERIVED (assumed axes) | `tcp_receiver.py` ~325, `get_history_payload` ~477 | use `physics.geo` + config site; confirm axes with Laptop A code |
| Telemetry table | BARO_P, AIR_T | packet fields; default 1013.25 hPa / 15.0 C when absent | REAL; defaults PLACEHOLDER | `_generate_channels_from_tcp` | show UNKNOWN when absent |
| Telemetry table | GAUGE_P | packet superpressure; default 142.25 Pa | REAL; default PLACEHOLDER | same | UNKNOWN when absent |
| Telemetry table | IMU_TILT | packet pitch angle (≈ 90 deg at rest) | DERIVED, mislabelled | capture: 88.5-91.5 deg | rename to pitch or compute tilt |
| Telemetry table | GNSS_VU | wind vector y-component | **FAKE** (wrong quantity) | ramp of +20 m/s shows 0.0 | derive from altitude differences of one stream, label DERIVED |
| Telemetry table | TANK_P | `max(180, 250 - MET*0.005)` | **FAKE** | changes with MET, not input (250 / 247 / 244 bar across identical inputs) | remove (no tank data) or get from Laptop A |
| Telemetry table | status / age / limit | `"MEASURED"`, `0.25 s`, `"NOMINAL"`, `limits: None` constants | **FAKE** | constants in `_generate_channels_from_tcp` | real age, dictionary limits |
| Strip charts | rows | `store.ts` builds rows from `digital_twin_telemetry`: `fused_altitude` = raw altitude, `rec: 'GO_RECOMMENDED'`, `MOTOR_T: -25.0`, TANK_P formula; MET falls back to `Date.now()` | **FAKE** (fused label, GO, motor), PLACEHOLDER | `store.ts` ~104-140 | real fused values or none |
| Map / trajectory | track | same rows, status `"FUSED"` | **FAKE** label (raw data) | `store.ts`, `tcp_receiver.get_history_payload` | label MEASURED |
| Flight / Overview | lat/lon fallback | `?? 37.1 / -6.733` (Flight), `?? 10.3015 / 79.8512` (store) | PLACEHOLDER | FlightPage.tsx ~441, store.ts ~136 | UNKNOWN state |
| Overview | twin card values before any packet | `latest_telemetry` initialised to `SAMPLE_TELEMETRY` (LEO_ORBIT fixture, alt 214 km) and always attached as `digital_twin_telemetry` | **FAKE** (fixture shown as live) | `tcp_receiver.py` ~217, `api/ground.py` ~180 | send nothing until a packet arrives |
| Overview | propellant / total mass | `?? 2000 kg / ?? 3670 kg` | PLACEHOLDER | OverviewPage.tsx ~587-591 | UNKNOWN |
| Overview | "sticky latch" | keeps the previous packet when altitude ≤ 0 | hides data | OverviewPage.tsx ~110 | remove after stream fix |
| Fused state, gates, recommendation, alarms, pre-flight checks, forecast, ML | none on TCP (no estimator) | n/a | not computed | perturbation: no `ground`, no `forecast` on the TCP path | route TCP into the estimator via the channel mapping |
| Post-flight | requirements REQ-SBRL-01..05 all PASS | hard-coded list | **FAKE** | `_tcp_postflight_analysis` (`api/ground.py` ~349-437) | real evaluation or NOT TESTED |
| Post-flight | recovery `chain_ok: True`, recovered = received | hard-coded | **FAKE** | same; no chain exists on TCP | NOT AVAILABLE |
| Post-flight | predicted vs actual 98.6 % / 97.9 % / 99.4 % / 99.1 % | literals | **FAKE** | same | compute or remove |
| Post-flight | forecast scorecard 12.4 / 7.2 / 3.8 m ... | literals | **FAKE** | same | compute from a logged forecast or remove |
| Post-flight | fault timeline ("Boundary layer turbulence ...") | literals | **FAKE** | same | real alarms |
| Post-flight | landing miss 34 m, inside all regions | literals | **FAKE** | same | compute or NOT TESTED |
| Post-flight | closure "RECOVERY_ASSET_COASTGUARD", "FLIGHT_DIRECTOR", backdated | pre-filled at import time | **FAKE** (fabricated sign-offs) | `_TCP_CLOSURE` | start empty; operator entries only |
| API | `POST .../tcp/inject`, inject `SAMPLE_TELEMETRY` | test hooks | FAKE risk (fixture can be injected as live) | `api/ground.py` ~588-595 | remove or PLAYBACK-only with banner |

## Path 1: ICD v1 / UDP and PLAYBACK

| Page | Item | Class | Evidence |
|---|---|---|---|
| Top bar | MET, AOS/LOS, rate, loss, latency, source, REC, alarm count | REAL | receiver/session.py `link()`; LOS test in tests/test_icd_receiver.py |
| Flight | telemetry table values, STALE ages, limit states | REAL / DERIVED | perturbation (UDP constant: values exact); dictionary limits |
| Flight | fused altitude / vertical speed / position with σ, trust ladder, χ² | REAL | NIS per dimension 0.93 (GNSS), 0.98 (NavIC); constant input -> constant fused output |
| Flight | gates, recommendation, GO/NO-GO poll | REAL (rules in config) | ground/station.py; poll recorded only |
| Flight | strip-chart predicted band | SIMULATED-LABELLED | ground/profile.py, labelled "predicted band" |
| Pre-flight | channel health, pad agreement, readiness + hash | REAL | receiver/preflight.py, tests/test_preflight_profile.py |
| Pre-flight | predicted profile, range zones | SIMULATED-LABELLED | zones labelled "illustrative, not NOTAM"; profile basis text |
| Trajectory | physics forecast | SIMULATED-LABELLED (PREDICTED) | responds to input (ramp); **weaker than constant-velocity extrapolation** (ML_AUDIT) |
| Trajectory | hybrid ML forecast, model card | SIMULATED-LABELLED ("experimental: simulator data") | falls back with a stated reason (OUT_OF_RANGE seen in the ramp test) |
| Trajectory | footprint, LOS regions, IIPs | SIMULATED-LABELLED | particle predictor; IIP labelled "illustrative model" |
| Post-flight | recovery, requirements, limit report, scorecard, exports, closure | REAL / DERIVED | receiver/postflight.py (tested on demo sessions) |

## Exception swallowing

| Location | Behaviour | Risk |
|---|---|---|
| `tcp_receiver.read_telemetry_packet` (`except Exception: return None`) | an undecodable JSON packet returns None, then `_handle_client` silently **closes the connection** | silent data loss, not logged |
| `tcp_receiver._handle_client` broad `except` | logs a warning, drops the client | acceptable, but no counter |
| `api/ground.py` `_generate_channels_from_tcp` `or` defaults | absent fields become plausible numbers (1013.25 hPa, 15 C, 142.25 Pa, 10.3015/79.8512) | PLACEHOLDER shown as MEASURED |
| `ml/predictor.py` | falls back to physics with a stated reason | acceptable (labelled) |
| `receiver/postflight.Source.recover` | returns None -> retried, counted as lost | acceptable |
| frontend `.catch(() => undefined)` | panels keep their previous state | acceptable (empty/loading states shown) |

## Counts (displayed items above)

| Class | Path 2 TCP (live demo) | Path 1 ICD |
|---|---|---|
| REAL / DERIVED | 7 (2 with wrong axes/labels) | 13 |
| SIMULATED-LABELLED | 0 | 6 |
| PLACEHOLDER | 6 | 0 |
| FAKE | 17 | 0 |
| not computed (no estimator on TCP) | gates, fused state, forecast, ML, pre-flight | - |
