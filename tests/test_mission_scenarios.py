"""End-to-end missions: truth sim -> sensors -> ground station (no link yet), with the recorded atmosphere."""
from pathlib import Path

import numpy as np
import pytest

from aerofire.config import load_config
from aerofire.ground.station import TIMELINE, GroundStation
from aerofire.physics import atmosphere as A
from aerofire.sim.mission import Faults, MissionSim
from aerofire.telemetry.sensors import SensorSuite

CFG = load_config()
SNAP = A.load_snapshot(str(Path(__file__).resolve().parent.parent / "config" / "atmosphere_snapshot.json"), CFG.atmosphere.levels_hpa)


def run(faults: dict, commit_on_go: bool = False, stop_at=None, horizon=4 * 3600, seed=1):
    sim = MissionSim(CFG, SNAP, Faults.from_dict(faults), seed=seed)
    sens = SensorSuite(CFG, np.random.default_rng(seed + 1), sim.faults)
    gs = GroundStation(CFG, SNAP)
    first: dict[str, float] = {}
    truths = []
    fr = None
    while not sim.done and sim.t < horizon:
        truth = sim.step()
        truths.append(truth)
        for r in sens.sample(truth):
            gs.ingest(r)
        fr = gs.frame(truth.t)
        first.setdefault(fr["phase"], truth.t)
        first.setdefault(fr["recommendation"], truth.t)
        if commit_on_go and fr["recommendation"] == "GO_RECOMMENDED" and sim.phase == "FLOAT":
            sim.commit()
            gs.operator = {"state": "committed", "by": "pytest"}
        if stop_at and stop_at(fr, sim):
            break
    return sim, gs, fr, first, truths


@pytest.fixture(scope="module")
def nominal():
    return run({}, commit_on_go=True)


def test_nominal_mission_flies_full_timeline(nominal):
    sim, gs, fr, first, truths = nominal
    assert fr["phase"] == "Landed"
    seen = [p for p in TIMELINE if p in first]
    assert seen == TIMELINE, seen
    assert first["GO_RECOMMENDED"] < first["Rocket burn"]
    assert "ABORT_RECOMMENDED" not in first


def test_nominal_has_no_false_alerts(nominal):
    _, gs, *_ = nominal
    keys = {a.key for a in gs.alerts}
    assert keys <= {"float", "go"}, keys


def test_ascent_rate_and_float_altitude(nominal):
    sim, _, _, first, truths = nominal
    t_float = sim.t_float
    climb = [tr for tr in truths if tr.phase == "ASCENT"]
    mean_rate = (climb[-1].z - climb[0].z) / (climb[-1].t - climb[0].t)
    assert 4.0 <= mean_rate <= 6.0
    lo, hi = CFG.gates.release_band_m
    assert lo < sim.release_point[2] < hi


def test_no_launch_without_operator_commit():
    """GO is only a recommendation: without commit the rocket never leaves the rail."""
    sim, gs, fr, first, truths = run({}, commit_on_go=False, horizon=8000)
    assert "GO_RECOMMENDED" in first
    assert sim.rocket_result is None and all(tr.rocket_phase == "ABOARD" for tr in truths)
    assert all(tr.phase in ("PAD", "ASCENT", "FLOAT") for tr in truths)


def test_tank_leak_aborts_before_float():
    sim, gs, fr, first, _ = run({"tank_leak_um": 100}, stop_at=lambda f, s: f["recommendation"] == "ABORT_RECOMMENDED")
    assert fr["recommendation"] == "ABORT_RECOMMENDED"
    assert first["ABORT_RECOMMENDED"] < (sim.t_float or 1e9)
    assert any(a.key == "tank_leak" for a in gs.alerts)
    assert fr["estimates"]["tank_leak_diameter"]["value"] == pytest.approx(100.0, rel=0.15)


def test_50um_leak_is_caught_unlike_the_old_rule():
    sim, gs, fr, first, _ = run({"tank_leak_um": 50}, stop_at=lambda f, s: f["recommendation"] in ("ABORT_RECOMMENDED", "GO_RECOMMENDED"))
    assert fr["recommendation"] == "ABORT_RECOMMENDED"


def test_gnss_spoof_fails_nav_integrity_and_blocks_go():
    sim, gs, fr, first, _ = run({"gnss_spoof_start_s": 3000.0}, horizon=7000)
    alert = next(a for a in gs.alerts if a.key == "nav_integrity")
    assert 3000 < alert.t < 3060
    assert "GO_RECOMMENDED" not in first
    assert fr["gates"]["nav"]["passed"] is False


def test_cold_motor_holds_the_gate():
    sim, gs, fr, first, _ = run({"motor_heater_failed": True}, horizon=7000)
    assert "GO_RECOMMENDED" not in first
    assert fr["gates"]["motor"]["passed"] is False
    assert any(a.key == "motor_cold" for a in gs.alerts)


def test_venting_is_reported_only_from_the_balloon_at_float(nominal):
    _, _, _, _, truths = nominal
    venting = [tr for tr in truths if tr.balloon["vent_rate"] > 0]
    assert venting and all(tr.phase in ("FLOAT", "IGNITION") for tr in venting)
    assert all(tr.balloon["volume"] >= CFG.balloon.V_max_m3 * 0.999 for tr in venting)


def test_every_estimate_has_a_valid_status(nominal):
    _, gs, fr, *_ = nominal
    for k, e in fr["estimates"].items():
        assert e["status"] in {"MEASURED", "FUSED", "PREDICTED", "RECONSTRUCTED", "UNKNOWN"}, k


def test_operator_terminate_cuts_down_with_rocket_aboard():
    sim = MissionSim(CFG, SNAP, Faults(), seed=3)
    while sim.phase != "ASCENT":
        sim.step()
    for _ in range(600):
        sim.step()
    ballast_at_cutdown = sim.bstate.m_ballast
    sim.terminate()
    while not sim.done:
        sim.step()
    assert sim.rocket_result is None
    assert sim.descent_mass == pytest.approx(CFG.balloon.m_gondola_kg + ballast_at_cutdown + CFG.rocket.m_dry_kg + CFG.rocket.m_prop_kg)
    with pytest.raises(ValueError):
        sim.commit()
