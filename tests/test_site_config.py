"""One site: everything site-dependent is derived from config/mission.yaml `site`."""
import json
import re
from pathlib import Path

from aerofire.config import load_config
from aerofire.ground import profile as PF
from aerofire.physics import geo

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config()


def test_approved_area_station_and_zones_follow_the_site():
    s = CFG.site
    assert geo.point_in_polygon(s.lat_deg, s.lon_deg, CFG.gates.approved_area)
    assert (CFG.link.station_lat_deg, CFG.link.station_lon_deg) == (s.lat_deg, s.lon_deg)
    moved = load_config(overrides={"site": {"lat_deg": s.lat_deg + 1.0, "lon_deg": s.lon_deg + 1.0}})
    assert moved.gates.approved_area[0] == [round(CFG.gates.approved_area[0][0] + 1.0, 6), round(CFG.gates.approved_area[0][1] + 1.0, 6)]
    rng = PF.load_range(CFG, ROOT / "config" / "range.yaml")
    assert rng["site"]["lat"] == s.lat_deg and len(rng["zones"]) >= 3
    assert all(max(abs(p[0] - s.lat_deg), abs(p[1] - s.lon_deg)) < 2.0 for z in rng["zones"] for p in z["polygon"])


def test_atmosphere_snapshot_was_recorded_for_the_site():
    snap = json.loads((ROOT / "config" / "atmosphere_snapshot.json").read_text(encoding="utf-8"))
    lat = float(re.search(r"latitude=([-\d.]+)", snap["request_url"]).group(1))
    lon = float(re.search(r"longitude=([-\d.]+)", snap["request_url"]).group(1))
    assert abs(lat - CFG.site.lat_deg) < 1e-3 and abs(lon - CFG.site.lon_deg) < 1e-3
