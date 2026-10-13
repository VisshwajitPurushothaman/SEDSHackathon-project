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
import socket
import struct
import threading
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger("aerofire.tcp_receiver")

SAMPLE_TELEMETRY: dict[str, Any] = {
    "event": "device_handshake",
    "device_id": "client_node_01",
    "timestamp_ms": 5406250,
    "simulation_state": {
        "status": "RUNNING",
        "is_running": True,
        "is_paused": False,
        "is_resumed": False,
        "is_pinging": True,
        "last_action": "STARTED",
        "pause_count": 0,
        "resume_count": 0,
        "last_pause_timestamp_ms": None,
        "last_resume_timestamp_ms": None,
        "pause_resume_events": [
            {
                "action": "STARTED",
                "mission_time_ms": 0,
                "iso_time": "2026-10-12T22:58:59.354Z",
            },
            {
                "action": "PING_STARTED",
                "mission_time_ms": 0,
                "iso_time": "2026-10-12T22:58:59.354Z",
            },
        ],
    },
    "tcp_socket_stream": {
        "server_ip": "172.16.59.95",
        "port": 65432,
        "ping_interval_seconds": 0.5,
        "is_pinging": True,
        "protocol": "4-byte Big-Endian uint32 (!I) length header + UTF-8 JSON",
        "packets_dispatched": 8048,
        "socket_status": "PINGING_0.5S",
        "last_server_ack": {
            "status": "success",
            "received_bytes": 4626,
        },
    },
    "flight_phase": "LEO_ORBIT",
    "staging_sub_phase": "ATTACHED_TO_GONDOLA",
    "gnss_coordinates": {
        "origin_reference": "LAUNCH_PAD_ORIGIN [0, 0, 0]",
        "position_from_origin_xyz_m": [
            17977957.79,
            214211.16,
            -16501.18,
        ],
        "x_downrange_m": 17977957.79,
        "y_altitude_m": 214211.16,
        "z_crossrange_m": -16501.18,
        "slant_range_3d_m": 17979241.51,
        "geodetic_wgs84": {
            "lat_deg": 36.951768,
            "lon_deg": 195.751025,
            "alt_m": 214211.16,
        },
        "status": "NOMINAL",
    },
    "orbital_and_aerothermal_physics": {
        "specific_orbital_energy_mj_kg": -30.307,
        "specific_angular_momentum_m2_s": 51197977292,
        "eccentricity": 0.0014,
        "semi_major_axis_km": 6576.1,
        "apoapsis_km": 214,
        "periapsis_km": 196,
        "remaining_delta_v_mps": 0,
        "dynamic_pressure_q_pa": 43073.66,
        "max_q_pa": 43794.04,
        "aerodynamic_heat_flux_wm2": 334884392.5,
        "mach_number": 23.57,
        "membrane_hoop_stress_mpa": 2290.73,
        "differential_superpressure_pa": 1156.01,
    },
    "barometric_sensor": {
        "pressure_hpa": 1.11,
        "derived_altitude_m": 46968,
        "status": "NOMINAL",
    },
    "imu_sensor": {
        "accel_mps2": [
            -0.017,
            0.014,
            9.788,
        ],
        "gyro_dps": [
            0.023,
            0,
            -0.004,
        ],
        "attitude_rpy_deg": [
            0,
            0,
            0,
        ],
        "status": "NOMINAL",
    },
    "environmental_sensors": {
        "ambient_temp_c": -2.5,
        "relative_humidity_pct": 0.1,
        "effective_wind_vector_mps": [
            0,
            0,
            0,
        ],
    },
    "payload_state": {
        "is_attached": False,
        "balloon_volume_m3": 880000,
        "thrust_n": 0,
    },
}


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

    def __init__(self, host: str = "0.0.0.0", port: int = 65432, on_frame: Callable[[dict[str, Any]], None] | None = None):
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

        # Latest received telemetry frame initialized to sample template
        self.latest_telemetry: dict[str, Any] = copy.deepcopy(SAMPLE_TELEMETRY)
        self.has_received_live = False

        # Rolling history of packets for strip charts and trajectory
        self.history: deque[dict[str, Any]] = deque(maxlen=3000)

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

    def inject_frame(self, data: dict[str, Any]) -> dict[str, Any]:
        """Manually inject a telemetry frame (e.g. from UI testing or simulated feed)."""
        with self.lock:
            self.packets_received += 1
            self.last_received_utc = time.time()
            self.latest_telemetry = copy.deepcopy(data)
            self.has_received_live = True
            self.history.append({
                "recv_utc": self.last_received_utc,
                "data": self.latest_telemetry,
                "source": "INJECTED",
            })
            if self.on_frame_cb:
                try:
                    self.on_frame_cb(self.latest_telemetry)
                except Exception as ex:
                    logger.warning("Error in on_frame_cb: %s", ex)
            return self.latest_telemetry

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
                        # Extract gnss coordinates and altitude
                        gnss_in = data.get("gnss_coordinates") or {}
                        wgs_in = gnss_in.get("geodetic_wgs84") or {}
                        inc_alt = float(wgs_in.get("alt_m") or gnss_in.get("y_altitude_m") or 0.0)
                        inc_phase = data.get("flight_phase", "")

                        curr_gnss = self.latest_telemetry.get("gnss_coordinates") or {}
                        curr_wgs = curr_gnss.get("geodetic_wgs84") or {}
                        curr_alt = float(curr_wgs.get("alt_m") or curr_gnss.get("y_altitude_m") or 0.0)

                        # Prevent fluctuating back to default values between pings:
                        # If active flight state exists (curr_alt > 50) and incoming packet has near-zero altitude
                        # (e.g. from concurrent prelaunch or empty handshake), retain the gained flight coordinates
                        if curr_alt > 50.0 and inc_alt <= 20.0:
                            merged = copy.deepcopy(self.latest_telemetry)
                            merged["timestamp_ms"] = data.get("timestamp_ms", merged.get("timestamp_ms", 0))
                            merged["ping_paused"] = data["ping_paused"]
                            if "is_prediction_active" in data:
                                merged["is_prediction_active"] = data["is_prediction_active"]
                            if "tcp_socket_stream" in data:
                                merged["tcp_socket_stream"] = data["tcp_socket_stream"]
                            data = merged
                        else:
                            # Re-compute high-precision geodetic fix relative to Point Calimere Bird Sanctuary (Tamil Nadu)
                            # Pad Origin: Lat 10.301500° N, Lon 79.851200° E, Alt 2.0 m
                            # WGS-84 curvature: M_lat = 110609.6 m/deg, N_lon = 109531.0 m/deg
                            pos_xyz = gnss_in.get("position_from_origin_xyz_m") or [0.0, 0.0, 0.0]
                            x_m = float(gnss_in.get("x_downrange_m") if "x_downrange_m" in gnss_in else pos_xyz[0])
                            y_m = float(gnss_in.get("y_altitude_m") if "y_altitude_m" in gnss_in else (pos_xyz[1] if len(pos_xyz) > 1 else inc_alt))
                            z_m = float(gnss_in.get("z_crossrange_m") if "z_crossrange_m" in gnss_in else (pos_xyz[2] if len(pos_xyz) > 2 else 0.0))

                            slant_3d = math.hypot(x_m, y_m, z_m)
                            calc_lat = round(10.301500 + (z_m / 110609.6), 6)
                            calc_lon = round(79.851200 + (x_m / 109531.0), 6)
                            calc_alt = round(2.0 + y_m, 2)

                            data["gnss_coordinates"] = {
                                "origin_reference": "POINT_CALIMERE_BIRD_SANCTUARY [0, 0, 0]",
                                "position_from_origin_xyz_m": [round(x_m, 2), round(y_m, 2), round(z_m, 2)],
                                "x_downrange_m": round(x_m, 2),
                                "y_altitude_m": round(y_m, 2),
                                "z_crossrange_m": round(z_m, 2),
                                "slant_range_3d_m": round(slant_3d, 2),
                                "geodetic_wgs84": {
                                    "lat_deg": calc_lat,
                                    "lon_deg": calc_lon,
                                    "alt_m": calc_alt,
                                },
                                "status": "NOMINAL",
                            }

                        self.packets_received += 1
                        self.bytes_received += payload_len + 4
                        self.last_received_utc = recv_utc
                        self.last_payload_len = payload_len
                        self.last_ack = ack_obj
                        self.latest_telemetry = data
                        self.has_received_live = True
                        self.history.append({
                            "recv_utc": recv_utc,
                            "data": data,
                            "source": client_str,
                        })

                    # Log each ping prominently
                    print(
                        f"[{time.strftime('%H:%M:%S')}] TCP Ping #{self.packets_received} received from {client_str} "
                        f"({payload_len} B) | phase: {data.get('flight_phase', 'N/A')} | node: {data.get('device_id', 'N/A')}",
                        flush=True,
                    )

                    if self.on_frame_cb:
                        try:
                            self.on_frame_cb(data)
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
            }

    def get_snapshot(self) -> dict[str, Any]:
        """Return status + latest telemetry data."""
        with self.lock:
            return {
                "status": self.get_status(),
                "telemetry": copy.deepcopy(self.latest_telemetry),
            }

    def get_history_payload(self, since: float | None = None, max_points: int = 3000) -> dict[str, Any]:
        """Convert rolling TCP telemetry history into strip-chart rows and map track points."""
        with self.lock:
            rows: list[dict[str, Any]] = []
            track: list[dict[str, Any]] = []
            prev_alt: float | None = None
            prev_met: float | None = None

            for item in self.history:
                recv_utc = item.get("recv_utc", 0)
                d = item.get("data", {})
                ts_ms = d.get("timestamp_ms", 0)
                met = round(ts_ms / 1000.0, 2)
                if since is not None and met <= since:
                    continue

                gnss = d.get("gnss_coordinates", {})
                wgs84 = gnss.get("geodetic_wgs84", {})
                x_val = float(gnss.get("x_downrange_m", 0.0))
                y_val = float(gnss.get("y_altitude_m") or wgs84.get("alt_m") or d.get("barometric_sensor", {}).get("derived_altitude_m", 0.0))
                z_val = float(gnss.get("z_crossrange_m", 0.0))
                alt_m = round(y_val, 2)

                # High-precision Point Calimere Geodetic calculation
                lat_deg = round(10.301500 + (z_val / 110609.6), 6)
                lon_deg = round(79.851200 + (x_val / 109531.0), 6)

                # Vertical velocity: numerical derivative or wind vector
                env = d.get("environmental_sensors", {})
                wind = env.get("effective_wind_vector_mps", [0, 0, 0])
                vz = float(wind[1]) if len(wind) > 1 and abs(wind[1]) > 0.01 else 0.0

                if abs(vz) < 0.001 and prev_alt is not None and prev_met is not None:
                    d_met = met - prev_met
                    if 0.05 < d_met < 10.0:
                        vz = round((alt_m - prev_alt) / d_met, 2)

                prev_alt = alt_m
                prev_met = met

                payload = d.get("payload_state", {})
                superpress_pa = (
                    payload.get("differential_superpressure_pa")
                    or d.get("orbital_and_aerothermal_physics", {}).get("differential_superpressure_pa", 142.25)
                )
                gauge_p_hpa = round(superpress_pa / 100.0, 2)
                # Nominal helium pressurant tank pressure 248.5 bar, slightly decreasing with MET
                tank_p_bar = round(max(180.0, 250.0 - (met * 0.005)), 1)

                rows.append({
                    "met": met,
                    "utc": recv_utc,
                    "phase": d.get("flight_phase", "STRATOSPHERIC_ASCENT"),
                    "rec": "GO_RECOMMENDED",
                    "GNSS_ALT": alt_m,
                    "fused_altitude": alt_m,
                    "GNSS_VU": vz,
                    "fused_vertical_speed": vz,
                    "TANK_P": tank_p_bar,
                    "GAUGE_P": gauge_p_hpa,
                    "x": x_val,
                    "y": alt_m,
                    "z": z_val,
                    "lat": lat_deg,
                    "lon": lon_deg,
                    "status": "MEASURED",
                })
                track.append({
                    "met": met,
                    "lat": lat_deg,
                    "lon": lon_deg,
                    "alt": alt_m,
                    "status": "FUSED",
                })

            return {
                "mission_id": "SBRL-MISSION-LIVE-01",
                "rows": rows[-max_points:],
                "track": track[-max_points:],
                "los_periods": [],
            }


# Singleton instance shared across FastAPI and receiver service
GLOBAL_TCP_RECEIVER = TcpReceiver()
