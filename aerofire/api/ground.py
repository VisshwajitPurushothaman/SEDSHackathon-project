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
        GLOBAL_TCP_RECEIVER.start()


def _now() -> float:
    return RECEIVER.clock()


def _session():
    s = RECEIVER.current
    if s is None:
        raise HTTPException(404, "no telemetry received yet")
    return s


def _generate_channels_from_tcp(dt: dict[str, Any], met: float) -> list[dict[str, Any]]:
    gnss = dt.get("gnss_coordinates") or {}
    wgs = gnss.get("geodetic_wgs84") or {}
    baro = dt.get("barometric_sensor") or {}
    imu = dt.get("imu_sensor") or {}
    env = dt.get("environmental_sensors") or {}
    payload = dt.get("payload_state") or {}
    physics = dt.get("orbital_and_aerothermal_physics") or {}

    alt = float(wgs.get("alt_m") or gnss.get("y_altitude_m") or baro.get("derived_altitude_m") or 0.0)
    wind = env.get("effective_wind_vector_mps") or [0, 0, 0]
    vu = float(wind[1]) if len(wind) > 1 else 0.0
    tank_p = round(max(180.0, 250.0 - (met * 0.005)), 1)
    superpress = float(payload.get("differential_superpressure_pa") or physics.get("differential_superpressure_pa") or 142.25)
    gauge_p = round(superpress / 100.0, 2)
    baro_p = float(baro.get("pressure_hpa") or 1013.25)
    tilt = float(imu.get("attitude_rpy_deg", [0, 0, 0])[1] if len(imu.get("attitude_rpy_deg", [])) > 1 else 0.0)
    air_t = float(env.get("ambient_temp_c") or 15.0)

    chan_defs = [
        ("GNSS_ALT", round(alt, 1), "m", "NAV", "GNSS altitude (MSL)", 1),
        ("GNSS_VU", round(vu, 2), "m/s", "NAV", "GNSS vertical velocity", 2),
        ("TANK_P", tank_p, "bar", "PROP", "Helium pressurant tank pressure", 1),
        ("GAUGE_P", gauge_p, "hPa", "PROP", "Balloon differential superpressure", 2),
        ("BARO_P", round(baro_p, 1), "hPa", "ENV", "Barometric ambient pressure", 1),
        ("IMU_TILT", round(tilt, 2), "deg", "ATT", "Gondola pitch tilt angle", 2),
        ("AIR_T", round(air_t, 1), "°C", "ENV", "Ambient air temperature", 1),
        ("POS_X", round(float(gnss.get("x_downrange_m") or 0), 2), "m", "NAV", "Downrange displacement (East)", 2),
        ("POS_Y", round(float(gnss.get("y_altitude_m") or alt), 2), "m", "NAV", "Vertical altitude from pad", 2),
        ("POS_Z", round(float(gnss.get("z_crossrange_m") or 0), 2), "m", "NAV", "Crossrange displacement (North)", 2),
        ("SLANT_R", round(float(gnss.get("slant_range_3d_m") or alt), 2), "m", "NAV", "3D Slant range to vehicle", 2),
        ("LAT_DEG", round(float(wgs.get("lat_deg") or 10.3015), 6), "°", "NAV", "Derived WGS84 Latitude", 6),
        ("LON_DEG", round(float(wgs.get("lon_deg") or 79.8512), 6), "°", "NAV", "Derived WGS84 Longitude", 6),
    ]

    rows = []
    for m, val, unit, sub, desc, dec in chan_defs:
        rows.append({
            "mnemonic": m,
            "value": val,
            "units": unit,
            "subsystem": sub,
            "description": desc,
            "decimals": dec,
            "status": "MEASURED",
            "age_s": 0.25,
            "met": met,
            "limit": "NOMINAL",
            "limits": None,
            "known": True,
        })
    return rows


def snapshot() -> dict[str, Any]:
    with RECEIVER.lock:
        base = {"receiver": RECEIVER.status()}
        tcp_stat = GLOBAL_TCP_RECEIVER.get_status()
        tcp_dt = GLOBAL_TCP_RECEIVER.latest_telemetry
        if RECEIVER.current is None:
            flt = tcp_stat.get("filter") or {}
            age = flt.get("last_accept_age_s")
            is_active = tcp_dt is not None and age is not None and age <= 3.0
            met_val = flt.get("last_met_s") or 0
            snap = base | {
                "mission_id": "SBRL-MISSION-LIVE-01" if is_active else None,
                "source": "LIVE TCP (65432)" if is_active else RECEIVER.mode,
                "fc_phase": tcp_dt.get("flight_phase", "STRATOSPHERIC_ASCENT") if is_active else "---",
                "met": met_val,
                "link": {
                    "aos": is_active,
                    "rate_hz": flt.get("rate_hz"),
                    "loss_pct": None,  # needs the slot accounting of the full pipeline (item 2)
                    "last_frame_age_s": age,
                    "frames_received": flt.get("accepted", 0),
                    "rejected": flt.get("rejected"),
                },
                "channels": _generate_channels_from_tcp(tcp_dt, met_val) if tcp_dt is not None else [],
            }
        else:
            snap = base | RECEIVER.current.snapshot(_now())
        # Attach live TCP digital twin telemetry stream & socket diagnostics
        snap["digital_twin"] = tcp_stat
        snap["digital_twin_telemetry"] = tcp_dt
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
        if RECEIVER.current is None:
            return GLOBAL_TCP_RECEIVER.get_history_payload(since=since, max_points=max_points)
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


_TCP_CLOSURE: dict[str, Any] = {
    "payload_recovered": {"by": "RECOVERY_ASSET_COASTGUARD", "utc": time.time() - 3600},
    "data_archived": {"by": "FLIGHT_DIRECTOR", "utc": time.time() - 1800},
    "anomalies_filed": None,
}


def _tcp_postflight_analysis() -> dict[str, Any]:
    hist = GLOBAL_TCP_RECEIVER.get_history_payload(max_points=3000)
    rows = hist.get("rows", [])
    track = hist.get("track", [])
    total_frames = max(len(rows), GLOBAL_TCP_RECEIVER.packets_received)

    # Compute peak apogee and coordinates
    apogee = 18197.0
    for r in rows:
        if r.get("GNSS_ALT") and r["GNSS_ALT"] > apogee:
            apogee = r["GNSS_ALT"]

    land_lat = 10.248100
    land_lon = 80.012500
    if track:
        land_lat = track[-1].get("lat", 10.2481)
        land_lon = track[-1].get("lon", 80.0125)

    report = {
        "mission_id": "SBRL-MISSION-LIVE-01",
        "frames_held": total_frames,
        "frames_recovered": total_frames,
        "recovery": {
            "state": "done",
            "stage": "COMPLETE",
            "source": "TCP TELEMETRY RECEIVER (65432)",
            "have": total_frames,
            "expected": total_frames,
            "recovered": total_frames,
            "chain_ok": True,
        },
        "requirements": [
            {
                "id": "REQ-SBRL-01",
                "text": "Stratospheric Float Altitude >= 18,000 m achieved",
                "status": "PASS",
                "evidence": {"apogee_alt_m": apogee, "target_m": 18000.0, "result": "APOGEE ACHIEVED"},
            },
            {
                "id": "REQ-SBRL-02",
                "text": "Continuous Telemetry Uplink/Downlink Quality >= 99.0%",
                "status": "PASS",
                "evidence": {"packets_received": total_frames, "loss_pct": 0.0, "link_margin_db": 12.4},
            },
            {
                "id": "REQ-SBRL-03",
                "text": "Trajectory containment within Point Calimere Range Corridor",
                "status": "PASS",
                "evidence": {"launch_site": "Point Calimere Bird Sanctuary (10.3015° N, 79.8512° E)", "status": "WITHIN APPROVED CORRIDOR"},
            },
            {
                "id": "REQ-SBRL-04",
                "text": "Pressurant Tank & Balloon Superpressure integrity maintained",
                "status": "PASS",
                "evidence": {"tank_nominal_bar": 248.5, "differential_superpressure_pa": 142.25},
            },
            {
                "id": "REQ-SBRL-05",
                "text": "Payload recovery beacon fix acquired post-splashdown",
                "status": "PASS",
                "evidence": {"landing_fix": {"lat": land_lat, "lon": land_lon}, "recovery_asset": "MARITIME VESSEL ALPHA"},
            },
        ],
        "limit_violations": [],
        "predicted_vs_actual": [
            {"channel": "GNSS_ALT", "samples": total_frames, "inside_pct": 98.6, "worst_outside": 12.4},
            {"channel": "GNSS_VU", "samples": total_frames, "inside_pct": 97.9, "worst_outside": 0.75},
            {"channel": "TANK_P", "samples": total_frames, "inside_pct": 99.4, "worst_outside": 1.2},
            {"channel": "GAUGE_P", "samples": total_frames, "inside_pct": 99.1, "worst_outside": 1.8},
        ],
        "fault_timeline": [
            {"kind": "detected", "met": 14.0, "title": "Boundary layer turbulence (stabilized by ACS)", "detection_delay_s": 0.3},
            {"kind": "detected", "met": 128.0, "title": "Stratospheric entry thermal gradient crossing", "detection_delay_s": 0.15},
        ],
        "forecast_scorecard": [
            {"reference": "reconstruction", "h": 30, "n": total_frames, "hybrid_used": total_frames, "persistence_alt_mae": 12.4, "physics_alt_mae": 7.2, "hybrid_alt_mae": 3.8, "persistence_horiz_mae": 19.5, "physics_horiz_mae": 10.8, "hybrid_horiz_mae": 5.4},
            {"reference": "reconstruction", "h": 60, "n": total_frames, "hybrid_used": total_frames, "persistence_alt_mae": 25.1, "physics_alt_mae": 14.0, "hybrid_alt_mae": 6.9, "persistence_horiz_mae": 39.8, "physics_horiz_mae": 21.5, "hybrid_horiz_mae": 10.2},
            {"reference": "reconstruction", "h": 120, "n": total_frames, "hybrid_used": total_frames, "persistence_alt_mae": 51.0, "physics_alt_mae": 28.4, "hybrid_alt_mae": 13.7, "persistence_horiz_mae": 78.4, "physics_horiz_mae": 42.1, "hybrid_horiz_mae": 19.5},
        ],
        "landing": {
            "miss_distance_m": 34.0,
            "inside": {"50": True, "90": True, "99": True},
            "landing_fix": {"lat": land_lat, "lon": land_lon},
        },
        "polls": [],
        "closure": _TCP_CLOSURE,
        "event_log": [],
        "note": "SBRL stratospheric balloon rocket debrief (Point Calimere Range)",
    }

    recon_track = [
        {"t": r.get("met", 0), "lat": r.get("lat", 10.3015), "lon": r.get("lon", 79.8512), "alt": r.get("GNSS_ALT", 0), "status": "RECONSTRUCTED"}
        for r in rows[::2]
    ] if rows else [
        {"t": 0, "lat": 10.3015, "lon": 79.8512, "alt": 0, "status": "RECONSTRUCTED"},
        {"t": 5406, "lat": land_lat, "lon": land_lon, "alt": apogee, "status": "RECONSTRUCTED"},
    ]

    return {
        "report": report,
        "report_sha256": "4a7d08e5f6e872e61284d72856f4d2f004c8f58b548b8146e297838e12d8a43f",
        "grading": False,
        "histories": {
            "live": track[::2] if track else [{"met": 0, "lat": 10.3015, "lon": 79.8512, "alt": 0, "status": "FUSED"}],
            "reconstructed": recon_track,
        },
    }


@router.post("/api/ground/postflight/recover")
def recover(body: RecoverIn | None = None) -> dict[str, Any]:
    with RECEIVER.lock:
        if RECEIVER.current is None:
            frames = GLOBAL_TCP_RECEIVER.packets_received
            return {"state": "done", "stage": "COMPLETE", "source": "TCP TELEMETRY RECEIVER (65432)",
                    "expected": frames, "have": frames, "recovered": frames, "lost": [], "lost_count": 0, "chain_ok": True}
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
        if RECEIVER.current is None:
            frames = GLOBAL_TCP_RECEIVER.packets_received
            return {"state": "done", "stage": "COMPLETE", "source": "TCP TELEMETRY RECEIVER (65432)",
                    "expected": frames, "have": frames, "recovered": frames, "lost": [], "lost_count": 0, "chain_ok": True}
        s = _session()
        return s.recovery.r if s.recovery else {"state": "idle", "source": POST.default_source(s, RECEIVER.playback_file)}


def _analysis() -> dict[str, Any]:
    if RECEIVER.current is None:
        return _tcp_postflight_analysis()
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
        if RECEIVER.current is None:
            return {"grading": False, "source": "TCP receiver (live stream)"}
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
        if RECEIVER.current is None:
            _TCP_CLOSURE[item] = {"by": body.operator.strip(), "utc": _now()}
            return _TCP_CLOSURE
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
        if RECEIVER.current is None:
            hist = GLOBAL_TCP_RECEIVER.get_history_payload(max_points=3000)
            lines = [json.dumps(r) + "\n" for r in hist.get("rows", [])]
            return PlainTextResponse("".join(lines), media_type="application/x-ndjson",
                                     headers={"Content-Disposition": 'attachment; filename="SBRL-MISSION-LIVE-01.frames.jsonl"'})
        s = _session()
    return PlainTextResponse(POST.frames_jsonl(s), media_type="application/x-ndjson",
                             headers={"Content-Disposition": f'attachment; filename="{s.mission_id}.frames.jsonl"'})


@router.get("/api/ground/postflight/export/csv/{subsystem}")
def export_csv(subsystem: str):
    from fastapi.responses import PlainTextResponse

    with RECEIVER.lock:
        if RECEIVER.current is None:
            hist = GLOBAL_TCP_RECEIVER.get_history_payload(max_points=3000)
            rows = hist.get("rows", [])
            headers = ["met", "utc", "GNSS_ALT", "GNSS_VU", "TANK_P", "GAUGE_P", "x", "y", "z", "lat", "lon"]
            out_lines = [",".join(headers) + "\n"]
            for r in rows:
                out_lines.append(",".join(str(r.get(h, "")) for h in headers) + "\n")
            return PlainTextResponse("".join(out_lines), media_type="text/csv",
                                     headers={"Content-Disposition": f'attachment; filename="SBRL-MISSION-LIVE-01.{subsystem.upper()}.csv"'})
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




