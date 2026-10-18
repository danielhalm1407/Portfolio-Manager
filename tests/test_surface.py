"""
SURFACE TESTS (13-02 Task 3) — does the smoother recover a shape it was given?

WHAT THESE TESTS ARE FOR
------------------------
``surface.py`` exists to answer one question about the parameter grid: is performance a BROAD
PLATEAU or a SPIKE? That question is answered by two reported numbers — effective degrees of
freedom and curvature at the optimum — and a number nobody has tested is a number nobody should
act on. So the central test here builds a deliberately broad surface and a deliberately spiky
one and asserts that the two numbers separate them, which makes the plateau claim TESTABLE
rather than visual.

The rest pin the things that are easy to get wrong and silent afterwards: a knot vector that
spans the wrong interval (caught by recovering a known quadratic and its argmax), and a snapping
cost that comes out negative (which would mean the "optimum" was not the optimum).

Everything is synthetic — no simulator, no parquet — so the suite stays fast.
"""

import numpy as np
import pytest

from portutils.strategies import surface
from portutils.strategies.surface import fit_1d, fit_2d, optimum, snap_to_strike


# ============================================================================
# 1-D: RECOVERING A KNOWN SHAPE
# ============================================================================

def test_fit_1d_recovers_a_quadratic_and_its_argmax():
    # A downward parabola peaking at x = 0.91 — roughly where the moneyness optimum is expected
    # to sit, so a basis that spanned the wrong interval would show up as a shifted peak.
    x = np.linspace(0.80, 1.00, 9)
    y = -40.0 * (x - 0.91) ** 2 + 5.0
    fit = fit_1d(x, y)

    assert np.allclose(fit.predict(x), y, atol=0.02)
    opt = optimum(fit, (0.80, 1.00))
    assert opt.point == pytest.approx(0.91, abs=0.01)
    assert opt.value == pytest.approx(5.0, abs=0.05)
    # Curvature of -40 x^2 is -80 everywhere. The smoother will not reproduce it exactly, but
    # the SIGN and the order of magnitude are what the plateau-versus-spike reading turns on.
    assert opt.curvature < 0
    assert opt.curvature == pytest.approx(-80.0, rel=0.35)


def test_fit_1d_reports_low_edf_for_a_straight_line():
    # Heavy smoothing shrinks towards a LINE, not towards a constant (the penalty is on second
    # differences). So a perfectly linear design should need about 2 degrees of freedom — and
    # anything much above that means the smoother is chasing noise that is not there.
    x = np.linspace(0.80, 1.00, 9)
    y = 3.0 * x + 1.0
    fit = fit_1d(x, y)
    assert fit.edf < 3.0
    assert fit.r2 > 0.999


def test_fit_1d_rejects_too_few_points():
    with pytest.raises(ValueError):
        fit_1d([0.8, 0.9, 1.0], [1.0, 2.0, 1.0])


def test_predict_does_not_extrapolate():
    # A point outside the fitted range is evaluated AT the boundary. Extrapolating a spline
    # continues the last polynomial piece and can swing anywhere, which would hand a caller a
    # confident number about a strike the grid never ran.
    x = np.linspace(0.80, 1.00, 9)
    y = -40.0 * (x - 0.91) ** 2 + 5.0
    fit = fit_1d(x, y)
    assert float(fit.predict(0.50)[0]) == pytest.approx(float(fit.predict(0.80)[0]))
    assert float(fit.predict(1.60)[0]) == pytest.approx(float(fit.predict(1.00)[0]))


# ============================================================================
# THE PLATEAU-VERSUS-SPIKE READING — the reason this module exists
# ============================================================================

def _grid(nx=9, ny=7):
    gx, gy = np.meshgrid(np.linspace(0.80, 1.00, nx), np.linspace(0.06, 0.20, ny),
                         indexing="ij")
    return gx.ravel(), gy.ravel()


def test_curvature_separates_a_broad_surface_from_a_spiky_one():
    x, y = _grid()
    # BROAD: a gentle bowl, so the top is nearly flat over a wide region.
    broad = -4.0 * (x - 0.91) ** 2 - 2.0 * (y - 0.12) ** 2 + 5.0
    # SPIKY: a narrow Gaussian bump on the same design points. Same optimum LOCATION, utterly
    # different shape around it — which is precisely the pair a ranked table cannot tell apart,
    # because both have the same best row.
    spiky = 5.0 * np.exp(-((x - 0.91) ** 2 / 0.0006 + (y - 0.12) ** 2 / 0.0004))

    fb = fit_2d(x, y, broad)
    fs = fit_2d(x, y, spiky)
    bounds = ((0.80, 1.00), (0.06, 0.20))
    ob = optimum(fb, bounds)
    os_ = optimum(fs, bounds)

    # CURVATURE is the discriminator, by three orders of magnitude. This is the assertion that
    # makes "broad plateau, not a spike" a testable claim instead of a look at a picture.
    assert os_.curvature < ob.curvature
    assert abs(os_.curvature) > 100 * abs(ob.curvature)


def test_edf_does_not_separate_them_in_2d_and_that_is_pinned_deliberately():
    # ========================================================================
    # PINNING A FINDING, NOT A FEATURE. 13-02's AC-5 expected effective degrees
    # of freedom to be the plateau-versus-spike number. On a DETERMINISTIC
    # design it is not: GCV trades residual against flexibility, and with no
    # noise to trade against it drives towards whatever fits closest — so edf
    # reports how easily the BASIS represents the surface, not how sharply the
    # surface is peaked. The narrow spike is HARDER to represent, so its
    # residual stays up, GCV stops short, and it comes back with the LOWER edf.
    #
    # Asserted the "wrong" way round on purpose. If a future change ever makes
    # edf behave the way the plan assumed, this test fails and the surface
    # module's header — which currently tells readers not to quote a 2-D edf as
    # spikiness — gets revisited instead of silently going stale.
    # ========================================================================
    x, y = _grid()
    broad = -4.0 * (x - 0.91) ** 2 - 2.0 * (y - 0.12) ** 2 + 5.0
    spiky = 5.0 * np.exp(-((x - 0.91) ** 2 / 0.0006 + (y - 0.12) ** 2 / 0.0004))
    assert fit_2d(x, y, spiky).edf < fit_2d(x, y, broad).edf


def test_gcv_tie_break_keeps_the_smoother_fit():
    # A straight line is in the NULL SPACE of a second-difference penalty, so it is reproduced
    # exactly at every smoothing parameter and GCV ties across the whole grid. Before the
    # residual floor and the descending sweep, that tie was decided by floating-point noise and
    # reliably returned the most flexible fit — a perfectly linear design came back with 31
    # effective degrees of freedom. Two degrees of freedom is the right answer: a line.
    x = np.linspace(0.80, 1.00, 9)
    fit = fit_1d(x, 3.0 * x + 1.0)
    assert fit.edf == pytest.approx(2.0, abs=0.05)
    # And the chosen lambda is at the SMOOTH end of the sweep, not the flexible end.
    assert fit.lam > 1.0


def test_fit_2d_recovers_a_separable_bowl_and_its_optimum():
    x, y = _grid()
    z = -4.0 * (x - 0.91) ** 2 - 2.0 * (y - 0.12) ** 2 + 5.0
    fit = fit_2d(x, y, z)
    assert fit.r2 > 0.99
    opt = optimum(fit, ((0.80, 1.00), (0.06, 0.20)))
    assert opt.point[0] == pytest.approx(0.91, abs=0.02)
    assert opt.point[1] == pytest.approx(0.12, abs=0.02)
    assert opt.curvature < 0


def test_fit_2d_lam_is_a_pair_and_can_differ_by_axis():
    # Anisotropic by construction: the two axes are different quantities in different units, so
    # a surface that is linear in one and curved in the other must be allowed to smooth them
    # differently rather than splitting the difference.
    x, y = _grid()
    z = 3.0 * x - 30.0 * (y - 0.12) ** 2
    fit = fit_2d(x, y, z)
    assert isinstance(fit.lam, tuple) and len(fit.lam) == 2
    assert fit.lam[0] != fit.lam[1]


def test_fit_2d_rejects_too_few_points():
    with pytest.raises(ValueError):
        fit_2d([0.8, 0.9], [0.1, 0.2], [1.0, 2.0])


# ============================================================================
# SNAPPING TO A TRADEABLE STRIKE
# ============================================================================

def test_snap_returns_the_nearest_increment():
    # Spot 100, so a 0.9137 moneyness is a 91.37 strike -> the listed 91.
    snap = snap_to_strike(0.9137, spot=100.0, increment=1.0)
    assert snap.strike == pytest.approx(91.0)
    assert snap.moneyness == pytest.approx(0.91)


def test_snap_cost_is_non_negative_at_a_true_optimum():
    x = np.linspace(0.80, 1.00, 9)
    y = -40.0 * (x - 0.9137) ** 2 + 5.0
    fit = fit_1d(x, y)
    opt = optimum(fit, (0.80, 1.00))
    snap = snap_to_strike(opt.point, spot=100.0, increment=1.0, predict=fit.predict)
    # Cost is continuous value minus snapped value. At the argmax nothing can score higher, so a
    # NEGATIVE cost would mean the reported optimum was not the optimum.
    assert snap.cost >= -1e-9
    # And on a surface this gentle, rounding 0.9137 to 0.91 should barely matter — which is the
    # reassuring case the figure's caption is allowed to claim.
    assert snap.cost < 0.05


def test_snap_cost_is_nan_without_a_predictor():
    snap = snap_to_strike(0.9137, spot=100.0)
    assert np.isnan(snap.cost)


def test_module_header_states_the_display_not_selection_boundary():
    # The boundary 13-03 must not cross is recorded in the module docstring, and a docstring
    # that gets trimmed in a future edit should fail here rather than silently licence a
    # smoother fitted inside a CPCV split.
    doc = surface.__doc__
    assert "DISPLAY, NOT A SELECTION RULE" in doc
    assert "DISCRETE GRID ARGMAX" in doc
    assert "NOT AN ESTIMATE OF GENERALISATION" in doc


# ============================================================================
# 13-02.1 — THE FIT LAYERS BENEATH fit_1d
# ============================================================================

def test_fit_poly_recovers_a_quadratic_exactly():
    x = np.linspace(0.80, 1.00, 9)
    y = 1.0 - 30.0 * (x - 0.9) ** 2
    fit = surface.fit_poly(x, y, degree=2)
    assert fit.r2 == pytest.approx(1.0)
    assert fit.edf == 3.0
    assert float(fit.predict(0.9)[0]) == pytest.approx(1.0, abs=1e-9)


def test_regression_spline_bends_where_a_global_quadratic_cannot():
    # A kink at 0.9: flat to the left, falling to the right. Three interior knots let the spline
    # follow both pieces; one global quadratic has to compromise across the whole axis.
    x = np.repeat(np.linspace(0.80, 1.00, 9), 6)
    y = np.where(x < 0.9, 1.0, 1.0 - 8.0 * (x - 0.9))
    poly = surface.fit_poly(x, y, degree=2)
    spline = surface.fit_regression_spline(x, y, (0.85, 0.90, 0.95))
    assert spline.r2 > poly.r2
    assert spline.edf == 3 + 3 + 1     # interior knots + degree + 1


def test_pspline_knots_are_the_ones_fit_1d_uses_and_span_the_range():
    x = np.repeat(np.linspace(0.80, 1.00, 9), 6)
    knots = surface.pspline_knots(x)
    assert knots[0] == pytest.approx(0.80) and knots[-1] == pytest.approx(1.00)
    # n_basis 8, cubic: 8 - 3 + 1 = 6 knots inside the range, equally spaced.
    assert len(knots) == 6
    assert np.allclose(np.diff(knots), np.diff(knots)[0])
