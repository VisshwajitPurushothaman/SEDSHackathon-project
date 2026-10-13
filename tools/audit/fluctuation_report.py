"""Audit A1/A2: link statistics and per-layer fluctuation metrics from the captures (read-only).

    python tools/audit/fluctuation_report.py

Inputs (data/audit/): tcp_latest.jsonl (layer 1, raw twin packets as held by the receiver),
backend_out.jsonl (layer 2, what the API/WebSocket sends), ui_values.jsonl (layer 3, rendered text, optional).
Per channel and layer: std of first differences, reversals (sign changes of the difference), max jump,
and the correlation of the layer series with layer 1 (on common timestamps).
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import numpy as np

AUD = Path(__file__).resolve().parents[2] / "data" / "audit"


def load(name: str) -> list[dict]:
    p = AUD / name
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()] if p.exists() else []


def get(d: dict, path: str):
    for k in path.split("."):
        if isinstance(d, list):
            try:
                d = d[int(k)]
            except (ValueError, IndexError):
                return None
        elif isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def metrics(vals: list[float]) -> dict:
    v = np.asarray([x for x in vals if isinstance(x, (int, float))], float)
    if len(v) < 3:
        return {"n": len(v)}
    d = np.diff(v)
    s = np.sign(d[d != 0])
    return {"n": len(v), "std_diff": float(np.std(d)), "reversals": int(np.sum(s[1:] != s[:-1])), "max_jump": float(np.max(np.abs(d))),
            "min": float(v.min()), "max": float(v.max())}


RAW_CH = {
    "alt_m": "gnss_coordinates.geodetic_wgs84.alt_m", "x_downrange_m": "gnss_coordinates.x_downrange_m",
    "z_crossrange_m": "gnss_coordinates.z_crossrange_m", "baro_hpa": "barometric_sensor.pressure_hpa",
    "air_t_c": "environmental_sensors.ambient_temp_c", "pitch_deg": "imu_sensor.attitude_rpy_deg.1",
    "wind_y": "environmental_sensors.effective_wind_vector_mps.1",
}
API_CH = {"GNSS_ALT": "alt_m", "POS_X": "x_downrange_m", "POS_Z": "z_crossrange_m", "BARO_P": "baro_hpa", "AIR_T": "air_t_c",
          "IMU_TILT": "pitch_deg", "GNSS_VU": "wind_y", "TANK_P": None}


def main() -> int:
    raw, api, uiv = load("tcp_latest.jsonl"), load("backend_out.jsonl"), load("ui_values.jsonl")
    out = []
    # ---------------- layer 1: link
    out.append(f"## Layer 1: packets held by the TCP receiver ({len(raw)} distinct)\n")
    if raw:
        pk = [r["packet"] for r in raw]
        disp = [get(p, "tcp_socket_stream.packets_dispatched") for p in pk]
        ts = [p.get("timestamp_ms") for p in pk]
        dev = Counter(p.get("device_id") for p in pk)
        phases = Counter(p.get("flight_phase") for p in pk)
        clients = Counter((r.get("receiver") or {}).get("connected_client") for r in raw)
        dur = raw[-1]["poll_utc"] - raw[0]["poll_utc"]
        out.append(f"- duration {dur:.1f} s; distinct packets/s observed {len(raw) / max(dur, 1e-9):.2f}")
        out.append(f"- device_id: {dict(dev)}; flight_phase: {dict(phases)}")
        out.append(f"- receiver 'connected_client' values seen: {dict(clients)}")
        d_ok = [x for x in disp if isinstance(x, (int, float))]
        if d_ok:
            dd = np.diff(d_ok)
            out.append(f"- sender counter packets_dispatched: first {d_ok[0]}, last {d_ok[-1]}, steps: {dict(Counter(int(x) for x in dd).most_common(6))}, "
                       f"backwards steps {int(np.sum(dd < 0))}, repeats {int(np.sum(dd == 0))}")
        t_ok = [x for x in ts if isinstance(x, (int, float))]
        if t_ok:
            dt = np.diff(t_ok)
            out.append(f"- timestamp_ms steps (ms): {dict(Counter(int(x) for x in dt).most_common(6))}; backwards {int(np.sum(dt < 0))}")
        iso = [get(p, "simulation_state.pause_resume_events.-1.iso_time") for p in pk]
        out.append("")
        out.append("| raw field | n | std of diff | reversals | max jump | min | max |")
        out.append("|---|---|---|---|---|---|---|")
        for name, path in RAW_CH.items():
            m = metrics([get(p, path) for p in pk])
            if m["n"] >= 3:
                out.append(f"| {name} | {m['n']} | {m['std_diff']:.4g} | {m['reversals']} | {m['max_jump']:.4g} | {m['min']:.4g} | {m['max']:.4g} |")
            else:
                out.append(f"| {name} | {m['n']} | - | - | - | - | - |")
    # ---------------- layer 2: API
    snaps = [a["snap"] for a in api if "snap" in a]
    out.append(f"\n## Layer 2: API snapshots ({len(snaps)} polls)\n")
    if snaps:
        out.append(f"- source values: {dict(Counter(s.get('source') for s in snaps))}; mission_id: {dict(Counter(s.get('mission_id') for s in snaps))}")
        out.append(f"- link.rate_hz values: {dict(Counter((s.get('link') or {}).get('rate_hz') for s in snaps))}; "
                   f"link.loss_pct values: {dict(Counter((s.get('link') or {}).get('loss_pct') for s in snaps))}")
        ages = [(s.get("link") or {}).get("last_frame_age_s") for s in snaps]
        ages = [a for a in ages if isinstance(a, (int, float))]
        if ages:
            out.append(f"- last_frame_age_s: median {statistics.median(ages):.2f}, max {max(ages):.2f}")
        out.append("")
        out.append("| channel | n (changes) | std of diff | reversals | max jump | min | max |")
        out.append("|---|---|---|---|---|---|---|")
        for m_ in API_CH:
            series, last = [], object()
            for s in snaps:
                v = next((c["value"] for c in s.get("channels") or [] if c["mnemonic"] == m_), None)
                if v != last:
                    series.append(v)
                    last = v
            m = metrics(series)
            if m["n"] >= 3:
                out.append(f"| {m_} | {m['n']} | {m['std_diff']:.4g} | {m['reversals']} | {m['max_jump']:.4g} | {m['min']:.4g} | {m['max']:.4g} |")
            else:
                out.append(f"| {m_} | {m['n']} | - | - | - | - | - |")
    # ---------------- layer 3: UI
    out.append(f"\n## Layer 3: rendered UI values ({len(uiv)} samples)\n")
    if uiv:
        keys = sorted({k for u in uiv for k in u.get("values", {})})
        out.append("| UI item | n (changes) | std of diff | reversals | max jump |")
        out.append("|---|---|---|---|---|")
        for k in keys:
            series, last = [], object()
            for u in uiv:
                v = u["values"].get(k)
                if v != last:
                    series.append(v)
                    last = v
            m = metrics(series)
            out.append(f"| {k} | {m['n']} | {m.get('std_diff', float('nan')):.4g} | {m.get('reversals', '-')} | {m.get('max_jump', float('nan')):.4g} |")
    text = "\n".join(out)
    (AUD / "fluctuation_metrics.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
