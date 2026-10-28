"""TCP Telemetry Receiver for Stratospheric Balloon Rocket Launching System.

Listens for TCP ping streams from the virtual environment / digital twin node.
Protocol:
  - 4-byte Big-Endian uint32 (!I) payload length header
  - UTF-8 encoded JSON payload
  - Response: 4-byte Big-Endian length header + JSON ACK:
    {"status": "success", "received_bytes": payload_len}
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
from pathlib import Path
import socket
import struct
import threading
import time
from collections import deque
from typing import Any, Callable

from .twin import TwinFilter

logger = logging.getLogger("aerofire.tcp_receiver")

def receive_exact(conn: socket.socket, num_bytes: int) -> bytes | None:
    """Ensure exactly num_bytes are read from the TCP stream."""
    buffer = bytearray()
    while len(buffer) < num_bytes:
        try:
            packet = conn.recv(num_bytes - len(buffer))
        except (socket.timeout, ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            return None
        if not packet:
            return None  # Connection closed prematurely
        buffer.extend(packet)
    return bytes(buffer)


def read_telemetry_packet(conn: socket.socket) -> tuple[dict[str, Any] | None, int]:
    """Read a packet either with 4-byte '!I' length header or raw JSON."""
    raw_len = receive_exact(conn, 4)
    if not raw_len:
        return None, 0

    # Auto-detect if raw JSON starts immediately with '{' (0x7B)
    if raw_len[0] == 0x7B:
        buf = bytearray(raw_len)
        brace_count = buf.count(b"{") - buf.count(b"}")
        conn.settimeout(2.0)
        while brace_count > 0:
            try:
                chunk = conn.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            buf.extend(chunk)
            brace_count = buf.count(b"{") - buf.count(b"}")
        conn.settimeout(120.0)
        try:
            return json.loads(buf.decode("utf-8")), len(buf)
        except Exception:
            return None, len(buf)

    # Standard 4-byte Big-Endian length header
    payload_len = struct.unpack("!I", raw_len)[0]
    if payload_len > 10 * 1024 * 1024:
        return None, 0
    raw_payload = receive_exact(conn, payload_len)
    if not raw_payload:
        return None, 0
    try:
        return json.loads(raw_payload.decode("utf-8")), payload_len
    except Exception:
        return None, payload_len


class TcpReceiver:
    """Thread-safe TCP receiver server listening for digital twin telemetry."""

    def __init__(self, host: str = "0.0.0.0", port: int = 65431, on_frame: Callable[[dict[str, Any]], None] | None = None):
        self.host = host
        self.port = port
        self.on_frame_cb = on_frame
        self.lock = threading.RLock()
        self.running = False
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

        # State tracking
        self.status = "STOPPED"
        self.packets_received = 0
        self.bytes_received = 0
        self.last_received_utc: float | None = None
        self.last_client_addr: str | None = None
        self.last_payload_len: int | None = None
        self.last_ack: dict[str, Any] | None = None
        self.active_clients = 0

        # Latest ACCEPTED packet (None until one arrives: no fixture is ever shown as live)
        self.latest_telemetry: dict[str, Any] | None = None
        self.has_received_live = False
        self.filter = TwinFilter(allowed_source=os.environ.get("AEROFIRE_TWIN_SOURCE") or None)
        self.rejected_log = Path(__file__).resolve().parents[2] / "data" / "sessions" / "twin_rejected.jsonl"


    def start(self) -> None:
        with self.lock:
            if self.running:
                return
            self.running = True
            self.status = "STARTING"
            self._thread = threading.Thread(target=self._run_server, name="tcp-telemetry-server", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self.lock:
            self.running = False
            self.status = "STOPPED"
            if self._server_sock:
                try:
                    self._server_sock.close()
                except Exception:
                    pass
                self._server_sock = None

    def _handle_client(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        client_str = f"{addr[0]}:{addr[1]}"
        print(f"[{time.strftime('%H:%M:%S')}] TCP Client connected from {client_str}", flush=True)
        with self.lock:
            self.active_clients += 1
            self.last_client_addr = client_str
            self.status = "CONNECTED"

        conn.settimeout(120.0)  # Allow keep-alive pinging
        try:
            with conn:
                while self.running:
                    data, payload_len = read_telemetry_packet(conn)
                    if data is None:
                        break

                    recv_utc = time.time()
                    ack_obj = {"status": "success", "received_bytes": payload_len}

                    # Detect ping_paused flag added by user
                    ping_paused_raw = (
                        data.get("ping_paused")
                        or (data.get("simulation_state", {}).get("ping_paused"))
                        or (data.get("tcp_socket_stream", {}).get("ping_paused"))
                    )
                    is_paused = ping_paused_raw in (1, True, "1", "true", "True")
                    data["ping_paused"] = 1 if is_paused else 0
                    if is_paused:
                        data["is_prediction_active"] = True

                    with self.lock:
                        ok, reason = self.filter.offer(data, addr[0], recv_utc)
                        self.packets_received += 1
                        self.bytes_received += payload_len + 4
                        self.last_received_utc = recv_utc
                        self.last_payload_len = payload_len
                        self.last_ack = ack_obj
                        if ok:
                            self.latest_telemetry = data
                            self.has_received_live = True
                        else:
                            self._log_rejected(data, client_str, recv_utc, reason)

                    # Log each ping prominently
                    if self.on_frame_cb and ok:
                        try:
                            self.on_frame_cb(data, addr[0], recv_utc)
                        except Exception as ex:
                            logger.warning("Error in on_frame callback: %s", ex)

                    # Send confirmation back to client
                    ack_bytes = json.dumps(ack_obj).encode("utf-8")
                    ack_packet = struct.pack("!I", len(ack_bytes)) + ack_bytes
                    conn.sendall(ack_packet)

        except Exception as exc:
            logger.warning("TCP Client %s handler exception: %s", client_str, exc)
        finally:
            print(f"[{time.strftime('%H:%M:%S')}] TCP Client {client_str} disconnected", flush=True)
            with self.lock:
                self.active_clients = max(0, self.active_clients - 1)
                if self.active_clients == 0 and self.running:
                    self.status = "LISTENING"

    def _run_server(self) -> None:
        while self.running:
            try:
                server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server_sock.bind((self.host, self.port))
                server_sock.listen(5)
                self._server_sock = server_sock
                with self.lock:
                    self.status = "LISTENING"
                logger.info("TCP Telemetry Server listening on %s:%d...", self.host, self.port)
                print(f"TCP Telemetry Server listening on {self.host}:{self.port}...", flush=True)

                while self.running:
                    try:
                        conn, addr = server_sock.accept()
                        client_th = threading.Thread(
                            target=self._handle_client,
                            args=(conn, addr),
                            name=f"tcp-client-{addr[0]}:{addr[1]}",
                            daemon=True,
                        )
                        client_th.start()
                    except socket.error:
                        if not self.running:
                            break
                        time.sleep(0.5)

            except Exception as e:
                logger.error("TCP server error on %s:%d: %s", self.host, self.port, e)
                with self.lock:
                    self.status = f"ERROR: {e}"
                time.sleep(2.0)
            finally:
                if self._server_sock:
                    try:
                        self._server_sock.close()
                    except Exception:
                        pass
                    self._server_sock = None

    def _log_rejected(self, data: dict[str, Any], client: str, utc: float, reason: str) -> None:
        """Rejected packets are logged, never applied."""
        try:
            self.rejected_log.parent.mkdir(parents=True, exist_ok=True)
            with self.rejected_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"recv_utc": utc, "src": client, "reason": reason, "timestamp_ms": data.get("timestamp_ms") if isinstance(data, dict) else None,
                                     "flight_phase": data.get("flight_phase") if isinstance(data, dict) else None}) + "\n")
        except OSError as exc:
            logger.warning("cannot log rejected twin packet: %s", exc)

    def get_status(self) -> dict[str, Any]:
        """Return diagnostic status of the TCP receiver."""
        with self.lock:
            now = time.time()
            age_s = (now - self.last_received_utc) if self.last_received_utc else None
            return {
                "server_status": self.status,
                "host": self.host,
                "port": self.port,
                "active_clients": self.active_clients,
                "connected_client": self.last_client_addr,
                "packets_received": self.packets_received,
                "bytes_received": self.bytes_received,
                "last_received_utc": self.last_received_utc,
                "last_frame_age_s": round(age_s, 2) if age_s is not None else None,
                "last_payload_len": self.last_payload_len,
                "last_ack": self.last_ack,
                "has_received_live": self.has_received_live,
                "filter": self.filter.stats(now),
            }

    def get_snapshot(self) -> dict[str, Any]:
        """Return status + latest telemetry data."""
        with self.lock:
            return {
                "status": self.get_status(),
                "telemetry": copy.deepcopy(self.latest_telemetry),
            }

# Singleton instance shared across FastAPI and receiver service
GLOBAL_TCP_RECEIVER = TcpReceiver()
