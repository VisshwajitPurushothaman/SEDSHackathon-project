# Fluctuation report (audit A1 + A2, 13 Oct 2026, ~07:45-08:05 local)

Measured on Laptop B against the running backend (PID 44520, `backend.py --tcp-port 65432`) while Laptop A
(`172.16.58.215`) was streaming. Capture tools: `tools/audit/api_tap.py` (layers 1-2, read-only GET at 20 Hz),
`tools/audit/ui_tap.mjs` (layer 3), `tools/audit/fluctuation_report.py` (metrics). Raw numbers:
`data/audit/fluctuation_metrics.md`, captures `data/audit/tcp_latest.jsonl`, `ui_values.jsonl`.

**Limitation of the capture:** port 65432 is owned by the running backend, so the raw listener
`link_probe` could not bind it without stopping the teammate's process. Layer 1 is therefore "the
latest packet held by the TCP receiver", polled at 20 Hz: packets arriving < 50 ms apart can be missed,
and the receiver rewrites the `gnss_coordinates` block (and merges near-zero-altitude packets) before
storing. Counts below are lower bounds for packets, upper bounds for loss.

## A1. Link

| Item | Measured |
|---|---|
| Protocol / port | TCP 65432, 4-byte big-endian length + JSON, ACK per packet (`aerofire/receiver/tcp_receiver.py`). UDP 5600 (ICD v1) also bound but unused by Laptop A |
| Bind | `0.0.0.0:65432` and `0.0.0.0:5600` (correct); API `127.0.0.1:8000` |
| Network profile | Wi-Fi "SEDHACKS" = **Public** (should be Private) |
| Firewall | no port-scoped rule; traffic passes via program rules "python.exe Allow (Private, Public)" for the 3.14 interpreter |
| Source IPs | only `172.16.58.215` (Laptop A); **203 distinct source ports in 90 s** = a new TCP connection for almost every packet; 3 connections were open simultaneously at one point |
| device_id | `client_node_01` only |
| Packet rate | 3.99 distinct packets/s total = three interleaved streams (below) |
| Live stream | MET 2857.0 -> 2947.0 s, 157 packets, step 500 ms (2 Hz), two gaps of ~3.5 s; 157 of ~181 expected (≤ 13 % loss, upper bound) |
| Out of order | 159 backward `timestamp_ms` steps among all held packets (from interleaving, not from reordering inside one stream) |
| Duplicates | the frozen packet below was received 172 times |
| Clock offset | **not measurable**: packets carry MET (`timestamp_ms`) but no sender UTC; `iso_time` exists only on start/pause events. The live MET advanced at 1.001 x wall time |
| Backend-reported link | `rate_hz` 2.0 and `loss_pct` 0.0 in all 1767 polls: **hard-coded**, not measured (`api/ground.py` `snapshot()` TCP branch) |

### The three streams on one port (all `device_id` `client_node_01`, `simulation_state.status` RUNNING, not paused)

| Stream | Packets / 90 s | timestamp_ms | Altitude |
|---|---|---|---|
| live | 157 | 2 857 000 -> 2 947 000, +500 per packet | 34 643 -> 36 472 m (rising) |
| frozen | 172 | always 1 813 295 | 18 199.45 m (identical packet, ~1.9 per s) |
| zero-time | 30 | 0 | mixed (receiver merge logic blends them with the previous packet) |

The packets alone cannot tell why Laptop A emits the frozen and zero-time streams (a second twin
process, a cached handshake re-sent per connection, or a reconnect routine). **Laptop A's code is not
in this repo**; it is needed in `external/laptopA/` to locate the sender-side cause.

## A2. Fluctuation by layer (same 90 s window for layers 1-2; layer 3 captured minutes earlier)

| Channel | Layer 1 packets: changes / reversals / max jump | Layer 2 API | Layer 3 UI |
|---|---|---|---|
| altitude | 359 / 313 / 18 272 m | 342 / 313 / 18 272 m | 84 / 45 / 18 890 m |
| crossrange z (north) | 359 / 311 / 16 680 m | 342 / 311 / 16 680 m | 84 / 47 / 16 750 m |
| baro pressure | 359 / 313 / 68.2 hPa | 336 / 313 / 68.3 hPa | 82 / 45 / 929.7 hPa |
| air temperature | 359 / 307 / 24.5 C | 310 / 307 / 24.5 C | 60 / 38 / 70.9 C |
| TANK_P (no tank data in packets) | n/a | 330 / 309 / 14.7 bar | 58 / 41 / 14.9 bar |

Reversals ≈ changes at every layer: the values flip back and forth between streams, they do not
drift. Layer 2 reproduces layer 1 one-for-one, and layer 3 shows the same flips (sampled more slowly).
**The fluctuation enters at layer 1, before any application code**, and is passed through unfiltered.

## Known causes: verdicts

| Cause | Verdict | Evidence |
|---|---|---|
| Two (or more) sources interleaved | **CONFIRMED (primary)** | three timestamp streams from Laptop A on one port, 203 connections in 90 s; receiver keeps "latest packet wins" with no stream/sequence discrimination (`tcp_receiver.py` `_handle_client`) |
| Out-of-order / duplicate packets applied without a seq check | **CONFIRMED** | no sequence field is used on the TCP path; the frozen packet is applied 172 times; 159 backward MET steps applied |
| Backend values derived from time, not inputs | **CONFIRMED (amplifier)** | `TANK_P = max(180, 250 - MET*0.005)` in `api/ground.py`, `tcp_receiver.py` and `frontend/src/ground/store.ts`: when MET flips between 0, 1813 and 2900 s, TANK_P flips 250 / 240.9 / 235.6 bar |
| Mitigations that hide rather than fix | **CONFIRMED** | receiver "merge" when altitude ≤ 20 m (`tcp_receiver.py` lines ~303-314) and UI "sticky latch" (`OverviewPage.tsx` ~line 110) mask some flips and blend packets from different streams |
| Sensor noise by design shown raw | RULED OUT as the visible cause | within the live stream the steps are smooth (x moves < 3 m, altitude ~+10 m per packet); the jumps are 10^4 m |
| Frontend randomness | RULED OUT | no `Math.random`, no value generators (static scan) |
| Backend randomness while serving | RULED OUT | `np.random` only in simulators/sender and seeded predictor construction |
| Unit/field mismatch | **CONFIRMED (separate defect)** | `GNSS_VU` is the wind vector's y-component, not vertical speed (perturbation: a +20 m/s ramp shows 0.0); `IMU_TILT` is the pitch angle (≈ 90 deg at rest: attitude convention, not tilt from vertical) |
| Time base | **CONFIRMED** | TCP history and the frontend use `timestamp_ms` (packet MET) but from whichever stream arrived last, so MET itself jumps; `store.ts` falls back to `Date.now()` when `timestamp_ms` is 0 |
| Kalman tuning (R, Q) | RULED OUT on the ICD path; **not applicable** on the TCP path | ICD playback: mean NIS per dimension GNSS 0.93, NavIC 0.98. The TCP path has no filter at all: raw values are labelled MEASURED, and the frontend labels raw altitude as `fused_altitude` / track status FUSED |
| React re-render / extra WebSockets | INCONCLUSIVE (dev only) | 1-2 open sockets per page under the Vite dev server with React StrictMode (effect mounts twice; cleanup closes the first). Verify on a production build |
| UI main-thread load | **CONFIRMED (separate defect)** | a 1 Hz DOM read took 4.7 s median, 14.8 s worst: the page is CPU-bound and renders in bursts |
