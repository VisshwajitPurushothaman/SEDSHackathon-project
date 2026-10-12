"""RF downlink emulator: packet loss, latency, blackout windows, received signal strength.

Part of the simulated world (it needs the true slant range for RSSI), so it
lives on the vehicle/world side of the truth firewall. The ground only sees
what `poll()` delivers: the transmitted record plus receiver metadata
(`rx.rssi_dbm`, `rx.arrival_time`).

RSSI = P_tx + G - FSPL(d) + N(0, sigma),  FSPL[dB] = 20 log10(d_km) + 20 log10(f_MHz) + 32.44
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..physics import geo


def fspl_db(distance_m: float, freq_mhz: float) -> float:
    return 20.0 * math.log10(max(distance_m, 1.0) / 1000.0) + 20.0 * math.log10(freq_mhz) + 32.44


@dataclass(order=True)
class _Delivery:
    arrival: float
    seq: int
    record: dict[str, Any] = field(compare=False)


class LinkEmulator:
    def __init__(self, cfg, faults, rng: np.random.Generator):
        lc = cfg.link
        self.cfg = lc
        self.rng = rng
        self.loss = faults.packet_loss if faults.packet_loss is not None else lc.packet_loss
        self.latency = faults.latency_s if faults.latency_s is not None else lc.latency_s
        self.blackouts = list(faults.blackouts)
        self._queue: list[_Delivery] = []
        self.sent = 0
        self.dropped_random = 0
        self.dropped_blackout = 0
        self.st_e, self.st_n = geo.latlon_to_enu(lc.station_lat_deg, lc.station_lon_deg, cfg.site.lat_deg, cfg.site.lon_deg)

    def in_blackout(self, t: float) -> bool:
        return any(a <= t < b for a, b in self.blackouts)

    def rssi(self, east: float, north: float, up: float) -> float:
        d = math.sqrt((east - self.st_e) ** 2 + (north - self.st_n) ** 2 + (up - self.cfg.station_alt_m) ** 2)
        return self.cfg.tx_power_dbm + self.cfg.antenna_gain_db - fspl_db(d, self.cfg.freq_mhz) + float(self.rng.normal(0.0, self.cfg.rssi_sigma_db))

    def transmit(self, record: dict[str, Any], truth_enu: tuple[float, float, float]) -> None:
        """Send one record (already written by the onboard recorder)."""
        self.sent += 1
        t = record["event_time"]
        if self.in_blackout(t):
            self.dropped_blackout += 1
            return
        if self.rng.random() < self.loss:
            self.dropped_random += 1
            return
        lat = max(0.0, float(self.rng.normal(self.latency, self.cfg.latency_jitter_s)))
        delivered = dict(record)
        delivered["rx"] = {"rssi_dbm": self.rssi(*truth_enu), "arrival_time": t + lat}
        heapq.heappush(self._queue, _Delivery(t + lat, record["seq"], delivered))

    def poll(self, now: float) -> list[dict[str, Any]]:
        """Records that have arrived at the ground station by `now` (in arrival order)."""
        out = []
        while self._queue and self._queue[0].arrival <= now:
            out.append(heapq.heappop(self._queue).record)
        return out

    def stats(self) -> dict[str, Any]:
        return {"sent": self.sent, "dropped_random": self.dropped_random, "dropped_blackout": self.dropped_blackout,
                "configured_loss": self.loss, "configured_latency_s": self.latency, "blackouts": self.blackouts}
