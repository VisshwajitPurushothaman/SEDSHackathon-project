"""Zero-pressure balloon: float altitude, mass conservation, emergent ascent rate, venting, drift."""
import math
from pathlib import Path

import pytest

from aerofire.config import load_config
from aerofire.physics import atmosphere as A
from aerofire.physics import balloon as Bl
from aerofire.sim.float_control import BallastController

CFG = load_config()
SNAPSHOT = Path(__file__).resolve().parent.parent / "config" / "atmosphere_snapshot.json"
B = CFG.balloon
M_FIXED = B.m_envelope_kg + B.m_gondola_kg + CFG.rocket.m_dry_kg + CFG.rocket.m_prop_kg
PARAMS = Bl.BalloonParams(M_FIXED, B.V_max_m3, B.Cd, R_gas=CFG.constants.R_He)
ISA = A.AtmosphereProfile().at
LIVE = A.load_snapshot(str(SNAPSHOT), CFG.atmosphere.levels_hpa).at


def fly(atmo, seconds, leak_area=0.0, control=True, dt=1.0):
    m0 = Bl.launch_gas_mass(PARAMS, atmo(CFG.site.elevation_m), B.free_lift_fraction, B.m_ballast_kg)
    s = Bl.BalloonState(CFG.site.elevation_m, 0.0, m0, B.m_ballast_kg)
    ctl = BallastController.from_config(B)
    hist = []
    vented = leaked = dropped = 0.0
    t = 0.0
    venting = False
    while t < seconds:
        drop = ctl.command(t, s.z, s.vz, venting, atmo(s.z), dt) if control else 0.0
        s, o = Bl.step(PARAMS, s, atmo, dt, leak_area_m2=leak_area, ballast_drop_kg_s=drop)
        t += dt
        venting = o.vent_rate_kg_s > 0
        vented += o.vent_rate_kg_s * dt
        leaked += o.leak_rate_kg_s * dt
        dropped += o.ballast_drop_kg_s * dt
        hist.append((t, s, o))
    return m0, s, hist, vented, leaked, dropped


def test_float_altitude_matches_hand_calculation():
    """Neutral buoyancy of the full balloon: (rho_air - rho_He) V_max = m_load."""
    zf = Bl.float_altitude(PARAMS, ISA, B.m_ballast_kg)
    T, p, rho = A.isa(zf)
    rho_he = p / (CFG.constants.R_He * T)
    assert (rho - rho_he) * B.V_max_m3 == pytest.approx(M_FIXED + B.m_ballast_kg, rel=1e-6)
    # Hand calculation at 28 km geometric = 27 877 gpm (USSA76 layer 20-32 km, L = +1 K/km):
    #   T = 216.65 + 7.877 = 224.53 K
    #   p = 5474.889 * (216.65 / 224.527)^34.1632 = 5474.889 * exp(-1.22007) = 1616.2 Pa
    #   rho_air = p / (287.053 T) = 0.025067,  rho_He = p / (2077.1 T) = 0.0034643
    #   lift = (rho_air - rho_He) * 5600 = 121.0 kg  > 120 kg load  ->  float just above 28 km
    T28, p28, rho28 = A.isa(28000.0)
    assert T28 == pytest.approx(224.53, abs=0.01)
    assert p28 == pytest.approx(1616.2, abs=0.5)
    lift_28 = (rho28 - p28 / (CFG.constants.R_He * T28)) * B.V_max_m3
    assert lift_28 == pytest.approx(121.0, abs=0.1)
    assert M_FIXED + B.m_ballast_kg == 120.0
    assert 28000 < zf < 28200


def test_full_balloon_at_float_stays_put():
    zf = Bl.float_altitude(PARAMS, ISA, B.m_ballast_kg)
    a = ISA(zf)
    m_full = a.p * B.V_max_m3 / (CFG.constants.R_He * a.T)
    s = Bl.BalloonState(zf, 0.0, m_full, B.m_ballast_kg)
    for _ in range(600):
        s, o = Bl.step(PARAMS, s, ISA, 1.0)
    assert abs(s.z - zf) < 1.0 and abs(s.vz) < 0.01


@pytest.mark.parametrize("atmo_name", ["isa", "live"])
def test_ascent_rate_emerges_in_expected_range(atmo_name):
    atmo = ISA if atmo_name == "isa" else LIVE
    m0, s, hist, *_ = fly(atmo, 7000, control=False)
    first_full = next(t for t, st, o in hist if o.vent_rate_kg_s > 0)
    z_full = next(st.z for t, st, o in hist if o.vent_rate_kg_s > 0)
    mean_rate = z_full / first_full
    assert 4.0 <= mean_rate <= 6.0, mean_rate
    rates = [st.vz for t, st, o in hist if 1000 < st.z < 20000 and t < first_full]
    assert 3.0 < min(rates) and max(rates) < 7.5


def test_no_venting_before_full_volume():
    _, _, hist, *_ = fly(ISA, 7000, control=False)
    for t, s, o in hist:
        if o.volume_m3 < B.V_max_m3 * 0.999:
            assert o.vent_rate_kg_s == 0.0
    assert any(o.vent_rate_kg_s > 0 for _, _, o in hist)


def test_mass_is_conserved_with_vent_and_envelope_leak():
    leak_area = math.pi / 4 * 0.01**2  # 1 cm tear
    m0, s, hist, vented, leaked, dropped = fly(ISA, 9000, leak_area=leak_area)
    assert leaked > 0 and vented > 0
    assert m0 - s.m_gas == pytest.approx(vented + leaked, abs=1e-9)
    assert B.m_ballast_kg - s.m_ballast == pytest.approx(dropped, abs=1e-9)


def test_float_is_held_by_ballast_controller():
    _, s, hist, *_ = fly(LIVE, 4 * 3600)
    z_late = [st.z for t, st, o in hist if t > 8000]
    lo, hi = CFG.gates.release_band_m
    assert lo < min(z_late) and max(z_late) < hi
    assert s.m_ballast > 0.5 * B.m_ballast_kg  # controller is frugal


def test_drift_follows_wind():
    prof = A.AtmosphereProfile(z=[0.0, 20000.0, 40000.0], lnp=[math.log(101325), math.log(5500), math.log(300)],
                               T=[288.0, 217.0, 250.0], u=[10.0, 10.0, 10.0], v=[-5.0, -5.0, -5.0], label="test")
    m0 = Bl.launch_gas_mass(PARAMS, prof.at(0.0), 0.1, 10.0)
    s = Bl.BalloonState(0.0, 0.0, m0, 10.0)
    for _ in range(1000):
        s, _ = Bl.step(PARAMS, s, prof.at, 1.0)
    assert s.east == pytest.approx(10_000.0, rel=1e-6) and s.north == pytest.approx(-5_000.0, rel=1e-6)


def test_launch_gas_gives_requested_free_lift():
    a0 = ISA(0.0)
    m0 = Bl.launch_gas_mass(PARAMS, a0, 0.10, 10.0)
    V0 = Bl.gas_volume(m0, a0.T, a0.p, PARAMS.R_gas)
    lift = a0.rho * V0 - m0 - (M_FIXED + 10.0)
    assert lift == pytest.approx(0.10 * (M_FIXED + 10.0), rel=1e-9)
