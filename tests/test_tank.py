"""Pressurant tank physics and the ground-side leak detector."""
import math

import numpy as np
import pytest

from aerofire.config import load_config
from aerofire.ground.leak_detector import LeakDetector, window_tstats
from aerofire.physics import tank as tk
from aerofire.sim import leak_mc

CFG = load_config()
PRM = tk.TankParams.from_config(CFG)
IDEAL = tk.TankParams(V=PRM.V, B2=0.0, C_wall=0.0, UA_sl=0.0, UA_floor=0.0)


def test_critical_pressure_ratio_helium():
    assert tk.critical_pressure_ratio(5.0 / 3.0) == pytest.approx(0.4871, abs=1e-4)
    assert tk.is_choked(200e5, 101325.0, PRM.gamma)
    assert not tk.is_choked(1.5e5, 101325.0, PRM.gamma)


def test_choked_flow_hand_value():
    """50 um hole, 200 bar, 299.15 K, ideal gas (Z = 1), Cd = 0.62:
    A = 1.9635e-9 m^2; sqrt(1.667 / (2077.1 * 299.15)) = 1.6380e-3; (2/2.667)^(2.667/1.334) = 0.5626
    mdot = 0.62 * 1.9635e-9 * 2e7 * 1.6380e-3 * 0.5626 = 2.244e-5 kg/s"""
    a = tk.diameter_to_area(50e-6)
    mdot = tk.orifice_mass_flow(tk.TankParams(V=0.01, B2=0.0), 200e5, 299.15, 101325.0, a)
    assert mdot == pytest.approx(2.244e-5, rel=2e-3)


def test_subsonic_branch_is_continuous_at_critical_ratio():
    P, T, a = 2e5, 290.0, 1e-6
    r_star = tk.critical_pressure_ratio(IDEAL.gamma)
    choked = tk.orifice_mass_flow(IDEAL, P, T, P * r_star * 0.999999, a)
    sub = tk.orifice_mass_flow(IDEAL, P, T, P * r_star * 1.000001, a)
    assert sub == pytest.approx(choked, rel=1e-4)
    assert tk.orifice_mass_flow(IDEAL, P, T, P, a) == 0.0


def test_compressibility_near_200_bar():
    """Ideal gas overstates He mass at 200 bar by ~9 % (virial Z ~ 1.09).
    NOT cross-checked against NIST REFPROP here; do that before relying on the absolute mass."""
    Z = tk.compressibility(200e5, 300.0, CFG.constants.B2_He_m3_per_mol)
    assert 1.08 < Z < 1.11
    m_real = tk.mass_from_pt(PRM, 200e5, 300.0)
    m_ideal = 200e5 * PRM.V / (PRM.R * 300.0)
    assert m_ideal / m_real == pytest.approx(Z)
    assert m_real / PRM.V == pytest.approx(29.4, abs=0.5)


def test_pressure_and_mass_are_inverse():
    for P in (1e5, 50e5, 200e5):
        m = tk.mass_from_pt(PRM, P, 280.0)
        assert tk.pressure(PRM, m, 280.0) == pytest.approx(P, rel=1e-12)


def test_adiabatic_blowdown_limit():
    """No wall, no heat transfer: dT/dt = -(gamma - 1) T mdot / m."""
    s = tk.initial_state(IDEAL, 200e5, 300.0)
    a = tk.diameter_to_area(200e-6)
    P = tk.pressure(IDEAL, s.m, s.T)
    mdot = tk.orifice_mass_flow(IDEAL, P, s.T, 1e3, a)
    s2, _ = tk.step(IDEAL, s, 300.0, 1e3, 0.0, 0.01, a)
    dTdt = (s2.T - s.T) / 0.01
    assert dTdt == pytest.approx(-(IDEAL.gamma - 1.0) * s.T * mdot / s.m, rel=1e-9)


def test_sealed_tank_conserves_mass_and_relaxes_to_ambient():
    s = tk.initial_state(PRM, 200e5, 299.15)
    m0 = s.m
    for _ in range(20000):
        s, mdot = tk.step(PRM, s, 250.0, 101325.0, 1.225, 1.0)
        assert mdot == 0.0
    assert s.m == m0
    assert s.T == pytest.approx(250.0, abs=0.5)


def test_leak_sizing_inverts_flow():
    a = tk.diameter_to_area(73e-6)
    mdot = tk.orifice_mass_flow(PRM, 180e5, 280.0, 2000.0, a)
    assert tk.area_to_diameter(tk.hole_area_from_flow(PRM, mdot, 180e5, 280.0)) == pytest.approx(73e-6, rel=1e-12)


# --- Detector ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def flight():
    return leak_mc.nominal_flight(CFG, 5600 + 3600)


def test_streaming_and_vectorised_detector_agree(flight):
    P, T = leak_mc.tank_truth(CFG, flight, 30.0)
    rng = np.random.default_rng(3)
    Pm = P + rng.normal(0, CFG.sensors.tank_P_sigma_bar * 1e5, len(P))
    Tm = T + rng.normal(0, CFG.sensors.tank_T_sigma_k, len(T))
    det = LeakDetector.from_config(CFG)
    stream = [r for t, p, tt in zip(flight.t, Pm, Tm) if (r := det.update(float(t), float(p), float(tt)))]
    m_hat = np.array([tk.mass_from_pt(PRM, p, tt) for p, tt in zip(Pm, Tm)])[None, :]
    t_ends, b, tst = window_tstats(flight.t, m_hat, CFG.leak_detector.window_s, CFG.leak_detector.stride_s)
    assert len(stream) == len(t_ends)
    assert np.allclose([r.t_stat for r in stream], tst[0], rtol=1e-9)
    assert det.detected


def test_sealed_false_alarm_rate_200_seeds(flight):
    r = leak_mc.evaluate(CFG, flight, 0.0, 200)
    print(f"\nsealed: false alarms {r.detection_rate_mission * 200:.0f}/200, mean significant windows/mission {r.false_windows:.2f}")
    assert r.detection_rate_mission < 0.01


@pytest.mark.parametrize("d_um", [20, 50, 100, 300])
def test_leaks_are_detected_before_float(flight, d_um):
    r = leak_mc.evaluate(CFG, flight, float(d_um), 200)
    print(f"\n{d_um} um: detected before float {r.detection_rate_before_float * 100:.1f} %, "
          f"median detection {r.median_detect_time_s:.0f} s, median |size error| {r.median_size_error_pct:.1f} %")
    assert r.detection_rate_before_float >= 0.95
    assert r.median_size_error_pct < 15.0


def test_50um_regression_old_rule_armed_new_detector_catches(flight):
    """Old backend: '> 10 g lost' rule. A 50 um leak lost 4.6 g on the old 120 m/s, 0.5 m^3 setup and armed."""
    r = leak_mc.evaluate(CFG, flight, 50.0, 50)
    assert r.detection_rate_before_float == 1.0
    assert r.median_detect_time_s <= 300.0


def test_min_detectable_leak_is_reported(flight):
    dmin, curve = leak_mc.min_detectable_before_float(CFG, flight, seeds=60)
    print(f"\nminimum leak detected before float (>= 90 % of 60 seeds): {dmin:.0f} um; search {curve}")
    assert 5.0 < dmin < 60.0  # whatever it is, it must be reported; this only guards against nonsense
