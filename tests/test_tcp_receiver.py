"""Laptop A digital-twin TCP input: one-stream filter (measured interleaving) and the socket exchange.

Replaces the earlier test that asserted the receiver stored the LEO sample fixture: the receiver no longer
holds any fixture (docs/audit/REALITY_TABLE.md).
"""
import json
import socket
import struct
import time

from aerofire.receiver.tcp_receiver import TcpReceiver
from aerofire.receiver.twin import STALL_RELOCK_S, TwinFilter


def pkt(t_ms, alt=1000.0, phase="STRATOSPHERIC_ASCENT"):
    return {"device_id": "client_node_01", "timestamp_ms": t_ms, "flight_phase": phase,
            "server_target": {"ping_interval_seconds": 0.5},
            "gnss_coordinates": {"geodetic_wgs84": {"lat_deg": 13.0, "lon_deg": 80.27, "alt_m": alt}}}


def test_interleaved_streams_from_the_raw_capture_are_reduced_to_one():
    """Pattern measured on the wire every 0.5 s: frozen (MET 1813.295 s), zero-time, live, frozen."""
    f = TwinFilter()
    accepted = []
    for i in range(40):
        now = 1000.0 + 0.5 * i
        live_t = 3_544_449 + 500 * i
        for p in (pkt(1_813_295, 18199.45), pkt(0, 0.0, "PRELAUNCH"), pkt(live_t, 38523.0 + i), pkt(1_813_295, 18199.45)):
            ok, _ = f.offer(p, "172.16.58.215", now)
            if ok:
                accepted.append(p["timestamp_ms"])
    # Lock only once the live stream has advanced twice; frozen and zero-time packets are never applied.
    assert accepted == [3_544_449 + 500 * i for i in range(2, 40)]
    st = f.stats(1020.0)
    # not_locked: cycles 0-1 (frozen, live, frozen) = 6, plus the first frozen of cycle 2; backward: 1 + 2 x 37
    assert st["rejected"] == {"not_locked": 7, "zero_time": 40, "backward": 75}
    assert st["accepted"] == 38 and abs(st["rate_hz"] - 2.0) < 0.15
    assert [f.seq(pkt(t)) for t in (1_000, 1_500, 3_000)] == [2, 3, 6]  # MET slot at 0.5 s


def test_restarted_mission_relocks_only_after_the_old_stream_stalls():
    f = TwinFilter()
    oks = [f.offer(pkt(500_000 + 500 * i), "a", 100.0 + 0.5 * i)[0] for i in range(10)]
    assert oks == [False, False] + [True] * 8
    # A restarted mission (MET from 1 s) is rejected while the old stream is alive...
    assert f.offer(pkt(1_000), "a", 105.0) == (False, "backward")
    # ...and accepted once the old stream has stalled and the new one advanced RELOCK_MIN times.
    t = 105.0 + STALL_RELOCK_S + 1.0
    results = [f.offer(pkt(1_000 + 500 * k), "a", t + 0.5 * k) for k in range(1, 8)]
    assert ("new_epoch" in [r for _, r in results]) and f.epoch == 2
    assert f.offer(pkt(999), "a", t + 10) == (False, "backward")


def test_source_lock_and_zero_time():
    f = TwinFilter(allowed_source="10.0.0.2")
    assert f.offer(pkt(1000), "10.0.0.9", 1.0) == (False, "foreign_source")
    assert f.offer(pkt(0), "10.0.0.2", 1.0) == (False, "zero_time")
    assert [f.offer(pkt(t), "10.0.0.2", 1.0)[1] for t in (1000, 1500, 2000)] == ["not_locked", "not_locked", "new_epoch"]
    assert f.offer(pkt(2000), "10.0.0.2", 1.5) == (False, "repeat")


def _send(sock, obj):
    body = json.dumps(obj).encode()
    sock.sendall(struct.pack("!I", len(body)) + body)
    head = sock.recv(4)
    return json.loads(sock.recv(struct.unpack("!I", head)[0]).decode())


def test_socket_exchange_acks_and_applies_only_accepted_packets(tmp_path):
    recv = TcpReceiver(host="127.0.0.1", port=65430)
    recv.rejected_log = tmp_path / "rejected.jsonl"
    got = []
    recv.on_frame_cb = lambda p, src, utc: got.append(p["timestamp_ms"])
    recv.start()
    time.sleep(0.3)
    try:
        assert recv.latest_telemetry is None  # nothing shown before data
        for t in (1500, 2000, 0, 2500, 2000):
            with socket.create_connection(("127.0.0.1", 65430), timeout=3) as s:
                assert _send(s, pkt(t))["status"] == "success"
        time.sleep(0.2)
        assert got == [2500] and recv.latest_telemetry["timestamp_ms"] == 2500
        st = recv.get_status()
        assert st["packets_received"] == 5 and st["filter"]["rejected"] == {"not_locked": 2, "zero_time": 1, "backward": 1}
        assert len(recv.rejected_log.read_text().splitlines()) == 4
    finally:
        recv.stop()
