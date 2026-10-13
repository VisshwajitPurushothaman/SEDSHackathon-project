"""Audit A3 perturbation test: send KNOWN synthetic telemetry and check that every displayed value and
prediction responds to its inputs, and only to them.

Run against an ISOLATED backend (never the live one):
    python backend.py --port 8012 --udp-port 5611 --tcp-port 65499 --snapshot
    python tools/audit/test_sender.py --api http://127.0.0.1:8012 --udp 5611 --tcp 65499

Cases (both paths): ramp (altitude rises 10 m per packet), constant (altitude 5000 m), step (5000 -> 8000 m).
TCP path: the digital-twin JSON format accepted by aerofire/receiver/tcp_receiver.py (4-byte length + JSON).
UDP path: ICD v1 frames (aerofire/telemetry/icd.py) with constant TANK_P 200 bar, MOTOR_T 20 C.
Writes data/audit/perturbation.json and prints a verdict per checked output.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from aerofire.config import load_config  # noqa: E402
from aerofire.physics import geo  # noqa: E402
from aerofire.telemetry import icd  # noqa: E402

CFG = load_config()


def state(api: str) -> dict:
    with urllib.request.urlopen(api + "/api/ground/state", timeout=5) as r:
        return json.loads(r.read().decode())


def chan(s: dict, m: str):
    return next((c["value"] for c in s.get("channels") or [] if c["mnemonic"] == m), None)


def twin_packet(t_ms: int, alt: float, x: float = 100.0, z: float = -50.0) -> dict:
    return {"event": "telemetry", "device_id": "audit_sender", "timestamp_ms": t_ms, "flight_phase": "STRATOSPHERIC_ASCENT",
            "gnss_coordinates": {"x_downrange_m": x, "y_altitude_m": alt, "z_crossrange_m": z},
            "barometric_sensor": {"pressure_hpa": 500.0, "derived_altitude_m": alt},
            "imu_sensor": {"attitude_rpy_deg": [0.0, 1.0, 0.0]},
            "environmental_sensors": {"ambient_temp_c": -20.0, "effective_wind_vector_mps": [3.0, 0.0, 1.0]},
            "payload_state": {"differential_superpressure_pa": 300.0}}


def tcp_case(api: str, port: int, alts: list[float], t0_ms: int) -> list[dict]:
    out = []
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        for i, a in enumerate(alts):
            body = json.dumps(twin_packet(t0_ms + 500 * i, a)).encode()
            s.sendall(struct.pack("!I", len(body)) + body)
            n = struct.unpack("!I", s.recv(4))[0]
            s.recv(n)
            time.sleep(0.5)
            st = state(api)
            out.append({"sent_alt": a, "GNSS_ALT": chan(st, "GNSS_ALT"), "TANK_P": chan(st, "TANK_P"), "GNSS_VU": chan(st, "GNSS_VU"),
                        "status": next((c["status"] for c in st.get("channels") or [] if c["mnemonic"] == "GNSS_ALT"), None),
                        "met": st.get("met"), "rate_hz": (st.get("link") or {}).get("rate_hz"), "loss_pct": (st.get("link") or {}).get("loss_pct"),
                        "ground": st.get("ground") is not None, "forecast": st.get("forecast") is not None})
    return out


def udp_case(api: str, port: int, alts: list[float], mission: str) -> list[dict]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    prev = icd.genesis_hash(CFG.telemetry.hmac_key_demo, CFG.telemetry.hmac_genesis)
    out = []
    for i, a in enumerate(alts):
        lat, lon = geo.enu_to_latlon(100.0, -50.0, CFG.site.lat_deg, CFG.site.lon_deg)
        ch = {"GNSS_LAT": lat, "GNSS_LON": lon, "GNSS_ALT": a, "NAVIC_LAT": lat, "NAVIC_LON": lon, "NAVIC_ALT": a,
              "BARO_P": 500.0, "AIR_T": -20.0, "TANK_P": 200.0, "TANK_T": 15.0, "MOTOR_T": 20.0, "MOTOR_HTR": 0,
              "IMU_TILT_E": 0.1, "IMU_TILT_N": 0.1, "IMU_RATE_E": 0.0, "IMU_RATE_N": 0.0, "FC_PHASE": "ASCENT", "FC_RKT_PHASE": "ABOARD", "FC_EVENT": ""}
        f = icd.build_frame(CFG.telemetry.hmac_key_demo, mission, i, 100.0 + i, time.time(), 1.0, ch, prev)
        prev = f["hash"]
        sock.sendto(icd.encode(f), ("127.0.0.1", port))
        time.sleep(1.0)
        st = state(api)
        E = (st.get("ground") or {}).get("estimates") or {}
        fc = st.get("forecast") or {}
        out.append({"sent_alt": a, "GNSS_ALT": chan(st, "GNSS_ALT"), "TANK_P": chan(st, "TANK_P"),
                    "fused_alt": (E.get("altitude") or {}).get("value"), "fused_status": (E.get("altitude") or {}).get("status"),
                    "forecast_phys_30": ((fc.get("horizons") or {}).get("30") or {}).get("physics", {}).get("alt"),
                    "forecast_status_30": ((fc.get("horizons") or {}).get("30") or {}).get("status"), "source": st.get("source")})
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8012")
    p.add_argument("--udp", type=int, default=5611)
    p.add_argument("--tcp", type=int, default=65499)
    a = p.parse_args()
    if a.api.endswith(":8000"):
        sys.exit("refusing to perturb the live backend on :8000; start an isolated instance")
    ramp = [1000.0 + 10 * i for i in range(12)]
    const = [5000.0] * 12
    step = [5000.0] * 6 + [8000.0] * 6
    res = {"tcp": {"ramp": tcp_case(a.api, a.tcp, ramp, 0), "constant": tcp_case(a.api, a.tcp, const, 600_000),
                   "step": tcp_case(a.api, a.tcp, step, 1_200_000)}}
    res["udp"] = {"constant": udp_case(a.api, a.udp, const, "audit-const"), "ramp": udp_case(a.api, a.udp, ramp, "audit-ramp")}
    out = ROOT / "data" / "audit" / "perturbation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    t = res["tcp"]
    verdict = {
        "TCP GNSS_ALT follows input (ramp)": all(abs((r["GNSS_ALT"] or 0) - (r["sent_alt"] + 2.0)) < 1.0 or abs((r["GNSS_ALT"] or 0) - r["sent_alt"]) < 1.0 for r in t["ramp"]),
        "TCP TANK_P constant while no tank input": len({r["TANK_P"] for r in t["constant"]}) == 1,
        "TCP TANK_P same across cases with identical (absent) tank input": len({r["TANK_P"] for c in t.values() for r in c}) == 1,
        "TCP GNSS_VU reflects the ramp (+20 m/s)": all(abs((r["GNSS_VU"] or 0) - 20.0) < 2.0 for r in t["ramp"][2:]),
        "TCP link rate_hz measured (not constant 2.0)": len({r["rate_hz"] for c in t.values() for r in c}) > 1,
        "TCP feeds the estimator (ground frame present)": any(r["ground"] for c in t.values() for r in c),
        "TCP feeds the forecast": any(r["forecast"] for c in t.values() for r in c),
        "UDP fused altitude constant for constant input": len({round(r["fused_alt"] or -1, 0) for r in res["udp"]["constant"][4:]}) == 1,
        "UDP forecast responds to the ramp": len({r["forecast_phys_30"] for r in res["udp"]["ramp"]}) > 1,
    }
    for k, v in verdict.items():
        print(f"{'PASS' if v else 'FAIL'}  {k}")
    res["verdict"] = verdict
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
