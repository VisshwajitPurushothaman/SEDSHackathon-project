"""Unit test for the TCP digital twin telemetry receiver."""
import json
import socket
import struct
import time
from aerofire.receiver.tcp_receiver import TcpReceiver, SAMPLE_TELEMETRY


def test_tcp_receiver_packet_exchange():
    # Use a high isolated port for test
    test_port = 65430
    recv = TcpReceiver(host="127.0.0.1", port=test_port)
    recv.start()

    time.sleep(0.2)  # Give thread a moment to bind

    try:
        assert recv.status in ("LISTENING", "STARTING")

        # Connect client
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.connect(("127.0.0.1", test_port))

        payload_bytes = json.dumps(SAMPLE_TELEMETRY).encode("utf-8")
        header = struct.pack("!I", len(payload_bytes))
        client_sock.sendall(header + payload_bytes)

        # Receive 4-byte ACK header
        ack_hdr = bytearray()
        while len(ack_hdr) < 4:
            chunk = client_sock.recv(4 - len(ack_hdr))
            assert chunk, "Client socket disconnected early"
            ack_hdr.extend(chunk)

        ack_len = struct.unpack("!I", ack_hdr)[0]
        ack_body = bytearray()
        while len(ack_body) < ack_len:
            chunk = client_sock.recv(ack_len - len(ack_body))
            assert chunk
            ack_body.extend(chunk)

        ack = json.loads(ack_body.decode("utf-8"))
        assert ack["status"] == "success"
        assert ack["received_bytes"] == len(payload_bytes)

        # Verify receiver state
        st = recv.get_status()
        assert st["packets_received"] >= 1
        assert recv.latest_telemetry["flight_phase"] == "LEO_ORBIT"
        assert recv.latest_telemetry["orbital_and_aerothermal_physics"]["mach_number"] == 23.57

        client_sock.close()
    finally:
        recv.stop()
