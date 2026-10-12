"""Ground-station API over the receiver (Laptop B).

    WS   /ws/telemetry?hz=4         ground-side state snapshots (2-10 Hz)
    GET  /api/ground/status         receiver mode, sessions
    GET  /api/ground/state          one snapshot (same object as the WebSocket)
    GET  /api/ground/history        decimated history rows and track
    GET  /api/ground/dictionary     config/channels.yaml
    GET  /api/ground/events         append-only event log
    POST /api/ground/alarms/{id}/ack   {"operator"}
    POST /api/ground/poll           {"operator", "vote": "GO"|"NO_GO", "note"}
    GET  /api/ground/preflight      checks, readiness, hashed readiness report
    GET  /api/ground/preflight/profile  predicted telemetry profile, range zones, footprint, IIPs
    POST /api/ground/preflight/{check_id}/waive  {"operator", "reason"}

Nothing here can send to the vehicle.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..config import load_config, load_raw
from ..physics import atmosphere as A
from ..receiver import preflight as PRE
from ..receiver.service import Receiver
from ..telemetry import icd

router = APIRouter()
CFG = load_config()
ROOT = Path(__file__).resolve().parents[2]


def forecast_profile() -> A.AtmosphereProfile:
    """Ground forecast atmosphere: recorded snapshot (AEROFIRE_ATMOSPHERE=snapshot) or live Open-Meteo with ISA fallback."""
    if os.environ.get("AEROFIRE_ATMOSPHERE") == "snapshot":
        raw = load_raw()
        return A.load_snapshot(str(ROOT / "config" / "atmosphere_snapshot.json"), raw["atmosphere"]["levels_hpa"], raw["atmosphere"]["blend_m"])
    return A.fetch_profile(CFG, CFG.site.lat_deg, CFG.site.lon_deg)


RECEIVER = Receiver(CFG, forecast_profile)
_tasks: list[asyncio.Task] = []


def start_receiver() -> None:
    """Start the source chosen by backend.py: AEROFIRE_RECEIVER=udp (AEROFIRE_UDP_HOST/PORT) or
    playback (AEROFIRE_PLAYBACK file, AEROFIRE_PLAYBACK_SPEED)."""
    kind = os.environ.get("AEROFIRE_RECEIVER")
    loop = asyncio.get_running_loop()
    if kind == "udp":
        host, port = os.environ.get("AEROFIRE_UDP_HOST", "0.0.0.0"), int(os.environ.get("AEROFIRE_UDP_PORT", "5600"))
        _tasks.append(loop.create_task(RECEIVER.run_udp(host, port)))
    elif kind == "playback":
        path, speed = os.environ["AEROFIRE_PLAYBACK"], float(os.environ.get("AEROFIRE_PLAYBACK_SPEED", "1"))
        _tasks.append(loop.create_task(RECEIVER.run_playback(path, speed)))
    else:
        RECEIVER.mode = "NO SOURCE"


def _now() -> float:
    return RECEIVER.clock()


def _session():
    s = RECEIVER.current
    if s is None:
        raise HTTPException(404, "no telemetry received yet")
    return s


def snapshot() -> dict[str, Any]:
    with RECEIVER.lock:
        base = {"receiver": RECEIVER.status()}
        if RECEIVER.current is None:
            return base | {"mission_id": None, "source": RECEIVER.mode, "link": {"aos": False}}
        return base | RECEIVER.current.snapshot(_now())


@router.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket, hz: float = 4.0) -> None:
    await ws.accept()
    period = 1.0 / min(10.0, max(2.0, hz))
    try:
        while True:
            await ws.send_json(await asyncio.to_thread(snapshot))
            await asyncio.sleep(period)
    except (WebSocketDisconnect, RuntimeError):
        return


@router.get("/api/ground/status")
def status() -> dict[str, Any]:
    with RECEIVER.lock:
        return RECEIVER.status()


@router.get("/api/ground/state")
def state() -> dict[str, Any]:
    return snapshot()


@router.get("/api/ground/history")
def history(max_points: int = Query(3000, ge=10, le=20000), since: float | None = None) -> dict[str, Any]:
    """Per-step rows and fused track. `since` (MET) returns only newer rows, undecimated up to max_points."""
    with RECEIVER.lock:
        s = _session()
        h, tr = s.history, s.track
        if since is not None:
            h = [r for r in h[-max_points * 4:] if r["met"] > since][-max_points:]
            tr = [r for r in tr[-max_points * 4:] if r["met"] > since][-max_points:]
            return {"mission_id": s.mission_id, "rows": h, "track": tr, "los_periods": s.los_periods}
        step = max(1, len(h) // max_points)
        tstep = max(1, len(tr) // max_points)
        return {"mission_id": s.mission_id, "rows": h[::step] + (h[-1:] if h and (len(h) - 1) % step else []),
                "track": tr[::tstep], "los_periods": s.los_periods}


@router.get("/api/ground/dictionary")
def dictionary() -> dict[str, Any]:
    return icd.load_channels()


@router.get("/api/ground/events")
def events() -> list[dict[str, Any]]:
    with RECEIVER.lock:
        return list(_session().event_log)


class OperatorIn(BaseModel):
    operator: str = Field(min_length=1, max_length=64)


class PollIn(OperatorIn):
    vote: Literal["GO", "NO_GO"]
    note: str = Field(default="", max_length=500)


@router.post("/api/ground/alarms/{alarm_id}/ack")
def ack(alarm_id: int, body: OperatorIn) -> dict[str, Any]:
    with RECEIVER.lock:
        try:
            return _session().ack(alarm_id, body.operator.strip(), _now())
        except KeyError:
            raise HTTPException(404, "unknown alarm") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None


@router.post("/api/ground/poll")
def poll(body: PollIn) -> dict[str, Any]:
    with RECEIVER.lock:
        return _session().poll(body.operator.strip(), body.vote, body.note, _now())


# ------------------------------------------------------------------ pre-flight


def _ctx() -> dict[str, Any]:
    return RECEIVER.prepare()


@router.get("/api/ground/preflight")
async def preflight() -> dict[str, Any]:
    ctx = await asyncio.to_thread(_ctx)
    with RECEIVER.lock:
        s = RECEIVER.current
        if s is None:
            rc = ctx["range_check"]
            items = [{"id": "telemetry", "title": "Pad telemetry received", "status": "NOT CHECKED", "evidence": {"reason": "no frames yet"}},
                     {"id": "profile", "title": "Predicted telemetry profile computed (forecast atmosphere)", "status": "PASS",
                      "evidence": {"samples": len(ctx["profile"]["samples"]), "atmo_source": ctx["profile"]["atmo_source"]}},
                     {"id": "range", "title": "Predicted landing footprint and IIPs clear of restricted zones", "status": rc["status"], "evidence": rc}]
            report = {"mission_id": None, "readiness": "HOLD", "items": items}
            return {"readiness": "HOLD", "counts": {}, "items": items, "report": report, "report_sha256": PRE.canonical_hash(report)}
        return PRE.evaluate(s)


@router.get("/api/ground/preflight/profile")
async def preflight_profile(step: int = Query(4, ge=1, le=100)) -> dict[str, Any]:
    ctx = await asyncio.to_thread(_ctx)
    prof = ctx["profile"]
    return {"samples": prof["samples"][::step], "track": prof["track"], "basis": prof["basis"], "atmo_source": prof["atmo_source"],
            "atmo_note": prof["atmo_note"], "footprint": ctx["footprint"], "iips": ctx["iips"], "iip_model": ctx["iip_model"],
            "release_point": ctx["release_point"], "range": ctx["range"], "range_check": ctx["range_check"]}


class WaiverIn(OperatorIn):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/api/ground/preflight/{check_id}/waive")
def waive(check_id: str, body: WaiverIn) -> dict[str, Any]:
    with RECEIVER.lock:
        s = _session()
        ids = {i["id"] for i in PRE.evaluate(s)["items"]}
        if check_id not in ids:
            raise HTTPException(404, "unknown check")
        return s.waive(check_id, body.operator.strip(), body.reason.strip(), _now())
