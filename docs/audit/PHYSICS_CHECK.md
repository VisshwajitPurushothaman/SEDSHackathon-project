# Physics spot-check (audit A5, 13 Oct 2026)

`python tools/audit/physics_check.py` (model vs hand values / tables):

| check | model | hand / table | rel. tol | result | reference |
|---|---|---|---|---|---|
| ISA p @ 11 km (geopotential) | 22632.1 | 22632.1 | 0.0001 | PASS | USSA-1976 table |
| ISA T @ 11 km (geopotential) | 216.65 | 216.65 | 1e-05 | PASS | USSA-1976 table |
| ISA p @ 20 km (geopotential) | 5474.89 | 5474.89 | 0.0001 | PASS | USSA-1976 table |
| ISA T @ 20 km (geopotential) | 216.65 | 216.65 | 1e-05 | PASS | USSA-1976 table |
| ISA p @ 32 km (geopotential) | 868.019 | 868.019 | 0.0001 | PASS | USSA-1976 table |
| ISA T @ 32 km (geopotential) | 228.65 | 228.65 | 1e-05 | PASS | USSA-1976 table |
| terminal speed 10 kg, Cd 1.5, D 3 m, sea level | 3.88594 | 3.88594 | 0.001 | PASS | hand formula |
| choked He flow, 100 um, 10 bar, ideal | 4.53193e-06 | 4.53193e-06 | 1e-06 | PASS | isentropic nozzle formula |
| pendulum period L = 15.0 m | 7.77079 | 7.77079 | 1e-09 | PASS | 2 pi sqrt(L/g) |
| float equilibrium at z = 28093 m: net lift (kg) / load | 1 | 1 | 0.0001 | PASS | Archimedes |

## Physics test suite

    =========================== short test summary info ===========================
    FAILED tests/test_gates_rocket_descent.py::test_impact_points_and_gate - Asse...

The single failure is `test_impact_points_and_gate`: physics is unchanged, but `87458be` moved the launch site to 10.3015 N while `gates.approved_area` stays at 13.0-14.6 N, so every impact point is outside the approved area. Configuration drift, not a model error. It also means GO can never be recommended on the ICD path until the area/zones are moved with the site.

## Units across ICD, receiver and UI

- ICD path: BARO_P hPa, TANK_P bar, temperatures C on the wire; converted to SI for the estimator and back for display; round trip tested (tests/test_icd_receiver.py).
- TCP path: pressure_hpa -> BARO_P hPa (consistent); differential_superpressure_pa / 100 -> GAUGE_P hPa (consistent); lat/lon from x/z with fixed metres per degree (110 609.6 / 109 531.0 at 10.3 N: within 0.01 % of WGS-84); **GNSS_VU uses a wind component (wrong quantity)**; **IMU_TILT is the pitch angle (about 90 deg at rest)**; TANK_P has no physical source.
