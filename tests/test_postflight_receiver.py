"""Post-flight on the receiver (aerofire/receiver/postflight.py, committed in 87458be untested): recovery,
analysis, report hash, exports, grading isolation, closure, and the twin (unsigned) case."""
import gzip
import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from aerofire.config import load_config, load_raw
from aerofire.physics import atmosphere as A
from aerofire.receiver import postflight as POST
from aerofire.receiver.service import Receiver, playback_sync
from aerofire.telemetry import icd

CFG = load_config()
RAW = load_raw()
PROFILE = A.load_snapshot("config/atmosphere_snapshot.json", RAW["atmosphere"]["levels_hpa"], RAW["atmosphere"]["blend_m"])
SESSION = "data/demo_sessions/tank_leak_100um.jsonl.gz"
VEHICLE = "data/demo_sessions/tank_leak_100um.vehicle.jsonl.gz"
OP = {"operator": "PF Tester"}


@pytest.fixture(scope="module")
def leak():
    r = Receiver(CFG, lambda: PROFILE)
    return playback_sync(r, SESSION)


def test_recovery_from_recorder_file_and_tamper_detection(leak, tmp_path):
    leak.recovery = rec = POST.FrameRecovery(leak, POST.Source(f"file:{VEHICLE}"))
    r = rec.run()
    assert r["state"] == "done" and r["lost_count"] == 0 and r["chain_ok"] is True
    assert r["recovered"] == r["missing_at_start"] > 0 and r["have"] == r["expected"]
    assert r["progress"][0]["stage"].startswith("rough picture")
    # A tampered recorder file: the altered frame is refused, the chain cannot verify.
    frames = [json.loads(x) for x in gzip.decompress(open(VEHICLE, "rb").read()).decode().splitlines()]
    s2 = playback_sync(Receiver(CFG, lambda: PROFILE), SESSION)
    victim = next(q for q in range(len(frames)) if q not in s2.frames)
    frames[victim]["ch"]["TANK_P"] = 999.0
    bad = tmp_path / "bad.jsonl"
    bad.write_text("".join(json.dumps(f) + "\n" for f in frames), encoding="utf-8")
    r2 = POST.FrameRecovery(s2, POST.Source(f"file:{bad}")).run()
    assert victim in r2["hmac_failures"] and victim not in s2.frames and r2["chain_ok"] is not True


def test_analysis_report_is_ground_side_and_hashed(leak):
    leak.truth = None
    a = POST.analyse(leak, grading=False)
    rep = a["report"]
    ids = {q["id"]: q["status"] for q in rep["requirements"]}
    assert ids["REQ-DATA-1"] == "PASS" and ids["REQ-DATA-2"] == "PASS" and ids["REQ-LEAK-1"] in ("PASS", "FAIL")
    assert ids["REQ-GATE-1"] == "NOT TESTED"  # the leak mission never had GO
    canon = json.dumps(rep, sort_keys=True, separators=(",", ":"), default=str)
    assert hashlib.sha256(canon.encode()).hexdigest() == a["report_sha256"]
    assert "truth" not in a["histories"] and "grading_view" not in a
    assert any(v["channel"] == "TANK_P" for v in rep["limit_violations"])  # the leak drives TANK_P out of limits
    pva = {x["channel"]: x for x in rep["predicted_vs_actual"]}
    assert pva["TANK_P"]["inside_pct"] < 100.0
    assert all(c["by"] is None if isinstance(c, dict) else c is None for c in rep["closure"].values())  # nothing pre-signed


def test_grading_view_is_separate_from_the_report(leak):
    leak.truth = POST.Source(f"file:{VEHICLE}").truth()
    a = POST.analyse(leak, grading=True)
    assert a["grading"] and a["histories"]["truth"] and a["grading_view"]["scenario"] == "tank_leak_100um"
    assert "truth" not in json.dumps(a["report"]).lower().replace("truth (grading)", "").replace("evaluator truth is never", "")
    injected = [f for f in a["grading_view"]["fault_timeline"] if f["kind"] == "injected (truth)"]
    assert injected and injected[0]["detection_delay_s"] is not None and injected[0]["detection_delay_s"] >= 0
    leak.truth = None


def test_exports(leak):
    csv = POST.subsystem_csv(leak, "PROP").splitlines()
    assert csv[0].startswith("seq,met_s,received,TANK_P")
    assert len(POST.frames_jsonl(leak).splitlines()) == len(leak.frames)


def test_api_postflight_end_to_end_and_twin_has_no_fake_recovery(monkeypatch):
    os.environ["AEROFIRE_ATMOSPHERE"] = "snapshot"
    from aerofire.api import ground as GA
    from aerofire.api.app import app

    rcv = Receiver(CFG, lambda: PROFILE)
    playback_sync(rcv, SESSION)
    monkeypatch.setattr(GA, "RECEIVER", rcv)
    c = TestClient(app)
    assert c.post("/api/ground/postflight/recover", json={}).status_code == 200
    for _ in range(200):
        st = c.get("/api/ground/postflight/recover").json()
        if st["state"] != "running":
            break
    assert st["state"] == "done" and st["chain_ok"] is True
    pf = c.get("/api/ground/postflight").json()
    assert {q["id"] for q in pf["report"]["requirements"]} >= {"REQ-DATA-1", "REQ-DATA-2"}
    assert c.post("/api/ground/postflight/closure/data_archived", json=OP).json()["data_archived"]["by"] == "PF Tester"
    assert c.post("/api/ground/postflight/closure/nope", json=OP).status_code == 404
    ex = c.get("/api/ground/postflight/export/report.json").json()
    canon = json.dumps(ex["report"], sort_keys=True, separators=(",", ":"), default=str)
    assert hashlib.sha256(canon.encode()).hexdigest() == ex["report_sha256"]

    # A twin (unsigned, live TCP) session: no recorder on Laptop A -> recovery is refused, not faked.
    from aerofire.receiver.twin import TwinFilter
    from test_twin_pipeline import twin  # same tests directory (pytest prepend import mode)

    tw = Receiver(CFG, lambda: PROFILE)
    tw.mode = "PLAYBACK"
    f = TwinFilter()
    for i in range(20):
        p = twin(1000 + 500 * i, 100.0 + 2 * i)
        if f.offer(p, "10.0.0.5", 1.0 + 0.5 * i)[0]:
            tw.on_twin_packet(p, "10.0.0.5", 1.0 + 0.5 * i, f)
    tw.playback_file = None
    tw.current.sender_addr = None
    monkeypatch.setattr(GA, "RECEIVER", tw)
    assert c.post("/api/ground/postflight/recover", json={}).status_code == 409
    pf = c.get("/api/ground/postflight").json()
    reqs = {q["id"]: q["status"] for q in pf["report"]["requirements"]}
    assert reqs["REQ-DATA-1"] == "NOT TESTED" and reqs["REQ-DATA-2"] == "NOT TESTED"
    assert pf["report"]["recovery"] is None and pf["grading"] is False
