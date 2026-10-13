"""Re-record config/atmosphere_snapshot.json for the configured launch site (same Open-Meteo request,
latitude/longitude taken from config/mission.yaml `site`).

    python tools/record_atmosphere_snapshot.py
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aerofire.config import load_config  # noqa: E402

SNAP = ROOT / "config" / "atmosphere_snapshot.json"


def main() -> int:
    cfg = load_config()
    old = json.loads(SNAP.read_text(encoding="utf-8"))
    url = re.sub(r"latitude=[-\d.]+", f"latitude={cfg.site.lat_deg:.4f}", old["request_url"])
    url = re.sub(r"longitude=[-\d.]+", f"longitude={cfg.site.lon_deg:.4f}", url)
    with urllib.request.urlopen(url, timeout=20) as r:  # noqa: S310 (fixed public API)
        payload = json.loads(r.read().decode("utf-8"))
    out = {"recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "request_url": url,
           "site": {"name": cfg.site.name, "lat_deg": cfg.site.lat_deg, "lon_deg": cfg.site.lon_deg},
           "note": old.get("note", "Raw Open-Meteo forecast response recorded for deterministic tests and offline demos."),
           "payload": payload}
    SNAP.write_text(json.dumps(out), encoding="utf-8")
    print(f"recorded {SNAP} for {cfg.site.name} ({cfg.site.lat_deg}, {cfg.site.lon_deg})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
