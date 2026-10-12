"""Motor thermal model and gondola pendulum."""
import math

import numpy as np
import pytest

from aerofire.config import load_config
from aerofire.physics import motor_thermal as M
from aerofire.physics import pendulum as P
from aerofire.sim import leak_mc

CFG = load_config()


# --- Motor -------------------------------------------------------------------------------------


def test_pure_convection_is_exponential():
    p = M.MotorParams(C=8000.0, UA_sl=2.0, UA_exp=0.5, eps_area=0.0, T_sky=220.0, heater_w=0.0, setpoint_k=0.0)
    T, T_air = 300.0, 250.0
    tau = p.C / p.UA_sl
    for _ in range(int(tau)):
        T = M.step(p, T, T_air, 1.225, False, 1.0)
    assert T - T_air == pytest.approx(50.0 * math.exp(-1.0), rel=1e-3)


def test_pure_radiation_initial_rate():
    p = M.MotorParams(C=8000.0, UA_sl=0.0, UA_exp=0.5, eps_area=0.12, T_sky=220.0, heater_w=0.0, setpoint_k=0.0)
    expected = -0.12 * 5.670374419e-8 * (290.0**4 - 220.0**4) / 8000.0
    assert M.dTdt(p, 290.0, 290.0, 0.0, False) == pytest.approx(expected, rel=1e-12)


def test_no_artificial_floor():
    """The old model clamped motor temperature at 12 C. Physics has no such floor."""
    p = M.MotorParams.from_config(CFG, heater_failed=True)
    T = 285.0
    for _ in range(20000):
        T = M.step(p, T, 220.0, 0.03, False, 1.0)
    assert T < 273.15 + 12.0 - 20.0


def test_heater_thermostat_hysteresis():
    p = M.MotorParams.from_config(CFG)
    assert M.heater_command(p, p.setpoint_k - 0.1, False)
    assert M.heater_command(p, p.setpoint_k + 0.5, True)  # inside the band: keep state
    assert not M.heater_command(p, p.setpoint_k + 0.5, False)
    assert not M.heater_command(p, p.setpoint_k + 1.1, True)


@pytest.fixture(scope="module")
def flight():
    return leak_mc.nominal_flight(CFG, 9200)


def _fly_motor(flight, failed):
    p = M.MotorParams.from_config(CFG, heater_failed=failed)
    T, on, T_float = CFG.motor.T0_c + 273.15, False, None
    for i in range(len(flight.t)):
        on = M.heater_command(p, T, on)
        T = M.step(p, T, flight.T_amb[i], flight.rho[i], on, 1.0)
        if i == int(flight.t_float):
            T_float = T
    return T_float - 273.15, T - 273.15


def test_nominal_motor_stays_qualified_and_failed_heater_does_not(flight):
    nominal = _fly_motor(flight, False)
    failed = _fly_motor(flight, True)
    print(f"\nmotor at float / end: heater on {nominal[0]:.1f} / {nominal[1]:.1f} C; heater failed {failed[0]:.1f} / {failed[1]:.1f} C")
    assert min(nominal) > CFG.motor.T_min_qualified_c
    assert failed[0] < CFG.motor.T_min_qualified_c


# --- Pendulum ----------------------------------------------------------------------------------


def test_small_amplitude_period():
    p = P.PendulumParams(L=15.0, zeta=0.0)
    th, w, t, dt = math.radians(0.5), 0.0, 0.0, 0.01
    crossings = []
    while len(crossings) < 11:
        th2, w2 = P.step(p, th, w, 0.0, dt, substeps=1)
        if th > 0 >= th2 or th < 0 <= th2:
            crossings.append(t + dt * th / (th - th2))
        th, w, t = th2, w2, t + dt
    period = 2 * (crossings[-1] - crossings[0]) / 10
    assert period == pytest.approx(2 * math.pi * math.sqrt(15.0 / 9.80665), rel=1e-4)
    assert p.period == pytest.approx(7.7708, abs=1e-3)


def test_damping_decay_rate():
    p = P.PendulumParams(L=15.0, zeta=0.05)
    th, w = math.radians(2.0), 0.0
    a0 = P.amplitude(p, th, w)
    T = 10 * p.period
    for _ in range(int(T / 0.05)):
        th, w = P.step(p, th, w, 0.0, 0.05, substeps=1)
    assert P.amplitude(p, th, w) / a0 == pytest.approx(math.exp(-p.zeta * p.omega * T), rel=0.02)


def test_amplitude_not_angle_is_gated():
    """At the zero crossing the angle reads 0 but the swing amplitude is the full 3 deg."""
    p = P.PendulumParams(L=15.0, zeta=0.0)
    th, w = math.radians(3.0), 0.0
    best = (1.0, None)
    for _ in range(2000):
        th, w = P.step(p, th, w, 0.0, 0.002, substeps=1)
        if abs(th) < best[0]:
            best = (abs(th), (th, w))
    th0, w0 = best[1]
    assert math.degrees(abs(th0)) < 0.05  # instantaneous angle would "pass" a 1.5 deg check
    assert math.degrees(P.amplitude(p, th0, w0)) == pytest.approx(3.0, rel=1e-3)  # amplitude correctly fails it


def test_wind_shear_excites_swing():
    p = P.PendulumParams(L=15.0, zeta=0.02)
    th, w = 0.0, 0.0
    th, w = P.step(p, th, w, 0.5, 1.0)  # 0.5 m/s^2 suspension-point acceleration for 1 s
    assert P.amplitude(p, th, w) > math.radians(0.5)
