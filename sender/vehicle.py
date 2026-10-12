"""Laptop A: the vehicle digital twin (truth simulator -> sensors -> onboard frame recorder).

Everything here is the simulated world. Its only output to the ground station is ICD v1
frames (aerofire/telemetry/icd.py); truth is served only by the grading endpoint after
the mission has finished.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

import numpy as np

from aerofire.config import Config
from aerofire.physics import atmosphere as A
from aerofire.sim.mission import Faults, MissionSim, sample_variability
from aerofire.telemetry import icd
from aerofire.telemetry.link import LinkEmulator
from aerofire.telemetry.sensors import SensorSuite

SCENARIOS: dict[str, dict[str, Any]] = {
    "nominal": {},
    "tank_leak_100um": {"tank_leak_um": 100.0},
    "gnss_spoof": {"gnss_spoof_start_s": 1800.0},
    "blackout_600s": {"blackouts": [[6000.0, 6600.0]]},
    "cold_motor": {"motor_heater_failed": True},
}

# Vehicle/range-side actions when no --commit-at/--terminate-at is given (the ground station cannot command them).
# (action, MET or None, seconds after float entry)
AUTO_ACTIONS: dict[str, tuple[str, float | None, float]] = {
    "nominal": ("commit", None, 300.0),
    "blackout_600s": ("commit", 6700.0, 300.0),  # after the blackout, so the GO poll happens with the link back
    "tank_leak_100um": ("terminate", 900.0, 0.0),
    "gnss_spoof": ("terminate", None, 300.0),
    "cold_motor": ("terminate", None, 300.0),
}

# Extra onboard channels not produced by aerofire.telemetry.sensors. All [ASSUMPTION], simulated:
GNSS_VEL_SIGMA_M_S = 0.1  # GNSS Doppler velocity noise
VENT_STATE_SIGMAS = 5.0  # onboard valve-open flag threshold on the noisy duct flow sensor
BATT_V_FULL, BATT_V_DROP_6H, BATT_V_COLD_PER_K, BATT_V_SIGMA = 12.6, 1.0, 0.004, 0.01


class Vehicle:
    def __init__(self, cfg: Config, profile: A.AtmosphereProfile, scenario: str, seed: int, time_scale: float,
                 packet_loss: float | None = None, mission_id: str | None = None, vary: bool = False):
        faults = dict(SCENARIOS[scenario])
        if packet_loss is not None:
            faults["packet_loss"] = packet_loss
        self.cfg, self.scenario, self.seed, self.time_scale = cfg, scenario, seed, time_scale
        self.faults = Faults.from_dict(faults)
        self.mission_id = mission_id or uuid.uuid4().hex[:12]
        self.variability = sample_variability(cfg, np.random.default_rng(seed + 4)) if vary else {}
        self.sim = MissionSim(cfg, profile, self.faults, seed=seed, variability=self.variability)
        self.sensors = SensorSuite(cfg, np.random.default_rng(seed + 1), self.faults)
        self.link = LinkEmulator(cfg, self.faults, np.random.default_rng(seed + 2))  # loss, blackout and RSSI model
        self.rng = np.random.default_rng(seed + 3)
        self.key, self.genesis = cfg.telemetry.hmac_key_demo, cfg.telemetry.hmac_genesis
        self.frames: list[dict[str, Any]] = []  # onboard recorder (every frame, transmitted or not)
        self.prev_hash = icd.genesis_hash(self.key, self.genesis)
        self.ch: dict[str, Any] = {}
        self.met = 0.0
        self.t_float: float | None = None
        self.done_at: float | None = None
        self.finished = False
        self.truth_track: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self._prev_en: tuple[float, float, float] | None = None
        self.sent = self.dropped_blackout = self.dropped_random = 0

    # ----------------------------------------------------------------- simulation

    def step(self) -> None:
        tr = self.sim.step()
        dt = self.cfg.sim.dt_s
        samples = self.sensors.sample(tr)
        if self._prev_en is None:
            ve = vn = 0.0
        else:
            ve, vn = (tr.east - self._prev_en[0]) / dt, (tr.north - self._prev_en[1]) / dt
        self._prev_en = (tr.east, tr.north, tr.z)
        f = self.faults
        if f.gnss_spoof_start_s is not None and tr.t >= f.gnss_spoof_start_s:
            ve, vn = ve + f.gnss_spoof_drift_m_s[0], vn + f.gnss_spoof_drift_m_s[1]
        n = lambda s: float(self.rng.normal(0.0, s))  # noqa: E731
        air_c = tr.atmo["T"] - icd.K0
        vent_sigma = self.cfg.sensors.vent_rate_sigma_kg_s
        vent = next((s["values"]["vent_rate"] for s in samples if s["sensor_id"] == "balloon"), 0.0)
        extra = {
            "GNSS_VE": ve + n(GNSS_VEL_SIGMA_M_S), "GNSS_VN": vn + n(GNSS_VEL_SIGMA_M_S), "GNSS_VU": tr.vz + n(GNSS_VEL_SIGMA_M_S),
            "GNSS_FIX": 3, "GNSS_SATS": int(9 + self.rng.integers(-2, 3)),
            "BATT_V": BATT_V_FULL - BATT_V_DROP_6H * tr.t / 21600.0 - BATT_V_COLD_PER_K * max(0.0, 20.0 - air_c) + n(BATT_V_SIGMA),
            "RSSI": self.link.rssi(tr.east, tr.north, tr.z),
            "VENT_STATE": 1 if vent > VENT_STATE_SIGMAS * vent_sigma else 0,
        }
        self.ch = {k: _round(k, x) for k, x in icd.channels_from_samples(samples, extra).items()}
        self.met = tr.t
        if tr.phase == "FLOAT" and self.t_float is None:
            self.t_float = tr.t
        if int(tr.t) % 5 == 0 or tr.events:
            self.truth_track.append({"t": tr.t, "lat": tr.lat, "lon": tr.lon, "z": tr.z, "vz": tr.vz, "phase": tr.phase,
                                     "rocket_phase": tr.rocket_phase, "rocket": tr.rocket})
        for ev in tr.events:
            self.events.append({"t": tr.t, "event": ev})
        if self.sim.done and self.done_at is None:
            self.done_at = tr.t
        if (self.done_at is not None and tr.t >= self.done_at + 10.0) or tr.t >= self.cfg.sim.max_mission_s:
            self.finished = True

    def act(self, action: str, by: str) -> str:
        """Vehicle/range-side action (Laptop A only)."""
        try:
            (self.sim.commit if action == "commit" else self.sim.terminate)()
        except ValueError as exc:
            return f"refused: {exc}"
        self.actions.append({"t": self.met, "action": action, "by": by})
        return f"{action} at MET {self.met:.0f} s"

    # ----------------------------------------------------------------- frames

    def next_frame(self, utc: float) -> tuple[dict[str, Any], bool]:
        """Record the next frame onboard; return it and whether the link delivers it."""
        f = icd.build_frame(self.key, self.mission_id, len(self.frames), self.met, utc, self.time_scale, dict(self.ch), self.prev_hash)
        self.prev_hash = f["hash"]
        self.frames.append(f)
        if self.link.in_blackout(self.met):
            self.dropped_blackout += 1
            return f, False
        if self.rng.random() < self.link.loss:
            self.dropped_random += 1
            return f, False
        self.sent += 1
        return f, True

    def manifest(self) -> dict[str, Any]:
        return {"v": icd.ICD_VERSION, "mission_id": self.mission_id, "frames": len(self.frames), "first_seq": 0 if self.frames else None,
                "last_seq": len(self.frames) - 1 if self.frames else None, "last_hash": self.prev_hash,
                "finished": self.finished, "genesis": icd.genesis_hash(self.key, self.genesis)}

    def truth(self) -> dict[str, Any]:
        s = self.sim
        return {"mission_id": self.mission_id, "scenario": self.scenario, "seed": self.seed, "faults": self.faults.to_dict(),
                "variability": self.variability,
                "track": self.truth_track, "events": self.events, "actions": self.actions,
                "landing": {"t": s.t, "lat": s.latlon()[0], "lon": s.latlon()[1]} if s.phase == "LANDED" else None,
                "rocket": None if s.rocket_result is None else {k: v for k, v in asdict(s.rocket_result).items() if k != "samples"},
                "label": "TRUTH: grading only"}


def rate_effective(rate_hz: float, speed: float, dt_s: float) -> float:
    """Frames per wall second: at least one per simulation step so no onboard sample is skipped."""
    return max(rate_hz, speed / dt_s)


def vehicle_float_auto(vehicle: Vehicle) -> tuple[str, float] | None:
    """The scheduled automatic action and its MET, once it can be determined."""
    action, at, after_float = AUTO_ACTIONS[vehicle.scenario]
    if at is not None:
        if vehicle.t_float is not None:
            at = max(at, vehicle.t_float + after_float) if action == "commit" else at
        elif action == "commit":
            return None
        return action, at
    if vehicle.t_float is None:
        return None
    return action, vehicle.t_float + after_float



def _round(mnemonic: str, x):
    """Wire precision: 1e-7 deg (about 1 cm) for latitude/longitude, 1e-4 otherwise; far below sensor noise."""
    if isinstance(x, float):
        return round(x, 7 if mnemonic.endswith(("_LAT", "_LON")) else 4)
    return x
