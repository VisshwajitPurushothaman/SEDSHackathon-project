"""Test / simulation client that sends telemetry to the AERO-FIRE TCP Ground Station.

Usage:
    python tools/send_tcp_telemetry.py                 # Single test packet
    python tools/send_tcp_telemetry.py --stream        # Stream packets every 0.5s
    python tools/send_tcp_telemetry.py --port 65432    # Custom port
"""
import argparse
import copy
import json
import math
import socket
import struct
import time

SAMPLE_PAYLOAD = {
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
        "server_ip": "127.0.0.1",
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


def receive_exact(conn, num_bytes):
    buf = bytearray()
    while len(buf) < num_bytes:
        chunk = conn.recv(num_bytes - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def send_packet(sock, payload):
    data = json.dumps(payload).encode("utf-8")
    header = struct.pack("!I", len(data))
    sock.sendall(header + data)

    # Read ACK header
    ack_hdr = receive_exact(sock, 4)
    if not ack_hdr:
        return None
    ack_len = struct.unpack("!I", ack_hdr)[0]
    ack_bytes = receive_exact(sock, ack_len)
    if not ack_bytes:
        return None
    return json.loads(ack_bytes.decode("utf-8"))


def main():
    p = argparse.ArgumentParser(description="Send TCP telemetry packet to GCS")
    p.add_argument("--host", default="127.0.0.1", help="GCS host")
    p.add_argument("--port", type=int, default=65432, help="GCS TCP port")
    p.add_argument("--stream", action="store_true", help="Send continuous telemetry stream every 0.5s")
    p.add_argument("--count", type=int, default=0, help="Number of packets (0 = infinite for stream)")
    args = p.parse_args()

    print(f"Connecting to GCS TCP at {args.host}:{args.port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((args.host, args.port))
    print("Connected successfully!")

    count = 0
    t0 = time.time()
    payload = copy.deepcopy(SAMPLE_PAYLOAD)

    try:
        while True:
            count += 1
            now_ms = int(time.time() * 1000)
            elapsed = time.time() - t0

            # Slightly wiggle values for dynamic stream demonstration
            payload["timestamp_ms"] = now_ms
            payload["tcp_socket_stream"]["packets_dispatched"] = count
            payload["gnss_coordinates"]["x_downrange_m"] = round(17977957.79 + elapsed * 7800, 2)
            payload["orbital_and_aerothermal_physics"]["mach_number"] = round(23.57 + 0.05 * math.sin(elapsed), 2)
            payload["orbital_and_aerothermal_physics"]["dynamic_pressure_q_pa"] = round(43073.66 + 50 * math.sin(elapsed * 2), 2)
            payload["imu_sensor"]["accel_mps2"] = [
                round(-0.017 + 0.005 * math.sin(elapsed), 3),
                round(0.014 + 0.005 * math.cos(elapsed), 3),
                round(9.788 + 0.01 * math.sin(elapsed), 3),
            ]

            ack = send_packet(sock, payload)
            print(f"[{count}] Dispatched packet ({len(json.dumps(payload))} bytes) -> Server ACK: {ack}")

            if not args.stream or (args.count > 0 and count >= args.count):
                break

            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping telemetry sender.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
