# AERO-FIRE verification

## Milestone 4 continuation (12–13 October 2026)

Started on `feature/full-mission` at `baa88e5`. The worktree and index were clean.
Claude's original five-milestone brief was recovered from his local project session;
the Post-flight implementation in that commit was retained.

### Reproduced defect and correction

- Existing mission `52f89df840c7` returned HTTP 500 without a CORS header from
  `/postflight`; `3b9d95828a9c` returned HTTP 200 with `Access-Control-Allow-Origin: *`.
- A ground-only reproducer raised `TypeError: unsupported operand type(s) for -:
  'float' and 'list'` at `ground/postflight.py:193`. The IMU sample window overwrote
  the float gate's window duration after the first GO epoch. Renaming that local
  variable fixes the exception without changing any model, limit or endpoint.
- A real HTTP snapshot mission with delayed operator commit checked ten GO epochs
  and returned HTTP 200 with CORS. Its cold reconstruction took 4.72 s.
- Fixed stale error/recovery/grading state on mission selection, guarded late
  responses, refreshed running mission summaries, and caught recovery poll errors.
- Undetected injected faults remain hidden until grading. JSON exports redact
  injection settings from the mission, fault rows and creation-event copy; the
  internal append-only log remains intact.
- The downloaded JSON initially failed its canonical report hash because browser
  reserialization changed numeric representations. Downloading the original
  response text preserves the hash. The browser test verifies the actual file
  with Python's canonicalization, rather than merely checking a 64-character label.

### Executed checks

| Command / check | Result |
|---|---|
| Baseline `python -m pytest -q` | 154 passed |
| Baseline `npm test` | 31 passed |
| Baseline `npm run typecheck` | passed |
| Affected API, firewall and new requirement tests | 51 passed, then 2 added grading/export cases passed |
| Final `python -m pytest -ra` | 160 passed; 1 dependency deprecation warning; 109.92 s |
| Final `npm test` | 31 passed |
| Final `npm run typecheck` | passed |
| `npm run test:browser -- postflight.spec.mjs` | 11 passed in 47.7 s |
| Post-flight screenshot at 1440 × 900 | reviewed: map, histories, chart, recovery, requirements, grading, action and closure visible |
| `git diff --check` | passed |

The eleven browser checks cover requirements before recovery; complete recovery
and HMAC verification; rough-picture progress; requirement refresh; hidden
injections; grading scorecard; revealed faults/third history; saved corrective
action; closure; downloaded JSON/hash/truth redaction; and zero browser/API errors.
They use a real backend and recorded atmosphere, without mocking mission results.

A browser automation download wait timed out once. The download itself had
completed; filesystem inspection distinguished the tool timeout from an application
failure. The repository test subsequently completed normally.

### Requirement outcomes are evidence, not test assertions

After recovery, the blackout mission passed prediction coverage, landing and both
data requirements. The extended GO audit reported a float-window mean of
−1.037 m/s at one GO epoch against the configured 1 m/s limit. Its FAIL remains
visible. The live filter and backward smoother can differ near a threshold;
neither the physical limit nor the requirement expectation was relaxed.

The first recovered mission held 79,622 records, with 6,152 recovered, zero lost,
two retries and an end-to-end verified chain. The subsequent automated run held
80,174 records: polling the live operator API changes commit timing slightly,
so record totals need not match between runs.

## Milestone 5

Final documentation, preset/offline regression and performance results are recorded
here when their checks finish.
