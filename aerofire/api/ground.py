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
    GET  /api/ground/ml/model       model card (version, test-mission evaluation, decision)
    GET  /api/ground/ml/report      docs/ML_RESULTS.md (text/markdown)
    POST /api/ground/postflight/recover   {"source"?}: progressive recovery from Laptop A (or a recorder file)
    GET  /api/ground/postflight/recover   recovery progress
    GET  /api/ground/postflight           reports (requirements, limits, predicted vs actual, faults, forecast scorecard,
                                          landing) + report SHA-256 + histories; grading view only when enabled
    POST /api/ground/postflight/grading   {"operator"}: fetch truth from Laptop A for grading (after the mission)
    POST /api/ground/postflight/closure/{item}  {"operator"}
    GET  /api/ground/postflight/export/frames.jsonl | export/csv/{subsystem} | export/report.json

Nothing here can send to the vehicle.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..config import load_config, load_raw
from ..physics import atmosphere as A
from ..receiver import postflight as POST
from ..receiver import preflight as PRE
from ..receiver.service import Receiver
from ..receiver.tcp_receiver import GLOBAL_TCP_RECEIVER
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
    playback (AEROFIRE_PLAYBACK file, AEROFIRE_PLAYBACK_SPEED), plus the TCP digital twin telemetry server."""
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

    # Start TCP digital twin receiver for stratospheric balloon rocket telemetry
    no_tcp = os.environ.get("AEROFIRE_NO_TCP", "").lower() in ("1", "true", "yes")
    if not no_tcp:
        tcp_host = os.environ.get("AEROFIRE_TCP_HOST", "0.0.0.0")
        tcp_port = int(os.environ.get("AEROFIRE_TCP_PORT", "65432"))
        GLOBAL_TCP_RECEIVER.host = tcp_host
        GLOBAL_TCP_RECEIVER.port = tcp_port
        # One pipeline: accepted twin packets enter the same receiver/estimator path as ICD frames.
        GLOBAL_TCP_RECEIVER.on_frame_cb = lambda p, src, utc: RECEIVER.on_twin_packet(p, src, utc, GLOBAL_TCP_RECEIVER.filter)
        GLOBAL_TCP_RECEIVER.start()


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
            snap = base | {"mission_id": None, "source": RECEIVER.mode, "link": {"aos": False}}
        else:
            snap = base | RECEIVER.current.snapshot(_now())
    # TCP socket diagnostics and the latest ACCEPTED raw twin packet (RAW, unprocessed; None before any).
    snap["digital_twin"] = GLOBAL_TCP_RECEIVER.get_status()
    snap["digital_twin_telemetry"] = GLOBAL_TCP_RECEIVER.latest_telemetry
    return snap


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


# ------------------------------------------------------------------ ML forecast


@router.get("/api/ground/ml/model")
async def ml_model() -> dict[str, Any]:
    ctx = await asyncio.to_thread(_ctx)
    h = ctx.get("hybrid")
    return h.summary() if h is not None else {"available": False}


@router.get("/api/ground/ml/report")
def ml_report():
    from fastapi.responses import PlainTextResponse

    path = ROOT / "docs" / "ML_RESULTS.md"
    if not path.exists():
        raise HTTPException(404, "no ML results yet: run python -m aerofire.ml.evaluate")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")


# ------------------------------------------------------------------ post-flight


class RecoverIn(BaseModel):
    source: str | None = Field(default=None, description="http://<laptop-A>:8100 or file:<recorder log>")


def _source_for(s, override: str | None) -> POST.Source:
    spec = override or POST.default_source(s, RECEIVER.playback_file)
    if not spec:
        raise HTTPException(409, "no recovery source: give {\"source\": \"http://<laptop-A-ip>:8100\"}")
    return POST.Source(spec)


@router.post("/api/ground/postflight/recover")
def recover(body: RecoverIn | None = None) -> dict[str, Any]:
    with RECEIVER.lock:
        s = _session()
        if s.recovery and s.recovery.r["state"] == "running":
            raise HTTPException(409, "recovery already running")
        s.recovery = POST.FrameRecovery(s, _source_for(s, body.source if body else None))
        s.log(_now(), "system", "RECOVERY STARTED", {"source": s.recovery.src.label()})
        threading.Thread(target=s.recovery.run, name="recovery", daemon=True).start()
        return s.recovery.r


@router.get("/api/ground/postflight/recover")
def recover_status() -> dict[str, Any]:
    with RECEIVER.lock:
        s = _session()
        return s.recovery.r if s.recovery else {"state": "idle", "source": POST.default_source(s, RECEIVER.playback_file)}


def _analysis() -> dict[str, Any]:
    s = _session()
    key = (len(s.frames), s.truth is not None, len(s.alarms), len(s.event_log))
    if s._pf_cache and s._pf_cache[0] == key:
        return s._pf_cache[1]
    out = POST.analyse(s, grading=s.truth is not None)
    s._pf_cache = (key, out)
    return out


@router.get("/api/ground/postflight")
async def postflight() -> dict[str, Any]:
    return await asyncio.to_thread(_analysis)


@router.post("/api/ground/postflight/grading")
def grading(body: OperatorIn) -> dict[str, Any]:
    with RECEIVER.lock:
        s = _session()
        src = s.recovery.src if s.recovery else _source_for(s, None)
    try:
        truth = src.truth()
    except Exception as exc:
        raise HTTPException(409, f"truth not available from {src.label()}: {exc}") from None
    with RECEIVER.lock:
        s.truth = truth
        s.log(_now(), body.operator.strip(), "GRADING ENABLED", {"source": src.label()})
    return {"grading": True, "source": src.label()}


@router.post("/api/ground/postflight/closure/{item}")
def closure(item: str, body: OperatorIn) -> dict[str, Any]:
    with RECEIVER.lock:
        s = _session()
        if item not in s.closure:
            raise HTTPException(404, "unknown closure item")
        s.closure[item] = {"by": body.operator.strip(), "utc": _now()}
        s.log(_now(), body.operator.strip(), "CLOSURE", {"item": item})
        return s.closure


@router.get("/api/ground/postflight/export/frames.jsonl")
def export_frames():
    from fastapi.responses import PlainTextResponse

    with RECEIVER.lock:
        s = _session()
    return PlainTextResponse(POST.frames_jsonl(s), media_type="application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{s.mission_id}.frames.jsonl"'})


@router.get("/api/ground/postflight/export/csv/{subsystem}")
def export_csv(subsystem: str):
    from fastapi.responses import PlainTextResponse

    with RECEIVER.lock:
        s = _session()
    try:
        text = POST.subsystem_csv(s, subsystem.upper())
    except KeyError:
        raise HTTPException(404, "unknown subsystem") from None
    return PlainTextResponse(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{s.mission_id}.{subsystem.upper()}.csv"'})


@router.get("/api/ground/postflight/export/report.json")
async def export_report():
    from fastapi.responses import Response

    a = await asyncio.to_thread(_analysis)
    body = json.dumps({"report": a["report"], "report_sha256": a["report_sha256"]}, sort_keys=True, separators=(",", ":"), default=str)
    return Response(body, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="postflight-report.json"'})


@router.get("/api/ground/digital-twin")
def digital_twin_state() -> dict[str, Any]:
    """Return digital twin TCP socket status, ping rate, and latest decoded telemetry frame."""
    return GLOBAL_TCP_RECEIVER.get_snapshot()




