"""Deterministic local demo/test server; same API and physics, recorded atmosphere."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aerofire.api import app as application
from aerofire.api import missions
from aerofire.physics.atmosphere import load_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    cfg = application.CFG
    profile = load_snapshot(str(Path(__file__).resolve().parents[1] / "config/atmosphere_snapshot.json"),
                            cfg.atmosphere.levels_hpa, cfg.atmosphere.blend_m)
    application.current_profile = lambda: profile
    missions.profile_for = lambda source, base: profile
    import uvicorn
    print(f"Recorded atmosphere: {profile.label}; no weather network requests", flush=True)
    uvicorn.run(application.app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
