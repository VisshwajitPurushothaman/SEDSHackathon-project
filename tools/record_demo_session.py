"""Record a demo ground session (deterministic, no network): vehicle twin -> ICD frames -> receiver.

    python tools/record_demo_session.py nominal --speed 20
    python tools/record_demo_session.py tank_leak_100um --speed 20

Writes data/demo_sessions/<scenario>.jsonl.gz (what Laptop B recorded, for `backend.py --playback`)
plus <scenario>.vehicle.jsonl.gz and <scenario>.truth.json (Laptop A side, for `python -m sender --replay`).
Wall-clock times are simulated: frames are sent at MET / speed and received 20 ms later; frames lost on
the emulated link (blackouts, random loss) are not received.
"""
import argparse
import gzip
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aerofire.config import load_config, load_raw  # noqa: E402
from aerofire.physics import atmosphere as A  # noqa: E402
from aerofire.receiver.service import Receiver  # noqa: E402
from sender.vehicle import SCENARIOS, Vehicle, vehicle_float_auto  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", choices=sorted(SCENARIOS))
    p.add_argument("--speed", type=float, default=20.0)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--out", default=str(ROOT / "data" / "demo_sessions"))
    a = p.parse_args()
    cfg, raw = load_config(), load_raw()
    profile = A.load_snapshot(str(ROOT / "config" / "atmosphere_snapshot.json"), raw["atmosphere"]["levels_hpa"], raw["atmosphere"]["blend_m"])
    v = Vehicle(cfg, profile, a.scenario, a.seed, a.speed, mission_id=f"demo-{a.scenario}"[:24])
    tmp = Path(tempfile.mkdtemp())
    rx = Receiver(cfg, lambda: profile, sessions_dir=tmp)
    t0, done_action, wall = 1_800_000_000.0, False, time.time()
    while not v.finished:
        v.step()
        f, delivered = v.next_frame(t0 + v.met / a.speed)
        if delivered:
            rx.on_frame(f, f["utc_sent"] + 0.02)
        rx.clock = lambda: t0 + v.met / a.speed + 0.02
        rx.tick()
        plan = vehicle_float_auto(v)
        if not done_action and plan and v.met >= plan[1]:
            print(f"MET {v.met:.0f}: {v.act(plan[0], 'scenario')}")
            done_action = True
    s = rx.current
    s.close()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with s.path.open("rb") as src, gzip.open(out / f"{a.scenario}.jsonl.gz", "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    with gzip.open(out / f"{a.scenario}.vehicle.jsonl.gz", "wt", encoding="utf-8", compresslevel=9) as fh:
        for f in v.frames:
            fh.write(json.dumps(f, separators=(",", ":")) + "\n")
    (out / f"{a.scenario}.truth.json").write_text(json.dumps(v.truth()), encoding="utf-8")
    link = s.link(rx.clock())
    print(f"{a.scenario}: MET {v.met:.0f} s, {len(v.frames)} frames onboard, {link['frames_received']} received, "
          f"{len(link['los_periods'])} LOS, recommendation seen: {sorted({r['rec'] for r in s.history})}, "
          f"{len(s.alarms)} alarms, {time.time() - wall:.0f} s")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
