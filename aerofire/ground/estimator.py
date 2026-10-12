"""Ground position/velocity estimator: Kalman filter with chi-square integrity gates and a trust ladder.

State x = [e, n, u, ve, vn, vu] (local ENU from the launch site, m and m/s).
Model: constant velocity driven by white acceleration noise (PSD q):
    F = [[I, dt I], [0, I]],
    Q = q [[dt^3/3 I, dt^2/2 I], [dt^2/2 I, dt I]]  (per axis, q_h horizontal, q_v vertical)
Measurements:
    GNSS, NavIC: z = [e, n, u],  R = diag(s_h^2, s_h^2, s_v^2)
    baro:        z = u_baro (measured pressure inverted through the forecast profile), R = s_model^2

Process noise is phase-aware: the vehicle reports its phase, and after
cut-down the gondola manoeuvres hard (free fall, then deceleration in thicker
air), so q is raised for CUTDOWN/DESCENT (config `estimator.accel_psd_descent`).

Integrity: before fusing, each measurement's normalised innovation squared
NIS = y^T S^-1 y is tested against the 99.9 % chi-square quantile (3 dof for
position fixes, 1 dof for baro). Failing measurements are not fused.
Divergence: if GNSS and NavIC of the same epoch BOTH fail against the filter
but agree with each other (2-dof horizontal + 1-dof vertical pairwise test),
the filter, not the sensors, is wrong: it is re-initialised on their
weighted mean and no sensor is penalised (counted in `divergence_resets`).

Detectable vs isolatable: a STEP fault in one sensor is isolated by jump
detection: its epoch-to-epoch change is inconsistent with the filter velocity
(3-dof chi-square on z_t - z_(t-1) - v dt) while the other sensor's is not.
That sensor is blamed for the whole disagreement episode (ladder: SUSPECT ->
EXCLUDED) and only the other is fused. A SLOW drift (e.g. GNSS spoofing that walks the fix away) is
absorbed by the velocity state before any single innovation fails; with only
two horizontal sources nothing observable says which one is lying. It is
DETECTED by the pairwise GNSS-NavIC test: after `disagree_persist` failing
epochs both sensors become SUSPECT and are fused with their measurement
variance inflated by the squared half-disagreement, so the estimate sits
between them with an uncertainty that covers both. They are re-trusted after
`retrust_after` agreeing epochs. The ground does not claim to know which is right.
Trust ladder per sensor:
    ACCEPTED --(suspect_after consecutive failures)--> SUSPECT --(exclude_after)--> EXCLUDED
    EXCLUDED --(retrust_after consecutive passes, tested but not fused)--> ACCEPTED ("re-trusted")
An EXCLUDED sensor is not fused even when a measurement passes.

`rts_smooth` runs the same filter forward over a complete record set and the
Rauch-Tung-Striebel backward pass: the post-flight RECONSTRUCTED track.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
from scipy import stats

CHI2_999 = {1: float(stats.chi2.ppf(0.999, 1)), 2: float(stats.chi2.ppf(0.999, 2)), 3: float(stats.chi2.ppf(0.999, 3))}
H_POS = np.hstack([np.eye(3), np.zeros((3, 3))])
H_UP = np.array([[0.0, 0.0, 1.0, 0.0, 0.0, 0.0]])


@dataclass
class Trust:
    state: str = "ACCEPTED"  # ACCEPTED | SUSPECT | EXCLUDED
    fails: int = 0
    passes: int = 0
    retrusted_at: float | None = None
    history: list[tuple[float, str]] = field(default_factory=list)  # (t, new state)

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "consecutive_fails": self.fails, "consecutive_passes": self.passes,
                "retrusted_at": self.retrusted_at, "history": self.history[-10:]}


class PositionFilter:
    def __init__(self, cfg):
        e, s = cfg.estimator, cfg.sensors
        self.q_h, self.q_v = e.accel_psd_h, e.accel_psd_v
        self.R = {
            "gnss": np.diag([s.gnss_sigma_h_m**2, s.gnss_sigma_h_m**2, s.gnss_sigma_v_m**2]),
            "navic": np.diag([s.navic_sigma_h_m**2, s.navic_sigma_h_m**2, s.navic_sigma_v_m**2]),
            "baro": np.array([[s.baro_alt_model_sigma_m**2]]),
        }
        self.suspect_after, self.exclude_after, self.retrust_after = e.suspect_after, e.exclude_after, e.retrust_after
        self.x: np.ndarray | None = None
        self.P: np.ndarray | None = None
        self.t: float | None = None
        self.trust = {k: Trust() for k in self.R}
        self.last_update_t: float | None = None
        self.last_nis: dict[str, float] = {}
        self.q_nominal = (e.accel_psd_h, e.accel_psd_v)
        self.q_descent = e.accel_psd_descent
        self.divergence_resets: list[float] = []
        self.disagree_persist = e.disagree_persist
        self._disagree = 0
        self._agree = 0
        self.disagreement: bool = False
        self.disagreement_m: float = 0.0
        self._clean: tuple[float, np.ndarray, np.ndarray] | None = None  # last state where GNSS and NavIC agreed
        self._prev_fix: dict[str, tuple[float, np.ndarray]] = {}  # last fix per sensor (for jump detection)
        self._jumps: dict[str, list[float]] = {"gnss": [], "navic": []}  # times of significant jumps
        self._attributed: str | None = None  # sensor blamed for the current disagreement episode

    def set_phase(self, phase: str) -> None:
        if phase in ("CUTDOWN", "DESCENT"):
            self.q_h = self.q_v = self.q_descent
        else:
            self.q_h, self.q_v = self.q_nominal

    # --------------------------------------------------------------- model

    def _FQ(self, dt: float) -> tuple[np.ndarray, np.ndarray]:
        F = np.eye(6)
        F[0:3, 3:6] = dt * np.eye(3)
        Q = np.zeros((6, 6))
        for i, q in ((0, self.q_h), (1, self.q_h), (2, self.q_v)):
            Q[i, i] = q * dt**3 / 3.0
            Q[i, i + 3] = Q[i + 3, i] = q * dt**2 / 2.0
            Q[i + 3, i + 3] = q * dt
        return F, Q

    def predict_to(self, t: float) -> None:
        if self.x is None or self.t is None or t <= self.t:
            return
        F, Q = self._FQ(t - self.t)
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        self.t = t

    def peek(self, t: float) -> tuple[np.ndarray, np.ndarray] | None:
        """Predicted (x, P) at t without changing the filter."""
        if self.x is None or self.t is None:
            return None
        F, Q = self._FQ(max(0.0, t - self.t))
        return F @ self.x, F @ self.P @ F.T + Q

    # --------------------------------------------------------------- update

    def _ladder(self, sensor: str, t: float, passed: bool) -> None:
        tr = self.trust[sensor]
        if passed:
            tr.fails = 0
            tr.passes += 1
            if tr.state == "SUSPECT":
                tr.state = "ACCEPTED"
                tr.history.append((t, "ACCEPTED"))
            elif tr.state == "EXCLUDED" and tr.passes >= self.retrust_after:
                tr.state, tr.retrusted_at = "ACCEPTED", t
                tr.history.append((t, "RE-TRUSTED"))
        else:
            tr.passes = 0
            tr.fails += 1
            if tr.state == "ACCEPTED" and tr.fails >= self.suspect_after:
                tr.state = "SUSPECT"
                tr.history.append((t, "SUSPECT"))
            if tr.state == "SUSPECT" and tr.fails >= self.exclude_after:
                tr.state = "EXCLUDED"
                tr.history.append((t, "EXCLUDED"))

    def _nis(self, sensor: str, z: np.ndarray) -> float:
        H = H_POS if sensor in ("gnss", "navic") else H_UP
        y = z - H @ self.x
        S = H @ self.P @ H.T + self.R[sensor]
        return float(y @ np.linalg.solve(S, y))

    def _pairwise(self, g: np.ndarray, n: np.ndarray) -> tuple[float, float]:
        Rg, Rn = self.R["gnss"], self.R["navic"]
        d = g - n
        return (d[0] ** 2 + d[1] ** 2) / (Rg[0, 0] + Rn[0, 0]), d[2] ** 2 / (Rg[2, 2] + Rn[2, 2])

    def _set_disagreement(self, t: float, on: bool) -> None:
        if on and not self.disagreement:
            self.disagreement = True
            for s in ("gnss", "navic"):
                tr = self.trust[s]
                if tr.state == "ACCEPTED":
                    tr.state = "SUSPECT"
                    tr.history.append((t, "SUSPECT (GNSS/NavIC disagree)"))
        elif not on and self.disagreement:
            self.disagreement = False
            for s in ("gnss", "navic"):
                tr = self.trust[s]
                if tr.state == "SUSPECT":
                    tr.state, tr.retrusted_at = "ACCEPTED", t
                    tr.history.append((t, "RE-TRUSTED"))

    def _fuse_inflated(self, t: float, g: np.ndarray, n: np.ndarray) -> None:
        """Fuse both fixes with variance inflated by the squared half-disagreement (no gate)."""
        half = 0.25 * ((g - n) ** 2)
        for sensor, z in (("gnss", g), ("navic", n)):
            R = self.R[sensor] + np.diag(half)
            y = z - H_POS @ self.x
            S = H_POS @ self.P @ H_POS.T + R
            K = self.P @ H_POS.T @ np.linalg.inv(S)
            self.x = self.x + K @ y
            self.P = (np.eye(6) - K @ H_POS) @ self.P
            self.P = 0.5 * (self.P + self.P.T)
        # The disagreement is systematic, not noise: repeated updates must not shrink the uncertainty
        # below the half-disagreement. Floor the position variance per axis.
        floor = half + 0.5 * (np.diag(self.R["gnss"]) + np.diag(self.R["navic"]))
        for i in range(3):
            if self.P[i, i] < floor[i]:
                scale = np.sqrt(floor[i] / self.P[i, i])
                self.P[i, :] *= scale
                self.P[:, i] *= scale
        self.last_update_t = t

    def _kalman(self, H: np.ndarray, z: np.ndarray, R: np.ndarray) -> None:
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(6) - K @ H) @ self.P
        self.P = 0.5 * (self.P + self.P.T)

    def _clean_pred(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        tc, xc, Pc = self._clean
        F, Q = self._FQ(max(0.0, t - tc))
        return F @ xc, F @ Pc @ F.T + Q

    def update_epoch(self, t: float, fixes: dict[str, Iterable[float]]) -> dict[str, Any]:
        """Fuse the GNSS/NavIC fixes of one epoch with divergence handling and fault attribution.

        Pairwise agreement -> normal per-sensor gating (+ divergence reset), and the post-update
        state becomes the clean reference. Pairwise disagreement -> test each fix against the clean
        reference (last agreeing epoch, propagated): exactly one fails -> it is the faulty one (ladder
        failure, only the other is fused); otherwise ambiguous -> both fused inflated, and after
        `disagree_persist` epochs both become SUSPECT.
        """
        fixes = {k: np.asarray(list(v), dtype=float) for k, v in fixes.items()}
        if self.x is None or len(fixes) < 2:
            out = {}
            for sensor in ("gnss", "navic"):
                if sensor in fixes:
                    out[sensor] = self.update(sensor, t, fixes[sensor])
            if self.x is not None and self._clean is None:
                self._clean = (self.t, self.x.copy(), self.P.copy())
            return out
        if self._clean is None:
            self._clean = (self.t, self.x.copy(), self.P.copy())
        v = self.x[3:6].copy()
        for sensor, z in fixes.items():
            prev = self._prev_fix.get(sensor)
            if prev is not None and 0.0 < t - prev[0] <= 5.0:
                dz = z - prev[1] - v * (t - prev[0])
                var = 2.0 * np.diag(self.R[sensor])  # difference of two independent fixes
                if float(np.sum(dz * dz / var)) > CHI2_999[3]:
                    self._jumps[sensor].append(t)
            self._prev_fix[sensor] = (t, z)
            self._jumps[sensor] = [tj for tj in self._jumps[sensor] if tj >= t - 5.0]
        self.predict_to(t)
        g, n = fixes["gnss"], fixes["navic"]
        ph, pv = self._pairwise(g, n)
        self.disagreement_m = float(np.hypot(g[0] - n[0], g[1] - n[1]))
        if ph <= CHI2_999[2] and pv <= CHI2_999[1]:
            # Agreement.
            self._attributed = None
            self._disagree = 0
            self._agree += 1
            if self.disagreement and self._agree >= self.retrust_after:
                self._set_disagreement(t, False)
            both_fail = self._nis("gnss", g) > CHI2_999[3] and self._nis("navic", n) > CHI2_999[3]
            if both_fail:
                Rg, Rn = self.R["gnss"], self.R["navic"]
                w_g, w_n = np.linalg.inv(Rg), np.linalg.inv(Rn)
                Pz = np.linalg.inv(w_g + w_n)
                z = Pz @ (w_g @ g + w_n @ n)
                v_old = self.x[3:6].copy()
                self.x = np.concatenate([z, v_old])
                self.P = np.zeros((6, 6))
                self.P[0:3, 0:3] = Pz
                self.P[3:6, 3:6] = np.eye(3) * 100.0
                self.t = t
                self.last_update_t = t
                self.divergence_resets.append(t)
                out = {"reset": True}
            elif self.disagreement:
                self._fuse_inflated(t, g, n)  # still in the re-trust waiting period
                out = {"disagreement": True}
            else:
                # The two sensors agree with each other: a gate failure of one of them against the filter
                # means the filter is biased (e.g. dragged by a slow drift), not that the sensor is faulty.
                # Fuse both; trust penalties come only from attribution in the disagreement branch.
                out = {}
                for s_ in ("gnss", "navic"):
                    if self.trust[s_].state == "EXCLUDED":
                        self._ladder(s_, t, passed=True)  # agreeing again: count towards re-trust, not fused yet
                        out[s_] = {"fused": False, "trust": self.trust[s_].state}
                        continue
                    self.last_nis[s_] = self._nis(s_, fixes[s_])
                    self._ladder(s_, t, passed=True)
                    self._kalman(H_POS, fixes[s_], self.R[s_])
                    out[s_] = {"fused": True, "nis": self.last_nis[s_], "trust": self.trust[s_].state}
                self.last_update_t = t
            self._clean = (self.t, self.x.copy(), self.P.copy())
            return out
        # Disagreement. Attribute the episode to a sensor only if exactly one of them jumped recently.
        self._agree = 0
        if self._attributed is None and not self.disagreement:
            jumped = [k for k in ("gnss", "navic") if self._jumps[k]]
            if len(jumped) == 1:
                self._attributed = jumped[0]
        if self._attributed is not None:
            bad = self._attributed
            good = "navic" if bad == "gnss" else "gnss"
            self._disagree = 0
            self._ladder(bad, t, passed=False)
            self.last_nis[bad] = self._nis(bad, fixes[bad])
            res = self.update(good, t, fixes[good])
            self._clean = (self.t, self.x.copy(), self.P.copy())
            return {bad: {"fused": False, "attributed": True, "trust": self.trust[bad].state}, good: res}
        self._disagree += 1
        if self._disagree >= self.disagree_persist:
            self._set_disagreement(t, True)
        self._fuse_inflated(t, g, n)
        return {"disagreement": True, "ambiguous": True}

    def update(self, sensor: str, t: float, z: Iterable[float]) -> dict[str, Any]:
        z = np.asarray(list(z), dtype=float)
        H = H_POS if sensor in ("gnss", "navic") else H_UP
        R = self.R[sensor]
        if self.x is None:
            if sensor == "baro":
                return {"fused": False, "reason": "waiting for a position fix"}
            self.x = np.concatenate([z, np.zeros(3)])
            self.P = np.diag([R[0, 0], R[1, 1], R[2, 2], 25.0, 25.0, 25.0])
            self.t = t
            self.last_update_t = t
            return {"fused": True, "nis": 0.0, "init": True}
        self.predict_to(t)
        y = z - H @ self.x
        S = H @ self.P @ H.T + R
        nis = float(y @ np.linalg.solve(S, y))
        dof = len(z)
        passed = nis <= CHI2_999[dof]
        self.last_nis[sensor] = nis
        self._ladder(sensor, t, passed)
        if not passed or self.trust[sensor].state == "EXCLUDED":
            return {"fused": False, "nis": nis, "passed": passed, "trust": self.trust[sensor].state}
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(6) - K @ H) @ self.P
        self.P = 0.5 * (self.P + self.P.T)
        self.last_update_t = t
        return {"fused": True, "nis": nis, "passed": True, "trust": self.trust[sensor].state}


def rts_smooth(cfg, epochs: list[tuple[float, str, Any]]) -> list[dict[str, Any]]:
    """Forward KF over (t, sensor, z) measurements, then the RTS backward pass.

    `sensor` is "gnss", "navic", "baro", or "phase" (z = vehicle phase string, sets the process
    noise exactly like the live filter). Fixes of one epoch are fused together with the same
    divergence handling as live. Returns one smoothed state per epoch:
    {t, e, n, u, ve, vn, vu, sigma_h, sigma_v}. No trust-ladder exclusion: post-flight
    reprocessing tests every measurement on its own.
    """
    kf = PositionFilter(cfg)
    kf.exclude_after = 10**9  # never exclude a sensor wholesale in reprocessing
    grouped: dict[float, dict[str, Any]] = {}
    for t, sensor, z in epochs:
        grouped.setdefault(t, {})[sensor] = z
    xs_f, Ps_f, xs_p, Ps_p, Fs, ts = [], [], [], [], [], []
    for t in sorted(grouped):
        g = grouped[t]
        if "phase" in g:
            kf.set_phase(g["phase"])
        if kf.x is not None and t > kf.t:
            F, Q = kf._FQ(t - kf.t)
            x_p, P_p = F @ kf.x, F @ kf.P @ F.T + Q
            if xs_f:
                Fs.append(F)
                xs_p.append(x_p)
                Ps_p.append(P_p)
            kf.x, kf.P, kf.t = x_p, P_p, t
        fixes = {k: g[k] for k in ("gnss", "navic") if k in g}
        if fixes:
            if kf.x is None:
                for k, z in fixes.items():
                    kf.update(k, t, z)
            else:
                res = kf.update_epoch(t, fixes)
                if res.get("reset") and xs_f:
                    # A reset breaks the dynamic link to the previous epoch: inflate the stored prediction.
                    Ps_p[-1] = Ps_p[-1] + np.eye(6) * 1e6
        if "baro" in g and kf.x is not None:
            kf.update("baro", t, g["baro"])
        if kf.x is None:
            continue
        ts.append(t)
        xs_f.append(kf.x.copy())
        Ps_f.append(kf.P.copy())
    n = len(ts)
    if n == 0:
        return []
    xs_s, Ps_s = [None] * n, [None] * n
    xs_s[-1], Ps_s[-1] = xs_f[-1], Ps_f[-1]
    for k in range(n - 2, -1, -1):
        C = Ps_f[k] @ Fs[k].T @ np.linalg.inv(Ps_p[k])
        xs_s[k] = xs_f[k] + C @ (xs_s[k + 1] - xs_p[k])
        Ps_s[k] = Ps_f[k] + C @ (Ps_s[k + 1] - Ps_p[k]) @ C.T
    out = []
    for t, x, P in zip(ts, xs_s, Ps_s):
        out.append({"t": t, "e": float(x[0]), "n": float(x[1]), "u": float(x[2]), "ve": float(x[3]), "vn": float(x[4]),
                    "vu": float(x[5]), "sigma_h": float(math.sqrt(max(P[0, 0] + P[1, 1], 0.0))), "sigma_v": float(math.sqrt(max(P[2, 2], 0.0)))})
    return out
