"""Launch-commit gates, illustrative rocket model, parachute descent, geodesy."""
import math
from pathlib import Path

import pytest

from aerofire.config import load_config
from aerofire.physics import atmosphere as A
from aerofire.physics import descent as D
from aerofire.physics import gates as G
from aerofire.physics import geo
from aerofire.physics import rocket as R

CFG = load_config()
SNAP = A.load_snapshot(str(Path(__file__).resolve().parent.parent / "config" / "atmosphere_snapshot.json"), CFG.atmosphere.levels_hpa)
RP = R.RocketParams.from_config(CFG)


# --- gates ------------------------------------------------------------------------------------


def test_float_gate():
    band = CFG.gates.release_band_m
    assert G.float_gate(28000, 0.2, band, 1.0).passed
    assert not G.float_gate(28000, 1.5, band, 1.0).passed
    assert not G.float_gate(24000, 0.0, band, 1.0).passed
    assert G.float_gate(None, None, band, 1.0).passed is None


def test_nav_gate_thresholds_are_chi_square_999():
    from scipy import stats

    assert CFG.gates.chi2_2dof_999 == pytest.approx(stats.chi2.ppf(0.999, 2), abs=1e-3)
    assert CFG.gates.chi2_1dof_999 == pytest.approx(stats.chi2.ppf(0.999, 1), abs=1e-3)


def test_nav_gate_mean_residual():
    s = CFG.sensors
    ok = [(1.0, -1.0, 5.0)] * 10
    assert G.nav_gate(ok, s, 13.816, 10.828).passed
    var_h = s.gnss_sigma_h_m**2 + s.navic_sigma_h_m**2
    # Mean horizontal offset just past the 99.9 % gate for N = 10 epochs:
    off = math.sqrt(13.816 * var_h / 10) * 1.05
    assert not G.nav_gate([(off, 0.0, 0.0)] * 10, s, 13.816, 10.828).passed
    assert G.nav_gate([(off * 0.9, 0.0, 0.0)] * 10, s, 13.816, 10.828).passed


def test_motor_and_swing_gates():
    assert G.motor_gate(-9.9, -10.0).passed and not G.motor_gate(-10.1, -10.0).passed
    assert G.swing_gate(1.49, 1.5).passed and not G.swing_gate(1.51, 1.5).passed


def test_recommendation_logic():
    passing = {k: G.GateResult(k, k, True, 0, 0, 0, "", "") for k in G.GATE_ORDER}
    tank_ok = G.tank_criterion(False, None, None, True)
    assert G.recommend(passing, tank_ok, all_pass_since=0.0, t=10.0, hold_s=10.0) == "GO_RECOMMENDED"
    assert G.recommend(passing, tank_ok, all_pass_since=0.0, t=9.0, hold_s=10.0) == "NO_GO"
    failing = dict(passing, swing=G.GateResult("swing", "s", False, 2, 1.5, -0.5, "", ""))
    assert G.recommend(failing, tank_ok, all_pass_since=None, t=100.0, hold_s=10.0) == "NO_GO"
    leak = G.tank_criterion(True, 50.0, -1e-5, True)
    assert G.recommend(passing, leak, all_pass_since=0.0, t=100.0, hold_s=10.0) == "ABORT_RECOMMENDED"


def test_no_gate_value_named_ignition_confirmed():
    import inspect

    src = inspect.getsource(G)
    assert "IGNITION_CONFIRMED" not in src.replace("Rename", "")


# --- geo -------------------------------------------------------------------------------------


def test_enu_roundtrip_and_offset():
    lat0, lon0 = CFG.site.lat_deg, CFG.site.lon_deg
    lat, lon = geo.enu_to_latlon(12_345.0, -6_789.0, lat0, lon0)
    e, n = geo.latlon_to_enu(lat, lon, lat0, lon0)
    assert (e, n) == pytest.approx((12_345.0, -6_789.0), abs=1e-6)
    # 1 degree of latitude near 13.7 N is ~110.6 km (WGS84 meridional radius)
    M, _ = geo.radii(lat0)
    assert math.radians(1.0) * M == pytest.approx(110_640, abs=100)


def test_point_in_polygon():
    poly = CFG.gates.approved_area
    assert geo.point_in_polygon(13.8, 80.5, poly)
    assert not geo.point_in_polygon(12.0, 80.5, poly)
    assert not geo.point_in_polygon(13.8, 83.0, poly)


# --- rocket (illustrative) --------------------------------------------------------------------


def test_rocket_vacuum_delta_v_matches_rocket_equation():
    """No drag, vertical launch, gravity small vs thrust: burnout speed ~ Isp g0 ln(m0/mf) - g t_b."""
    p = R.RocketParams(15.0, 10.0, 210.0, 8.0, 0.12, 0.0, 90.0, 90.0, 3.0)
    r = R.simulate(p, 28000.0, SNAP.at, dt=0.01)
    dv = 210.0 * 9.80665 * math.log(25.0 / 15.0) - A.gravity(30000.0) * 8.0
    assert r.burnout_v_m_s == pytest.approx(dv, rel=0.01)


def test_rocket_nominal_outputs_are_labelled_and_sane():
    r = R.simulate(RP, 28000.0, SNAP.at)
    assert r.label == "illustrative model, not validated"
    assert r.apogee_m > 28000.0 and r.range_m > 0.0
    assert 0.0 < r.max_q_pa < 50_000.0  # ascent max-Q at 28 km release is small
    assert r.reentry_max_q_pa > r.max_q_pa


def test_failure_cases_land_shorter():
    nominal = R.simulate(RP, 28000.0, SNAP.at)
    early = R.simulate(RP, 28000.0, SNAP.at, burn_fraction=0.3)
    dud = R.simulate(RP, 28000.0, SNAP.at, ignite=False)
    assert dud.range_m == pytest.approx(0.0, abs=1.0)
    assert dud.range_m < early.range_m < nominal.range_m


def test_impact_points_and_gate():
    pts = G.impact_points(RP, CFG.site.lat_deg, CFG.site.lon_deg, 28000.0, SNAP.at, CFG.gates.early_burnout_fractions,
                          CFG.gates.approved_area, dt=0.2)
    assert [p.case for p in pts][0] == "nominal" and pts[-1].case == "no ignition"
    assert G.impact_gate(pts).passed
    far = G.impact_points(RP, 13.1, 79.0, 28000.0, SNAP.at, [0.3], CFG.gates.approved_area, dt=0.2)
    assert not G.impact_gate(far).passed


# --- descent ----------------------------------------------------------------------------------


def test_terminal_velocity_formula():
    p = D.ParachuteParams(1.5, 6.0)
    a = A.AtmoSample(288.15, 101325.0, 1.225, 0.0, 0.0, "isa")
    v = D.terminal_velocity(p, 50.0, a, 0.0)
    assert v == pytest.approx(math.sqrt(2 * 50 * 9.80665 / (1.225 * 1.5 * math.pi * 9.0)), rel=1e-12)


def test_descent_lands_and_drifts_with_wind():
    p = D.ParachuteParams(CFG.descent.parachute_cd, CFG.descent.parachute_diameter_m)
    windy = A.AtmosphereProfile(z=[0.0, 20000.0, 40000.0], lnp=[math.log(101325), math.log(5500), math.log(300)],
                                T=[288.0, 217.0, 250.0], u=[5.0, 5.0, 5.0], v=[0.0, 0.0, 0.0], label="test")
    t, e, n, path = D.descend(p, 50.0, 28000.0, 0.0, 0.0, windy.at)
    assert path[-1][1] == pytest.approx(0.0)
    assert e == pytest.approx(5.0 * t, rel=1e-6) and n == pytest.approx(0.0)
    assert 20 * 60 < t < 90 * 60
