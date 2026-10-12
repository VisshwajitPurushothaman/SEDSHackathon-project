# AERO-FIRE physics

Every model used by the simulator (truth) and the ground station (estimates), with equations, units, sources, assumptions and the test that checks it. All numeric parameters live in `config/mission.yaml`, each tagged `[source: …]` or `[ASSUMPTION]`. **No parameter is measured on hardware.**

Units are SI unless noted. *z* is geometric altitude and *h* is geopotential altitude.

---

## 1. Atmosphere: `aerofire/physics/atmosphere.py`

### 1.1 US Standard Atmosphere 1976 (USSA76)
Source: NOAA-S/T 76-1562 (1976). Geopotential layers, base values integrated from sea level (288.15 K, 101 325 Pa):

| Layer base *h* (km) | Lapse *L* (K/km) | *T_b* (K) | *p_b* (Pa) |
|---|---|---|---|
| 0 | −6.5 | 288.15 | 101 325 |
| 11 | 0 | 216.65 | 22 632.06 |
| 20 | +1.0 | 216.65 | 5 474.889 |
| 32 | +2.8 | 228.65 | 868.0187 |
| 47 | 0 | 270.65 | 110.9063 |
| 51 | −2.8 | 270.65 | 66.93887 |
| 71 | −2.0 | 214.65 | 3.956420 |

- Gradient layer: *T* = *T_b* + *L*(*h* − *h_b*), *p* = *p_b* (*T_b*/*T*)^(*g₀M*/(*R\*L*)).
- Isothermal layer: *p* = *p_b* exp(−*g₀M*(*h* − *h_b*)/(*R\*T_b*)).
- Density: ρ = *p*/(*R_air T*), *R_air* = 287.053 J/(kg·K).
- *g₀* = 9.806 65 m/s², *R\** = 8.314 32 J/(mol·K), *M* = 0.028 964 4 kg/mol.
- Geopotential conversion: *h* = *r₀z*/(*r₀* + *z*), *r₀* = 6 356 766 m (USSA76 eq. 18).
- Gravity: *g*(*z*) = *g₀*(*r₀*/(*r₀* + *z*))².

The full table to 84.852 km geopotential is implemented because the illustrative rocket reaches about 77 km; the brief only required 0–47 km. Above 84.852 km the last layer continues isothermally (outside USSA76; labelled).

**Tests** (`tests/test_atmosphere.py`): all seven layer bases within 0.5 % (actual agreement ~10⁻⁶); sea-level density 1.225; pressure inverse; geopotential round trip. **Regression:** the removed fallback `z = 44330(1 − (p/p₀)^0.1903)` placed 10 hPa at 25.9 km. USSA76 gives 31.06 km geopotential (31.21 km geometric).

### 1.2 Live profile (Open-Meteo)
- Request: temperature, wind speed (m/s), **wind direction** and geopotential height for 15 pressure levels (1000…10 hPa), `timezone=UTC`, one past and two forecast days.
- **Hour:** the forecast hour nearest *now* (the old code used index 0, which was 15 h stale at audit time).
- **Levels:** only levels with all four values non-null are used, and pressure must fall monotonically with height. The dropped levels are listed in the profile note. Fewer than 3 usable levels raises an error, which leads to a labelled fallback.
- Heights: Open-Meteo returns geopotential metres, converted to geometric.
- **Interpolation:** ln *p*, *T*, *u*, *v* linear in *z* between levels. Below the lowest level: hydrostatic extrapolation at that level's *T*.
- **Above the top level:** ISA offset to match the top level, with the offset faded out linearly over 2 km (`blend_m`). Wind is held at the top-level value (ISA has no wind; labelled).
- **Wind components:** *u* = −*V* sin θ, *v* = −*V* cos θ, where θ is the meteorological "from" direction.
- **Source label** on every sample: `open-meteo@<valid hour>Z`, `blend` or `isa-fallback`. A failed fetch is retried once on HTTP 5xx (we observed a transient 503), then falls back to ISA with zero wind and an explicit note. It never falls back silently.
- `atmo_source` cannot show the model **run** time: the Open-Meteo forecast endpoint does not return it. We show the valid hour plus the fetch time instead.
- A recorded response (`config/atmosphere_snapshot.json`, 2026-10-12 15 Z, Sriharikota area) makes tests and offline demos deterministic.

**Tests:** nearest-hour selection, missing-level handling, ln-p midpoint = geometric mean, blend continuity in *p* and *T* at both blend ends, fetch failure labelled, hydrostatic consistency of the live profile (d*p*/d*z* = −ρ*g* within 5 % from 1 to 30 km).

---

## 2. Zero-pressure balloon: `aerofire/physics/balloon.py`
- Gas: helium, *R_He* = 2077.1 J/(kg·K). **ASSUMPTION:** gas temperature = ambient air temperature (no solar or IR superheat, no lag).
- Volume: *V_free* = *m_gas R_He T/p*, *V* = min(*V_free*, *V_max*).
- **Venting:** if *V_free* > *V_max*, the excess leaves through the base ducts: *m_gas* ← *pV_max/(R_He T)*. Vent rate = removed mass/Δt, reported every step as `balloon_vent_rate_kg_s`.
- Envelope leak (fault): ṁ = *C_d A* √(2 ρ_gas Δ*p*), Δ*p* = 5 Pa (ASSUMPTION).
- Forces:
  - *B* = ρ_air *V g*;
  - *W* = (*m_load* + *m_gas*) *g*;
  - *D* = ½ ρ_air *C_d* π*r*² |*v*| *v*, with *r* = (3*V*/4π)^(1/3) and *C_d* = 0.45 (ASSUMPTION, range 0.3–0.5).
- Equation of motion: (*m_load* + *m_gas* + 0.5 ρ_air *V*) d*v*/d*t* = *B* − *W* − *D* (added mass of a sphere: potential flow). RK4, Δ*t* = 1 s.
- Launch gas: free lift = 10 % of the load (ASSUMPTION), giving (ρ_air − ρ_He)*V₀* = *m_load*(1 + *f*).
- Horizontal: the balloon moves with the local wind (ASSUMPTION, no lag). East/north are converted to lat/lon with WGS84 radii of curvature (`geo.py`).
- **Ascent rate is not prescribed:** it comes out of the force balance. With the default config it averages 4.9 m/s from pad to full volume (3.6 m/s low down, up to about 6.7 m/s high up). The old constant `ascent_rate = 120.0` is gone.

### 2.1 Float and ballast
At float a full balloon carries exactly its load: (ρ_air − ρ_He)*V_max* = *m_load*. **Hand calculation** (in the test), ISA at 28 km geometric (27 877 gpm):
- *T* = 224.53 K;
- *p* = 5474.889 × exp(−1.22007) = 1616.2 Pa;
- (ρ_air − ρ_He)·5600 = (0.025 067 − 0.003 464) × 5600 = 121.0 kg, just above the 120 kg load.

So the float altitude is just above 28 km; the model finds 28.06 km.

Inertia carries the balloon about 600 m past float, and it vents slightly too much gas there. With *T_gas* = *T_air* a partly full balloon has constant lift *m_gas g*(*R_He/R_air* − 1), so after over-venting it sinks indefinitely. This is real zero-pressure behaviour, normally handled with ballast. The simulated vehicle has an **onboard ballast controller** (`sim/float_control.py`, all parameters ASSUMPTION):
- it filters the vertical speed (τ = 120 s, longer than the float bounce);
- when the filtered sink rate exceeds 0.15 m/s, it drops the mass whose weight equals the drag at that sink rate (× 1.2): *m* = 1.2 · ½ρ*C_d A v̄*²/*g*;
- then it waits 300 s.

It is flight control, not a launch action. It holds 27.5–28.2 km for over 2 h on about 2.5 kg of the 10 kg of ballast. (A naive "drop while sinking" rule over-corrected and used all the ballast; the drop must be sized to the drag deficit, which is about 35 g for a 0.4 m/s sink at float.)

**Tests** (`tests/test_balloon.py`): hand-calculated float; a full balloon placed at float stays within 1 m for 10 min; mean ascent rate 4–6 m/s (ISA and live profile); no venting before full volume; **mass conservation** (gas lost = vented + leaked, ballast dropped = ballast lost, to 10⁻⁹ kg); float held inside the release band for 4 h; drift = wind × time.

---

## 3. Pressurant tank: `aerofire/physics/tank.py`
- Tank: 10 L COPV, 200 bar, 26 °C (ASSUMPTION; the original backend used 0.5 m³).
- **Real gas:** virial equation *Z* = 1 + *B₂P*/(*R_u T*), with *B₂*(He) ≈ 11.8 cm³/mol near 300 K (Dymond et al., Landolt-Börnstein IV/21A). Closed form *P* = ρ*RT*/(1 − ρ*B₂/M*). *Z* ≈ 1.09 at 200 bar, so an ideal gas would overstate the helium mass by about 9 %. **Not yet cross-checked against NIST REFPROP.**
- **Orifice flow:** critical ratio *r\** = (2/(γ+1))^(γ/(γ−1)) = 0.487 for γ = 5/3.
  - Choked (*p_amb/P* < *r\**): ṁ = *C_d A P* √(γ/(*ZRT*)) (2/(γ+1))^((γ+1)/(2(γ−1))).
  - Subsonic: ṁ = *C_d A P* √(2γ/((γ−1)*ZRT*) [*r*^(2/γ) − *r*^((γ+1)/γ)]).
  - The choked condition is checked every step, not assumed. *Z* enters as a first-order real-gas correction (approximation; labelled).
- **Thermal**, lumped gas + wall at one temperature (ASSUMPTION): (*m c_v* + *C_wall*) d*T*/d*t* = *UA*(ρ)(*T_amb* − *T*) − ṁ*RT*.
  - *UA*(ρ) = max(*UA_floor*, *UA_sl*(ρ/1.225)^0.5). The exponent 0.5 comes from laminar natural convection, *Nu* ∝ *Ra*^(1/4) with *Ra* ∝ ρ².
  - The −ṁ*RT* term is the adiabatic blowdown. With *C_wall* = *UA* = 0 it reduces exactly to d*T*/d*t* = −(γ−1)*T*ṁ/*m* (tested).

**Tests** (`tests/test_tank.py`):
- critical ratio 0.4871;
- choked flow vs hand value: 50 µm, 200 bar, 299.15 K gives 2.244 × 10⁻⁵ kg/s;
- subsonic/choked continuity at *r\**;
- *P*(*m*, *T*) and *m*(*P*, *T*) are inverses;
- blowdown limit;
- a sealed tank conserves mass and relaxes to ambient;
- leak sizing inverts the flow law.

### 3.1 Ground-side leak detector: `aerofire/ground/leak_detector.py`
The ground sees only *P_meas* = *P* + N(0, 0.2 bar) and *T_meas* = *T* + N(0, 0.3 K) (σ: ASSUMPTION).
1. *m̂* = *P_meas V*/(*Z*(*P_meas*, *T_meas*) *R T_meas*) per sample.
2. Every 30 s: OLS slope *b* of *m̂* over the last 120 s; *se* = *s_res*/√Σ(*t* − *t̄*)².
3. One-sided test *b/se* < −*t*₀.₉₉₉,ₙ₋₂ (α = 0.001).
4. Leak declared after 3 consecutive significant windows.
5. Size: invert the choked-flow law at the window's mean *P*, *T*; 95 % interval from *b* ± 1.96 *se*.

This replaces the "more than 10 g lost" rule, which read the simulator's true mass. On the old setup a 50 µm leak lost 4.6 g and a 70 µm leak 9.1 g, and **both armed the gate**.

**Monte Carlo** (`python -m aerofire.sim.leak_mc`, nominal flight with the recorded atmosphere, leak present from launch, float reached at t = 5625 s):

| Case | Result |
|---|---|
| Sealed, 200 seeds | 0/200 false alarms (95 % upper bound 1.49 %) |
| Sealed, 2000 seeds | 0/2000 false alarms (95 % upper bound **0.15 %**) |
| 20 µm | detected before float 100 %, median 240 s, median size error 8.2 % |
| 50 µm | 100 %, 180 s, 1.7 % |
| 100 µm | 100 %, 180 s, 1.0 % |
| 300 µm | 100 %, 180 s, 8.8 % (the tank drains noticeably within a window) |
| **Minimum leak detected before float in ≥ 90 % of seeds** | **17 µm** |

180 s is the design minimum latency: a 120 s window plus 2 more strides for 3 consecutive windows.

**These results hold only under the model's assumptions:** white Gaussian sensor noise, no sensor bias or drift, gas and wall at the same temperature, and the same equation of state in simulator and estimator. Real performance will be worse; see `docs/LIMITS.md`.

---

## 4. Motor thermal: `aerofire/physics/motor_thermal.py`
- *C* d*T*/d*t* = −*UA*(ρ)(*T* − *T_air*) − (εA)σ(*T*⁴ − *T_sky*⁴) + *Q_heater*.
- *UA*(ρ) = *UA_sl*(ρ/1.225)^0.5.
- RK2 integration. All parameters are ASSUMPTIONS:
  - *C* = 8000 J/K;
  - εA = 0.12 m² (insulation blanket);
  - *T_sky* = 220 K;
  - 80 W heater, thermostat at 10 °C with 1 K hysteresis;
  - no solar input (night).
- The old `max(12.0, …)` floor is removed (it was non-physical).
- Gate: *T* ≥ −10 °C (qualified minimum, ASSUMPTION).
- Calibrated consistency on the nominal flight: heater on, 10.1 °C at float; heater failed, −15.2 °C at float. The "motor too cold" scenario therefore fails the gate because of physics, not scripting.

**Tests:** pure convection gives *e*⁻¹ after one time constant; pure-radiation initial rate; no floor; thermostat hysteresis; nominal motor above the limit and failed heater below it.

---

## 5. Gondola swing: `aerofire/physics/pendulum.py`
- One damped pendulum per horizontal axis: θ'' + 2ζωθ' + ω² sin θ = −*a_s*(*t*) cos θ/*L*, with ω = √(*g/L*).
- *L* = 15 m and ζ = 0.02 (ASSUMPTION). RK4 with 10 substeps per step.
- *a_s* = horizontal acceleration of the suspension point = wind change seen by the drifting balloon (shear × climb rate) + white gust noise with σ = 0.02 m/s² (ASSUMPTION).
- **Gate on the amplitude:** *A* = √(θ² + (θ'/ω)²), with both axes combined, ≤ 1.5°. A pendulum passes θ = 0 at its maximum rate, so a test on the instantaneous angle "passes" at exactly the worst moment.
- The ground computes *A* from IMU tilt and tilt rate (median over one period).

**Tests:** small-amplitude period = 2π√(*L/g*) = 7.7708 s to 10⁻⁴; damping decay e^(−ζω*t*); at the zero crossing the angle reads < 0.05° while *A* = 3.0°; wind shear excites the swing.

---

## 6. Launch-commit gates: `aerofire/physics/gates.py`
The ground station evaluates these on **estimates** and returns a recommendation: `GO_RECOMMENDED`, `NO_GO` or `ABORT_RECOMMENDED`. The software never confirms ignition: `IGNITION_CONFIRMED` is gone, and only the operator's commit leads to ignition.

1. **Float:** altitude in the release band (26–30 km) and |mean *v_z*| < 1 m/s, where the mean is taken over 60 s of Kalman-fused vertical speed. A floating zero-pressure balloon bounces by about ±1 m/s; testing the instantaneous value made GO flicker on and off every few seconds, which an operator cannot act on.
2. **Navigation integrity:** chi-square at 99.9 % (13.816 for 2 dof horizontal GNSS−NavIC; 10.828 for 1 dof vertical GNSS−baro). The baro altitude is the measured pressure inverted through the forecast profile, with σ_model = 30 m (ASSUMPTION).
   - It is tested on the **mean residual over the last 10 epochs**: *d²* = *N* |*r̄*|²/σ².
   - Reason: a single-epoch 99.9 % test fires on about 0.2 % of epochs by construction, which is roughly ten false alarms per flight. The first implementation did exactly that, and the false alarm then masked the real spoofing alert.
   - Alerts need **20 s** of persistent failure and re-arm after 30 s of recovery. Because consecutive 10-epoch windows overlap, short exceedances are strongly correlated. Measured on pure noise (400 simulated 2.8 h missions): a 5 s persistence raises a false alert in 35 % of missions, 10 s in 0.8 %, 20 s in 0/400 (95 % upper bound 0.75 %). The gate itself (which only blocks GO) still uses every evaluation.
   - GNSS spoofing (a fix drifting at 2 m/s) blocks the gate within seconds and raises the alert about 30 s after onset in the scenario test.
3. **Motor temperature** ≥ −10 °C.
4. **Swing amplitude** ≤ 1.5°.
5. **Impact safety:** instantaneous impact points for nominal flight, early burnout at 30 % and 60 %, and no ignition must all lie inside the approved polygon (illustrative, not an official range). Recomputed every 30 s at float.

**Abort criterion** (in addition to the five): no pressurant leak declared. The original backend aborted on a leak; the brief's five gates don't cover it, so it is kept as a separate criterion.

All five gates must pass continuously for 10 s before `GO_RECOMMENDED`.

---

## 7. Rocket flight: `aerofire/physics/rocket.py` (ILLUSTRATIVE MODEL, NOT VALIDATED)
- 2-D point mass in the vertical plane of the rail azimuth.
- Thrust *F* = ṁ*I_sp g₀* (constant), d*m*/d*t* = −*F*/(*I_sp g₀*).
- Drag ½ρ*v*²*C_d A* (constant *C_d*, no Mach dependence); gravity *g*(*z*).
- Thrust acts along the rail while on it (80° elevation), then along the velocity vector (gravity turn).
- The flight is computed **air-relative**; the ground track adds the release-altitude wind × flight time. The first version used the balloon drift as an inertial start velocity, which tipped the gravity turn upright and landed the rocket 3.6 km *behind* the release point.
- Parameters (all ASSUMPTION): 15 kg dry, 10 kg propellant, *I_sp* 210 s, 8 s burn, Ø 0.12 m, *C_d* 0.4.
- Outputs: apogee, range, ascent max-Q, re-entry max-Q, impact speed. Nominal from 28 km: apogee about 77 km, range about 52 km, ascent max-Q about 6.9 kPa.

**Tests:** burnout speed matches the rocket equation (Δ*v* = *I_sp g₀* ln(*m₀/m_f*) − *g t_b*) within 1 % with drag off; failure cases land shorter; impact gate pass/fail.

## 8. Descent: `aerofire/physics/descent.py`
- *v_t*(*z*) = √(2*mg*/(ρ*C_d A*)), quasi-steady at every altitude.
- Round canopy, *C_d* 1.5, Ø 6 m (ASSUMPTION), assumed open at cut-down (inflation transient ignored).
- Horizontal motion follows the wind.
- A 47.5 kg gondola from 28 km falls at 29 m/s at release and 4.3 m/s at sea level, taking about 54 min.

**Tests:** *v_t* formula; lands at ground level; drift = wind × time.

---

## 9. Truth/estimate separation
- `aerofire/sim` (truth) integrates the models above.
- `aerofire/telemetry/sensors.py` is the only bridge: it adds noise and faults.
- `aerofire/ground` builds every number from records alone.
- `tests/test_firewall.py` parses imports with `ast` and fails if `aerofire.ground` imports `aerofire.sim` (or if physics imports sim, ground or api). The checker is itself tested on synthetic violations.

---

## 10. Telemetry chain (Milestone 2)
1. **Sensors** (`telemetry/sensors.py`) add Gaussian noise to truth. All σ values are ASSUMPTIONS from config `sensors`.
2. **Onboard recorder** (`telemetry/recorder.py`): every record gets `seq` and is stored *before* it is transmitted.
   - Chain: mac_n = HMAC-SHA256(key, mac_(n−1) ‖ canonical_json(record_n)), with mac_(−1) = HMAC(key, genesis).
   - Canonical JSON uses sorted keys and excludes `mac` and the ground's receiver metadata `rx`.
   - The key in config is a **demo key, not secure**.
   - Tests: editing, deleting, reordering, forging a MAC or using another key are each detected at the first affected seq.
3. **Link emulator** (`telemetry/link.py`, world side):
   - blackout windows drop everything in them;
   - independent loss *p*;
   - latency N(μ, σ) truncated at 0;
   - RSSI = *P_tx* + *G* − FSPL(*d*) + N(0, σ), with FSPL = 20 log₁₀ *d*_km + 20 log₁₀ *f*_MHz + 32.44.
   - Tests: blackout exactness, loss rate, mean latency, 100× range = 40 dB.
4. **Ground** receives only what the link delivers, re-orders by event time (1 s buffer), and fuses.

## 11. Ground position estimator (`ground/estimator.py`)
- Constant-velocity Kalman filter in local ENU: state [*e, n, u, v_e, v_n, v_u*], white-acceleration process noise.
  - *q* = 0.05 (m/s²)²·s while ascending or floating, 20 after cut-down. The phase comes from the vehicle's flight-computer record. A single small *q* could not follow the parachute deceleration and excluded every sensor.
- Measurements:
  - GNSS and NavIC (3-D, σ from config);
  - barometric altitude: the measured pressure inverted through the forecast profile, σ_model 30 m.
- **Gating:** innovation NIS against χ²₀.₉₉₉ (3 dof for fixes, 1 dof for baro).
- **Divergence:** if both fixes fail against the filter but agree with each other, the *filter* is wrong. It is re-initialised on their weighted mean and no sensor is penalised.
- **Fault attribution** (two horizontal sources):
  - *Step* fault: isolated by jump detection. One sensor's epoch-to-epoch change is inconsistent with the filter velocity (χ²₃ on *z_t* − *z_(t−1)* − *v*Δ*t*) while the other's is not. That sensor goes through the trust ladder (ACCEPTED → SUSPECT after 2 failures → EXCLUDED after 5 → RE-TRUSTED after 30 agreeing epochs).
  - *Slow drift* (spoofer walking the fix away): **detectable but not isolatable** with two sources. It is absorbed by the velocity state, so nothing observable says which sensor is lying. After 10 epochs of pairwise disagreement both become SUSPECT; both are fused with variance inflated by the squared half-disagreement and **floored** at that value, so the reported uncertainty covers both sensors (repeated updates would otherwise shrink it, because the error is systematic, not noise).
  - Tested on a fault matrix of 0.5 and 2 m/s drift, 40 and 300 m steps, and no fault, over 3 seeds each:
    - steps: GNSS excluded, error ≤ 7.4 m, then re-trusted;
    - drifts: both SUSPECT, no wrong exclusion, truth inside 3σ;
    - no fault: nothing flagged.
  - The first two attribution designs (gate against the live filter; gate against a "clean" reference) both **excluded the honest NavIC** during a slow GNSS drift. They are recorded here because that failure mode is the reason for the current design.
- **RTS smoother** (`rts_smooth`): the same filter run forward over all recovered records, then the Rauch–Tung–Striebel backward pass; phase-aware. On a nominal flight with a 10-minute blackout its error against truth is 0.9 m median horizontal (3.7 m p99) and 0.8 m vertical (4.4 m p99).

## 12. Blackout prediction (`ground/predictor.py`)
- When no fix has been fused for 5 s (`estimator.stale_after_s`), position, altitude and vertical speed switch to **PREDICTED** from a particle ensemble (300 particles). Every other stale value becomes **UNKNOWN**, with its last value and age shown, never as a live value.
- Particles start from the Kalman state and covariance at the last fix. Each carries a persistent forecast-wind error (σ 3 m/s per component) and a climb-rate error (σ 0.5 m/s), all ASSUMPTIONS.
- Propagation follows the last known phase:
  - ascent towards the model float altitude, which the ground computes from the vehicle design and the forecast;
  - float held;
  - descent at parachute terminal velocity.
  - Horizontal motion is forecast wind plus the particle's error. The forecast is tabulated every 50 m, so the ensemble propagates with numpy.
- Regions: Gaussian fit, ellipses at 50/90/99 % (χ²₂ = 1.386, 4.605, 9.210). Calibration test: on Gaussian clouds the achieved coverage is within 2.5 % of the claim at every level.
- Landing footprint: 100 particles descend under the parachute from the current state ("if cut down now" before cut-down; actual during descent).
- At reconnect the ground scores its own prediction (error to the first fix, inside 50/90/99 %).
- Example: a 601 s blackout during descent. Prediction error at reconnect was 115 m (ground-measured) against 112 m from truth, and truth was inside all regions at all 20 snapshots. That 100 % coverage at the 50 % level suggests the regions are conservative, mainly because the simulator's truth wind equals the forecast (see LIMITS).

## 13. Recovery and post-flight (`ground/recovery.py`, `ground/postflight.py`)
- Recovery downloads every record that did not arrive live, in two stages:
  1. every 10th missing record first, in batches of 200 (a rough picture);
  2. the rest, in batches of 200.
- A failed batch is retried up to 3 times (5 % failure probability per transfer, ASSUMPTION); otherwise its records are listed as lost.
- Every record is HMAC-verified against its predecessor, then the whole chain is verified from genesis.
- Post-flight requirements (config `requirements`) evaluate to PASS / FAIL / NOT TESTED / INCONCLUSIVE with evidence. They are computed **without truth**, from the reconstruction and the recovered records. The gate audit (REQ-GATE-1) uses exactly the live gate definitions; an earlier version checked the instantaneous rather than the mean vertical speed and reported a false violation.
- The grading scorecard (`evaluation/scorecard.py`) is the only code that compares with truth. It is served only when `api.truth_enabled`.
