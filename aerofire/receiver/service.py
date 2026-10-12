"""Ground-station receiver service: async UDP listener or session-file playback.

Receive only: no socket here ever sends to the vehicle.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..physics import atmosphere as A
from ..telemetry import icd
from .session import GroundSession

TICK_S = 0.5


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

    def forecast(self) -> A.AtmosphereProfile:
        if self._forecast is None:
            self._forecast = self._forecast_fn()
        return self._forecast

    def on_datagram(self, data: bytes, recv_utc: float | None = None) -> str:
        recv_utc = self.clock() if recv_utc is None else recv_utc
        try:
            frame = icd.decode(data)
        except icd.FrameError as exc:
            with self.lock:
                self.rejected += 1
                self.last_reject = str(exc)
            return "rejected"
        return self.on_frame(frame, recv_utc)

    def on_frame(self, frame: dict[str, Any], recv_utc: float) -> str:
        with self.lock:
            mid = frame["mission_id"]
            s = self.sessions.get(mid)
            if s is None:
                s = GroundSession(self.cfg, self.forecast(), mid, source=self.mode, sessions_dir=self.sessions_dir,
                                  record=self.mode != "PLAYBACK")
                self.sessions[mid] = s
                self.current = s
            return s.ingest(frame, recv_utc)

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
        loop = asyncio.get_running_loop()
        receiver = self

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: Any) -> None:  # noqa: D401
                receiver.on_datagram(data)

        self.mode, self.listen = "LIVE UDP", f"udp://{host}:{port}"
        transport, _ = await loop.create_datagram_endpoint(Proto, local_addr=(host, port))
        try:
            await self._ticker()
        finally:
            transport.close()

    async def run_playback(self, path: str, speed: float = 1.0) -> None:
        """Replay a recorded session file with its original receive timing (divided by `speed`)."""
        self.mode, self.playback_file = "PLAYBACK", path
        rows = load_session(path)
        ticker = asyncio.create_task(self._ticker())
        t0, first = self.clock(), rows[0]["rx"]["utc"]
        for row in rows:
            delay = (row["rx"]["utc"] - first) / speed - (self.clock() - t0)
            if delay > 0:
                await asyncio.sleep(delay)
            self.on_frame(row, self.clock())
        self.playback_done = True
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
    return [r for r in rows if r.get("rx", {}).get("hmac_ok", True)]


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
