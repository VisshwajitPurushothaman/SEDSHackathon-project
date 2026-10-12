"""Contract tests for the legacy pre-flight endpoints (no network: recorded atmosphere)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aerofire.api import app as app_module
from aerofire.physics import atmosphere as A

SNAP = A.load_snapshot(str(Path(__file__).resolve().parent.parent / "config" / "atmosphere_snapshot.json"),
                       app_module.CFG.atmosphere.levels_hpa)

LEGACY_KEYS = {"time_s", "altitude_m", "ext_temp_c", "ext_pres_hpa", "tank_pres_bar", "motor_temp_c", "mass_loss_g",
               "tilt_deg", "leak_detected", "leak_status", "gate_status", "is_abort", "is_ignition"}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(app_module, "current_profile", lambda: SNAP)
    return TestClient(app_module.app)


def test_health(client):
    assert client.get("/api/health").json() == {"status": "AERO-FIRE Backend Operational"}


def test_simulate_sealed_recommends_go_never_ignites(client):
    body = client.post("/api/simulate", json={"puncture_um": 0}).json()
    s, frames = body["summary"], body["telemetry"]
    assert s["verdict"] == "GO_RECOMMENDED"
    assert s["atmo_source"].startswith("open-meteo@")
    assert all(LEGACY_KEYS <= f.keys() for f in frames)
    assert not any(f["is_ignition"] for f in frames)
    assert frames[-1]["gate_status"] == "ARMED" and frames[-1]["go_recommended"]
    assert 26000 < frames[-1]["altitude_m"] < 30000
    ts = [f["time_s"] for f in frames]
    assert ts == sorted(ts)


def test_simulate_leak_aborts(client):
    s = client.post("/api/simulate", json={"puncture_um": 50}).json()["summary"]
    assert s["verdict"] == "ABORT_RECOMMENDED"
    assert "Pressurant leak detected" in s["abort_reason"]


def test_stream_has_ids_and_explicit_end(client):
    with client.stream("GET", "/api/stream", params={"puncture_um": 300, "pace_s": 0}) as r:
        text = "".join(r.iter_text())
    events = [e for e in text.split("\n\n") if e.strip()]
    ids = [int(line.split(": ")[1]) for e in events for line in e.splitlines() if line.startswith("id: ")]
    assert ids == list(range(1, len(events) + 1))
    assert "event: end" in events[-1]
    frames = [json.loads(e.split("data: ", 1)[1]) for e in events[:-1]]
    assert frames[-1]["is_abort"] and not frames[-1]["is_ignition"]
