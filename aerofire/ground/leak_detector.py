"""Ground-side pressurant leak detector. Uses MEASURED pressure and temperature only.

1. Estimate the gas mass from each measurement pair (same real-gas model as
   the physics package; the true mass is never available here):
       m_hat = P_meas V / (Z(P_meas, T_meas) R T_meas)
2. Every `stride_s`, fit an ordinary-least-squares line to m_hat over the last
   `window_s` seconds. Slope b, standard error se = s_res / sqrt(sum (t - tbar)^2).
3. One-sided test H0: b >= 0 vs H1: b < 0 at level alpha: significant when
   b / se < -t_{1-alpha, n-2}.
4. Declare a leak after `consecutive` significant windows in a row.
5. Size the hole by inverting the choked-flow law with mdot = -b at the
   window's mean P and T. A 95 % interval follows from b +- 1.96 se.

This replaces the original "more than 10 g lost" rule, which read the
simulator's true mass and let a 50 um leak (4.6 g) arm the gate.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from ..physics import tank as tk


@dataclass
class WindowResult:
    t_end: float
    n: int
    slope_kg_s: float
    se_kg_s: float
    t_stat: float
    significant: bool
    mdot_est_kg_s: float
    d_est_um: float | None
    d_ci_um: tuple[float, float] | None


@dataclass
class LeakDetector:
    params: tk.TankParams
    window_s: float = 120.0
    stride_s: float = 30.0
    alpha: float = 0.001
    consecutive: int = 3
    _buf: deque = field(default_factory=deque)
    _next_eval: float | None = None
    _streak: int = 0
    detected: bool = False
    detected_at: float | None = None
    last: WindowResult | None = None
    history: list[WindowResult] = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg) -> "LeakDetector":
        d = cfg.leak_detector
        return cls(tk.TankParams.from_config(cfg), d.window_s, d.stride_s, d.alpha, d.consecutive)

    def t_crit(self, n: int) -> float:
        return float(stats.t.ppf(1.0 - self.alpha, n - 2))

    def update(self, t: float, P_meas: float | None, T_meas: float | None) -> WindowResult | None:
        """Feed one sample (None = dropout). Returns a WindowResult when a window was evaluated."""
        if P_meas is not None and T_meas is not None and T_meas > 0:
            m_hat = tk.mass_from_pt(self.params, P_meas, T_meas)
            self._buf.append((t, m_hat, P_meas, T_meas))
        while self._buf and self._buf[0][0] < t - self.window_s:
            self._buf.popleft()
        if self._next_eval is None:
            self._next_eval = t + self.window_s
        if t < self._next_eval:
            return None
        self._next_eval += self.stride_s
        if len(self._buf) < max(10, int(0.5 * self.window_s)):  # too many dropouts: no decision
            self._streak = 0
            return None
        arr = np.asarray(self._buf)
        res = self._evaluate(t, arr[:, 0], arr[:, 1], arr[:, 2].mean(), arr[:, 3].mean())
        self._streak = self._streak + 1 if res.significant else 0
        if self._streak >= self.consecutive and not self.detected:
            self.detected, self.detected_at = True, t
        self.last = res
        self.history.append(res)
        return res

    def _evaluate(self, t_end: float, ts: np.ndarray, ms: np.ndarray, P_mean: float, T_mean: float) -> WindowResult:
        n = len(ts)
        tc = ts - ts.mean()
        sxx = float(np.dot(tc, tc))
        b = float(np.dot(tc, ms - ms.mean()) / sxx)
        resid = ms - ms.mean() - b * tc
        s2 = float(np.dot(resid, resid) / (n - 2))
        se = math.sqrt(s2 / sxx) if s2 > 0 else 1e-30
        tstat = b / se
        sig = tstat < -self.t_crit(n)
        mdot = max(-b, 0.0)
        d_est = d_ci = None
        if sig:
            def diam(md: float) -> float:
                return tk.area_to_diameter(tk.hole_area_from_flow(self.params, max(md, 0.0), P_mean, T_mean)) * 1e6

            d_est = diam(mdot)
            d_ci = (diam(-b - 1.96 * se), diam(-b + 1.96 * se))
        return WindowResult(t_end, n, b, se, tstat, sig, mdot, d_est, d_ci)


def window_tstats(ts: np.ndarray, m_hat: np.ndarray, window_s: float, stride_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised version of the detector's window test for Monte Carlo.

    ts: (n,) uniformly sampled times; m_hat: (seeds, n). Returns (t_end, slope, t_stat) per window:
    shapes (w,), (seeds, w), (seeds, w). Windows end at ts[0] + window_s, then every stride_s,
    and contain samples with t >= t_end - window_s (same rule as LeakDetector.update).
    """
    dt = ts[1] - ts[0]
    t_ends = np.arange(ts[0] + window_s, ts[-1] + 1e-9, stride_s)
    out_b = np.empty((m_hat.shape[0], len(t_ends)))
    out_t = np.empty_like(out_b)
    for j, te in enumerate(t_ends):
        i1 = int(round((te - ts[0]) / dt)) + 1
        i0 = int(math.ceil((te - window_s - ts[0]) / dt - 1e-9))
        tw = ts[i0:i1]
        tc = tw - tw.mean()
        sxx = float(np.dot(tc, tc))
        mw = m_hat[:, i0:i1]
        mc = mw - mw.mean(axis=1, keepdims=True)
        b = mc @ tc / sxx
        resid = mc - np.outer(b, tc)
        s2 = np.einsum("ij,ij->i", resid, resid) / (len(tw) - 2)
        out_b[:, j] = b
        out_t[:, j] = b / np.sqrt(s2 / sxx)
    return t_ends, out_b, out_t
