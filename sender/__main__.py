"""Laptop A: vehicle digital twin and downlink sender (ICD v1, docs/ICD.md).

    python -m sender --target 192.168.1.20 --scenario nominal --speed 20
    python -m sender --target 127.0.0.1 --replay data/vehicle/<mission>.jsonl

Console commands while running: commit, terminate, status, quit.
The ground station cannot command this process; it only receives.
"""
from __future__ import annotations

import argparse
import json
import queue
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from aerofire.config import load_config, load_raw
from aerofire.physics import atmosphere as A
from aerofire.receiver.service import read_lines
from aerofire.telemetry import icd

from .vehicle import AUTO_ACTIONS, SCENARIOS, Vehicle, rate_effective, vehicle_float_auto

ROOT = Path(__file__).resolve().parents[1]
MAX_RECOVER = 2000


class Onboard:
    """What the HTTP endpoints can see: recorded frames, manifest, truth after the mission."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.frames: list[dict[str, Any]] = []
        self.manifest: dict[str, Any] = {}
        self.truth: dict[str, Any] | None = None  # set only when the mission has finished


def serve_http(onboard: Onboard, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: Any) -> None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            with onboard.lock:
                if url.path == "/manifest":
                    return self._send(200, onboard.manifest)
                if url.path == "/recover":
                    try:
                        a, b = int(q.get("from_seq", 0)), int(q.get("to_seq", len(onboard.frames) - 1))
                    except ValueError:
                        return self._send(422, {"detail": "from_seq/to_seq must be integers"})
                    b = min(b, a + MAX_RECOVER - 1, len(onboard.frames) - 1)
                    return self._send(200, {"frames": onboard.frames[max(a, 0): b + 1] if b >= a else []})
                if url.path == "/truth":
                    if onboard.truth is None:
                        return self._send(403, {"detail": "truth is available for grading only after the mission has finished"})
                    return self._send(200, onboard.truth)
            return self._send(404, {"detail": "not found"})

        def log_message(self, *args: Any) -> None:  # keep the console readable
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="recovery-http").start()
    return server


def stdin_commands(q: "queue.Queue[str]") -> None:
    for line in sys.stdin:
        q.put(line.strip().lower())


def run_live(args: argparse.Namespace, onboard: Onboard, sock: socket.socket) -> None:
    raw = load_raw()
    cfg = load_config()
    if args.atmosphere == "snapshot":
        profile = A.load_snapshot(str(ROOT / "config" / "atmosphere_snapshot.json"), raw["atmosphere"]["levels_hpa"], raw["atmosphere"]["blend_m"])
    else:
        profile = A.fetch_profile(cfg, cfg.site.lat_deg, cfg.site.lon_deg)
    v = Vehicle(cfg, profile, args.scenario, args.seed, args.speed, packet_loss=args.loss)
    out = Path(args.out or ROOT / "data" / "vehicle" / f"{v.mission_id}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"AERO-FIRE vehicle {v.mission_id}: scenario {args.scenario}, seed {args.seed}, speed {args.speed}x, "
          f"atmosphere {profile.label} -> udp://{args.target}:{args.port}; onboard log {out}", flush=True)
    commands: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=stdin_commands, args=(commands,), daemon=True).start()
    period = 1.0 / rate_effective(args.rate, args.speed, cfg.sim.dt_s)
    auto_done = args.manual
    t0 = time.monotonic()
    next_tick, last_status = t0, 0.0
    with out.open("w", encoding="utf-8") as log:
        while not v.finished:
            target = (time.monotonic() - t0) * args.speed
            new: list[dict[str, Any]] = []
            while v.sim.t < target and not v.finished:
                v.step()
                new.append(v.next_frame(time.time()))
            if not new:
                new.append(v.next_frame(time.time()))  # heartbeat: same MET and values
            for frame, delivered in new:
                log.write(json.dumps(frame, separators=(",", ":")) + "\n")
                if delivered:
                    sock.sendto(icd.encode(frame), (args.target, args.port))
            with onboard.lock:
                onboard.frames = v.frames
                onboard.manifest = v.manifest()
            # Vehicle/range-side actions: explicit times, console commands, or the scenario's automatic action.
            for action, at in (("commit", args.commit_at), ("terminate", args.terminate_at)):
                if at is not None and v.met >= at and not any(a["action"] == action for a in v.actions):
                    print(f"[MET {v.met:7.0f}] {v.act(action, 'schedule')}", flush=True)
                    auto_done = True
            if not auto_done and args.commit_at is None and args.terminate_at is None:
                plan = vehicle_float_auto(v)
                if plan and v.met >= plan[1]:
                    print(f"[MET {v.met:7.0f}] automatic {v.act(plan[0], 'scenario')}", flush=True)
                    auto_done = True
            while not commands.empty():
                cmd = commands.get()
                if cmd in ("commit", "terminate"):
                    print(f"[MET {v.met:7.0f}] {v.act(cmd, 'console')}", flush=True)
                    auto_done = True
                elif cmd == "quit":
                    v.finished = True
                elif cmd == "status":
                    last_status = 0.0
            now = time.monotonic()
            if now - last_status >= 5.0:
                last_status = now
                print(f"[MET {v.met:7.0f}] {v.sim.phase:9s} alt {v.ch.get('GNSS_ALT', 0):8.0f} m  frames {len(v.frames)}  "
                      f"sent {v.sent}  lost(blackout {v.dropped_blackout}, random {v.dropped_random})", flush=True)
            next_tick += period
            time.sleep(max(0.0, next_tick - time.monotonic()))
    with onboard.lock:
        onboard.manifest = v.manifest() | {"finished": True}
        onboard.truth = v.truth()
    out.with_suffix(".truth.json").write_text(json.dumps(onboard.truth), encoding="utf-8")
    print(f"mission finished at MET {v.met:.0f} s; {len(v.frames)} frames onboard. Recovery/truth served on :{args.http_port}.", flush=True)


def run_replay(args: argparse.Namespace, onboard: Onboard, sock: socket.socket) -> None:
    path = Path(args.replay)
    frames = [json.loads(line) for line in read_lines(path)]
    for f in frames:
        f.pop("rx", None)
    truth_path = Path(str(path).removesuffix(".gz").removesuffix(".jsonl") + ".truth.json")
    print(f"replaying {len(frames)} frames from {path} -> udp://{args.target}:{args.port}", flush=True)
    t0, first = time.monotonic(), frames[0]["utc_sent"]
    with onboard.lock:
        onboard.frames = frames
    for f in frames:
        time.sleep(max(0.0, (f["utc_sent"] - first) / args.replay_speed - (time.monotonic() - t0)))
        sock.sendto(icd.encode(f), (args.target, args.port))
        with onboard.lock:
            onboard.manifest = {"v": icd.ICD_VERSION, "mission_id": f["mission_id"], "frames": f["seq"] + 1, "first_seq": 0,
                                "last_seq": f["seq"], "last_hash": f["hash"], "finished": False}
    with onboard.lock:
        onboard.manifest["finished"] = True
        onboard.truth = json.loads(truth_path.read_text(encoding="utf-8")) if truth_path.exists() else None
    print("replay finished; recovery served until Ctrl+C", flush=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m sender", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", default="127.0.0.1", help="ground station (Laptop B) IP")
    p.add_argument("--port", type=int, default=5600, help="ground station UDP port")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), default="nominal")
    p.add_argument("--rate", type=float, default=2.0, help="frames per wall second (raised to one per simulation step when --speed is higher)")
    p.add_argument("--speed", type=float, default=1.0, help="mission seconds per wall second (time compression)")
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--atmosphere", choices=["live", "snapshot"], default="live")
    p.add_argument("--loss", type=float, default=None, help="extra random frame loss (default config link.packet_loss)")
    p.add_argument("--commit-at", type=float, default=None, help="vehicle commit (rocket release sequence) at this MET")
    p.add_argument("--terminate-at", type=float, default=None, help="cut-down without launch at this MET")
    p.add_argument("--manual", action="store_true", help="no automatic scenario action; use console commands")
    p.add_argument("--http-host", default="0.0.0.0")
    p.add_argument("--http-port", type=int, default=8100)
    p.add_argument("--out", default=None, help="onboard frame log (default data/vehicle/<mission>.jsonl)")
    p.add_argument("--replay", default=None, help="resend a recorded frame log instead of simulating")
    p.add_argument("--replay-speed", type=float, default=1.0)
    p.add_argument("--exit", action="store_true", help="exit when finished instead of serving recovery until Ctrl+C")
    args = p.parse_args(argv)
    if not args.manual and args.commit_at is None and args.terminate_at is None and not args.replay:
        action, at, after = AUTO_ACTIONS[args.scenario]
        when = f"MET {at:.0f} s" if at is not None and action == "terminate" else f"{after:.0f} s after float" + (f" (not before MET {at:.0f} s)" if at else "")
        print(f"automatic vehicle action for {args.scenario}: {action} {when} (override with --commit-at/--terminate-at/--manual)")

    onboard = Onboard()
    server = serve_http(onboard, args.http_host, args.http_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        (run_replay if args.replay else run_live)(args, onboard, sock)
        if not args.exit:
            while True:
                time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
