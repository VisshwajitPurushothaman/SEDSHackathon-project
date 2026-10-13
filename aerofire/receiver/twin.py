"""Laptop A digital-twin input (TCP JSON packets): one-stream filter and mapping into ICD frames.

Measured on the wire (docs/audit/FLUCTUATION_REPORT.md, raw capture 13 Oct): Laptop A interleaves three
streams from one IP and one device_id: the current mission (MET advancing at 2 Hz), a frozen packet that
repeats one old MET, and PRELAUNCH handshakes with timestamp_ms = 0. Applying "latest packet wins" made the
display flip by kilometres. This filter accepts ONE stream:

- source lock: a configured IP (AEROFIRE_TWIN_SOURCE) or the first source whose stream advances LOCK_MIN times;
- zero-time packets (timestamp_ms 0 / missing) are rejected;
- MET must strictly increase: repeats and backward packets are rejected;
- re-lock: if the locked stream stops advancing for STALL_RELOCK_S and another stream from the same
  source has advanced RELOCK_MIN times in a row, the filter switches to it (a new mission session).

Rejected packets are counted by reason and returned to the caller for logging; they are never applied.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

STALL_RELOCK_S = 10.0
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
        if t > self.last_met_ms:
            return self._accept(p, t, now, new_epoch=False)
        # Not advancing the locked stream: maybe a different (restarted) stream.
        c = self._track_candidate(t, now)
        stalled = self.last_accept_utc is not None and now - self.last_accept_utc > STALL_RELOCK_S
        if stalled and c.advances >= RELOCK_MIN:
            self.candidates.remove(c)
            return self._accept(p, t, now, new_epoch=True)
        return self._reject("repeat" if t == self.last_met_ms else "backward")

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
        """Sequence number = MET slot index at the sender's ping interval (gaps = missing slots)."""
        return int(round(met_ms(p) / (self.interval * 1000.0)))

    def stats(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        while self.arrivals and self.arrivals[0] < now - RATE_WINDOW_S:
            self.arrivals.popleft()
        return {"source": self.source, "allowed_source": self.allowed_source, "epoch": self.epoch, "accepted": self.accepted,
                "rejected": dict(self.rejected), "rejected_total": sum(self.rejected.values()),
                "rate_hz": len(self.arrivals) / RATE_WINDOW_S,
                "last_accept_age_s": None if self.last_accept_utc is None else now - self.last_accept_utc,
                "last_met_s": None if self.last_met_ms is None else self.last_met_ms / 1000.0, "interval_s": self.interval}
