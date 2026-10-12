"""ICD v1 (docs/ICD.md): downlink frames, their HMAC chain, and the channel dictionary.

Shared by the vehicle sender (Laptop A) and the ground receiver (Laptop B). It imports
neither the simulator nor the ground station: it only converts between onboard sensor
samples, wire channels and the sensor packets the ground estimator ingests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..physics import geo

ICD_VERSION = 1
CHANNELS_PATH = Path(__file__).resolve().parents[2] / "config" / "channels.yaml"


# ----------------------------------------------------------------------------- frames


def canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def genesis_hash(key: str, genesis: str) -> str:
    return hmac.new(key.encode("utf-8"), genesis.encode("utf-8"), hashlib.sha256).hexdigest()


def frame_hash(key: str, frame: dict[str, Any]) -> str:
    body = {k: v for k, v in frame.items() if k not in ("hash", "rx")}
    return hmac.new(key.encode("utf-8"), (frame["prev_hash"] + canonical(body)).encode("utf-8"), hashlib.sha256).hexdigest()


def build_frame(key: str, mission_id: str, seq: int, met_s: float, utc_sent: float, time_scale: float,
                ch: dict[str, Any], prev_hash: str) -> dict[str, Any]:
    f = {"v": ICD_VERSION, "mission_id": mission_id, "seq": seq, "met_s": met_s, "utc_sent": utc_sent,
         "time_scale": time_scale, "ch": ch, "prev_hash": prev_hash}
    f["hash"] = frame_hash(key, f)
    return f


def encode(frame: dict[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in frame.items() if k != "rx"}, separators=(",", ":")).encode("utf-8")


class FrameError(ValueError):
    pass


def decode(data: bytes) -> dict[str, Any]:
    """Parse and structurally validate one datagram (no HMAC check)."""
    try:
        f = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"not JSON: {exc}") from exc
    if not isinstance(f, dict):
        raise FrameError("not an object")
    if f.get("v") != ICD_VERSION:
        raise FrameError(f"unsupported ICD version {f.get('v')!r}")
    for k, typ in (("mission_id", str), ("seq", int), ("met_s", (int, float)), ("utc_sent", (int, float)),
                   ("ch", dict), ("prev_hash", str), ("hash", str)):
        if not isinstance(f.get(k), typ) or isinstance(f.get(k), bool):
            raise FrameError(f"field {k!r} missing or wrong type")
    f.setdefault("time_scale", 1.0)
    return f


def verify(key: str, frame: dict[str, Any]) -> bool:
    return hmac.compare_digest(frame_hash(key, frame), frame.get("hash", ""))


def verify_chain(key: str, genesis: str, frames: list[dict[str, Any]]) -> tuple[bool, int | None]:
    """(ok, first bad seq) for a contiguous run of frames starting at seq 0."""
    prev = genesis_hash(key, genesis)
    for i, f in enumerate(frames):
        if f["seq"] != i or f["prev_hash"] != prev or not verify(key, f):
            return False, i
        prev = f["hash"]
    return True, None


# ----------------------------------------------------------------------------- channels

K0 = 273.15


def channels_from_samples(samples: Iterable[dict[str, Any]], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Onboard: one step of sensor samples (aerofire.telemetry.sensors format) -> wire channels."""
    ch: dict[str, Any] = {}
    for s in samples:
        sid, v = s["sensor_id"], s["values"]
        if sid == "gnss":
            ch.update(GNSS_LAT=v["lat"], GNSS_LON=v["lon"], GNSS_ALT=v["up"])
        elif sid == "navic":
            ch.update(NAVIC_LAT=v["lat"], NAVIC_LON=v["lon"], NAVIC_ALT=v["up"])
        elif sid == "baro":
            ch["BARO_P"] = v["p"] / 100.0
        elif sid == "air_temp":
            ch["AIR_T"] = v["T"] - K0
        elif sid == "imu":
            ch.update(IMU_TILT_E=v["tilt_e"], IMU_TILT_N=v["tilt_n"], IMU_RATE_E=v["rate_e"], IMU_RATE_N=v["rate_n"],
                      IMU_TILT=math.hypot(v["tilt_e"], v["tilt_n"]), IMU_RATE=math.hypot(v["rate_e"], v["rate_n"]))
        elif sid == "tank":
            ch.update(TANK_P=v["P"] / 1e5, TANK_T=v["T"] - K0)
        elif sid == "motor":
            ch.update(MOTOR_T=v["T"] - K0, MOTOR_HTR=1 if v["heater_on"] else 0)
        elif sid == "balloon":
            ch.update(BLN_VOL=v["volume"], VENT_RATE=v["vent_rate"] * 1e3, BALLAST_RATE=v["ballast_drop"] * 1e3)
        elif sid == "rocket":
            ch.update(RKT_DR=v["downrange"], RKT_ALT=v["up"], RKT_SPD=v["speed"])
        elif sid == "fc":
            ch.update(FC_PHASE=v["phase"], FC_RKT_PHASE=v["rocket_phase"], FC_EVENT=v["event"])
    ch.update(extra or {})
    return ch


def packets_from_frame(frame: dict[str, Any], lat0: float, lon0: float, arrival_time: float | None = None) -> list[dict[str, Any]]:
    """Ground: wire channels -> sensor packets in the format GroundStation.ingest expects."""
    ch, t = frame["ch"], float(frame["met_s"])
    out: list[dict[str, Any]] = []

    def pkt(sid: str, values: dict[str, Any], source: str) -> None:
        out.append({"sensor_id": sid, "event_time": t, "values": values, "source": source, "seq": frame["seq"]})

    for sid, p, src in (("gnss", "GNSS", "gnss.l1"), ("navic", "NAVIC", "navic.l5")):
        if all(f"{p}_{k}" in ch for k in ("LAT", "LON", "ALT")):
            e, n = geo.latlon_to_enu(ch[f"{p}_LAT"], ch[f"{p}_LON"], lat0, lon0)
            pkt(sid, {"east": e, "north": n, "up": ch[f"{p}_ALT"], "lat": ch[f"{p}_LAT"], "lon": ch[f"{p}_LON"]}, src)
    if "BARO_P" in ch:
        pkt("baro", {"p": ch["BARO_P"] * 100.0}, "baro.static")
    if "AIR_T" in ch:
        pkt("air_temp", {"T": ch["AIR_T"] + K0}, "air.probe")
    if all(k in ch for k in ("IMU_TILT_E", "IMU_TILT_N", "IMU_RATE_E", "IMU_RATE_N")):
        pkt("imu", {"tilt_e": ch["IMU_TILT_E"], "tilt_n": ch["IMU_TILT_N"], "rate_e": ch["IMU_RATE_E"], "rate_n": ch["IMU_RATE_N"]}, "imu.gondola")
    if "TANK_P" in ch and "TANK_T" in ch:
        pkt("tank", {"P": ch["TANK_P"] * 1e5, "T": ch["TANK_T"] + K0}, "tank.pt1")
    if "MOTOR_T" in ch:
        pkt("motor", {"T": ch["MOTOR_T"] + K0, "heater_on": bool(ch.get("MOTOR_HTR", 0))}, "motor.tc1")
    if all(k in ch for k in ("BLN_VOL", "VENT_RATE", "BALLAST_RATE")):
        pkt("balloon", {"volume": ch["BLN_VOL"], "vent_rate": ch["VENT_RATE"] / 1e3, "ballast_drop": ch["BALLAST_RATE"] / 1e3}, "balloon.duct")
    if all(k in ch for k in ("RKT_DR", "RKT_ALT", "RKT_SPD")):
        pkt("rocket", {"downrange": ch["RKT_DR"], "up": ch["RKT_ALT"], "speed": ch["RKT_SPD"]}, "rocket.model")
    if "FC_PHASE" in ch:
        pkt("fc", {"phase": ch["FC_PHASE"], "rocket_phase": ch.get("FC_RKT_PHASE", ""), "event": ch.get("FC_EVENT", "")}, "fc")
    if arrival_time is not None and out:
        out[0]["rx"] = {"rssi_dbm": ch.get("RSSI"), "arrival_time": arrival_time}  # one receipt per frame
    return out


# ----------------------------------------------------------------------------- dictionary


@lru_cache(maxsize=4)
def load_channels(path: str | None = None) -> dict[str, Any]:
    import yaml

    with open(path or CHANNELS_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def limits_for(mnemonic: str, phase: str | None, dictionary: dict[str, Any] | None = None) -> dict[str, Any] | None:
    d = (dictionary or load_channels())["channels"].get(mnemonic)
    if not d or not d.get("limits"):
        return None
    lim = d["limits"]
    return lim.get(phase or "", lim.get("default"))


def limit_state(mnemonic: str, value: Any, phase: str | None, dictionary: dict[str, Any] | None = None) -> str:
    """NOMINAL | CAUTION (yellow) | ALARM (red) | NO_LIMIT for one value in one phase."""
    lim = limits_for(mnemonic, phase, dictionary)
    if lim is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return "NO_LIMIT"

    def out(band: list[Any] | None) -> bool:
        if not band:
            return False
        lo, hi = band
        return (lo is not None and value < lo) or (hi is not None and value > hi)

    if out(lim.get("red")):
        return "ALARM"
    if out(lim.get("yellow")):
        return "CAUTION"
    return "NOMINAL"
