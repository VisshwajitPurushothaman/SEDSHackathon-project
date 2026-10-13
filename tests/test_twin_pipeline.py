"""Laptop A twin packets go through the ONE ground pipeline (filter -> frame -> GroundSession -> estimator)."""
import pytest

from aerofire.config import load_config, load_raw
from aerofire.physics import atmosphere as A
from aerofire.physics import geo
from aerofire.receiver.service import Receiver
from aerofire.receiver.twin import TwinFilter, declared_origin, map_phase, twin_channels

CFG = load_config()
RAW = load_raw()
PROFILE = A.load_snapshot("config/atmosphere_snapshot.json", RAW["atmosphere"]["levels_hpa"], RAW["atmosphere"]["blend_m"])


def twin(t_ms, alt, phase="STRATOSPHERIC_ASCENT", east=0.0, north=0.0):
    lat, lon = geo.enu_to_latlon(east, north, CFG.site.lat_deg, CFG.site.lon_deg)
    return {"device_id": "client_node_01", "timestamp_ms": t_ms, "flight_phase": phase, "status": "active",
            "server_target": {"ping_interval_seconds": 0.5},
            "gnss_coordinates": {"launch_site": f"Test pad ({CFG.site.lat_deg:.4f}°N, {CFG.site.lon_deg:.4f}°E)",
                                 "x_downrange_m": east, "y_altitude_m": alt, "z_crossrange_m": north,
                                 "geodetic_wgs84": {"lat_deg": lat, "lon_deg": lon, "alt_m": alt}},
            "barometric_sensor": {"pressure_hpa": PROFILE.at(alt).p / 100.0},
            "imu_sensor": {"accel_mps2": [0.0, 0.0, 9.81], "gyro_dps": [0.01, 0.0, 0.0], "attitude_rpy_deg": [0.0, 88.6, 5.0]},
            "environmental_sensors": {"ambient_temp_c": PROFILE.at(alt).T - 273.15, "effective_wind_vector_mps": [0.1, 0.1, -1.8]},
            "payload_state": {"is_attached": True, "rocket_status": "SLUNG", "balloon_volume_m3": 880000,
                              "differential_superpressure_pa": 69.2, "is_balloon_burst": False}}


def test_phase_mapping_and_origin():
    assert map_phase("PRELAUNCH") == "PAD" and map_phase("STRATOSPHERIC_ASCENT") == "ASCENT"
    assert map_phase("SBRL_RELEASE_WINDOW") == "FLOAT" and map_phase("LEO_ORBIT") == "IGNITION"
    assert map_phase("PARACHUTE_DESCENT") == "DESCENT" and map_phase("SPLASHDOWN") == "LANDED" and map_phase("NEW_THING") is None
    assert declared_origin({"gnss_coordinates": {"launch_site": "Chennai Launch Pad, India (13.0827°N, 80.2707°E)"}}) == (13.0827, 80.2707)
    ch, unknown = twin_channels(twin(1000, 500.0), None)
    assert ch["FC_PHASE"] == "ASCENT" and ch["FC_RKT_PHASE"] == "ABOARD" and unknown is None
    assert ch["IMU_TILT"] == pytest.approx(0.0) and ch["IMU_PITCH"] == 88.6 and "TANK_P" not in ch and "GNSS_VU" not in ch
    ch, unknown = twin_channels(twin(1000, 500.0, "NEW_THING"), "FLOAT")
    assert ch["FC_PHASE"] == "FLOAT" and unknown == "NEW_THING"


def test_twin_stream_reaches_the_estimator_with_measured_link_and_no_signature():
    r = Receiver(CFG, lambda: PROFILE)
    r.mode = "PLAYBACK"  # no session file in tests
    f = TwinFilter()
    utc = 5000.0
    for i in range(240):  # 120 s at 2 Hz, 4.8 m/s climb, with the measured interleaving
        t = 600_000 + 500 * i
        for p in (twin(1_813_295, 18199.0), twin(0, 0.0, "PRELAUNCH"), twin(t, 3000.0 + 2.4 * i, east=1.5 * i)):
            ok, _ = f.offer(p, "172.16.58.215", utc)
            if ok and i % 10 != 7:  # every 10th live packet lost on the link
                r.on_twin_packet(p, "172.16.58.215", utc, f)
        utc += 0.5
    s = r.current
    assert s.mission_id.startswith("twin-client_node_01-e1-")
    snap = s.snapshot(utc)
    E = snap["ground"]["estimates"]
    assert E["altitude"]["status"] == "FUSED" and E["altitude"]["value"] == pytest.approx(3000.0 + 2.4 * 239, abs=15.0)
    assert E["vertical_speed"]["value"] == pytest.approx(4.8, abs=0.5)
    assert set(snap["ground"]["gates"]) == {"float", "nav", "motor", "swing", "impact"}
    L = snap["link"]
    assert L["integrity"].startswith("none") and L["hmac_failures"] == 0 and L["latency_s"] is None
    assert L["loss_pct"] == pytest.approx(10.0, abs=1.5) and len(L["gaps"]) >= 20
    rows = {c["mnemonic"]: c for c in snap["channels"]}
    assert rows["GNSS_ALT"]["status"] == "STALE" or rows["GNSS_ALT"]["status"] == "MEASURED"
    assert rows["TANK_P"]["status"] == "UNKNOWN" and rows["TANK_P"]["value"] is None  # no tank data in twin packets
    assert rows["FC_PHASE_RAW"]["value"] == "STRATOSPHERIC_ASCENT"
    assert all(a["key"] != "origin" for a in s.alarms)  # packet origin matches the config site


def test_origin_mismatch_raises_an_alarm():
    r = Receiver(CFG, lambda: PROFILE)
    r.mode = "PLAYBACK"
    f = TwinFilter()
    for i in range(5):
        p = twin(1000 + 500 * i, 100.0 + i)
        p["gnss_coordinates"]["launch_site"] = "Elsewhere (1.5000°N, 103.8000°E)"
        if f.offer(p, "a", 1.0 + i)[0]:
            r.on_twin_packet(p, "a", 1.0 + i, f)
    assert any(a["key"] == "origin" for a in r.current.alarms)
