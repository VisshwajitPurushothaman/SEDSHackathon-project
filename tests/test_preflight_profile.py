"""Pre-flight: predicted telemetry profile and bands, channel health, pad agreement, range safety, OFF-NOMINAL."""
import pytest

from aerofire.config import load_config, load_raw
from aerofire.ground import profile as PF
from aerofire.physics import atmosphere as A
from aerofire.receiver import preflight as PRE
from aerofire.receiver.service import Receiver, load_session
from aerofire.telemetry import icd

CFG = load_config()
RAW = load_raw()
PROFILE = A.load_snapshot("config/atmosphere_snapshot.json", RAW["atmosphere"]["levels_hpa"], RAW["atmosphere"]["blend_m"])
KEY, GEN = CFG.telemetry.hmac_key_demo, CFG.telemetry.hmac_genesis


@pytest.fixture(scope="module")
def receiver():
    r = Receiver(CFG, lambda: PROFILE)
    r.mode = "PLAYBACK"
    r.prepare()
    return r


def test_profile_is_deterministic_and_bands_contain_nominal(receiver):
    p = receiver.preflight_ctx["profile"]
    again = PF.predicted_profile(CFG, PROFILE)
    assert again["samples"][100] == p["samples"][100]
    phases = {s["phase"] for s in p["samples"]}
    assert {"PAD", "ASCENT", "FLOAT"} <= phases
    for s in p["samples"]:
        for m in PF.PREDICTED:
            assert s["lo"][m] <= s["ch"][m] <= s["hi"][m]
    lo, nom, hi = PF.band_at(p, 1002.5, "TANK_P")
    a, b = p["samples"][199], p["samples"][200]  # MET 1000, 1005
    assert nom == pytest.approx((a["ch"]["TANK_P"] + b["ch"]["TANK_P"]) / 2)
    assert PF.band_at(p, 1e9, "TANK_P") is None and PF.band_at(p, 100.0, "IMU_TILT") is None


def test_polygon_intersection_and_range_conflicts(receiver):
    sq = [[0, 0], [0, 1], [1, 1], [1, 0]]
    assert PF.polygons_intersect(sq, [[0.5, 0.5], [0.5, 2], [2, 2], [2, 0.5]])
    assert PF.polygons_intersect(sq, [[-1, 0.4], [2, 0.4], [2, 0.6], [-1, 0.6]])  # crossing bar, no vertex inside
    assert not PF.polygons_intersect(sq, [[2, 2], [2, 3], [3, 3], [3, 2]])
    ctx = receiver.preflight_ctx
    assert ctx["range_check"]["status"] == "PASS" and ctx["range_check"]["zones_checked"] >= 3
    nominal = next(p for p in ctx["iips"] if p["case"] == "nominal")
    zone = {"name": "test zone", "kind": "restricted", "polygon": [[nominal["lat"] - 0.01, nominal["lon"] - 0.01], [nominal["lat"] - 0.01, nominal["lon"] + 0.01],
                                                                    [nominal["lat"] + 0.01, nominal["lon"] + 0.01], [nominal["lat"] + 0.01, nominal["lon"] - 0.01]]}
    res = PF.range_check(ctx["range"] | {"zones": ctx["range"]["zones"] + [zone]}, ctx["footprint"], ctx["iips"])
    assert res["status"] == "FAIL" and {"zone": "test zone", "kind": "restricted", "what": "IIP nominal"} in res["conflicts"]


def pad_session(receiver, n=30, ch_fn=None, mission="pad-test"):
    prev, base = icd.genesis_hash(KEY, GEN), {
        "GNSS_LAT": 13.72, "GNSS_LON": 80.23, "GNSS_ALT": 5.0, "NAVIC_LAT": 13.72, "NAVIC_LON": 80.23, "NAVIC_ALT": 5.0,
        "BARO_P": PROFILE.at(5.0).p / 100.0, "TANK_P": 200.0, "FC_PHASE": "PAD"}
    for i in range(n):
        ch = dict(base)
        ch["GNSS_ALT"] += 0.5 * ((i % 3) - 1)
        ch["TANK_P"] += 0.01 * (i % 2)
        if ch_fn:
            ch.update(ch_fn(i))
        f = icd.build_frame(KEY, mission, i, float(i), 1000.0 + i, 1.0, ch, prev)
        prev = f["hash"]
        receiver.on_frame(f, 1000.0 + i)
    return receiver.sessions[mission]


def test_channel_health_rate_limits_frozen_and_waiver(receiver):
    s = pad_session(receiver, ch_fn=lambda i: {"BATT_V": 12.4, "MOTOR_T": 20.0 + 0.1 * (i % 2)} | ({"AIR_T": 25.0} if i % 3 == 0 else {}))
    items = {i["id"]: i for i in PRE.channel_health(s.dictionary, s.pad)}
    assert items["ch:TANK_P"]["status"] == "PASS"
    assert items["ch:BATT_V"]["status"] == "FAIL" and "frozen (zero variance)" in items["ch:BATT_V"]["evidence"]["failures"]
    assert items["ch:AIR_T"]["status"] == "FAIL" and "rate" in items["ch:AIR_T"]["evidence"]["failures"]  # 0.33 Hz, frozen too
    assert items["ch:RSSI"]["status"] == "NOT CHECKED"  # absent, vehicle still on the pad
    assert "ch:BLN_VOL" not in items  # not expected on the pad
    ev = PRE.evaluate(s)
    assert ev["readiness"] == "HOLD" and len(ev["report_sha256"]) == 64
    s.waive("ch:BATT_V", "A. Operator", "spare pack fitted", 2000.0)
    assert {i["id"]: i for i in PRE.evaluate(s)["items"]}["ch:BATT_V"]["status"] == "WAIVED"
    # After release the evidence freezes and absent channels become FAIL.
    s.pad.add(31.0, {"FC_PHASE": "ASCENT"}, "ASCENT")
    assert {i["id"]: i for i in PRE.channel_health(s.dictionary, s.pad)}["ch:RSSI"]["status"] == "FAIL"


def test_pad_agreement_detects_a_biased_navic(receiver):
    good = pad_session(receiver, mission="agree-ok")
    assert PRE.pad_agreement(CFG, PROFILE, good.pad)["status"] == "PASS"
    bad = pad_session(receiver, mission="agree-bad", ch_fn=lambda i: {"NAVIC_LAT": 13.72 + 60.0 / 111_000})  # NavIC 60 m north
    res = PRE.pad_agreement(CFG, PROFILE, bad.pad)
    assert res["status"] == "FAIL" and res["evidence"]["chi2_gnss_navic_h"] > CFG.gates.chi2_2dof_999


def test_off_nominal_caution_for_the_leak_but_not_nominal(receiver):
    def alarms(name, until):
        r = Receiver(CFG, lambda: PROFILE)
        r.mode, r.preflight_ctx = "PLAYBACK", receiver.preflight_ctx
        for row in load_session(f"data/demo_sessions/{name}.jsonl.gz"):
            if row["met_s"] > until:
                break
            r.on_frame(row, row["rx"]["utc"])
        return [a for a in r.current.alarms if a["source"] == "prediction"]

    assert alarms("nominal", 3000) == []
    leak = alarms("tank_leak_100um", 600)
    assert leak and leak[0]["title"] == "OFF-NOMINAL: TANK_P outside predicted band" and leak[0]["met"] < 181
