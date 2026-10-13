#!/usr/bin/env python3
"""
AERO-FIRE: ground station API for a balloon-launched rocket (rockoon).

Entry point kept for `python backend.py`. The application lives in the
`aerofire` package:
  aerofire/physics    verified physical models (docs/PHYSICS.md)
  aerofire/sim        truth simulator (never imported by the ground code)
  aerofire/ground     ground station: estimates from telemetry only
  aerofire/api        FastAPI app

    aerofire/receiver  ICD v1 telemetry receiver (UDP or session playback)

    python backend.py                         # receive UDP frames on 0.0.0.0:5600
    python backend.py --playback data/demo_sessions/nominal.jsonl --playback-speed 10
    python backend.py --snapshot              # recorded forecast atmosphere (no weather network)

Environment: AEROFIRE_HOST (default 127.0.0.1), AEROFIRE_PORT (default 8000).
"""
import argparse
import os

from aerofire.api.app import app  # noqa: F401  (re-exported for `uvicorn backend:app`)
from aerofire.api.legacy import execute_flight_engine  # noqa: F401  (legacy name, new physics)

if __name__ == "__main__":
    import uvicorn

    p = argparse.ArgumentParser(description="AERO-FIRE ground station (Laptop B)")
    p.add_argument("--host", default=os.environ.get("AEROFIRE_HOST", "127.0.0.1"), help="API/UI bind address")
    p.add_argument("--port", type=int, default=int(os.environ.get("AEROFIRE_PORT", "8000")))
    p.add_argument("--udp-host", default="0.0.0.0", help="telemetry receive address")
    p.add_argument("--udp-port", type=int, default=5600)
    p.add_argument("--no-udp", action="store_true", help="do not listen for telemetry")
    p.add_argument("--tcp-host", default=os.environ.get("AEROFIRE_TCP_HOST", "0.0.0.0"), help="TCP digital twin telemetry bind address")
    p.add_argument("--tcp-port", type=int, default=int(os.environ.get("AEROFIRE_TCP_PORT", "65432")), help="TCP telemetry port")
    p.add_argument("--no-tcp", action="store_true", help="do not listen for TCP digital twin telemetry")
    p.add_argument("--playback", default=None, help="replay a recorded session file instead of listening")
    p.add_argument("--playback-speed", type=float, default=1.0)
    p.add_argument("--snapshot", action="store_true", help="use the recorded forecast atmosphere")
    a = p.parse_args()
    if a.snapshot:
        os.environ["AEROFIRE_ATMOSPHERE"] = "snapshot"
    if a.no_tcp:
        os.environ["AEROFIRE_NO_TCP"] = "1"
    else:
        os.environ["AEROFIRE_TCP_HOST"] = a.tcp_host
        os.environ["AEROFIRE_TCP_PORT"] = str(a.tcp_port)
    if a.playback:
        os.environ.update(AEROFIRE_RECEIVER="playback", AEROFIRE_PLAYBACK=a.playback, AEROFIRE_PLAYBACK_SPEED=str(a.playback_speed))
    elif not a.no_udp:
        os.environ.update(AEROFIRE_RECEIVER="udp", AEROFIRE_UDP_HOST=a.udp_host, AEROFIRE_UDP_PORT=str(a.udp_port))
    uvicorn.run(app, host=a.host, port=a.port)
