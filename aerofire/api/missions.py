"""Mission API: /api/missions/*.

Operator commands (commit, hold, terminate, alert acknowledgement, waivers,
corrective actions, closure) all require an operator name and are written to
the mission's append-only event log. Nothing fires, releases or ignites
without an explicit operator call.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import load_raw
from ..mission_runner import CommandError, MissionRun, build_mission
from ..physics import atmosphere as A

router = APIRouter(prefix="/api/missions", tags=["missions"])
MISSIONS: dict[str, MissionRun] = {}

# Indirection so tests can pin the atmosphere (no network).
def profile_for(source: str, base) -> A.AtmosphereProfile:
    from pathlib import Path

    if source == "snapshot":
        snap = Path(__file__).resolve().parents[2] / "config" / "atmosphere_snapshot.json"
        return A.load_snapshot(str(snap), base["atmosphere"]["levels_hpa"], base["atmosphere"]["blend_m"])
    from ..config import Config

    cfg = Config(base)
    return A.fetch_profile(cfg, cfg.site.lat_deg, cfg.site.lon_deg)


class FaultsIn(BaseModel):
    tank_leak_um: float = 0.0
    tank_leak_onset_s: float = 0.0
    balloon_leak_mm: float = 0.0
    balloon_leak_onset_s: float = 0.0
    gnss_spoof_start_s: float | None = None
    gnss_spoof_drift_m_s: tuple[float, float] = (2.0, 0.0)
    motor_heater_failed: bool = False
    blackouts: list[tuple[float, float]] = Field(default_factory=list)
    packet_loss: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_s: float | None = Field(default=None, ge=0.0)


class MissionIn(BaseModel):
    name: str = ""
    seed: int = 0
    time_scale: float | None = Field(default=None, ge=0.0, description="mission s per wall s; 0 = as fast as possible")
    atmosphere: str = Field(default="live", pattern="^(live|snapshot)$")
    faults: FaultsIn = Field(default_factory=FaultsIn)
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    autostart: bool = Field(default=True, description="false: create paused (tests / scripted demos step it explicitly)")


class OperatorIn(BaseModel):
    operator: str = Field(min_length=1, max_length=64)


class HoldIn(OperatorIn):
    release: bool = False


class WaiverIn(OperatorIn):
    reason: str = Field(min_length=1, max_length=500)


class TextIn(OperatorIn):
    text: str = Field(min_length=1, max_length=2000)


def get_mission(mission_id: str) -> MissionRun:
    m = MISSIONS.get(mission_id)
    if m is None:
        raise HTTPException(404, f"unknown mission {mission_id}")
    return m


def _call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except CommandError as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("", status_code=201)
def create_mission(body: MissionIn) -> dict[str, Any]:
    base = load_raw()
    profile = profile_for(body.atmosphere, base)
    m = build_mission(base, profile, overrides=body.config_overrides, faults=body.faults.model_dump(), seed=body.seed,
                      time_scale=body.time_scale, name=body.name)
    MISSIONS[m.id] = m
    if body.autostart:
        m.start()
    return {"mission_id": m.id, "summary": m.summary()}


@router.get("")
def list_missions() -> list[dict[str, Any]]:
    return [m.summary() for m in MISSIONS.values()]


@router.get("/{mission_id}")
def mission_summary(mission_id: str) -> dict[str, Any]:
    return get_mission(mission_id).summary()


@router.get("/{mission_id}/frame")
def latest_frame(mission_id: str) -> dict[str, Any]:
    m = get_mission(mission_id)
    with m.lock:
        if not m.frames:
            raise HTTPException(404, "no frame yet")
        return m.frames[-1]


@router.get("/{mission_id}/preflight")
def preflight(mission_id: str) -> dict[str, Any]:
    m = get_mission(mission_id)
    items = m.preflight()
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("NOT CHECKED", "PASS", "FAIL", "WAIVED")}
    return {"mission_id": mission_id, "items": items, "counts": counts}


@router.post("/{mission_id}/preflight/{check_id}/waive")
def waive(mission_id: str, check_id: str, body: WaiverIn) -> dict[str, Any]:
    return _call(get_mission(mission_id).waive, check_id, body.operator, body.reason)


@router.get("/{mission_id}/stream")
async def stream(mission_id: str, last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
                 from_id: int | None = Query(default=None, alias="from")):
    """SSE of ground frames. `id:` = frame id; resumes after Last-Event-ID; ends with `event: end`."""
    m = get_mission(mission_id)
    start = from_id if from_id is not None else (int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)

    async def gen():
        i = start  # frames[i] has id i + 1
        idle = 0.0
        while True:
            with m.lock:
                batch = m.frames[i:i + 50]
                finished = m.state == "finished"
            for f in batch:
                yield f"id: {f['id']}\ndata: {json.dumps(f, default=str)}\n\n"
            i += len(batch)
            if batch:
                idle = 0.0
                continue
            if finished:
                yield f"id: {i + 1}\nevent: end\ndata: {json.dumps({'frames': i, 'state': 'finished'})}\n\n"
                return
            await asyncio.sleep(0.1)
            idle += 0.1
            if idle >= 15.0:  # keep-alive comment so proxies do not drop the connection
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{mission_id}/track")
def track(mission_id: str, step: int = Query(5, ge=1)) -> dict[str, Any]:
    """What the operator saw live (append-only): fused or predicted positions with status."""
    m = get_mission(mission_id)
    with m.lock:
        return {"mission_id": mission_id, "track": m.track[::step]}


@router.post("/{mission_id}/commit")
def commit(mission_id: str, body: OperatorIn) -> dict[str, Any]:
    return _call(get_mission(mission_id).commit, body.operator)


@router.post("/{mission_id}/hold")
def hold(mission_id: str, body: HoldIn) -> dict[str, Any]:
    return _call(get_mission(mission_id).hold, body.operator, body.release)


@router.post("/{mission_id}/terminate")
def terminate(mission_id: str, body: OperatorIn) -> dict[str, Any]:
    return _call(get_mission(mission_id).terminate, body.operator)


@router.post("/{mission_id}/alerts/{alert_id}/ack")
def ack(mission_id: str, alert_id: int, body: OperatorIn) -> dict[str, Any]:
    return _call(get_mission(mission_id).ack_alert, alert_id, body.operator)


@router.get("/{mission_id}/events")
def events(mission_id: str) -> list[dict[str, Any]]:
    m = get_mission(mission_id)
    with m.lock:
        return list(m.event_log)


@router.post("/{mission_id}/recover")
def recover(mission_id: str, batch_delay_s: float = Query(0.05, ge=0.0, le=2.0)) -> dict[str, Any]:
    return _call(get_mission(mission_id).start_recovery, batch_delay_s)


@router.get("/{mission_id}/recover")
def recover_status(mission_id: str) -> dict[str, Any]:
    return get_mission(mission_id).recovery_status()


@router.get("/{mission_id}/postflight")
def postflight(mission_id: str, tracks: bool = True) -> dict[str, Any]:
    m = get_mission(mission_id)
    if m.state != "finished":
        raise HTTPException(409, "post-flight analysis is available after the mission has finished")
    return m.postflight(with_track=tracks)


@router.post("/{mission_id}/faults/{fault_id}/action")
def corrective_action(mission_id: str, fault_id: str, body: TextIn) -> dict[str, Any]:
    return _call(get_mission(mission_id).set_corrective_action, fault_id, body.operator, body.text)


@router.post("/{mission_id}/closure/{item}")
def closure(mission_id: str, item: str, body: OperatorIn) -> dict[str, Any]:
    return _call(get_mission(mission_id).close_item, item, body.operator)


@router.get("/{mission_id}/export")
def export(mission_id: str, format: str = Query("json", pattern="^(json|csv)$")):
    m = get_mission(mission_id)
    if m.state != "finished":
        raise HTTPException(409, "export is available after the mission has finished")
    if format == "csv":
        return PlainTextResponse(m.export_csv(), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{m.id}-telemetry.csv"'})
    return m.export_json()


@router.get("/{mission_id}/truth")
def truth(mission_id: str) -> dict[str, Any]:
    """EVALUATOR ONLY: simulator truth. Disabled unless config api.truth_enabled."""
    m = get_mission(mission_id)
    if not m.cfg.api.truth_enabled:
        raise HTTPException(403, "truth endpoint disabled (api.truth_enabled = false)")
    with m.lock:
        return m.truth()
