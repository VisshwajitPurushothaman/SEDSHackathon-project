"""Laptop A digital-twin input (TCP JSON packets): one-stream filter and mapping into ICD frames.

Measured on the wire (docs/audit/FLUCTUATION_REPORT.md, raw capture 13 Oct): Laptop A interleaves three
streams from one IP and one device_id: the current mission (MET advancing at 2 Hz), a frozen packet that
repeats one old MET, and PRELAUNCH handshakes with timestamp_ms = 0. Applying "latest packet wins" made the
display flip by kilometres. This filter accepts ONE stream:

- source lock: a configured IP (AEROFIRE_TWIN_SOURCE) or the first source whose stream advances LOCK_MIN times;
- zero-time packets (timestamp_ms 0 / missing) are rejected;
- continuation only: MET must advance by at most MAX_STEP_MS, or resume after a gap consistently with
  the elapsed wall time; repeats, backward steps and forward jumps (e.g. a frozen packet whose MET is
  ahead of the live one) are rejected;
- switch: if the locked stream has been silent for STALL_RELOCK_S and another stream from the same
  source has advanced RELOCK_MIN times in a row, the filter switches to it (backwards in MET = a
  restarted mission = a new epoch and session; forwards = the same epoch).

Rejected packets are counted by reason and returned to the caller for logging; they are never applied.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

STALL_RELOCK_S = 3.0  # = the receiver's LOS threshold
MAX_STEP_MS = 5000.0  # largest MET step accepted as a continuation without wall-time support
RELOCK_MIN = 5
LOCK_MIN = 2  # advances needed before the first lock
CANDIDATE_GAP_MS = 5000.0  # a packet continues a candidate stream if its MET advanced by at most this
RATE_WINDOW_S = 10.0
DEFAULT_INTERVAL_S = 0.5


def met_ms(p: dict[str, Any]) -> float | None:
    t = p.get("timestamp_ms")
    return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) else None


def interval_s(p: dict[str, Any]) -> float:
    for k in ("server_target", "tcp_socket_stream"):
        v = (p.get(k) or {}).get("ping_interval_seconds")
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return DEFAULT_INTERVAL_S


@dataclass
class _Candidate:
    last: float
    advances: int = 0
    last_utc: float = 0.0


@dataclass
class TwinFilter:
    allowed_source: str | None = None
    source: str | None = None
    last_met_ms: float | None = None
    last_accept_utc: float | None = None
    epoch: int = 0
    epoch_first_met_ms: float | None = None
    accepted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    arrivals: deque = field(default_factory=deque)
    candidates: list[_Candidate] = field(default_factory=list)
    interval: float = DEFAULT_INTERVAL_S

    def _reject(self, reason: str) -> tuple[bool, str]:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1
        return False, reason

    def _track_candidate(self, t: float, now: float) -> _Candidate:
        for c in self.candidates:
            if t == c.last:
                c.last_utc = now
                return c
            if 0.0 < t - c.last <= CANDIDATE_GAP_MS:
                c.last, c.advances, c.last_utc = t, c.advances + 1, now
                return c
        c = _Candidate(t, 0, now)
        self.candidates = [k for k in self.candidates if now - k.last_utc < 30.0][-7:] + [c]
        return c

    def offer(self, p: dict[str, Any], src: str, now: float | None = None) -> tuple[bool, str]:
        """(accepted, reason). reason is 'ok', 'new_epoch' (first packet of a newly locked stream) or a rejection."""
        now = time.time() if now is None else now
        if not isinstance(p, dict):
            return self._reject("malformed")
        if self.allowed_source and src != self.allowed_source:
            return self._reject("foreign_source")
        t = met_ms(p)
        if not t:
            return self._reject("zero_time")
        if self.source is None:
            self.source = src
        elif src != self.source:
            return self._reject("foreign_source")
        if self.last_met_ms is None:
            # Initial lock only on a stream that is actually advancing (never on a frozen or one-off packet).
            c = self._track_candidate(t, now)
            if c.advances >= LOCK_MIN:
                self.candidates.remove(c)
                return self._accept(p, t, now, new_epoch=True)
            return self._reject("not_locked")
        dt_met, dt_wall = t - self.last_met_ms, now - (self.last_accept_utc or now)
        if 0.0 < dt_met <= MAX_STEP_MS:
            return self._accept(p, t, now, new_epoch=False)
        if dt_met > 0.0 and abs(dt_met / 1000.0 - dt_wall) <= max(2.0, 0.2 * dt_wall):
            return self._accept(p, t, now, new_epoch=False)  # resumed after a gap, MET consistent with wall time
        # Not a continuation of the locked stream: a frozen/stale stream, or a restarted mission.
        c = self._track_candidate(t, now)
        if dt_wall >= STALL_RELOCK_S and c.advances >= RELOCK_MIN:
            self.candidates.remove(c)
            return self._accept(p, t, now, new_epoch=t < self.last_met_ms)
        return self._reject("repeat" if dt_met == 0.0 else ("backward" if dt_met < 0.0 else "jump"))

    def _accept(self, p: dict[str, Any], t: float, now: float, new_epoch: bool) -> tuple[bool, str]:
        if new_epoch:
            self.epoch += 1
            self.epoch_first_met_ms = t
        self.last_met_ms, self.last_accept_utc = t, now
        self.accepted += 1
        self.interval = interval_s(p)
        self.arrivals.append(now)
        return True, "new_epoch" if new_epoch else "ok"

    def seq(self, p: dict[str, Any]) -> int:
        """Receiver-side sequence number = index of the accepted packet. The twin protocol has no packet counter
        and the sender may run time-compressed (MET advanced 1.8 s per 1.0 s wall on 13 Oct), so MET slots are
        not a valid loss basis; the session measures loss from receipts vs the declared ping interval instead."""
        return self.accepted

    def stats(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        while self.arrivals and self.arrivals[0] < now - RATE_WINDOW_S:
            self.arrivals.popleft()
        return {"source": self.source, "allowed_source": self.allowed_source, "epoch": self.epoch, "accepted": self.accepted,
                "rejected": dict(self.rejected), "rejected_total": sum(self.rejected.values()),
                "rate_hz": len(self.arrivals) / RATE_WINDOW_S,
                "last_accept_age_s": None if self.last_accept_utc is None else now - self.last_accept_utc,
                "last_met_s": None if self.last_met_ms is None else self.last_met_ms / 1000.0, "interval_s": self.interval}


# ----------------------------------------------------------------------------- mapping into ICD frames

# Laptop A flight_phase -> flight-computer phase used by the ground estimator. Ordered, substring match.
# Seen on the wire: PRELAUNCH, STRATOSPHERIC_ASCENT, SBRL_RELEASE_WINDOW (and LEO_ORBIT in A's sample).
# [ASSUMPTION] for names not yet seen; unknown names keep the previous phase and raise an INFO alarm.
PHASE_RULES: tuple[tuple[str, str], ...] = (
    ("PRELAUNCH", "PAD"), ("PAD", "PAD"), ("DESCENT", "DESCENT"), ("PARACHUTE", "DESCENT"),
    ("SPLASH", "LANDED"), ("TOUCHDOWN", "LANDED"), ("LANDED", "LANDED"),
    ("ASCENT", "ASCENT"), ("RELEASE_WINDOW", "FLOAT"), ("FLOAT", "FLOAT"),
    ("IGNITION", "IGNITION"), ("BURN", "IGNITION"), ("BOOST", "IGNITION"), ("COAST", "IGNITION"),
    ("STAG", "IGNITION"), ("ORBIT", "IGNITION"),
)
ROCKET_RULES: tuple[tuple[str, str], ...] = (("SLUNG", "ABOARD"), ("ATTACH", "ABOARD"), ("ARMED", "ABOARD"),
                                             ("IMPACT", "IMPACT"), ("COAST", "COAST"), ("BURN", "BURN"), ("IGNIT", "BURN"))


def map_phase(raw: str | None, rules=PHASE_RULES) -> str | None:
    up = str(raw or "").upper()
    return next((v for k, v in rules if k in up), None)


def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def declared_origin(p: dict[str, Any]) -> tuple[float, float] | None:
    """Launch site the sender states, e.g. 'Chennai Launch Pad, India (13.0827°N, 80.2707°E)'."""
    import re

    txt = str((p.get("gnss_coordinates") or {}).get("launch_site") or "")
    m = re.search(r"([-\d.]+)\s*°?\s*([NS])\s*,\s*([-\d.]+)\s*°?\s*([EW])", txt)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(3))
    return (-lat if m.group(2) == "S" else lat), (-lon if m.group(4) == "W" else lon)


def twin_channels(p: dict[str, Any], prev_phase: str | None) -> tuple[dict[str, Any], str | None]:
    """Twin JSON -> dictionary channels (config/channels.yaml units). Only fields the packet carries."""
    g = p.get("gnss_coordinates") or {}
    w = g.get("geodetic_wgs84") or {}
    baro = p.get("barometric_sensor") or {}
    imu = p.get("imu_sensor") or {}
    env = p.get("environmental_sensors") or {}
    pay = p.get("payload_state") or {}
    phy = p.get("orbital_and_aerothermal_physics") or {}
    ch: dict[str, Any] = {}

    def put(k: str, v: Any) -> None:
        v = _num(v) if not isinstance(v, str) else v
        if v is not None:
            ch[k] = v

    lat, lon = _num(w.get("lat_deg", w.get("latitude_deg"))), _num(w.get("lon_deg", w.get("longitude_deg")))
    alt = _num(w.get("alt_m", w.get("altitude_msl_m")))
    if lat is not None and lon is not None and alt is not None:
        ch.update(GNSS_LAT=lat, GNSS_LON=lon, GNSS_ALT=alt)
    put("POS_DOWNRANGE", g.get("x_downrange_m"))
    put("POS_ALT_PAD", g.get("y_altitude_m"))
    put("POS_CROSSRANGE", g.get("z_crossrange_m"))
    put("BARO_P", baro.get("pressure_hpa", env.get("ambient_pressure_hpa")))
    put("AIR_T", env.get("ambient_temp_c"))
    put("RH", env.get("relative_humidity_pct"))
    wind = env.get("effective_wind_vector_mps")
    if isinstance(wind, list) and len(wind) == 3:
        put("WIND_X", wind[0]), put("WIND_Y", wind[1]), put("WIND_Z", wind[2])
    acc, gyr, rpy = imu.get("accel_mps2"), imu.get("gyro_dps"), imu.get("attitude_rpy_deg")
    if isinstance(acc, list) and len(acc) == 3 and all(_num(a) is not None for a in acc) and acc[2] > 0:
        ax, ay, az = (float(a) for a in acc)
        ch.update(IMU_TILT_E=math.degrees(math.atan2(ax, az)), IMU_TILT_N=math.degrees(math.atan2(ay, az)),
                  IMU_TILT=math.degrees(math.atan2(math.hypot(ax, ay), az)))
        if isinstance(gyr, list) and len(gyr) == 3 and all(_num(x) is not None for x in gyr):
            ch.update(IMU_RATE_E=float(gyr[0]), IMU_RATE_N=float(gyr[1]), IMU_RATE=math.hypot(gyr[0], gyr[1]))
    if isinstance(rpy, list) and len(rpy) == 3:
        put("IMU_ROLL", rpy[0]), put("IMU_PITCH", rpy[1]), put("IMU_YAW", rpy[2])
    put("BLN_VOL", pay.get("balloon_volume_m3"))
    put("BLN_DP", pay.get("differential_superpressure_pa", phy.get("differential_superpressure_pa")))
    if isinstance(pay.get("is_balloon_burst"), bool):
        ch["BLN_BURST"] = 1 if pay["is_balloon_burst"] else 0
    put("THRUST", pay.get("thrust_n"))
    put("PROP_MASS", pay.get("propellant_kg"))
    put("TOTAL_MASS", pay.get("total_mass_kg"))
    put("MACH", phy.get("mach_number"))
    put("DYN_P", phy.get("dynamic_pressure_q_pa"))
    sim = p.get("simulation_state") or {}
    paused = p.get("ping_paused", sim.get("ping_paused"))
    if paused is not None or isinstance(sim.get("is_paused"), bool):
        ch["TWIN_PAUSED"] = 1 if (paused in (1, True, "1", "true", "True") or sim.get("is_paused") is True) else 0
    raw_phase = p.get("flight_phase")
    if isinstance(raw_phase, str):
        ch["FC_PHASE_RAW"] = raw_phase
    phase = map_phase(raw_phase) or prev_phase
    if phase:
        ch["FC_PHASE"] = phase
    rs = pay.get("rocket_status")
    if isinstance(rs, str):
        ch["RKT_STATUS"] = rs
    rk = map_phase(rs, ROCKET_RULES) or ("ABOARD" if pay.get("is_attached") is True else None)
    if rk:
        ch["FC_RKT_PHASE"] = rk
    ch["FC_EVENT"] = ""
    return ch, (map_phase(raw_phase) is None and raw_phase or None)


def twin_frame(p: dict[str, Any], mission_id: str, seq: int, ch: dict[str, Any]) -> dict[str, Any]:
    """An ICD-shaped frame for GroundSession. Unsigned: the twin protocol carries no HMAC (integrity 'none')."""
    return {"v": 1, "mission_id": mission_id, "seq": seq, "met_s": met_ms(p) / 1000.0, "utc_sent": None, "time_scale": 1.0,
            "ch": ch, "prev_hash": "", "hash": "", "integrity": "none", "source": "tcp-twin",
            "interval_s": interval_s(p)}
