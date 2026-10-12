"""Onboard recorder: every record is sequenced and written BEFORE it is transmitted.

Integrity: an HMAC-SHA256 hash chain. For record n,
    mac_n = HMAC(key, mac_{n-1} || canonical_json(record_n)),   mac_{-1} = HMAC(key, genesis)
so editing, deleting, inserting or reordering any record breaks every later
MAC. The demo key in config is labelled "demo, NOT secure".
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Iterable


# Not covered by the MAC: the MAC itself and receiver metadata added by the ground (`rx`).
UNSIGNED_FIELDS = ("mac", "rx")


def canonical(record: dict[str, Any]) -> str:
    """Stable serialisation (sorted keys, no whitespace) of everything the vehicle signed."""
    body = {k: v for k, v in record.items() if k not in UNSIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _mac(key: bytes, prev_mac: str, record: dict[str, Any]) -> str:
    return hmac.new(key, (prev_mac + canonical(record)).encode("utf-8"), hashlib.sha256).hexdigest()


def genesis_mac(key: bytes, genesis: str) -> str:
    return hmac.new(key, genesis.encode("utf-8"), hashlib.sha256).hexdigest()


class OnboardRecorder:
    """Append-only store. Records are kept as canonical JSON strings to stay compact."""

    def __init__(self, key: str, genesis: str):
        self._key = key.encode("utf-8")
        self._genesis = genesis_mac(self._key, genesis)
        self._rows: list[str] = []  # canonical JSON including the mac
        self._last_mac = self._genesis

    @classmethod
    def from_config(cls, cfg) -> "OnboardRecorder":
        return cls(cfg.telemetry.hmac_key_demo, cfg.telemetry.hmac_genesis)

    def write(self, sample: dict[str, Any]) -> dict[str, Any]:
        """Assign seq, compute the chained MAC, store, and return the record to transmit."""
        rec = {"seq": len(self._rows), "status": "MEASURED", **sample}
        rec["mac"] = _mac(self._key, self._last_mac, rec)
        self._last_mac = rec["mac"]
        self._rows.append(json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        return rec

    def __len__(self) -> int:
        return len(self._rows)

    def read(self, seq: int) -> dict[str, Any]:
        return json.loads(self._rows[seq])

    def read_many(self, seqs: Iterable[int]) -> list[dict[str, Any]]:
        return [self.read(s) for s in seqs]

    def prev_mac(self, seq: int) -> str:
        return self._genesis if seq == 0 else json.loads(self._rows[seq - 1])["mac"]

    @property
    def genesis(self) -> str:
        return self._genesis


def verify_record(key: str, prev_mac: str, record: dict[str, Any]) -> bool:
    return hmac.compare_digest(_mac(key.encode("utf-8"), prev_mac, record), record.get("mac", ""))


def verify_chain(key: str, genesis: str, records: list[dict[str, Any]]) -> tuple[bool, int | None]:
    """Verify a complete, ordered chain starting at seq 0. Returns (ok, first bad seq)."""
    prev = genesis_mac(key.encode("utf-8"), genesis)
    for i, rec in enumerate(records):
        if rec.get("seq") != i or not verify_record(key, prev, rec):
            return False, i
        prev = rec["mac"]
    return True, None
