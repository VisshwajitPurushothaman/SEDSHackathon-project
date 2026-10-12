"""Ground Kalman filter (integrity gates, trust ladder, divergence), RTS smoother, particle predictor."""
from pathlib import Path

import numpy as np
import pytest

from aerofire.config import load_config
from aerofire.ground.estimator import CHI2_999, PositionFilter, rts_smooth
from aerofire.ground.predictor import Predictor, ellipse, inside_ellipse
from aerofire.physics import atmosphere as A

CFG = load_config()
S = CFG.sensors
SNAP = A.load_snapshot(str(Path(__file__).resolve().parent.parent / "config" / "atmosphere_snapshot.json"), CFG.atmosphere.levels_hpa)


def truth(t):
    """Gentle climb with a little horizontal drift."""
    return np.array([2.0 * t, -1.0 * t, 1000.0 + 5.0 * t])


def noisy(rng, p, sh, sv):
    return p + rng.normal(0.0, [sh, sh, sv])


def test_chi2_quantiles():
    assert CHI2_999[1] == pytest.approx(10.828, abs=1e-3)
    assert CHI2_999[2] == pytest.approx(13.816, abs=1e-3)
    assert CHI2_999[3] == pytest.approx(16.266, abs=1e-3)


def test_filter_tracks_and_beats_single_sensor():
    rng = np.random.default_rng(0)
    kf = PositionFilter(CFG)
    errs, raw = [], []
    for t in range(600):
        g = noisy(rng, truth(t), S.gnss_sigma_h_m, S.gnss_sigma_v_m)
        n = noisy(rng, truth(t), S.navic_sigma_h_m, S.navic_sigma_v_m)
        kf.update_epoch(float(t), {"gnss": g, "navic": n})
        if t > 100:
            errs.append(np.linalg.norm(kf.x[:2] - truth(t)[:2]))
            raw.append(np.linalg.norm(g[:2] - truth(t)[:2]))
    assert np.mean(errs) < 0.5 * np.mean(raw)
    assert abs(kf.x[5] - 5.0) < 0.3  # vertical speed
    assert all(tr.state == "ACCEPTED" for tr in kf.trust.values())


def test_step_spoof_is_isolated_excluded_and_retrusted():
    """A STEP fault in GNSS fails the gate against the filter memory: GNSS excluded, NavIC kept."""
    rng = np.random.default_rng(1)
    kf = PositionFilter(CFG)
    for t in range(900):
        g = noisy(rng, truth(t), S.gnss_sigma_h_m, S.gnss_sigma_v_m)
        if 300 <= t < 500:
            g[0] += 300.0  # spoofer jumps the fix 300 m east
        n = noisy(rng, truth(t), S.navic_sigma_h_m, S.navic_sigma_v_m)
        kf.update_epoch(float(t), {"gnss": g, "navic": n})
        if t == 499:
            assert np.linalg.norm(kf.x[:2] - truth(t)[:2]) < 20.0  # the filter did not follow the spoofer
    hist = [s for _, s in kf.trust["gnss"].history]
    assert "EXCLUDED" in hist and hist[-1] == "RE-TRUSTED"
    assert "EXCLUDED" not in [s for _, s in kf.trust["navic"].history]
    assert kf.trust["gnss"].state == "ACCEPTED"


def test_slow_drift_spoof_is_detected_not_isolated_and_covered_by_uncertainty():
    """A slow drift cannot be attributed with two horizontal sources: both go SUSPECT and the
    uncertainty widens to cover the disagreement; both are re-trusted after they agree again."""
    rng = np.random.default_rng(1)
    kf = PositionFilter(CFG)
    covered = []
    for t in range(1200):
        g = noisy(rng, truth(t), S.gnss_sigma_h_m, S.gnss_sigma_v_m)
        if 300 <= t < 500:
            g[0] += 0.5 * (t - 300)  # slow walk-off: absorbed by the velocity state, no single innovation fails
        n = noisy(rng, truth(t), S.navic_sigma_h_m, S.navic_sigma_v_m)
        kf.update_epoch(float(t), {"gnss": g, "navic": n})
        if 400 <= t < 500:
            err = np.linalg.norm(kf.x[:2] - truth(t)[:2])
            covered.append(err <= 3.0 * np.sqrt(kf.P[0, 0] + kf.P[1, 1]))
    for s in ("gnss", "navic"):
        hist = [h for _, h in kf.trust[s].history]
        assert "SUSPECT (GNSS/NavIC disagree)" in hist and hist[-1] == "RE-TRUSTED"
    assert np.mean(covered) > 0.95  # truth stays inside the widened 3-sigma


def test_consistent_jump_resets_filter_instead_of_excluding_sensors():
    rng = np.random.default_rng(2)
    kf = PositionFilter(CFG)
    for t in range(200):
        kf.update_epoch(float(t), {"gnss": noisy(rng, truth(t), 2.5, 4), "navic": noisy(rng, truth(t), 5, 8)})
    jump = np.array([0.0, 0.0, -500.0])  # an unmodelled manoeuvre seen consistently by both sensors
    for t in range(200, 210):
        kf.update_epoch(float(t), {"gnss": noisy(rng, truth(t) + jump, 2.5, 4), "navic": noisy(rng, truth(t) + jump, 5, 8)})
    assert kf.divergence_resets and kf.divergence_resets[0] == 200.0
    assert all(tr.state != "EXCLUDED" for tr in kf.trust.values())
    assert abs(kf.x[2] - (truth(209)[2] - 500.0)) < 15.0


def test_rts_smoother_beats_forward_filter():
    rng = np.random.default_rng(3)
    epochs, kf, fwd = [], PositionFilter(CFG), []
    for t in range(400):
        g = noisy(rng, truth(t), 2.5, 4)
        n = noisy(rng, truth(t), 5, 8)
        epochs += [(float(t), "gnss", list(g)), (float(t), "navic", list(n))]
        kf.update_epoch(float(t), {"gnss": g, "navic": n})
        fwd.append(np.linalg.norm(kf.x[:3] - truth(t)))
    smooth = rts_smooth(CFG, epochs)
    s_err = [np.linalg.norm(np.array([p["e"], p["n"], p["u"]]) - truth(p["t"])) for p in smooth]
    assert len(smooth) == 400
    assert np.mean(s_err[50:350]) < 0.8 * np.mean(fwd[50:350])


def test_ellipse_levels_are_calibrated_for_gaussian_clouds():
    rng = np.random.default_rng(4)
    cloud = rng.multivariate_normal([100.0, -50.0], [[400.0, 150.0], [150.0, 200.0]], 4000)
    test = rng.multivariate_normal([100.0, -50.0], [[400.0, 150.0], [150.0, 200.0]], 4000)
    for lvl in (50, 90, 99):
        cov = np.mean([inside_ellipse(cloud, tuple(p), lvl) for p in test])
        assert cov == pytest.approx(lvl / 100.0, abs=0.025)
    poly = ellipse(cloud, CFG.site.lat_deg, CFG.site.lon_deg, 90)["polygon"]
    assert len(poly) == 32


def test_predictor_spreads_with_time_and_drifts_with_wind():
    pr = Predictor(CFG, SNAP)
    x = np.array([0.0, 0.0, 28000.0, 0.0, 0.0, 0.0])
    pr.start(0.0, x, np.diag([25.0, 25.0, 100.0, 1.0, 1.0, 1.0]), "FLOAT")
    pr.advance(60.0)
    s1 = pr.summary()
    pr.advance(600.0)
    s2 = pr.summary()
    assert s2["sigma_h_m"] > s1["sigma_h_m"] > 0
    a = SNAP.at(28000.0)
    assert s2["mean"]["east"] == pytest.approx(a.u * 600.0, abs=0.35 * CFG.predictor.wind_sigma_m_s * 600.0)
    assert s2["status"] == "PREDICTED" and [r["level"] for r in s2["regions"]] == [50, 90, 99]


def test_landing_footprint_from_float_lands_and_has_regions():
    pr = Predictor(CFG, SNAP)
    fp = pr.landing_footprint(0.0, 0.0, 28000.0, 10.0, rocket_aboard=False)
    assert len(fp["points_enu"]) == CFG.predictor.footprint_particles
    assert fp["regions"][1]["level"] == 90 and fp["regions"][1]["semi_axes_m"][0] > 0
