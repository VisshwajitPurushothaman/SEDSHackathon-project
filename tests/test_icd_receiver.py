"""ICD v1 frames, the ground receiver (gaps, HMAC, LOS) and deterministic playback."""
import copy
import json

import pytest

from aerofire.config import load_config, load_raw
from aerofire.physics import atmosphere as A
from aerofire.receiver.service import Receiver, playback_sync
from aerofire.telemetry import icd
from sender.vehicle import Vehicle

CFG = load_config()
RAW = load_raw()
KEY, GEN = CFG.telemetry.hmac_key_demo, CFG.telemetry.hmac_genesis


def snapshot_profile():
    return A.load_snapshot("config/atmosphere_snapshot.json", RAW["atmosphere"]["levels_hpa"], RAW["atmosphere"]["blend_m"])


PROFILE = snapshot_profile()


def frames(n, mission="m0", period=0.5, start_utc=1000.0, ch=None):
    out, prev = [], icd.genesis_hash(KEY, GEN)
    for i in range(n):
        f = icd.build_frame(KEY, mission, i, float(i), start_utc + i * period, 1.0, dict(ch or {"GNSS_ALT": 10.0 + i, "FC_PHASE": "PAD"}), prev)
        prev = f["hash"]
        out.append(f)
    return out


def receiver(tmp_path, record=False):
    r = Receiver(CFG, lambda: PROFILE, sessions_dir=tmp_path)
    if not record:
        r.mode = "PLAYBACK"  # no session file
    return r


def test_frame_round_trip_and_chain():
    fs = frames(5)
    back = [icd.decode(icd.encode(f)) for f in fs]
    assert back == fs and all(icd.verify(KEY, f) for f in back)
    assert icd.verify_chain(KEY, GEN, back) == (True, None)
    swapped = [back[0], back[2], back[1], back[3], back[4]]
    assert icd.verify_chain(KEY, GEN, swapped)[0] is False
    with pytest.raises(icd.FrameError):
        icd.decode(b'{"v": 2}')
    with pytest.raises(icd.FrameError):
        icd.decode(b"not json")


def test_channel_encoding_round_trips_to_sensor_packets():
    v = Vehicle(CFG, PROFILE, "nominal", seed=3, time_scale=1.0)
    for _ in range(40):
        v.step()
    f, _ = v.next_frame(0.0)
    pk = {p["sensor_id"]: p for p in icd.packets_from_frame(f, CFG.site.lat_deg, CFG.site.lon_deg, arrival_time=f["met_s"])}
    assert {"gnss", "navic", "baro", "air_temp", "imu", "tank", "motor", "balloon", "fc"} <= set(pk)
    assert pk["baro"]["values"]["p"] == pytest.approx(f["ch"]["BARO_P"] * 100.0)
    assert pk["tank"]["values"]["T"] == pytest.approx(f["ch"]["TANK_T"] + 273.15)
    assert abs(pk["gnss"]["values"]["up"] - f["ch"]["GNSS_ALT"]) < 1e-9
    assert pk["fc"]["values"]["phase"] == "ASCENT"


def test_hmac_failure_is_counted_and_not_ingested(tmp_path):
    r = receiver(tmp_path)
    fs = frames(3)
    bad = copy.deepcopy(fs[1])
    bad["ch"]["GNSS_ALT"] = 99999.0
    assert r.on_frame(fs[0], 1000.0) == "ok"
    assert r.on_frame(bad, 1000.5) == "hmac_fail"
    s = r.current
    assert s.channels["GNSS_ALT"]["value"] == 10.0 and 1 not in s.frames
    assert s.link(1001.0)["hmac_failures"] == 1
    assert any(a["title"].startswith("Frame 1 failed HMAC") for a in s.alarms)
    assert r.on_datagram(b"garbage", 1001.0) == "rejected" and r.rejected == 1


def test_gaps_duplicates_and_reordering(tmp_path):
    r = receiver(tmp_path)
    fs = frames(10)
    order = [0, 1, 2, 5, 6, 7, 9, 8, 8]  # 3, 4 lost; 8 late then duplicated
    for i, k in enumerate(order):
        r.on_frame(fs[k], 1000.0 + 0.5 * i)
    link = r.current.link(1005.0)
    assert link["gaps"] == [[3, 4]]
    assert link["loss_pct"] == pytest.approx(20.0)
    assert link["duplicates"] == 1 and link["out_of_order"] == 1
    assert link["chain_breaks"] == 0


def test_los_after_three_seconds_of_silence_then_aos(tmp_path):
    r = receiver(tmp_path)
    fs = frames(12)
    for f in fs[:10]:
        r.on_frame(f, f["utc_sent"] + 0.05)
    last = fs[9]["utc_sent"] + 0.05
    s = r.current
    s.tick(last + 2.9)
    assert s.link(last + 2.9)["aos"] is True and not s.los_periods
    s.tick(last + 3.1)
    assert s.link(last + 3.1)["aos"] is False and len(s.los_periods) == 1
    assert any(a["title"].startswith("Loss of signal") for a in s.alarms)
    table = {row["mnemonic"]: row for row in s.channel_table(last + 3.1)}
    assert table["GNSS_ALT"]["status"] == "STALE" and table["GNSS_ALT"]["age_s"] > 3.0
    assert s.met_now(last + 5.0) == pytest.approx(9.0 + (5.0 - 0.0) * 1.0, abs=0.1)  # extrapolated while LOS
    r.on_frame(fs[11], last + 6.0)
    assert s.link(last + 6.0)["aos"] is True and s.los_periods[0]["end_utc"] == last + 6.0
    assert s.link(last + 6.0)["gaps"] == [[10, 10]]


def test_limits_raise_alarms_and_unknown_channels_display_raw(tmp_path):
    r = receiver(tmp_path)
    fs = frames(12, ch={"TANK_P": 175.0, "FC_PHASE": "PAD", "XYZ_NEW": 7})
    r.on_frame(fs[0], 1000.0)
    s = r.current
    assert s.limit_states.get("TANK_P") is None  # within the 10 s phase settle time: not evaluated
    r.on_frame(fs[11], 1000.0)
    rows = {row["mnemonic"]: row for row in s.channel_table(1000.1)}
    assert rows["TANK_P"]["limit"] == "ALARM"  # pad red band [180, 220] bar
    assert rows["XYZ_NEW"]["value"] == 7 and rows["XYZ_NEW"]["known"] is False and rows["XYZ_NEW"]["subsystem"] == "RAW"
    alarm = next(a for a in s.alarms if a["key"].startswith("limit:TANK_P"))
    s.ack(alarm["id"], "A. Operator", 1001.0)
    assert alarm["acknowledged_by"] == "A. Operator"
    with pytest.raises(ValueError):
        s.ack(alarm["id"], "B", 1002.0)
    p = s.poll("A. Operator", "NO_GO", "pad pressure low", 1003.0)
    assert p["recommendation"] in ("NO_GO", "ABORT_RECOMMENDED") and s.event_log[-1]["action"] == "POLL NO_GO"


def test_playback_is_deterministic(tmp_path):
    # Record a short live session (vehicle frames, a 6 s link blackout), then play it back twice.
    v = Vehicle(CFG, PROFILE, "nominal", seed=5, time_scale=1.0)
    live = Receiver(CFG, lambda: PROFILE, sessions_dir=tmp_path)
    t = 5000.0
    while v.met < 300:
        v.step()
        f, delivered = v.next_frame(t)
        if delivered and not (120 <= v.met < 126):
            live.on_frame(f, t + 0.02)
        t += 1.0
    path = live.current.path
    live.current.close()
    outs = []
    for _ in range(2):
        s = playback_sync(Receiver(CFG, lambda: PROFILE, sessions_dir=tmp_path / "pb"), str(path))
        outs.append(json.dumps({"h": s.history, "t": s.track, "a": [(a["key"], a["met"]) for a in s.alarms],
                                "los": s.los_periods}, sort_keys=True, default=str))
    assert outs[0] == outs[1]
    s_last = json.loads(outs[0])
    assert len(s_last["los"]) == 1 and s_last["h"][-1]["phase"] == "Ascent"
