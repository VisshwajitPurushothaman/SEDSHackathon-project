"""Legacy pre-flight endpoints (/api/simulate, /api/stream) on the new physics.

The frame shape of the original backend is kept so the pre-flight console
keeps working, but every value now comes from the GROUND station (noisy
measurements and estimates), not from simulator truth, and the semantics are
corrected:
  - `is_ignition` is always false: software never confirms ignition.
  - `gate_status` "ARMED" now means the ground recommends GO (all 5 gates + no leak).
  - verdicts: GO_RECOMMENDED | ABORT_RECOMMENDED | NO_GO (horizon reached without a recommendation).
New fields are added alongside (phase, recommendation, atmo_source, vertical speed, balloon volume/vent, swing amplitude).

The run stops at the first GO/ABORT recommendation: the pre-flight console
ends there; flying the mission is the job of /api/missions (operator commit).
"""
from __future__ import annotations

from typing import Any, Iterator

import numpy as np

from ..config import Config
from ..ground.station import GroundStation
from ..physics.atmosphere import AtmosphereProfile
from ..sim.mission import Faults, MissionSim
from ..telemetry.sensors import SensorSuite

FRAME_PERIOD_S = 60.0


def legacy_frame(fr: dict[str, Any]) -> dict[str, Any]:
    E = fr["estimates"]

    def v(key: str, default: Any = None) -> Any:
        return E[key]["value"] if key in E else default

    leak = bool(v("tank_leak_detected", False))
    d = v("tank_leak_diameter")
    rec = fr["recommendation"]
    tilt = v("tilt", 0.0)
    m_change = v("tank_mass_change", 0.0) or 0.0
    return {
        "time_s": round(fr["t"], 1),
        "altitude_m": round(v("altitude", 0.0), 1),
        "ext_temp_c": round(v("air_temperature", 273.15) - 273.15, 2),
        "ext_pres_hpa": round(v("pressure", 0.0) / 100.0, 3),
        "tank_pres_bar": round(v("tank_pressure", 0.0) / 1e5, 3),
        "motor_temp_c": round(v("motor_temperature", 273.15) - 273.15, 2),
        "mass_loss_g": round(max(0.0, -m_change) * 1000.0, 2),
        "tilt_deg": round(tilt, 3),
        "leak_detected": leak,
        "leak_status": (f"LEAK:{d:.0f}µm" if d else "LEAK") if leak else "SEALED (OK)",
        "gate_status": "ARMED" if rec == "GO_RECOMMENDED" else "NO-GO",
        "is_abort": rec == "ABORT_RECOMMENDED",
        "is_ignition": False,
        # --- added fields
        "go_recommended": rec == "GO_RECOMMENDED",
        "recommendation": rec,
        "phase": fr["phase"],
        "vertical_speed_m_s": round(v("vertical_speed", 0.0), 3),
        "swing_amplitude_deg": round(v("swing_amplitude", 0.0), 3),
        "balloon_volume_m3": round(v("balloon_volume", 0.0), 1),
        "balloon_vent_rate_kg_s": round(v("balloon_vent_rate", 0.0), 5),
        "balloon_venting": bool(v("balloon_venting", False)),
        "atmo_source": fr["atmo_source"],
        "gates": {k: g["passed"] for k, g in fr["gates"].items()},
    }


def execute_flight_engine(cfg: Config, profile: AtmosphereProfile, puncture_um: float = 0.0, seed: int = 0) -> Iterator[dict[str, Any]]:
    """Legacy frames every 60 s (and on every recommendation change) until GO/ABORT or the horizon."""
    sim = MissionSim(cfg, profile, Faults(tank_leak_um=float(puncture_um)), seed=seed)
    sensors = SensorSuite(cfg, np.random.default_rng(seed + 1000), sim.faults)
    ground = GroundStation(cfg, profile)
    next_emit = 0.0
    last_rec = None
    while sim.t < cfg.sim.max_mission_s:
        truth = sim.step()
        for r in sensors.sample(truth):
            ground.ingest(r)
        fr = ground.frame(truth.t)
        terminal = fr["recommendation"] in ("GO_RECOMMENDED", "ABORT_RECOMMENDED")
        if truth.t >= next_emit or fr["recommendation"] != last_rec or terminal:
            yield legacy_frame(fr)
            next_emit = truth.t + FRAME_PERIOD_S
        last_rec = fr["recommendation"]
        if terminal:
            return


def summarize(frames: list[dict[str, Any]], puncture_um: float, profile: AtmosphereProfile) -> dict[str, Any]:
    last = frames[-1] if frames else None
    verdict = "NO_GO"
    reason = None
    if last and last["go_recommended"]:
        verdict = "GO_RECOMMENDED"
    elif last and last["is_abort"]:
        verdict = "ABORT_RECOMMENDED"
        reason = f"Pressurant leak detected: {last['leak_status']}"
    return {
        "verdict": verdict,
        "abort_reason": reason,
        "puncture_input_um": puncture_um,
        "final_altitude_m": last["altitude_m"] if last else 0.0,
        "final_mass_loss_g": last["mass_loss_g"] if last else 0.0,
        "total_frames": len(frames),
        "atmo_source": profile.label,
        "atmo_note": profile.note,
    }
