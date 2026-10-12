#!/usr/bin/env python3
"""
AERO-FIRE: ground station API for a balloon-launched rocket (rockoon).

Entry point kept for `python backend.py`. The application lives in the
`aerofire` package:
  aerofire/physics    verified physical models (docs/PHYSICS.md)
  aerofire/sim        truth simulator (never imported by the ground code)
  aerofire/ground     ground station: estimates from telemetry only
  aerofire/api        FastAPI app

Environment: AEROFIRE_HOST (default 127.0.0.1), AEROFIRE_PORT (default 8000).
"""
import os

from aerofire.api.app import app  # noqa: F401  (re-exported for `uvicorn backend:app`)
from aerofire.api.legacy import execute_flight_engine  # noqa: F401  (legacy name, new physics)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("AEROFIRE_HOST", "127.0.0.1"), port=int(os.environ.get("AEROFIRE_PORT", "8000")))
