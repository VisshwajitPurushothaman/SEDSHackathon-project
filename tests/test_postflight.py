"""Post-flight requirement regressions, using ground telemetry only."""
import pytest

from aerofire.config import load_config
from aerofire.ground.postflight import verify_requirements


@pytest.mark.parametrize("bad_gate", [None, "float", "motor", "swing"])
def test_gate_audit_checks_multiple_go_epochs(bad_gate):
    """The IMU sample window must not replace the float window's duration.

    A second GO epoch used to raise TypeError instead of producing evidence.
    Violations must still be reported rather than hidden by fixing the crash.
    """
    times = [100.0, 110.0, 120.0]
    ctx = {
        "recon": [{"t": t, "u": 28000.0, "vu": 0.0} for t in times],
        "leak": {"available": False}, "live": {}, "recovery": None,
        "prediction_log": [], "footprint_at_cutdown": None, "landing_fix": None,
        "go_times": times,
        "records": [
            {"sensor_id": sid, "event_time": t, "values": values}
            for t in times
            for sid, values in (
                ("motor", {"T": 283.15}),
                ("imu", {"tilt_e": 0.2, "tilt_n": 0.0, "rate_e": 0.0, "rate_n": 0.0}),
            )
        ],
    }
    if bad_gate == "float":
        ctx["recon"][-1]["u"] = 31000.0
    elif bad_gate == "motor":
        ctx["records"][-2]["values"]["T"] = 253.15
    elif bad_gate == "swing":
        for r in ctx["records"]:
            if r["sensor_id"] == "imu":
                r["values"]["tilt_e"] = 3.0

    gate = next(r for r in verify_requirements(load_config(), ctx) if r["id"] == "REQ-GATE-1")
    assert gate["evidence"]["go_epochs_checked"] == 3
    assert gate["status"] == ("FAIL" if bad_gate else "PASS")
    assert {v["gate"] for v in gate["evidence"]["violations"]} == ({bad_gate} if bad_gate else set())
