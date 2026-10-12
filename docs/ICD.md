# AERO-FIRE Interface Control Document: ICD v1

Between **Laptop A** (vehicle / digital twin, `python -m sender`) and **Laptop B**
(ground station, `python backend.py --udp`). Laptop B never receives truth during a
mission; the only link is the one described here. The ground station has **no
uplink**: it sends nothing to the vehicle.

## 1. Downlink frames (UDP)

| Item | Value |
|---|---|
| Transport | UDP/IPv4, one JSON object (UTF-8) per datagram, no fragmentation above 8 kB |
| Port | 5600 on Laptop B (configurable: `--port` on both sides) |
| Rate | `--rate` Hz wall clock (default 2). When `--speed / sim.dt_s` exceeds it, one frame per simulation step is sent instead, so no onboard sample is skipped |
| Order | not guaranteed; the receiver reorders by `seq` |

```json
{"v": 1, "mission_id": "a3f9c2d1e0b4", "seq": 812, "met_s": 5829.0, "utc_sent": 1791900000.123,
 "time_scale": 20.0, "ch": {"GNSS_LAT": 13.7412345, "TANK_P": 176.24, "FC_PHASE": "FLOAT"},
 "prev_hash": "<64 hex>", "hash": "<64 hex>"}
```

| Field | Meaning |
|---|---|
| `v` | ICD version, integer, currently 1. A receiver rejects other versions |
| `mission_id` | 12 hex characters chosen by the sender at start; a new value starts a new ground session |
| `seq` | frame number, 0, 1, 2… with no gaps onboard. Gaps at the receiver are lost/late frames |
| `met_s` | mission elapsed time of the samples in `ch`, s |
| `utc_sent` | sender wall clock (Unix s) when the datagram was built |
| `time_scale` | mission seconds per wall second on the sender (`--speed`) |
| `ch` | channel values keyed by mnemonic (see `config/channels.yaml`). Frames without a new sample repeat the previous `met_s` and values (heartbeat) |
| `prev_hash`, `hash` | HMAC-SHA256 chain (below) |

### Integrity

`hash = HMAC-SHA256(key, prev_hash + canonical(frame without "hash"))`, where
`canonical` is JSON with sorted keys and separators `(",", ":")`. Frame 0 uses
`prev_hash = HMAC-SHA256(key, "AERO-FIRE-GENESIS")`. The key is
`telemetry.hmac_key_demo` in `config/mission.yaml`: **demo key, not secure**. The
chain detects edits, deletions and reordering. It is not confidentiality or
authentication against anyone who has the published demo key.

The receiver verifies each frame on its own (`hash` over its body and `prev_hash`)
and the link between consecutive frames when both are held. A frame that fails is
counted, logged and **not** fed to the estimator.

### Channel encoding

Values are numbers, booleans or short strings, in the units of the dictionary
(`config/channels.yaml`): pressure in hPa (BARO_P) and bar (TANK_P), temperatures in
°C, angles in degrees, positions in degrees/metres. Mnemonics are upper case.
**Unknown mnemonics are displayed raw** and are not used by the estimator. A
channel absent from a frame was not sampled (for example TANK_* after release).

## 2. Recovery and manifest (HTTP on Laptop A, port 8100)

| Request | Response |
|---|---|
| `GET /manifest` | `{"v":1, "mission_id", "frames": <count onboard>, "first_seq", "last_seq", "last_hash", "finished": bool, "genesis": "<hex>"}` |
| `GET /recover?from_seq=a&to_seq=b` | `{"frames": [...]}`: onboard frames a..b inclusive, exactly as recorded (max 2000 per call) |
| `GET /truth` | Grading only. `403` until the mission has finished; then the truth track, events and landing point. Laptop B calls it only when the operator enables grading after the flight |

Recovery is pull-only and read-only. Laptop A accepts no commands over the network.

## 3. Vehicle actions

Commit (rocket release) and termination (cut-down) are actions of the vehicle/range
side on Laptop A (`--commit-at`, `--terminate-at`, or typing `commit` / `terminate`
in the sender console). The ground station records an operator GO/NO-GO poll; it
cannot cause either action.

## 4. Session files

Laptop B appends every received frame (one JSON per line, with
`{"rx": {"utc": <receive time>}}` added outside the hashed body) to
`data/sessions/<mission_id>.jsonl`. `--playback <file>` replays such a file with the
original timing; the console shows **PLAYBACK**.
