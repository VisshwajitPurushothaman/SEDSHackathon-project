"""Post-flight recovery of records the ground did not receive live.

Protocol (ground side):
  1. Missing seqs = everything the recorder holds (0..max) that never arrived live.
  2. Rough picture first: every `sparse_stride`-th missing record, in batches.
  3. Then the rest, in batches of `batch_size`.
  4. Each batch transfer may fail (recovery link); it is retried up to `max_retries` times.
     Records of a batch that still fails are listed as LOST.
  5. Every record is verified with the HMAC chain against its predecessor's MAC
     (a record whose predecessor is not yet held is verified later). Finally the
     whole chain from genesis is verified if it is complete.
`fetch(seqs)` is supplied by the caller (the vehicle side): it returns the
records, or None when the transfer failed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..telemetry.recorder import genesis_mac, verify_record

Fetch = Callable[[list[int]], list[dict[str, Any]] | None]


@dataclass
class RecoveryReport:
    state: str = "idle"  # idle | running | done
    stage: str = ""
    expected: int = 0
    received_live: int = 0
    missing_at_start: int = 0
    recovered: int = 0
    retries: int = 0
    failed_batches: int = 0
    lost: list[int] = field(default_factory=list)
    hmac_failures: list[int] = field(default_factory=list)
    chain_ok: bool | None = None
    chain_first_bad: int | None = None
    started_wall: float | None = None
    finished_wall: float | None = None
    progress: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["lost_count"] = len(self.lost)
        d["have"] = self.received_live + self.recovered
        return d


class RecoveryClient:
    def __init__(self, cfg, key: str, genesis: str, held: dict[int, dict[str, Any]], max_seq: int, fetch: Fetch,
                 batch_delay_s: float = 0.0):
        rc = cfg.recovery
        self.batch_size, self.stride, self.max_retries = rc.batch_size, rc.sparse_stride, rc.max_retries
        self.key, self.genesis = key, genesis
        self.held = held  # seq -> record (live records, then recovered ones), shared with the caller
        self.max_seq = max_seq
        self.fetch = fetch
        self.batch_delay_s = batch_delay_s
        self.report = RecoveryReport()

    def _verify_new(self, recs: list[dict[str, Any]]) -> None:
        for r in recs:
            self.held[r["seq"]] = r
        # Verify every held record whose predecessor is held (cheap enough to re-check the new neighbourhood).
        for r in recs:
            for s in (r["seq"], r["seq"] + 1):
                cur = self.held.get(s)
                if cur is None:
                    continue
                prev = genesis_mac(self.key.encode(), self.genesis) if s == 0 else self.held.get(s - 1, {}).get("mac")
                if prev is not None and not verify_record(self.key, prev, cur):
                    if s not in self.report.hmac_failures:
                        self.report.hmac_failures.append(s)

    def _transfer(self, seqs: list[int]) -> None:
        for start in range(0, len(seqs), self.batch_size):
            batch = seqs[start:start + self.batch_size]
            got = None
            for attempt in range(self.max_retries + 1):
                if attempt:
                    self.report.retries += 1
                got = self.fetch(batch)
                if got is not None:
                    break
            if got is None:
                self.report.failed_batches += 1
                self.report.lost.extend(batch)
            else:
                self._verify_new(got)
                self.report.recovered += len(got)
            self.report.progress.append({"stage": self.report.stage, "have": len(self.held), "expected": self.report.expected,
                                         "recovered": self.report.recovered, "lost": len(self.report.lost), "wall": time.time()})
            if self.batch_delay_s:
                time.sleep(self.batch_delay_s)

    def run(self) -> RecoveryReport:
        r = self.report
        r.state, r.started_wall = "running", time.time()
        r.expected = self.max_seq + 1
        r.received_live = len(self.held)
        # Verify everything received live first (tamper on the downlink would show here).
        self._verify_new(list(self.held.values()))
        missing = [s for s in range(r.expected) if s not in self.held]
        r.missing_at_start = len(missing)
        sparse = missing[:: self.stride]
        r.stage = "rough picture (every %dth missing record)" % self.stride
        self._transfer(sparse)
        rest = [s for s in missing if s not in self.held and s not in r.lost]
        r.stage = "full detail (batches of %d)" % self.batch_size
        self._transfer(rest)
        # Whole chain, if complete.
        if len(self.held) == r.expected and not r.lost:
            prev = genesis_mac(self.key.encode(), self.genesis)
            r.chain_ok, r.chain_first_bad = True, None
            for s in range(r.expected):
                rec = self.held[s]
                if not verify_record(self.key, prev, rec):
                    r.chain_ok, r.chain_first_bad = False, s
                    break
                prev = rec["mac"]
        else:
            r.chain_ok = None if not r.hmac_failures else False
        r.lost.sort()
        r.state, r.stage, r.finished_wall = "done", "complete", time.time()
        return r
