"""Audit A1: raw telemetry listener. Logs every packet with arrival time and source, without app code.

    python tools/audit/link_probe.py --tcp 65431 --seconds 120      # Laptop A twin (4-byte length + JSON)
    python tools/audit/link_probe.py --udp 5600 --seconds 120       # ICD v1 sender

The port must be free (stop the backend first). TCP clients get the same ACK the receiver sends, so
the sender behaves as normal. Writes data/audit/raw_capture.jsonl:
{recv_utc, proto, src, conn, n_in_conn, bytes, payload (parsed JSON or null), error}
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import threading
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "data" / "audit" / "raw_capture.jsonl"
lock = threading.Lock()


def log(fh, rec: dict) -> None:
    with lock:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        fh.flush()


def exact(c: socket.socket, n: int) -> bytes | None:
    b = bytearray()
    while len(b) < n:
        part = c.recv(n - len(b))
        if not part:
            return None
        b += part
    return bytes(b)


def tcp_client(c: socket.socket, addr, fh, conn_id: int) -> None:
    n = 0
    with c:
        c.settimeout(30)
        while True:
            try:
                head = exact(c, 4)
                if head is None:
                    return
                body = exact(c, struct.unpack("!I", head)[0])
                if body is None:
                    return
            except OSError:
                return
            n += 1
            rec = {"recv_utc": time.time(), "proto": "tcp", "src": f"{addr[0]}:{addr[1]}", "conn": conn_id, "n_in_conn": n, "bytes": len(body)}
            try:
                rec["payload"] = json.loads(body.decode("utf-8"))
            except Exception as exc:  # keep the evidence
                rec["payload"], rec["error"], rec["raw"] = None, str(exc), body[:200].decode("utf-8", "replace")
            log(fh, rec)
            ack = json.dumps({"status": "success", "received_bytes": len(body)}).encode()
            c.sendall(struct.pack("!I", len(ack)) + ack)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tcp", type=int)
    p.add_argument("--udp", type=int)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--seconds", type=float, default=120.0)
    a = p.parse_args()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + a.seconds
    with OUT.open("w", encoding="utf-8") as fh:
        if a.udp:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind((a.host, a.udp))
            s.settimeout(0.5)
            while time.time() < end:
                try:
                    data, addr = s.recvfrom(65535)
                except socket.timeout:
                    continue
                rec = {"recv_utc": time.time(), "proto": "udp", "src": f"{addr[0]}:{addr[1]}", "bytes": len(data)}
                try:
                    rec["payload"] = json.loads(data.decode("utf-8"))
                except Exception as exc:
                    rec["payload"], rec["error"] = None, str(exc)
                log(fh, rec)
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((a.host, a.tcp or 65431))
            s.listen(16)
            s.settimeout(0.5)
            conn_id = 0
            while time.time() < end:
                try:
                    c, addr = s.accept()
                except socket.timeout:
                    continue
                conn_id += 1
                threading.Thread(target=tcp_client, args=(c, addr, fh, conn_id), daemon=True).start()
    print(f"capture written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
