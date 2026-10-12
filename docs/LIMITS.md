# AERO-FIRE limitations

This is a software simulation and an engineering demonstration, not a validated
flight system. No hardware measurements, qualification tests, real sensor logs,
RF trials, range approvals or hardware actuation were used. Numeric assumptions
remain explicitly tagged in `config/mission.yaml`; no constants were changed to
make demonstrations look better.

## Physics and estimation

- The balloon is zero-pressure, with helium at ambient temperature. Solar/IR
  heating, gas/wall temperature lag, envelope deformation and horizontal inertia
  are omitted. Its float-keeping ballast controller is an assumed onboard model.
- Tank gas/wall temperature is lumped. The second-virial helium approximation
  and first-order real-gas flow correction have not been cross-checked against
  NIST REFPROP or a real COPV. Thermal coefficients and puncture discharge
  coefficients are assumptions.
- Motor thermal behavior assumes night, no sunlight and an ideal thermostat.
  The −10 °C qualified minimum is an assumption, not a certified motor limit.
- The gondola uses two damped pendulums with assumed white gust forcing.
- The rocket is an **illustrative model, not validated**: 2-D point mass,
  constant thrust and drag coefficient, uniform release-altitude wind, flat
  Earth apart from altitude-dependent gravity, no rotation or structural loads.
  Its shape/path in the console is illustrative and is labelled accordingly.
- Parachute opening is instantaneous; canopy inflation, failure, reefing and
  aerodynamic variability are not modeled. Prediction uses quasi-steady descent
  and assumed gondola/remaining-ballast mass; truth retains descent inertia.
- Sensor noise is independent white Gaussian noise. Bias, drift, correlation,
  calibration errors, damaged sensors and real RF propagation are not validated.
  Detector Monte Carlo numbers in PHYSICS.md apply only to those assumptions;
  a low modeled false-alarm rate is not a field reliability claim.
- Slow GNSS/NavIC disagreement is detectable but cannot be attributed with only
  two horizontal sources. Both can be SUSPECT; the uncertainty widens to cover
  the disagreement. No extra truth feed or ML classifier is used to choose one.
- Truth wind and forecast wind share the simulated profile. Particle regions can
  therefore be conservative: the recorded blackout example achieved 100 %
  coverage even for the claimed 50 % region. Snapshots from one blackout are
  correlated and do not establish independent statistical calibration.
- The local tangent-plane conversion, assumed approved polygon and link budget
  are illustrative. They are not geographic range clearance or a radio license.

## Post-flight evidence

- Requirements use ground-held live/recovered telemetry, not truth. Before
  recovery, gaps can produce incomplete or misleading checks. Recovery must be
  complete before assessing data completeness and chain integrity.
- Reconstruction runs the existing RTS smoother and emits a 5 s sampled track.
  The live filter and backward smoother differ; sampled-window means can cross a
  threshold even when the live recommendation passed. The extended GO example
  reported −1.037 m/s against the unchanged 1 m/s limit and remains FAIL.
- REQ-GATE-1 reprocesses float, motor and swing evidence at sampled GO epochs;
  navigation and impact are retained as live evidence, rather than independently
  re-certified. An absent GO produces NOT TESTED, not PASS.
- Blackout coverage uses reconstructed position. Ground truth comparison belongs
  to the explicitly labelled grading scorecard. Small/correlated samples and
  missing landing fixes can yield NOT TESTED or INCONCLUSIVE.
- The two-stage recovery has assumed independent transfer failures and retries.
  Failed batches can remain lost; a chain with gaps cannot be verified end to end.
  The UI does not transform these outcomes into success.
- JSON's report SHA-256 detects report changes; it is not a signature or a trust
  certificate. Onboard HMAC uses a clearly labelled public demo key and provides
  no production security. Receiver metadata is deliberately outside that MAC.
- Normal JSON exports exclude grading tracks, scorecards and injected settings.
  CSV contains held sensor records, not an evaluator truth trajectory. Original
  numeric representations in the downloaded JSON must be preserved when checking
  Python's canonical hash; cross-language canonical-number formatting is not a
  separately implemented standard.

## Runtime, offline and operator scope

- Mission runners, onboard records and telemetry history are held **in memory**.
  Restarting the backend loses them. Export before stopping. There is no database,
  persistent mission import, multi-worker coordination or retention policy.
- Recovery is simulated after a mission finishes; clicking it is not evidence
  that a real payload was retrieved. Closure entries are operator attestations.
- The API is local, unauthenticated and has open CORS without credentials.
  `api.truth_enabled` is a demonstration/evaluator configuration gate, not user
  authentication. The UI reveal toggle controls visibility, not access control.
  Grading configuration and scenario setup are not secrets in a local evaluator
  deployment. This has not been tested as a public or multi-user service.
- High time scales shorten real-time GO windows. The 1.5 s hold is unchanged and
  the backend rechecks current gates. A withdrawn recommendation may refuse the
  command; no scenario forces ignition to make a demonstration succeed.
- Pre-flight hold-to-commit is the preserved frontend-only animation. Flight
  commit/hold/terminate call the simulated operator API. No real hardware commands
  exist. Without an explicit operator completion a mission can reach its six-hour
  horizon rather than land.
- Backend-down fixture playback supports the existing Pre-flight Sealed and
  100 µm recordings only. Flight/Post-flight require mission records. The recorded
  atmosphere server supports deterministic full missions without weather access;
  it does not turn backend-down mode into invented flight telemetry.
- Open-Meteo valid/fetch times are available, model run time is not. Forecast
  errors are not field validated. Weather failure is a labelled ISA/zero-wind
  fallback. External map tiles, fonts, labels and HDR can fail independently of
  local telemetry; there is no fully cached offline base map or installable PWA.
- The existing old CLI scratch file and tracked bytecode were preserved. They
  are historical artifacts, not the current execution path.

## Verification scope and dependencies

See [VERIFICATION.md](VERIFICATION.md) for exact executed checks. Windows, Chrome
and Intel UHD were exercised locally. Linux runner syntax is supplied, but a
Linux host, Firefox, Safari, touch devices, real radio links, prolonged multi-hour
wall-clock operation, many concurrent missions and production deployment were
not tested. Browser inspections target Vite's dev module graph.

The Flight 3D view reached about 60 fps in a two-second sample on this Intel UHD
laptop, with DPR 1. That is a measurement of this view/workload, not a guarantee
for all scenes, display scales or devices. Cold reconstruction and large JSON
downloads take seconds; long horizon missions grow in memory. Hidden-scene and
reduced-motion camera behavior have dedicated browser regressions.

`npm audit` on the preserved runtime/toolchain reported 14 advisories (5 moderate,
6 high, 3 critical). Direct affected packages included MapLibre, Tailwind, Vite
and Vitest; suggested upgrades span major versions. Forced framework/visual-stack
upgrades were not performed as part of this strict continuation. Keep this demo
local; reassess those advisories before any public deployment. The Python test
client also reports an upstream Starlette/httpx deprecation warning; tests still
pass. No push or deployment was performed.
