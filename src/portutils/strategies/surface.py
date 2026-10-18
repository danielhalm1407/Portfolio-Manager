"""
RESPONSE SURFACE — penalised smoothers over a simulated parameter grid (13-02 Task 3).

WHAT THIS MODULE IS FOR
-----------------------
The target variable here does not exist in any dataset. "Performance of a put struck at 0.92 of
spot, monetised at a 12% drawdown" is MANUFACTURED by running the simulator, one run per design
point. That makes this a response-surface / surrogate-modelling exercise: choose design points,
run the simulator at each, fit a cheap smooth approximation through the results, and read the
approximation between the points as well as on them.

==============================================================================
THE IN-SAMPLE R-SQUARED OF THESE FITS IS NOT AN ESTIMATE OF GENERALISATION
ERROR. It is a measure of how closely a smoother tracks points it was fitted
to, and nothing else. At a FIXED window the simulator is deterministic, so the
scatter around the fit is path-specific detail, not measurement noise — there
is no sampling distribution here to average over.
==============================================================================

==============================================================================
AND THE SAME DETERMINISM MAKES edf A POOR PLATEAU-VERSUS-SPIKE SIGNAL IN 2-D.
MEASURED 2026-10-17 on this module's own tests, and recorded because the plan
expected the opposite. GCV trades residual against flexibility; with NO NOISE
there is nothing to trade, so it drives towards whatever fits closest and edf
ends up reporting how easily the BASIS can represent the surface rather than
how sharply the surface is peaked. On a 9x7 design:
    broad quadratic bowl   edf 31.3   curvature at the optimum     -8
    narrow Gaussian spike  edf 21.9   curvature at the optimum  -3232
The spike scored the LOWER edf — it is harder to represent, so its residual
stays up and GCV stops short — which is backwards from the reading AC-5 asks
for. CURVATURE separates the two by three orders of magnitude and is the number
to quote. edf is still reported, and is still meaningful in 1-D (a straight
line comes back at 2.0, a quadratic at 8.0), but a 2-D edf must not be read as
"how spiky".
==============================================================================

==============================================================================
THE SMOOTH IS A DISPLAY, NOT A SELECTION RULE. Its optimum is reported as an
IN-SAMPLE optimum and nothing consumes it. 13-03 selects, on each of its 15
CPCV splits, by DISCRETE GRID ARGMAX — never by refitting this smoother inside
a split, because a smoother fitted per split is a second fitted object, with
its own smoothing parameter, for leakage to travel through, in service of a
comparison that is not yet earned. The raw-argmax-versus-smoothed-argmax
comparison that CONTEXT.md section 2.4 wanted is deferred to a 13-04 plan whose
whole job is that question. This is said here, at the module that would be
imported, so that importing it from 13-03 is a deliberate act rather than a
convenience.

And one more boundary, because it is the easiest thing to over-read off a nice
picture: a SMOOTH surface rules out dependence on one lucky cell. It does NOT
rule out a smooth ridge in the wrong place. Only 13-03 addresses the second.
==============================================================================

WHY P-SPLINES AND NOT pygam, AND WHY NOT A TREE
------------------------------------------------
``pygam`` is not installed and is not worth a dependency for two axes and sixty-odd design
points. What is used instead is the Eilers-Marx penalised B-spline (P-spline): a B-spline basis
with a second-difference ROUGHNESS PENALTY on the coefficients, which is the same object a GAM
fits, written out in thirty lines of numpy. It gives the three things the plan asks each fit to
report — the smoothing parameter, the effective degrees of freedom and a fitted callable — EXACTLY
rather than by approximation, because the smoother matrix is available in closed form and its
trace IS the effective degrees of freedom.

``scipy`` is used (``BSpline.design_matrix``) and is now DECLARED in ``pyproject.toml`` rather
than relied on transitively through scikit-learn. That is the same objection ``pricing.py``
records about scipy and the same one ``markdown-it-py`` was declared for.

Rejected, and why, so the choice is not relitigated: lasso/ridge — there is no variable-selection
problem here and the design is two-dimensional, not high-dimensional. Trees and random forests —
piecewise-constant in the parameters, so they produce exactly the spiky surface this exercise
exists to argue against. A Gaussian process is the sensible upgrade IF uncertainty bands are ever
wanted, and is noted rather than built.

Pure: no IO, no simulator, no plotting. Everything takes arrays and returns arrays or fits.
"""

from collections import namedtuple

import numpy as np
from scipy.interpolate import BSpline

# A fitted smoother. A named tuple rather than a bare 3-tuple because `fit.edf` at a call site
# reads as a claim about the fit, where `fit[2]` reads as nothing at all.
#
#   predict  callable: 1-D takes x, 2-D takes (x, y); accepts scalars or arrays
#   lam      the smoothing parameter(s) chosen by GCV — a float in 1-D, a pair in 2-D
#   edf      EFFECTIVE DEGREES OF FREEDOM, the trace of the smoother matrix. This is the
#            number that distinguishes a broad plateau from a spike, and it is a deliverable
#            of every fit rather than a diagnostic: edf near 2 is essentially a straight line,
#            edf near the number of design points is interpolation
#   gcv      the generalised cross-validation score at the chosen lam
#   r2       IN-SAMPLE only. See the module header — this is NOT generalisation error
SmoothFit = namedtuple("SmoothFit", "predict lam edf gcv r2")

# An optimum, with the local shape around it. `curvature` is the thing that answers "broad
# plateau or spike" quantitatively: the second derivative (1-D) or the most negative Hessian
# eigenvalue (2-D) at the argmax, so a flat top is a number near zero and a spike is a large
# negative number.
Optimum = namedtuple("Optimum", "point value curvature")

# A continuous optimum snapped to a tradeable strike, and what that snap cost.
Snap = namedtuple("Snap", "moneyness strike cost")


# ============================================================================
# THE BASIS AND THE PENALTY
# ============================================================================

def _knots(lo, hi, n_basis, degree):
    """Equally spaced knot vector for ``n_basis`` B-splines of the given degree on [lo, hi].

    A B-spline basis of ``n_basis`` functions and degree ``k`` needs ``n_basis + k + 1`` knots,
    with ``k`` of them hanging off each end. Spelled out rather than taken from a helper because
    an off-by-one here produces a basis that silently spans the wrong interval, and the symptom
    is a fit that looks fine in the middle and bends absurdly at the edges — which is exactly
    where the interesting parameter values sit.
    """
    dx = (hi - lo) / (n_basis - degree)
    return lo + dx * np.arange(-degree, n_basis + 1, dtype=float)


def _design(x, lo, hi, n_basis, degree):
    """The B-spline design matrix, one row per observation and one column per basis function."""
    t = _knots(lo, hi, n_basis, degree)
    # Clipped to the fitted range. Extrapolating a spline is meaningless — it continues the last
    # polynomial piece, which can swing anywhere — so a point outside the design is evaluated AT
    # the boundary instead. Callers are never given a silently extrapolated number.
    xc = np.clip(np.asarray(x, dtype=float), lo, hi)
    return np.asarray(BSpline.design_matrix(xc, t, degree, extrapolate=True).todense())


def _penalty(n_basis, order=2):
    """Second-difference penalty matrix D, so ``coef.T @ D.T @ D @ coef`` is the roughness.

    SECOND differences, not first: penalising first differences shrinks towards a CONSTANT, and
    the one thing everyone already agrees on is that the score is not constant in moneyness.
    Penalising second differences shrinks towards a STRAIGHT LINE, so the limit of heavy
    smoothing is a linear trend — which is the honest null for "does the optimum sit in the
    interior or at an edge".
    """
    return np.diff(np.eye(n_basis), n=order, axis=0)


def _solve(B, P, y, lam):
    """One penalised least-squares solve. Returns ``(coef, edf, rss)``.

    ``edf`` is the trace of the smoother matrix ``H = B (B'B + lam P)^-1 B'``. Computed as
    ``tr((B'B + lam P)^-1 B'B)`` rather than by forming H: H is n-by-n in the OBSERVATIONS,
    while this form is n-by-n in the COEFFICIENTS, and the trace is identical by the cyclic
    property. With 63 design points either would do; the habit matters once 13-03 fits this
    inside a loop.
    """
    btb = B.T @ B
    a = btb + lam * P
    # `lstsq` rather than `solve`: at very small lam with more basis functions than distinct
    # design points the system is singular, and a least-norm answer is a usable fit where an
    # exception is just a hole in the lambda grid.
    coef = np.linalg.lstsq(a, B.T @ y, rcond=None)[0]
    edf = float(np.trace(np.linalg.lstsq(a, btb, rcond=None)[0]))
    resid = y - B @ coef
    return coef, edf, float(resid @ resid)


def _gcv(rss, edf, n, tss=None, floor_rel=1e-10):
    """Generalised cross-validation score. Lower is better.

    ``n * RSS / (n - edf)^2`` — the standard form. It is a LEAVE-ONE-OUT approximation, which is
    the right criterion for choosing how much to smooth a deterministic surface: it asks "how
    well would this fit have predicted a design point it had not seen", which is a question about
    the SMOOTHER's flexibility and not a claim about out-of-sample performance of the strategy.
    Those two are routinely confused; they are not the same thing and this one carries no
    information about the second.

    ==========================================================================
    THE RESIDUAL IS FLOORED, AND WITHOUT THAT THE CRITERION PICKS THE WIGGLIEST
    FIT EVERY TIME. Found 2026-10-17 on this module's own tests. Some designs
    are reproduced EXACTLY by the basis at any smoothing parameter — a straight
    line is in the null space of a second-difference penalty, and a quadratic on
    nine points is within rounding of it. The residual is then ~1e-28 for every
    lambda, GCV is ~0 for every lambda, and the "best" one is decided by
    floating-point noise — which, with the grid walked from small lambda up,
    reliably returned the most flexible fit. A perfectly straight design came
    back with 31 effective degrees of freedom.
    That is not a near-miss; it is backwards. The whole plateau-versus-spike
    reading is edf, so a criterion that inflates edf whenever the fit is good
    would make every surface look spiky. Residuals below ``floor_rel`` of the
    total sum of squares are therefore treated as a TIE, and the caller walks
    lambda from large to small so a tie keeps the SMOOTHER fit. Among fits that
    predict equally well, prefer the one claiming less structure.
    ==========================================================================
    """
    denom = n - edf
    if denom <= 1e-9:
        return np.inf
    if tss is not None and tss > 0:
        rss = max(rss, floor_rel * tss)
    return n * rss / denom ** 2


def _choose_lam(candidates):
    """Pick the GCV-best fit from ``(score, payload)`` candidates ALREADY ORDERED from smoothest
    to most flexible, keeping the smoothest among ties.

    Strict ``<`` plus that ordering is the whole tie-break: a more flexible fit has to be
    genuinely better to displace a smoother one, rather than winning on the last bit of a float.
    """
    best = None
    for score, payload in candidates:
        if best is None or score < best[0]:
            best = (score, payload)
    return best


# ============================================================================
# 1-D
# ============================================================================

def fit_1d(x, y, n_basis=8, degree=3, lam_grid=None, penalty_order=2):
    """A 1-D penalised smoothing spline, roughness penalty chosen by GCV.

    FITTED FIRST, BEFORE ANY 2-D SURFACE, and that staging is deliberate (AC-5). A 2-D smooth
    has two smoothing parameters and an interaction, so when it looks wrong there are three
    candidate causes. Fitting each axis alone first means a one-dimensional answer is already
    known — "the score rises to about 0.90 and flattens" — against which the surface either
    agrees or raises a question that can be named.

    Returns a ``SmoothFit``. ``n_basis`` is capped at the number of distinct design points, since
    more basis functions than points is an interpolation problem wearing a smoother's clothes.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    if len(x) < 4:
        raise ValueError(f"need at least 4 finite design points to fit a spline, got {len(x)}")

    lo, hi = float(x.min()), float(x.max())
    n_basis = int(min(n_basis, len(np.unique(x))))
    # degree + 1 basis functions is the minimum that still spans a cubic piece.
    n_basis = max(n_basis, degree + 1)
    B = _design(x, lo, hi, n_basis, degree)
    D = _penalty(n_basis, penalty_order)
    P = D.T @ D

    # A wide logarithmic sweep. Wide on purpose: the right smoothing parameter for a surface
    # whose values span -1 to +6 is not knowable in advance, and a grid that bottoms out at its
    # own edge is a grid that was too narrow — which the returned `lam` makes checkable.
    lam_grid = np.logspace(-6, 6, 61) if lam_grid is None else np.asarray(lam_grid, dtype=float)
    tss = float(((y - y.mean()) ** 2).sum())
    # DESCENDING — smoothest first. See _gcv: a tie must keep the smoother fit.
    candidates = []
    for lam in np.sort(lam_grid)[::-1]:
        coef, edf, rss = _solve(B, P, y, lam)
        candidates.append((_gcv(rss, edf, len(y), tss), (float(lam), coef, edf, rss)))
    gcv, (lam, coef, edf, rss) = _choose_lam(candidates)

    r2 = 1.0 - rss / tss if tss > 0 else float("nan")

    def predict(xq):
        return _design(np.atleast_1d(xq), lo, hi, n_basis, degree) @ coef

    return SmoothFit(predict=predict, lam=float(lam), edf=float(edf), gcv=float(gcv),
                     r2=float(r2))


# ============================================================================
# THE LAYERS BENEATH fit_1d (13-02.1). The published page builds the fitting
# procedure up in three steps — a global polynomial, an unpenalised regression
# spline with a handful of knots, then the penalised P-spline above — so each
# step's weakness motivates the next. The first two exist ONLY for that display;
# like fit_1d they are never a selection rule (see the module header).
# ============================================================================

def pspline_knots(x, n_basis=8, degree=3):
    """The knot positions ``fit_1d`` uses for these design points, inside the fitted range.

    Equally spaced on [min x, max x]: ``n_basis - degree + 1`` of them including both ends. A
    P-spline puts its knots on a REGULAR grid and controls wiggliness through the penalty, unlike a
    classical smoothing spline, which puts a knot at EVERY distinct x. Exposed so a figure can draw
    them as vertical lines: they are not visible in the fit, and a reader cannot otherwise tell
    where the pieces join.
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    lo, hi = float(x.min()), float(x.max())
    # The same n_basis capping fit_1d applies, so the drawn knots are the fitted ones.
    n_basis = max(int(min(n_basis, len(np.unique(x)))), degree + 1)
    t = _knots(lo, hi, n_basis, degree)
    return t[(t >= lo - 1e-12) & (t <= hi + 1e-12)]


def _fixed_fit(x, y, B):
    # Ordinary least squares on a fixed design matrix, packaged as a SmoothFit so the three
    # layers are reported in one shape. lam is 0.0 (no penalty) and edf is the column count,
    # which is exactly the trace of an unpenalised projection.
    coef = np.linalg.lstsq(B, y, rcond=None)[0]
    resid = y - B @ coef
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    edf = float(B.shape[1])
    return coef, SmoothFit(predict=None, lam=0.0, edf=edf, gcv=float(_gcv(rss, edf, len(y), tss)),
                           r2=1.0 - rss / tss if tss > 0 else float("nan"))


def fit_poly(x, y, degree=2):
    """LAYER (i): one global polynomial ``f(m) = b0 + b1 m + ... + bd m^d`` by least squares.

    The simplest smooth there is, and the one whose failure motivates splines: a single global
    polynomial has to bend everywhere at once, so a change of slope at the deep-OTM end drags the
    fit at the at-the-money end with it.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    # Centred and scaled before powering, so the design matrix is not ill-conditioned when x sits
    # in a narrow band like 0.80-1.00 (raw powers of 0.9 are nearly collinear).
    mu, sd = float(x.mean()), float(x.std()) or 1.0
    B = np.vander((x - mu) / sd, degree + 1, increasing=True)
    coef, fit = _fixed_fit(x, y, B)

    def predict(xq):
        return np.vander((np.atleast_1d(np.asarray(xq, dtype=float)) - mu) / sd, degree + 1,
                         increasing=True) @ coef

    return fit._replace(predict=predict)


def fit_regression_spline(x, y, interior_knots, degree=3):
    """LAYER (ii): an UNPENALISED cubic regression spline with the given interior knots.

    Piecewise cubic, joined smoothly (continuous up to the second derivative) at each knot, fitted
    by plain least squares. It fixes layer (i)'s problem, since each piece bends independently, but
    swaps it for a new one: the fit depends on how many knots are chosen and where, and nothing in
    the data chooses them. Layer (iii), ``fit_1d``, answers that by placing many knots on a regular
    grid and letting a penalty, tuned by GCV, decide how much of that flexibility to use.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y)
    x, y = x[keep], y[keep]
    lo, hi = float(x.min()), float(x.max())
    inner = np.sort(np.asarray(interior_knots, dtype=float))
    # Full knot vector: the boundary knots repeated degree+1 times (a clamped spline), the interior
    # ones once each. n_basis = len(inner) + degree + 1.
    t = np.concatenate([np.full(degree + 1, lo), inner, np.full(degree + 1, hi)])
    B = np.asarray(BSpline.design_matrix(x, t, degree).todense())
    coef, fit = _fixed_fit(x, y, B)

    def predict(xq):
        xc = np.clip(np.atleast_1d(np.asarray(xq, dtype=float)), lo, hi)
        return np.asarray(BSpline.design_matrix(xc, t, degree).todense()) @ coef

    return fit._replace(predict=predict)


# ============================================================================
# 2-D
# ============================================================================

def _tensor_design(bx, by):
    """Row-wise Kronecker product: one row per observation, ``n_x * n_y`` columns.

    NOT ``np.kron(bx, by)``, which would build an (n^2)-row matrix pairing every observation's x
    basis with every OTHER observation's y basis. The tensor basis is per-observation — row i is
    the outer product of row i's two marginal bases, flattened.
    """
    return (bx[:, :, None] * by[:, None, :]).reshape(len(bx), -1)


def fit_2d(x, y, z, n_basis=6, degree=3, lam_grid=None, penalty_order=2):
    """A penalised TENSOR-PRODUCT smooth over both parameters. Anisotropic: one smoothing
    parameter per axis, because the two axes are different quantities measured in different
    units and forcing them to share a penalty would impose a relationship nobody argued for.

    The penalty is ``lam_x * (P_x (x) I) + lam_y * (I (x) P_y)`` — second differences ALONG each
    axis, which is the standard tensor P-spline. Returns a ``SmoothFit`` whose ``lam`` is the
    chosen pair.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    z = np.asarray(z, dtype=float)
    keep = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[keep], y[keep], z[keep]
    if len(x) < 9:
        raise ValueError(f"need at least 9 finite design points for a 2-D smooth, got {len(x)}")

    lox, hix = float(x.min()), float(x.max())
    loy, hiy = float(y.min()), float(y.max())
    nx = max(min(n_basis, len(np.unique(x))), degree + 1)
    ny = max(min(n_basis, len(np.unique(y))), degree + 1)

    bx = _design(x, lox, hix, nx, degree)
    by = _design(y, loy, hiy, ny, degree)
    B = _tensor_design(bx, by)

    dx, dy = _penalty(nx, penalty_order), _penalty(ny, penalty_order)
    px = np.kron(dx.T @ dx, np.eye(ny))
    py = np.kron(np.eye(nx), dy.T @ dy)

    # Coarser than the 1-D sweep because this is a product grid: 25 x 25 would be 625 solves of
    # a (nx*ny)-square system for no extra resolution that a reader could act on.
    lam_grid = np.logspace(-5, 5, 21) if lam_grid is None else np.asarray(lam_grid, dtype=float)
    tss = float(((z - z.mean()) ** 2).sum())
    # DESCENDING on both axes — smoothest corner first, so a tie keeps the smoother pair.
    descending = np.sort(lam_grid)[::-1]
    candidates = []
    for lx in descending:
        for ly in descending:
            coef, edf, rss = _solve(B, lx * px + ly * py, z, 1.0)
            candidates.append((_gcv(rss, edf, len(z), tss),
                               ((float(lx), float(ly)), coef, edf, rss)))
    gcv, (lam, coef, edf, rss) = _choose_lam(candidates)

    r2 = 1.0 - rss / tss if tss > 0 else float("nan")

    def predict(xq, yq):
        xq = np.atleast_1d(np.asarray(xq, dtype=float))
        yq = np.atleast_1d(np.asarray(yq, dtype=float))
        bxq = _design(xq, lox, hix, nx, degree)
        byq = _design(yq, loy, hiy, ny, degree)
        return _tensor_design(bxq, byq) @ coef

    return SmoothFit(predict=predict, lam=lam, edf=float(edf), gcv=float(gcv), r2=float(r2))


# ============================================================================
# READING AN OPTIMUM OFF A FIT
# ============================================================================

def optimum(fit, bounds, n_mesh=201):
    """The argmax of a fit over a fine mesh, WITH the local curvature around it.

    ``bounds`` is ``(lo, hi)`` for a 1-D fit or ``((lox, hix), (loy, hiy))`` for a 2-D one.

    THE CURVATURE IS A DELIVERABLE, NOT A DIAGNOSTIC. "A broad flat top" and "a spike" are the
    two answers this plan exists to distinguish, and they look identical in a table of ranked
    cells: both have a best row. Curvature near zero says the surface barely cares where the
    parameter sits inside a region — which would be the strongest possible result, because a
    recommendation that does not need precision is a recommendation that can survive being
    rounded to a tradeable strike. A large negative curvature says the opposite, and makes the
    snapping cost below a live concern rather than a formality.

    Reported as the second derivative (1-D) or the MOST NEGATIVE Hessian eigenvalue (2-D) — the
    sharpest direction, since a surface that is flat along one axis and spiked along the other
    is spiked.
    """
    two_d = hasattr(bounds[0], "__len__")
    if not two_d:
        lo, hi = bounds
        xs = np.linspace(lo, hi, n_mesh)
        vals = np.asarray(fit.predict(xs), dtype=float)
        i = int(np.nanargmax(vals))
        h = (hi - lo) / (n_mesh - 1)
        # Central second difference, clamped away from the mesh ends where it is undefined. An
        # optimum ON the boundary is itself a finding — it means the axis was cut too narrow —
        # so the curvature is reported from the nearest interior point rather than suppressed.
        j = int(np.clip(i, 1, n_mesh - 2))
        curv = float((vals[j - 1] - 2 * vals[j] + vals[j + 1]) / h ** 2)
        return Optimum(point=float(xs[i]), value=float(vals[i]), curvature=curv)

    (lox, hix), (loy, hiy) = bounds
    xs = np.linspace(lox, hix, n_mesh)
    ys = np.linspace(loy, hiy, n_mesh)
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    vals = np.asarray(fit.predict(gx.ravel(), gy.ravel()), dtype=float).reshape(gx.shape)
    i, j = np.unravel_index(int(np.nanargmax(vals)), vals.shape)
    hx = (hix - lox) / (n_mesh - 1)
    hy = (hiy - loy) / (n_mesh - 1)
    ii = int(np.clip(i, 1, n_mesh - 2))
    jj = int(np.clip(j, 1, n_mesh - 2))
    fxx = (vals[ii - 1, jj] - 2 * vals[ii, jj] + vals[ii + 1, jj]) / hx ** 2
    fyy = (vals[ii, jj - 1] - 2 * vals[ii, jj] + vals[ii, jj + 1]) / hy ** 2
    fxy = ((vals[ii + 1, jj + 1] - vals[ii + 1, jj - 1]
            - vals[ii - 1, jj + 1] + vals[ii - 1, jj - 1]) / (4 * hx * hy))
    eig = np.linalg.eigvalsh(np.array([[fxx, fxy], [fxy, fyy]]))
    return Optimum(point=(float(xs[i]), float(ys[j])), value=float(vals[i, j]),
                   curvature=float(eig.min()))


def snap_to_strike(moneyness, spot, increment=1.0, predict=None, other=None):
    """The nearest TRADEABLE strike to a continuous optimum, and what snapping to it cost.

    A surface's optimum is a real number — "strike at 0.9137 of spot" — and no exchange lists
    that. SPY options trade in whole-dollar (and sub-dollar near the money) increments, so the
    deployable parameter is whatever the continuous optimum rounds to. If the score difference
    between the two is MATERIAL, the surface is too sharp to trust and that is itself the
    finding, not a rounding footnote.

    ``predict`` and ``other`` are optional: pass the fit's predict and (for a 2-D fit) the value
    of the other axis to get ``cost`` filled in, otherwise it is NaN. Cost is defined as
    continuous value minus snapped value, so it is non-negative whenever ``moneyness`` really is
    the argmax.
    """
    strike = float(np.round(moneyness * spot / increment) * increment)
    snapped = strike / spot
    cost = float("nan")
    if predict is not None:
        if other is None:
            a = float(np.asarray(predict(moneyness)).ravel()[0])
            b = float(np.asarray(predict(snapped)).ravel()[0])
        else:
            a = float(np.asarray(predict(moneyness, other)).ravel()[0])
            b = float(np.asarray(predict(snapped, other)).ravel()[0])
        cost = a - b
    return Snap(moneyness=snapped, strike=strike, cost=cost)
