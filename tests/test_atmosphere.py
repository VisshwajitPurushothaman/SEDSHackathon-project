"""US Standard Atmosphere 1976 and Open-Meteo profile handling."""
import datetime as dt
import math
from pathlib import Path

import pytest

from aerofire.config import load_config
from aerofire.physics import atmosphere as A

CFG = load_config()
SNAPSHOT = Path(__file__).resolve().parent.parent / "config" / "atmosphere_snapshot.json"


def rel(a, b):
    return abs(a - b) / abs(b)


# --- USSA76 table values (NOAA-S/T 76-1562, Table 4), geopotential altitude ---------------


@pytest.mark.parametrize(
    "h, T, p",
    [
        (0.0, 288.15, 101325.0),
        (11000.0, 216.65, 22632.06),
        (20000.0, 216.65, 5474.889),
        (32000.0, 228.65, 868.0187),
        (47000.0, 270.65, 110.9063),
        (51000.0, 270.65, 66.93887),
        (71000.0, 214.65, 3.956420),
    ],
)
def test_isa_layer_bases(h, T, p):
    T_, p_, _ = A.isa_geopotential(h)
    assert T_ == pytest.approx(T, abs=0.01)
    assert rel(p_, p) < 0.005  # brief: +-0.5 %; actual agreement is ~1e-6


def test_isa_sea_level_density():
    assert A.isa(0.0)[2] == pytest.approx(1.225, abs=0.0005)


def test_constants_match_config():
    c = CFG.constants
    assert A.G0 == c.g0 and A.R_STAR == c.R_star and A.M_AIR == c.M_air and A.R_AIR == c.R_air and A.R0_EARTH == c.r0_earth


def test_geopotential_conversion_roundtrip():
    for z in (0.0, 11000.0, 28000.0, 80000.0):
        assert A.geopotential_to_geometric(A.geometric_to_geopotential(z)) == pytest.approx(z, abs=1e-6)
    # Geometric 11 km is geopotential ~10 981 m.
    assert A.geometric_to_geopotential(11000.0) == pytest.approx(10981.0, abs=1.0)


def test_10hpa_is_near_31km_not_26km():
    """Regression for the old fallback `z = 44330 (1 - (p/p0)^0.1903)`, which put 10 hPa at 25.9 km."""
    old = 44330.0 * (1.0 - (1000.0 / 101325.0) ** 0.1903)
    assert old == pytest.approx(25922, abs=5)
    h = A._isa_geopotential_for_pressure(1000.0)
    assert h == pytest.approx(31055, abs=20)
    assert A.isa_altitude_for_pressure(1000.0) == pytest.approx(31207, abs=20)  # geometric


def test_pressure_inverse():
    for z in (100.0, 9000.0, 25000.0, 40000.0, 60000.0):
        assert A.isa_altitude_for_pressure(A.isa(z)[1]) == pytest.approx(z, abs=0.01)


def test_gravity_decreases_with_altitude():
    assert A.gravity(0.0) == A.G0
    assert A.gravity(30000.0) == pytest.approx(A.G0 * (A.R0_EARTH / (A.R0_EARTH + 30000.0)) ** 2)


def test_wind_components_convention():
    u, v = A.wind_components(10.0, 90.0)  # wind FROM the east blows towards the west
    assert u == pytest.approx(-10.0) and v == pytest.approx(0.0, abs=1e-9)
    u, v = A.wind_components(10.0, 0.0)  # from the north -> towards south
    assert v == pytest.approx(-10.0)
    s = A.AtmoSample(1, 1, 1, -3.0, 4.0, "x")
    assert s.wind_speed == pytest.approx(5.0)
    assert A.wind_components(s.wind_speed, s.wind_from_deg) == pytest.approx((-3.0, 4.0))


# --- Open-Meteo parsing ----------------------------------------------------------------------


def _payload(times, levels):
    """levels: {hPa: (z_gpm, T_c, ws, wd)} applied to every hour unless value is a list."""
    hourly = {"time": times}
    for lvl, (z, t, ws, wd) in levels.items():
        for key, val in (("geopotential_height", z), ("temperature", t), ("wind_speed", ws), ("wind_direction", wd)):
            hourly[f"{key}_{lvl}hPa"] = val if isinstance(val, list) else [val] * len(times)
    return {"hourly": hourly}


def test_nearest_hour_not_index_zero():
    times = ["2026-10-12T00:00", "2026-10-12T01:00", "2026-10-12T02:00"]
    pay = _payload(times, {1000: ([100, 110, 120], 20, 5, 90), 500: (5800, -10, 10, 90), 100: (16500, -75, 20, 90)})
    prof = A.profile_from_open_meteo(pay, [1000, 500, 100], now=dt.datetime(2026, 10, 12, 1, 40, tzinfo=dt.timezone.utc))
    assert prof.meta["valid_time_utc"] == "2026-10-12T02:00"
    assert prof.z[0] == pytest.approx(A.geopotential_to_geometric(120))
    assert prof.label == "open-meteo@2026-10-12T02:00Z"


def test_missing_levels_are_dropped_and_reported():
    times = ["2026-10-12T00:00"]
    pay = _payload(times, {1000: (100, 20, 5, 90), 500: (5800, -10, 10, 90), 100: (16500, -75, 20, 90), 10: (None, None, None, None)})
    pay["hourly"].pop("temperature_20hPa", None)
    prof = A.profile_from_open_meteo(pay, [1000, 500, 100, 20, 10], now=dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc))
    assert len(prof.z) == 3
    assert "10" in prof.note and "20" in prof.note


def test_too_few_levels_raises():
    pay = _payload(["2026-10-12T00:00"], {1000: (100, 20, 5, 90)})
    with pytest.raises(ValueError):
        A.profile_from_open_meteo(pay, [1000], now=dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc))


def test_lnp_interpolation_is_geometric_mean():
    pay = _payload(["2026-10-12T00:00"], {1000: (0, 15, 0, 0), 500: (5500, -20, 0, 0), 100: (16000, -70, 0, 0)})
    prof = A.profile_from_open_meteo(pay, [1000, 500, 100], now=dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc))
    z_mid = 0.5 * (prof.z[0] + prof.z[1])
    assert prof.at(z_mid).p == pytest.approx(math.sqrt(100000.0 * 50000.0), rel=1e-9)
    # Linear-in-p would give 75 000 Pa; ln-p gives 70 711 Pa.
    assert prof.at(z_mid).p < 71000


def test_blend_is_continuous_and_labelled():
    prof = A.load_snapshot(str(SNAPSHOT), CFG.atmosphere.levels_hpa, blend_m=2000.0)
    top = prof.z_top
    below, above = prof.at(top - 1e-6), prof.at(top + 1e-6)
    assert above.p == pytest.approx(below.p, rel=1e-6) and above.T == pytest.approx(below.T, abs=1e-4)
    assert prof.at(top + 1000).source == "blend"
    end_in, end_out = prof.at(top + 2000 - 1e-6), prof.at(top + 2000 + 1e-6)
    assert end_out.source == "isa-fallback"
    assert end_out.p == pytest.approx(end_in.p, rel=1e-5)
    assert end_out.T == pytest.approx(A.isa(top + 2000)[0], abs=1e-3)
    assert prof.at(1000).source.startswith("open-meteo@")


def test_isa_only_profile_is_labelled_and_windless():
    s = A.AtmosphereProfile().at(12000.0)
    assert s.source == "isa-fallback" and s.u == 0.0 and s.v == 0.0
    assert s.p == pytest.approx(A.isa(12000.0)[1])


def test_fetch_failure_is_not_silent(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr(A, "_get_json", boom)
    A._cache.clear()
    prof = A.fetch_profile(CFG, 1.0, 2.0)
    assert not prof.is_live
    assert "Open-Meteo unavailable" in prof.note and "network down" in prof.note
    assert prof.at(1000).source == "isa-fallback"


def test_snapshot_profile_is_physical():
    prof = A.load_snapshot(str(SNAPSHOT), CFG.atmosphere.levels_hpa)
    zs = [prof.at(z) for z in range(0, 40000, 500)]
    assert all(a.p > b.p for a, b in zip(zs, zs[1:])), "pressure must fall monotonically"
    assert all(150 < s.T < 320 for s in zs)
    # Hydrostatic sanity: dp/dz = -rho g within 5 % between 1 and 30 km.
    for z in range(1000, 30000, 1000):
        s0, s1 = prof.at(z - 50), prof.at(z + 50)
        dpdz = (s1.p - s0.p) / 100.0
        rho = prof.at(z).rho
        assert dpdz == pytest.approx(-rho * A.gravity(z), rel=0.05)
