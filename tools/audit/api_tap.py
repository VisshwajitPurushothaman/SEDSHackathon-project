"""Audit tap (read-only): poll the running ground-station API and log what it receives and emits.

    python tools/audit/api_tap.py --seconds 120 --hz 20 --api http://127.0.0.1:8000

Writes, one JSON per line:
  data/audit/backend_out.jsonl   every /api/ground/state snapshot (what the UI is sent)
  data/audit/tcp_latest.jsonl    the latest raw TCP digital-twin packet held by the receiver, logged
                                 whenever it differs from the previous poll (consecutive dedup), with the
                                 receiver's connection info. NOTE: the receiver may have modified the
                                 packet (gnss block recomputed / merged) before storing it.
Changes no application state; it only issues GET requests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "audit"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--hz", type=float, default=20.0)
    a = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    seen_last = None
    n = changes = errors = 0
    t_end = time.time() + a.seconds
    with (OUT / "backend_out.jsonl").open("w", encoding="utf-8") as out, (OUT / "tcp_latest.jsonl").open("w", encoding="utf-8") as raw:
        while time.time() < t_end:
            t0 = time.time()
            try:
                with urllib.request.urlopen(a.api + "/api/ground/state", timeout=2.0) as r:
                    snap = json.loads(r.read().decode("utf-8"))
            except Exception as exc:
                errors += 1
                out.write(json.dumps({"poll_utc": t0, "error": str(exc)}) + "\n")
                time.sleep(1.0 / a.hz)
                continue
            n += 1
            dt = snap.pop("digital_twin_telemetry", None)
            out.write(json.dumps({"poll_utc": t0, "snap": snap}, separators=(",", ":")) + "\n")
            if dt is not None:
                key = hashlib.sha256(json.dumps(dt, sort_keys=True).encode()).hexdigest()
                if key != seen_last:
                    seen_last = key
                    changes += 1
                    raw.write(json.dumps({"poll_utc": t0, "receiver": snap.get("digital_twin"), "packet": dt}, separators=(",", ":")) + "\n")
            time.sleep(max(0.0, 1.0 / a.hz - (time.time() - t0)))
    print(f"polls {n}, distinct packets {changes}, errors {errors} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
