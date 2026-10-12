"""Onboard recorder (HMAC chain, tamper detection) and the downlink emulator."""
import copy

import numpy as np
import pytest

from aerofire.config import load_config
from aerofire.ground.recovery import RecoveryClient
from aerofire.sim.mission import Faults
from aerofire.telemetry.link import LinkEmulator, fspl_db
from aerofire.telemetry.recorder import OnboardRecorder, verify_chain

CFG = load_config()
KEY, GEN = CFG.telemetry.hmac_key_demo, CFG.telemetry.hmac_genesis


def sample(i):
    return {"sensor_id": "tank", "event_time": float(i), "values": {"P": 2e7 - i, "T": 299.0}, "units": {"P": "Pa", "T": "K"}, "source": "tank.pt1"}


@pytest.fixture()
def chain():
    rec = OnboardRecorder.from_config(CFG)
    out = [rec.write(sample(i)) for i in range(50)]
    return rec, out


def test_chain_verifies(chain):
    rec, out = chain
    assert [r["seq"] for r in out] == list(range(50))
    assert verify_chain(KEY, GEN, out) == (True, None)
    assert rec.read(17) == out[17]


@pytest.mark.parametrize("attack", ["edit", "delete", "reorder", "forge_mac", "wrong_key"])
def test_tamper_is_detected(chain, attack):
    _, out = chain
    bad = copy.deepcopy(out)
    if attack == "edit":
        bad[20]["values"]["P"] += 1.0
        expected = 20
    elif attack == "delete":
        del bad[20]
        expected = 20
    elif attack == "reorder":
        bad[20], bad[21] = bad[21], bad[20]
        expected = 20
    elif attack == "forge_mac":
        bad[30]["values"]["P"] = 0.0
        bad[30]["mac"] = "0" * 64
        expected = 30
    else:
        assert verify_chain("another-key", GEN, bad) == (False, 0)
        return
    ok, first_bad = verify_chain(KEY, GEN, bad)
    assert not ok and first_bad == expected


def test_receiver_metadata_is_not_signed(chain):
    _, out = chain
    with_rx = [dict(r, rx={"rssi_dbm": -90.0, "arrival_time": r["event_time"] + 0.4}) for r in out]
    assert verify_chain(KEY, GEN, with_rx) == (True, None)


def test_recovery_gets_100_percent_on_clean_link(chain):
    rec, out = chain
    held = {r["seq"]: r for r in out if r["seq"] % 3 != 0}  # a third never arrived live
    client = RecoveryClient(CFG, KEY, GEN, held, len(rec) - 1, lambda seqs: rec.read_many(seqs))
    rep = client.run()
    assert rep.recovered == rep.missing_at_start == 17
    assert rep.lost == [] and rep.retries == 0 and rep.chain_ok is True
    # Rough picture first: the first stage fetches every 10th missing record.
    assert rep.progress[0]["stage"].startswith("rough picture")


def test_recovery_retries_and_lists_lost_records(chain):
    rec, out = chain
    held = {r["seq"]: r for r in out if r["seq"] >= 10}
    calls = {"n": 0}

    def flaky(seqs):
        calls["n"] += 1
        return None if calls["n"] <= 2 else rec.read_many(seqs)  # two failures, then success

    rep = RecoveryClient(CFG, KEY, GEN, held, len(rec) - 1, flaky).run()
    assert rep.retries == 2 and rep.lost == [] and rep.chain_ok

    dead = RecoveryClient(CFG, KEY, GEN, {r["seq"]: r for r in out if r["seq"] >= 10}, len(rec) - 1, lambda s: None).run()
    assert dead.lost == list(range(10)) and dead.chain_ok is None


def test_recovery_flags_a_tampered_recovered_record(chain):
    rec, out = chain
    held = {r["seq"]: r for r in out if r["seq"] != 5}

    def evil(seqs):
        recs = rec.read_many(seqs)
        recs[0]["values"]["P"] = -1.0
        return recs

    rep = RecoveryClient(CFG, KEY, GEN, held, len(rec) - 1, evil).run()
    assert 5 in rep.hmac_failures and rep.chain_ok is False and rep.chain_first_bad == 5


# --- link ------------------------------------------------------------------------------------


def test_blackout_drops_everything_in_window_and_nothing_else():
    link = LinkEmulator(CFG, Faults(blackouts=[(10.0, 20.0)], packet_loss=0.0), np.random.default_rng(0))
    rec = OnboardRecorder.from_config(CFG)
    for i in range(30):
        link.transmit(rec.write(sample(i)), (0.0, 0.0, 1000.0))
    got = link.poll(1e9)
    assert sorted(r["event_time"] for r in got) == [float(i) for i in range(30) if not 10 <= i < 20]
    assert link.dropped_blackout == 10


def test_random_loss_rate_and_latency():
    link = LinkEmulator(CFG, Faults(packet_loss=0.2, latency_s=1.0), np.random.default_rng(1))
    rec = OnboardRecorder.from_config(CFG)
    for i in range(5000):
        link.transmit(rec.write(sample(i)), (0.0, 0.0, 1000.0))
    assert link.poll(100.0)[-1]["rx"]["arrival_time"] <= 100.0  # nothing delivered before it arrives
    got = link.poll(1e9)
    assert abs(link.dropped_random / 5000 - 0.2) < 0.02
    lat = np.array([r["rx"]["arrival_time"] - r["event_time"] for r in got])
    assert abs(lat.mean() - 1.0) < 0.02


def test_rssi_falls_with_range():
    link = LinkEmulator(CFG, Faults(), np.random.default_rng(2))
    near = np.mean([link.rssi(0.0, 0.0, 1000.0) for _ in range(200)])
    far = np.mean([link.rssi(0.0, 0.0, 100_000.0) for _ in range(200)])
    assert near - far == pytest.approx(40.0, abs=1.0)  # 100x range = 40 dB free-space loss
    assert fspl_db(1000.0, 2400.0) == pytest.approx(100.04, abs=0.05)


def test_stale_link_latency_is_unknown_then_restored_by_a_new_packet():
    from aerofire.ground.station import GroundStation
    from aerofire.physics.atmosphere import AtmosphereProfile

    ground = GroundStation(CFG, AtmosphereProfile())
    packet = {"sensor_id": "fc", "event_time": 0.0, "values": {"phase": "PAD"},
              "rx": {"rssi_dbm": -70.0, "arrival_time": 0.4}}
    ground.ingest(packet)
    fresh = ground.frame(1.0)["estimates"]["link_latency"]
    assert fresh["status"] == "MEASURED" and fresh["value"] == pytest.approx(0.4)
    stale = ground.frame(30.0)["estimates"]["link_latency"]
    assert stale["status"] == "UNKNOWN" and stale["value"] is None
    assert stale["last_value"] == pytest.approx(0.4) and stale["age_s"] > CFG.estimator.stale_after_s
    ground.ingest(dict(packet, event_time=30.0, rx={"rssi_dbm": -71.0, "arrival_time": 30.4}))
    restored = ground.frame(31.0)["estimates"]["link_latency"]
    assert restored["status"] == "MEASURED" and restored["value"] == pytest.approx(0.4)
