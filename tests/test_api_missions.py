"""API contract tests for /api/missions/* (recorded atmosphere, missions stepped deterministically)."""
import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from aerofire.api import app as app_module
from aerofire.api import missions as M

OP = {"operator": "A. Operator"}


@pytest.fixture(scope="module")
def client():
    orig = M.profile_for
    M.profile_for = lambda source, base: orig("snapshot", base)
    yield TestClient(app_module.app)
    M.profile_for = orig


def create(client, **body):
    r = client.post("/api/missions", json={"autostart": False, "time_scale": 0, "atmosphere": "snapshot", **body})
    assert r.status_code == 201, r.text
    return r.json()["mission_id"], M.MISSIONS[r.json()["mission_id"]]


def advance(m, until, limit=30000):
    while not until(m) and m.sim.t < limit and m.state != "finished":
        with m.lock:
            m.step()


@pytest.fixture(scope="module")
def flown(client):
    """A nominal mission flown to GO, committed through the API, landed."""
    mid, m = create(client, name="contract", seed=11)
    log = {}
    # Before GO: commit is refused.
    advance(m, lambda m: m.sim.t >= 600)
    log["early_commit"] = client.post(f"/api/missions/{mid}/commit", json=OP)
    log["preflight_ascent"] = client.get(f"/api/missions/{mid}/preflight").json()
    advance(m, lambda m: m.ground.recommendation == "GO_RECOMMENDED")
    # Hold blocks commit; release re-allows it.
    log["hold"] = client.post(f"/api/missions/{mid}/hold", json=OP)
    log["commit_while_held"] = client.post(f"/api/missions/{mid}/commit", json=OP)
    log["release"] = client.post(f"/api/missions/{mid}/hold", json=OP | {"release": True})
    advance(m, lambda m: m.ground.recommendation == "GO_RECOMMENDED")
    log["empty_operator"] = client.post(f"/api/missions/{mid}/commit", json={"operator": ""})
    log["commit"] = client.post(f"/api/missions/{mid}/commit", json=OP)
    log["second_commit"] = client.post(f"/api/missions/{mid}/commit", json=OP)
    log["recover_early"] = client.post(f"/api/missions/{mid}/recover")
    log["postflight_early"] = client.get(f"/api/missions/{mid}/postflight")
    advance(m, lambda m: False)
    return mid, m, log


def test_create_list_summary(client):
    mid, m = create(client, name="listed")
    assert any(s["id"] == mid for s in client.get("/api/missions").json())
    s = client.get(f"/api/missions/{mid}").json()
    assert s["name"] == "listed" and s["state"] == "created" and s["atmo_source"].startswith("open-meteo@")
    assert client.get("/api/missions/nope").status_code == 404
    assert client.get(f"/api/missions/{mid}/frame").status_code == 404  # nothing yet


def test_frame_has_status_on_every_estimate(client):
    mid, m = create(client, seed=2)
    advance(m, lambda m: m.sim.t >= 120)
    fr = client.get(f"/api/missions/{mid}/frame").json()
    assert fr["phase"] in ("Pad", "Ascent")
    assert {e["status"] for e in fr["estimates"].values()} <= {"MEASURED", "FUSED", "PREDICTED", "RECONSTRUCTED", "UNKNOWN"}
    assert fr["estimates"]["altitude"]["status"] == "FUSED"
    assert set(fr["gates"]) == {"float", "nav", "motor", "swing", "impact"}


def test_operator_command_rules(flown):
    _, _, log = flown
    assert log["early_commit"].status_code == 409 and "no GO" in log["early_commit"].json()["detail"]
    assert log["hold"].status_code == 200 and log["hold"].json()["state"] == "held"
    assert log["commit_while_held"].status_code == 409
    assert log["release"].status_code == 200 and log["release"].json()["state"] == "none"
    assert log["empty_operator"].status_code == 422
    assert log["commit"].status_code == 200 and log["commit"].json()["by"] == OP["operator"]
    assert log["second_commit"].status_code == 409
    assert log["recover_early"].status_code == 409
    assert log["postflight_early"].status_code == 409


def test_mission_landed_and_event_log_records_operator(flown, client):
    mid, m, _ = flown
    s = client.get(f"/api/missions/{mid}").json()
    assert s["state"] == "finished" and s["end_reason"] == "landed" and s["phase"] == "Landed"
    ev = client.get(f"/api/missions/{mid}/events").json()
    actions = [(e["actor"], e["action"]) for e in ev]
    assert (OP["operator"], "COMMIT") in actions and (OP["operator"], "HOLD") in actions
    assert [e["n"] for e in ev] == list(range(1, len(ev) + 1))  # append-only, gap-free


def test_preflight_items_and_waiver(flown, client):
    mid, _, log = flown
    items = {i["id"]: i for i in log["preflight_ascent"]["items"]}
    assert {"atmosphere_live", "link_budget", "recorder_chain", "tank_fill", "motor_temp", "nav_agreement", "leak_check",
            "truth_endpoint", "assumptions_reviewed"} <= set(items)
    assert items["recorder_chain"]["status"] == "PASS"
    assert items["assumptions_reviewed"]["status"] == "NOT CHECKED"
    assert client.post(f"/api/missions/{mid}/preflight/assumptions_reviewed/waive", json=OP | {"reason": ""}).status_code == 422
    assert client.post(f"/api/missions/{mid}/preflight/nope/waive", json=OP | {"reason": "x"}).status_code == 404
    r = client.post(f"/api/missions/{mid}/preflight/assumptions_reviewed/waive", json=OP | {"reason": "demo: no hardware data"})
    assert r.status_code == 200
    items = {i["id"]: i for i in client.get(f"/api/missions/{mid}/preflight").json()["items"]}
    assert items["assumptions_reviewed"]["status"] == "WAIVED"


def test_alert_acknowledgement(flown, client):
    mid, m, _ = flown
    fr = client.get(f"/api/missions/{mid}/frame").json()
    go = next(a for a in fr["alerts"] if a["key"] == "go")
    r = client.post(f"/api/missions/{mid}/alerts/{go['id']}/ack", json=OP)
    assert r.status_code == 200 and r.json()["acknowledged_by"] == OP["operator"]
    assert client.post(f"/api/missions/{mid}/alerts/{go['id']}/ack", json=OP).status_code == 409
    assert client.post(f"/api/missions/{mid}/alerts/9999/ack", json=OP).status_code == 404


def test_stream_ids_resume_and_end(flown, client):
    mid, m, _ = flown

    def events(headers=None):
        with client.stream("GET", f"/api/missions/{mid}/stream", headers=headers or {}) as r:
            return [e for e in "".join(r.iter_text()).split("\n\n") if e.strip()]

    evs = events()
    ids = [int(line.split(": ")[1]) for e in evs for line in e.splitlines() if line.startswith("id: ")]
    assert ids == list(range(1, len(evs) + 1))
    assert "event: end" in evs[-1] and len(evs) - 1 == len(m.frames)
    resumed = events({"Last-Event-ID": "100"})
    first = int(resumed[0].splitlines()[0].split(": ")[1])
    assert first == 101 and "event: end" in resumed[-1]
    frame = json.loads(evs[0].split("data: ", 1)[1])
    assert frame["mission_id"] == mid and "estimates" in frame


def test_recovery_postflight_export(flown, client):
    mid, m, _ = flown
    assert client.post(f"/api/missions/{mid}/recover", params={"batch_delay_s": 0}).status_code == 200
    for _ in range(600):
        st = client.get(f"/api/missions/{mid}/recover").json()
        if st["state"] == "done":
            break
        time.sleep(0.05)
    assert st["state"] == "done" and st["lost_count"] == 0 and st["have"] == st["expected"] and st["chain_ok"] is True
    pf = client.get(f"/api/missions/{mid}/postflight").json()
    reqs = {r["id"]: r["status"] for r in pf["requirements"]}
    assert reqs["REQ-DATA-1"] == "PASS" and reqs["REQ-DATA-2"] == "PASS" and reqs["REQ-GATE-1"] == "PASS"
    assert reqs["REQ-LEAK-1"] == "NOT TESTED" and reqs["REQ-PRED-1"] == "NOT TESTED"
    assert reqs["REQ-LAND-1"] in ("PASS", "FAIL")
    assert pf["reconstruction"] and pf["reconstruction"][0]["status"] == "RECONSTRUCTED"
    assert "scorecard" in pf and pf["scorecard"]["false_alarms"] == []
    # Corrective action and closure.
    assert client.post(f"/api/missions/{mid}/faults/none/action", json=OP | {"text": "n/a"}).status_code == 200
    assert client.post(f"/api/missions/{mid}/closure/data_archived", json=OP).status_code == 200
    assert client.post(f"/api/missions/{mid}/closure/nope", json=OP).status_code == 404
    # Export: JSON with a report hash that matches, and CSV.
    ex = client.get(f"/api/missions/{mid}/export", params={"format": "json"}).json()
    canon = json.dumps(ex["report"], sort_keys=True, separators=(",", ":"), default=str)
    assert hashlib.sha256(canon.encode()).hexdigest() == ex["report_sha256"]
    assert len(ex["records"]) == st["expected"] and "scorecard" not in ex["report"]
    csv = client.get(f"/api/missions/{mid}/export", params={"format": "csv"}).text
    assert csv.splitlines()[0] == "seq,event_time,sensor_id,source,status,received_live,field,value,unit"


def test_truth_endpoint_is_gated(flown, client):
    mid, _, _ = flown
    t = client.get(f"/api/missions/{mid}/truth").json()
    assert t["track"] and t["landing"] is not None
    hidden, _ = create(client, config_overrides={"api": {"truth_enabled": False}})
    assert client.get(f"/api/missions/{hidden}/truth").status_code == 403


def test_terminate_cuts_down_without_launch(client):
    mid, m = create(client, seed=5)
    advance(m, lambda m: m.sim.t >= 600)
    r = client.post(f"/api/missions/{mid}/terminate", json=OP)
    assert r.status_code == 200 and r.json()["state"] == "terminated"
    assert client.post(f"/api/missions/{mid}/commit", json=OP).status_code == 409
    advance(m, lambda m: False)
    assert m.state == "finished" and m.sim.rocket_result is None
