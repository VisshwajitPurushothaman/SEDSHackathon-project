"""The one telemetry schema shared by vehicle, recorder, link and ground.

A `TelemetryRecord` is one sensor sample (or one flight-computer event) as it
leaves the vehicle. Status on the vehicle side is always MEASURED; the ground
adds FUSED / PREDICTED / RECONSTRUCTED / UNKNOWN on its own estimates.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Status(str, Enum):
    MEASURED = "MEASURED"  # a sensor reading received live
    FUSED = "FUSED"  # combined from several measurements by the ground estimator
    PREDICTED = "PREDICTED"  # model forecast with no fresh measurement (e.g. during blackout)
    RECONSTRUCTED = "RECONSTRUCTED"  # post-flight, from recovered records
    UNKNOWN = "UNKNOWN"  # no basis for a value


class TelemetryRecord(BaseModel):
    seq: int = Field(description="Monotonic record number assigned by the onboard recorder")
    event_time: float = Field(description="Mission elapsed time of the sample, s")
    sensor_id: str
    values: dict[str, Any]
    units: dict[str, str]
    status: Status = Status.MEASURED
    source: str = Field(description="Producer, e.g. 'gnss.l1', 'tank.pt1', 'fc'")


class Estimate(BaseModel):
    value: Any
    unit: str = ""
    status: Status
    sigma: float | None = None
    source: str = ""
    age_s: float | None = Field(default=None, description="Time since the newest measurement used")


SENSOR_UNITS: dict[str, dict[str, str]] = {
    "gnss": {"east": "m", "north": "m", "up": "m", "lat": "deg", "lon": "deg"},
    "navic": {"east": "m", "north": "m", "up": "m", "lat": "deg", "lon": "deg"},
    "baro": {"p": "Pa"},
    "air_temp": {"T": "K"},
    "imu": {"tilt_e": "deg", "tilt_n": "deg", "rate_e": "deg/s", "rate_n": "deg/s"},
    "tank": {"P": "Pa", "T": "K"},
    "motor": {"T": "K", "heater_on": "bool"},
    "balloon": {"volume": "m^3", "vent_rate": "kg/s", "ballast_drop": "kg/s"},
    "link": {"rssi": "dBm"},
    "fc": {"phase": "", "event": ""},
    "rocket": {"downrange": "m", "up": "m", "speed": "m/s"},
}
