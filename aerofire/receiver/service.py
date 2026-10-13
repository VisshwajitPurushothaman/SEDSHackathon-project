"""Ground-station receiver service: async UDP listener or session-file playback.

Receive only: no socket here ever sends to the vehicle.
"""
from __future__ import annotations

import asyncio
import queue
import gzip
import json
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..ground import profile as PF
from ..ground.forecast import PhysicsForecaster
from ..ml.predictor import HybridPredictor
from ..ground.predictor import Predictor
from ..physics import atmosphere as A
from ..physics import gates as G
from ..physics import geo
from ..physics import rocket as Rk
from ..telemetry import icd
from .session import GroundSession
from .twin import declared_origin, twin_channels, twin_frame

TICK_S = 0.5
RANGE_PATH = Path(__file__).resolve().parents[2] / "config" / "range.geojson"


class Receiver:
    def __init__(self, cfg, forecast: Callable[[], A.AtmosphereProfile], sessions_dir: Path | None = None,
                 clock: Callable[[], float] = time.time):
        self.cfg, self._forecast_fn, self.sessions_dir, self.clock = cfg, forecast, sessions_dir, clock
        self._forecast: A.AtmosphereProfile | None = None
        self.lock = threading.RLock()
        self.sessions: dict[str, GroundSession] = {}
        self.current: GroundSession | None = None
        self.mode = "LIVE UDP"
        self.listen: str | None = None
        self.rejected = 0
        self.last_reject: str | None = None
        self.playback_file: str | None = None
        self.playback_done = False
        self.preflight_ctx: dict[str, Any] = {}
        self._twin_epoch: tuple | None = None
        self._twin_mission: str | None = None
        self._twin_phase: str | None = None

    def forecast(self) -> A.AtmosphereProfile:
        if self._forecast is None:
            self._forecast = self._forecast_fn()
        return self._forecast

    def prepare(self) -> dict[str, Any]:
        """Ground-only pre-flight products (no telemetry needed): forecast, predicted profile, range check."""
        if self.preflight_ctx:
            return self.preflight_ctx
        cfg, fc = self.cfg, self.forecast()
        prof = PF.predicted_profile(cfg, fc)
        rng = PF.load_range(RANGE_PATH)
        e = prof["end"]
        lat, lon = geo.enu_to_latlon(e["east"], e["north"], cfg.site.lat_deg, cfg.site.lon_deg)
        fp = PF.wind_drift_landing(Predictor(cfg, fc, np.random.default_rng(0)), e)
        iips = [asdict(p) for p in G.impact_points(Rk.RocketParams.from_config(cfg), lat, lon, e["alt"], fc.at,
                                                   cfg.gates.early_burnout_fractions, cfg.gates.approved_area, dt=0.2)]
        self.preflight_ctx = {"profile": prof, "range": rng, "footprint": fp, "iips": iips,
                              "release_point": {"lat": lat, "lon": lon, "alt": e["alt"]},
                              "range_check": PF.range_check(rng, fp, iips), "iip_model": Rk.LABEL,
                              "forecaster": PhysicsForecaster(cfg, fc), "hybrid": HybridPredictor()}
        return self.preflight_ctx

    def on_datagram(self, data: bytes, recv_utc: float | None = None, addr: str | None = None) -> str:
        recv_utc = self.clock() if recv_utc is None else recv_utc
        try:
            frame = icd.decode(data)
        except icd.FrameError as exc:
            with self.lock:
                self.rejected += 1
                self.last_reject = str(exc)
            return "rejected"
        res = self.on_frame(frame, recv_utc)
        if addr and self.current is not None:
            self.current.sender_addr = addr
        return res

    def on_frame(self, frame: dict[str, Any], recv_utc: float) -> str:
        with self.lock:
            mid = frame["mission_id"]
            s = self.sessions.get(mid)
            if s is None:
                s = GroundSession(self.cfg, self.forecast(), mid, source=self.mode if self.mode != "NO SOURCE" else "LIVE TCP",
                                  sessions_dir=self.sessions_dir,
                                  record=self.mode != "PLAYBACK", preflight_ctx=self.prepare(), lock=self.lock)
                self.sessions[mid] = s
                self.current = s
            return s.ingest(frame, recv_utc)

    def on_twin_packet(self, p: dict[str, Any], src: str, recv_utc: float, flt) -> str:
        """An ACCEPTED Laptop A twin packet (aerofire.receiver.twin.TwinFilter) -> ICD frame -> GroundSession."""
        with self.lock:
            key = (flt.epoch, flt.epoch_first_met_ms)
            if key != self._twin_epoch or self._twin_mission is None:
                self._twin_epoch = key
                self._twin_mission = f"twin-{p.get('device_id', 'unknown')}-e{flt.epoch}-{int((flt.epoch_first_met_ms or 0) / 1000)}"
                self._twin_phase = None
            prev_mode = self.mode
            self.mode = "LIVE TCP"
            ch, unknown_phase = twin_channels(p, self._twin_phase)
            self._twin_phase = ch.get("FC_PHASE", self._twin_phase)
            res = self.on_frame(twin_frame(p, self._twin_mission, flt.seq(p), ch), recv_utc)
            s = self.sessions[self._twin_mission]
            s.sender_addr = src
            if s.ingested_met == twin_frame(p, "", 0, {})["met_s"] and not getattr(s, "_twin_checked", False):
                s._twin_checked = True
                org = declared_origin(p)
                site = (self.cfg.site.lat_deg, self.cfg.site.lon_deg)
                if org and (abs(org[0] - site[0]) > 0.01 or abs(org[1] - site[1]) > 0.01):
                    s._raise(recv_utc, "WARNING", "origin", "Sender launch origin differs from the ground site config",
                             {"sender_origin": org, "ground_site": site, "fix": "set config/mission.yaml site to the sender's origin"}, source="link")
            if unknown_phase:
                s._raise(recv_utc, "INFO", f"phase:{unknown_phase}", f"Unmapped twin phase '{unknown_phase}': previous phase kept",
                         {"raw_phase": unknown_phase}, source="link")
            if prev_mode == "PLAYBACK":
                self.mode = prev_mode
            return res

    def tick(self) -> None:
        with self.lock:
            if self.current:
                self.current.tick(self.clock())

    def status(self) -> dict[str, Any]:
        return {"mode": self.mode, "listen": self.listen, "rejected_datagrams": self.rejected, "last_reject": self.last_reject,
                "playback_file": self.playback_file, "playback_done": self.playback_done,
                "sessions": [{"mission_id": k, "frames": len(s.frames), "file": str(s.path) if s.path else None} for k, s in self.sessions.items()],
                "current": self.current.mission_id if self.current else None}

    # ------------------------------------------------------------------ async runners

    async def run_udp(self, host: str, port: int) -> None:
        """Datagrams are queued by the event loop and processed by a worker thread (the estimator and the
        forecast are CPU work; the API and WebSocket must stay responsive)."""
        loop = asyncio.get_running_loop()
        inbox: queue.SimpleQueue = queue.SimpleQueue()

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: Any) -> None:  # noqa: D401
                inbox.put((data, time.time(), addr[0] if addr else None))

        def worker() -> None:
            while True:
                data, utc, addr = inbox.get()
                self.on_datagram(data, utc, addr)

        self.mode, self.listen = "LIVE UDP", f"udp://{host}:{port}"
        await asyncio.to_thread(self.prepare)
        threading.Thread(target=worker, name="receiver-worker", daemon=True).start()
        transport, _ = await loop.create_datagram_endpoint(Proto, local_addr=(host, port))
        try:
            await self._ticker()
        finally:
            transport.close()

    async def run_playback(self, path: str, speed: float = 1.0) -> None:
        """Replay a recorded session on a virtual clock in the recording's own time base (receive times,
        ages, latency and LOS are as recorded), advanced `speed` times faster than real time. Frames are
        processed in a worker thread so the API stays responsive."""
        self.mode, self.playback_file = "PLAYBACK", path
        rows = load_session(path)
        await asyncio.to_thread(self.prepare)
        base = {"first": rows[0]["rx"]["utc"], "t0": time.time()}
        self.clock = lambda: base["first"] + (time.time() - base["t0"]) * speed

        def play() -> None:
            for row in rows:
                delay = (row["rx"]["utc"] - self.clock()) / speed
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.2:  # processing cannot keep up: slow the virtual clock instead of faking a LOS
                    base["t0"] -= delay
                self.on_frame(row, row["rx"]["utc"])
            self.playback_done = True

        ticker = asyncio.create_task(self._ticker())
        await asyncio.to_thread(play)
        await ticker

    async def _ticker(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(TICK_S)


def read_lines(path: str | Path) -> list[str]:
    """Lines of a .jsonl or .jsonl.gz file."""
    p = Path(path)
    data = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
    return [line for line in data.decode("utf-8").splitlines() if line.strip()]


def load_session(path: str) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in read_lines(path)]
    return [r for r in rows if r.get("rx", {}).get("hmac_ok", True) is not False]  # None = unsigned (twin)


def playback_sync(receiver: Receiver, path: str) -> GroundSession:
    """Deterministic, as-fast-as-possible playback (tests, report generation): the file's receive times are the clock."""
    receiver.mode, receiver.playback_file = "PLAYBACK", path
    rows = load_session(path)
    last_tick = None
    for row in rows:
        utc = row["rx"]["utc"]
        if last_tick is not None:
            t = last_tick + TICK_S
            while t < utc:
                receiver.clock = lambda t=t: t
                receiver.tick()
                t += TICK_S
        receiver.on_frame(row, utc)
        last_tick = utc
    receiver.playback_done = True
    return receiver.current
