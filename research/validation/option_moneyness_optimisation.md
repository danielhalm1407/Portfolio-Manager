# Optimising put option moneyness for drawdown-adjusted returns

*SPY, 5,201 daily bars, 2006-01-09 → 2026-09-18, priced with the ticker's own measured implied
volatility. 63 parameter combinations, each simulated once over the full history and scored on six
indicative windows. Every put is sized to the same premium budget. Script:
[`option_moneyness_optimisation.py`](option_moneyness_optimisation.py).*

## Why this exists

Two things are established by this point. [Pricing](../../docs/probing/index.html) showed that
protection cost is steeply convex in the strike — an at-the-money put costs roughly 14% of
notional a year, a 0.90× put about 3.4%, a 0.80× put almost nothing — and that a hedge which is
never closed early can double in value and still expire worthless. [Running the
structures](../../docs/validation/index.html) through the 2007-09 crisis showed that adding a
monetisation rule was worth about seven percentage points against an otherwise identical blind
roll.

Both of those used hand-picked numbers: a 0.90 strike and a 10% monetisation trigger, chosen to
exercise the mechanism. Neither was selected, and a threshold tested on the window that motivated
it establishes nothing about the threshold.

This section asks the parameter question properly. **How far out of the money should the put be
struck, and how far should the market fall before the hedge is closed?**

The answer has to be read on both kinds of regime, and that constraint shapes everything below. In
a crisis window, more protection always looks better — the hedge pays and the carry it cost over
the preceding quiet years happened outside the window. Scored on crises alone, the exercise would
mechanically select the most expensive hedge available. The calm windows are what turn it into a
trade-off rather than a tautology.

## The design

Two axes, full Cartesian product, 63 combinations:

| axis | values |
|---|---|
| strike, as a fraction of spot | 0.800, 0.825, 0.850, 0.875, 0.900, 0.925, 0.950, 0.975, 1.000 |
| monetisation trigger, drawdown below running peak | none (blind roll), 6%, 8%, 10%, 12%, 16%, 20% |

Tenor is held fixed at 63 bars. The "none" column is the blind roll — the reference the whole
monetisation question is asked against. After a monetisation the hedge waits until implied vol is
back under 0.18 before buying again, and reopens after 126 flat bars regardless.

Each combination is simulated **once** over the full twenty years, and every window is then scored
by slicing the stored result. This is not only a cost saving (63 runs rather than 378); it is also
the right measurement. A standing hedge programme entering a crisis is already carrying a
position and has already paid premium for years. Scoring a slice reproduces that; restarting flat
at each window's first bar would not.

### Every strike spends the same money

Every put is sized to an **equal premium budget**: each period the hedge buys as many contracts at
its strike as the premium of one 0.95× put would buy, per unit of SPY held. A 0.95× hedge holds one
put per unit; a 0.80× hedge holds about twelve and a half.

This matters more than anything else on the page. Buying one put per unit at every strike, as the
first version of this study did, compares a 0.80× hedge that cost almost nothing with a 0.95× hedge
that cost several times as much. The deep strike's value could reach seven times its premium and
still barely move the portfolio — seven times almost nothing is almost nothing — so the strike axis
was really a *spend* axis in disguise. Holding the spend fixed turns the question into the one worth
asking: **for the same money, which strike protects best?**

Measured over the full history, the budget costs between 5.7% and 7.1% of equity a year depending on
how often each configuration monetises and reopens.

### Windows

Three drawdowns and three calm stretches:

| window | period | kind | SPY return | SPY max drawdown | peak implied vol |
|---|---|---|---|---|---|
| GFC | 2007-10 → 2009-03 | drawdown | −48.5% | 56.5% | 0.76 |
| COVID | 2020-02 → 2020-03 | drawdown | −20.5% | 34.1% | 0.79 |
| Rate shock | 2022-01 → 2022-10 | drawdown | −19.2% | 25.4% | 0.31 |
| | 2013-01 → 2015-06 | calm | +40.8% | 7.7% | 0.26 |
| | 2017-01 → 2017-12 | calm | +18.5% | 3.0% | 0.13 |
| | 2023-01 → 2024-12 | calm | +53.9% | 10.3% | 0.29 |

Nothing starts before 2006, because that is where measured implied volatility begins.

## How a strike wins — the mechanism

Before any grid, the paths. Every figure in this part has the same three stacked panels on one
time axis: **equity against SPY** with every fill marked (▲ opened, ◆ rolled, ▼ monetised —
hover any marker for the legs traded, what they cost or returned, how many contracts were bought,
and the rule's state, drawdown, value multiple and implied vol on that bar), then **the long leg's
value as a multiple of the premium paid**, then **implied vol** with the re-entry level and a band
wherever that strike sat unhedged.

The middle panel is where moneyness shows. A deep out-of-the-money put is almost all volatility
exposure: it does little until the market falls a long way *and* implied vol jumps, and then its
value multiplies far faster than a near-the-money put's. Under equal-budget sizing those multiples
are on many more contracts, so they reach the portfolio. Whether that wins depends on the *shape* of
the fall: how deep, how fast, and how far implied vol spikes.

## Step 1 — the blind roll, through the crisis

Four strikes, all sized to the same budget, held to expiry and re-struck every 63 bars, starting
flat at the GFC's first bar.

| strike | return | max drawdown | score |
|---|---|---|---|
| 0.80× | −66.0% | 66.2% | 0.56 |
| 0.85× | −62.0% | 62.2% | 0.67 |
| 0.90× | −53.5% | 53.8% | 0.95 |
| 0.95× | −42.7% | 43.2% | 1.46 |
| SPY | −48.5% | 56.5% | 1.00 |

Blind, the deep strikes are a disaster — worse than holding no hedge at all. The middle panel shows
why: the 0.80× position's value climbs to many times its premium into each leg of the fall and then
decays back towards zero before expiry, because nothing in the rule ever takes the money. Twelve
contracts' worth of premium is spent every quarter and given straight back.

## Step 2: the same ladder, monetised at a 10% drawdown

One thing changes: the hedge is closed when SPY is 10% below its running peak.

| strike | return | max drawdown | score | monetisations |
|---|---|---|---|---|
| 0.80× | −36.6% | 43.6% | **1.60** | 3 |
| 0.85× | −37.8% | 44.9% | 1.52 | 3 |
| 0.90× | −39.3% | 46.6% | 1.43 | 3 |
| 0.95× | −40.1% | 47.5% | 1.38 | 3 |

The order reverses completely. Taken at the moment the trigger fires, the deep strike's large
multiple is cashed rather than decayed, and on twelve contracts it is the best hedge of the four.
Every strike now beats SPY on both return and drawdown.

## Step 2b — blind against monetised, two strikes

The 0.85× and 0.95× strikes, each blind and monetised, on one axis. Arming the trigger moved the
0.85× hedge from 0.67 to 1.52 and the 0.95× hedge from 1.46 to 1.38 — a large gain for the deep
strike and a small loss for the near one. The two decisions are **not separable**: how much the
monetisation rule is worth depends on the strike, so the surface below has a genuine interaction
between its two axes, and reading either axis alone would mislead.

## The mechanism, window by window

The same four strikes at the 10% trigger, sliced from the full-history standing programme — so
each window opens with a hedge already in place, as it would for a real holder.

| window | 0.80× | 0.85× | 0.90× | 0.95× | best |
|---|---|---|---|---|---|
| GFC 2007-2009 | 1.32 | 1.29 | 1.39 | **1.42** | 0.95× |
| COVID 2020 | **1.86** | 1.61 | 1.36 | 1.10 | 0.80× |
| Rate shock 2022 | **2.08** | 1.91 | 1.63 | 1.35 | 0.80× |
| 2013-2015 | 0.88 | 0.89 | **0.91** | 0.90 | 0.90× |
| 2017 | **0.99** | 0.94 | 0.89 | 0.84 | 0.80× |
| 2023-2024 | 1.03 | 1.12 | **1.19** | 1.13 | 0.90× |

Reading the panels against the hypothesis that a sharp, vol-spiking crash favours deep strikes and a
slower decline without a vol spike favours nearer ones:

- **COVID is the clean case for the first half.** Implied vol went from 0.16 to 0.79 in a month.
  The trigger fired on 27 February, a week into the fall, and by then the 0.80× position was already
  worth **16.7 times** its premium against 5.8 for the 0.95×. Vol then stayed far above the 0.18
  re-entry level, so every strike sat unhedged through the rest of March. The difference between
  strikes is therefore almost entirely what was cashed on that one day, and the score ordering
  follows the strikes exactly, deepest best.
- **The GFC is the one window where the nearest strike wins**, narrowly. It was a long grind with
  several legs down. The standing programme monetised three times inside it, and between
  monetisations it sat out stretches waiting for vol to fall back under 0.18, so the deep strikes'
  bigger multiples (8.2× against 4.0×) were partly paid for with unhedged time. This is the
  slow-decline pattern the hypothesis expects.
- **The 2022 rate shock does not fit the second half.** It was slow and vol never passed 0.31, yet
  the deepest strike won by a wide margin. Three monetisations at a 10% trigger cashed the deep
  hedge's 3.8× each time the fall resumed, and at twelve contracts per unit that was enough. A
  slow decline favours nearer strikes only if nothing cashes the deep hedge along the way.
- **In calm windows, every strike loses some return to premium** — that is unavoidable at a fixed
  budget — and the score turns on whether the hedge also trimmed the small drawdowns there were.
  2013-2015 and 2017 never monetise, and every strike scores just below SPY. 2023-2024 opened with
  vol at 0.22 and one monetisation; the middle strikes beat the index there.

So the hypothesis survives in its strong form only for the sharp crash. **The monetisation trigger
changes it**: with the trigger armed, the deep strike's convexity is cashed at each leg of a slow
decline too, and deep strikes win more often than the hypothesis predicts.

## Building up the score — maximum drawdown on its own

The score is built from two ingredients, and each is worth seeing alone first. Every grid below is
strike against window at the 10% trigger, with SPY as its own row and the 5%-a-year portfolio-budget
hedge (see the last section) beneath it. Colour is normalised within each column, so it ranks inside
a window; the printed number is the raw value.

Maximum drawdown first, where lower is better. Through all three crises **every strike improved on
SPY**: the GFC's 56.5% came down to 47-49%, COVID's 34.1% to 22-32%, the rate shock's 25.4% to
14-20%, with the deep strikes cutting most in the last two. In calm windows there is little to cut.
2013-2015 matched SPY's 7.7% to within a fraction of a point, 2017 came out marginally *deeper*
than SPY's 3.0% for the nearer strikes, and 2023-2024's 10.3% fell to around 8%.

## Building up the score — return on its own

Total return, where higher is better. In crises every hedge lost less than SPY. In calm windows every
hedge earned less: 2013-2015 returned about 26% against SPY's 41%, and 2023-2024 28-40% against 54%.
That gap is the premium budget, and it is the price of everything in the previous grid.

## Return per unit of drawdown

Combining the two as (1 + R) / MDD: growth of a unit of wealth per unit of drawdown suffered.

<div class="math">
(1 + R<sub>w</sub>) / max( MDD<sub>w</sub> , 0.02 )
<span class="where">R<sub>w</sub> total return over the window · MDD<sub>w</sub> maximum peak-to-trough drawdown within it, floored at 2%</span>
</div>

Unlike a plain Calmar ratio this cannot invert. 1 + R is a wealth multiple, positive for any loss
short of total ruin, so a deeper drawdown can only lower it. It is still not comparable *across*
windows — 2017's tiny drawdowns put every configuration near 35, the GFC's near 1.2 — so it ranks
within a column but cannot be pooled.

## The optimisation metric

Dividing through by SPY's own value on the same window fixes that:

<div class="math">
score = [ (1 + R<sub>w</sub>) / (1 + R<sub>w</sub><sup>SPY</sup>) ] / [ max(MDD<sub>w</sub>, f) / max(MDD<sub>w</sub><sup>SPY</sup>, f) ]
<span class="where">relative wealth over relative drawdown, both measured on the same slice · f = 2% · SPY scores exactly 1.0 on every window</span>
</div>

Above 1.0 a configuration ended richer than SPY, fell less, or both. Because both factors are
positive, the score always rises with return and falls with drawdown, on winning and losing windows
alike. Because SPY is 1.0 everywhere, every window shares one scale, so this grid uses a single
colour scale centred on 1.0 rather than one per column.

**This replaces the floored Calmar ratio the first version of this study used**, which had a defect
that mattered. When the window return is negative, dividing by a *larger* drawdown moves the ratio
*towards* zero, so a deeper drawdown scored better. On the GFC, a 0.80× hedge that lost 54.0% with
a 59.5% drawdown outranked a 0.95× hedge that lost 42.4% with a 44.5% drawdown. All three crisis
windows have negative returns, so every crisis ranking inherited the inversion. The floored Calmar
is still computed on every row for comparison; nothing ranks on it.

The drawdown floor never binds: every window here, calm ones included, has a drawdown of at least
3%.

## Nothing works in both regimes — revisited

The first version of this study found an actively *negative* trade-off: a rank correlation of
−0.64 between the crisis set and the calm set, and no configuration in the top ten of both. Under
equal-budget sizing that is gone:

| | one put per unit, floored Calmar | one put per unit, this score | equal budget, this score |
|---|---|---|---|
| Rank correlation, crisis set vs calm set | −0.64 | −0.43 | **+0.13** |
| Configurations in the top ten of both | 0 | 0 | **2** |
| Best crisis configuration | 1.00× | 1.00× blind | 0.80× / 20% |

The middle column separates the two changes. The new metric alone leaves the trade-off negative and
still crowns the at-the-money put. **The sizing is what changes the answer**: once every strike
spends the same money, deep strikes stop being token hedges, and the configurations that do well in
crises are no longer the ones that bleed worst in calm markets.

Configurations are ranked within each regime set and sorted by the *worse* of the two ranks,
because averaging would let a configuration that tops one set and bottoms the other look like a
compromise. By that measure the best is **0.80× with an 8% trigger**: 8th in the crisis set and 8th
in the calm set. The two configurations in both top tens are 0.80× and 0.825×, both at 8%. The single
best crisis configuration (0.80× / 20%) is 23rd in calm markets; the best calm configuration
(0.90× / 6%) is 36th in crises. The blind rolls of the three deepest strikes are the three worst
configurations of all 63 in both sets.

## The quiet decades no longer decide it

The spread of scores *within* each window says where the parameter choice actually bites:

| window | kind | spread across all 54 monetising configurations |
|---|---|---|
| GFC 2007-2009 | drawdown | 2.30 |
| COVID 2020 | drawdown | **4.50** |
| Rate shock 2022 | drawdown | 1.78 |
| 2013-2015 | calm | 0.74 |
| 2017 | calm | **0.38** |
| 2023-2024 | calm | 0.46 |

This is the reverse of the first version, where the GFC spread was 0.088 and the calm windows
spanned up to 5. Under one-put-per-unit sizing every hedge looked alike in a crash because the
cheap ones bought almost nothing. Under equal budgets the crash is where the strike and the trigger
matter most, and calm markets, where every configuration pays the same premium, are where they
matter least.

The figure below is the full design: strike across, trigger down, colour the score averaged over
all six windows. Nothing here is smoothed.

## Fitting a curve — the observations at a fixed trigger

The grid gives 54 points per window, and a smooth fitted through them lets the optimum be read
between grid points as well as on them. Before any surface, the same idea in one dimension: fix the
trigger at 10% and plot the score against the strike, **one point per strike per window** — six
observations stacked above each of nine strikes. Filled markers are crisis windows, open markers
calm ones; the dashed line is SPY.

Two things are visible before any fitting. The spread *between* windows at a given strike is far
larger than the change *along* the strike axis. And within each window the points mostly fall, or
stay flat, as the strike moves towards the money: averaged over windows the score runs from 1.36 at
0.80× down to 1.08 at 1.00×.

## Layer 1 — a global polynomial

The simplest smooth is one polynomial for the whole axis, fitted by least squares:

<div class="math">
f(m) = β<sub>0</sub> + β<sub>1</sub> m + β<sub>2</sub> m<sup>2</sup>
&nbsp;&nbsp;&nbsp; minimising &nbsp; Σ<sub>i</sub> ( y<sub>i</sub> − f(m<sub>i</sub>) )<sup>2</sup>
<span class="where">m moneyness (strike / spot) · y<sub>i</sub> the score of observation i · three coefficients, so three degrees of freedom</span>
</div>

Its weakness is that it is *global*: one set of coefficients has to fit both ends at once, so a bend
at the deep-out-of-the-money end drags the fit at the at-the-money end with it. Here the data barely
curve, so the quadratic comes back close to a straight line (in-sample R² 0.091, peak at 0.80×).

## Layer 2 — a regression spline with knots

A spline cuts the axis at **knots** and fits a separate cubic on each piece, joined so that the
curve, its slope and its curvature are all continuous at every knot. In B-spline form:

<div class="math">
f(m) = Σ<sub>j=1</sub><sup>K+4</sup> α<sub>j</sub> B<sub>j</sub>(m)
&nbsp;&nbsp;&nbsp; minimising &nbsp; Σ<sub>i</sub> ( y<sub>i</sub> − f(m<sub>i</sub>) )<sup>2</sup>
<span class="where">B<sub>j</sub> cubic B-spline basis functions, each non-zero over at most four adjacent pieces · K interior knots, here three, at 0.85, 0.90 and 0.95 (dotted verticals) · K + 4 = 7 coefficients</span>
</div>

Each piece bends on its own, which fixes the polynomial's problem, but swaps it for a new one:
nothing in the data chooses how many knots to use or where to put them. Too few and the curve
cannot bend where it needs to; too many and it chases every point. With three hand-placed knots it
fits no better than the quadratic here (R² 0.092) and spends seven coefficients doing it.

## Layer 3 — a penalised spline, smoothing chosen by the data

The fit actually used is a **P-spline** (Eilers and Marx): many knots on an evenly spaced grid, and a
penalty on how sharply neighbouring coefficients change, so that the *penalty* rather than the knot
count controls how wiggly the curve can be.

<div class="math">
minimise &nbsp; ‖ y − Bα ‖<sup>2</sup> + λ ‖ D<sub>2</sub> α ‖<sup>2</sup>
&nbsp;&nbsp;&nbsp; so &nbsp; α̂ = ( BᵀB + λ D<sub>2</sub>ᵀD<sub>2</sub> )<sup>−1</sup> Bᵀy
<span class="where">B the n × 8 cubic B-spline basis on six evenly spaced knots from 0.80 to 1.00 (dotted verticals) · D<sub>2</sub> second differences, (D<sub>2</sub>α)<sub>j</sub> = α<sub>j</sub> − 2α<sub>j+1</sub> + α<sub>j+2</sub> · λ ≥ 0 the smoothing parameter</span>
</div>

λ = 0 is the unpenalised regression spline of layer 2 on more knots. As λ grows, the penalty
flattens the second differences, and the limit is a **straight line**, because a straight line has
zero second differences. That makes a straight line the honest null hypothesis: the curve bends
only if the data pay for the bend.

λ is chosen by **generalised cross-validation**, an approximation to leaving each point out in turn:

<div class="math">
GCV(λ) = n · RSS(λ) / ( n − edf(λ) )<sup>2</sup>
&nbsp;&nbsp;&nbsp; edf(λ) = trace[ B ( BᵀB + λ D<sub>2</sub>ᵀD<sub>2</sub> )<sup>−1</sup> Bᵀ ]
<span class="where">RSS residual sum of squares · edf effective degrees of freedom: about 2 for a straight line, up to the coefficient count for an unpenalised fit</span>
</div>

This differs from a classical **smoothing spline**, which puts a knot at *every* distinct data value
and penalises the integrated squared second derivative ∫ f″(m)² dm. The two are close relatives —
a second-difference penalty on evenly spaced B-spline coefficients is a discrete version of that
integral — and with nine distinct strikes they would give nearly the same curve. The P-spline is
used because it needs eight coefficients rather than one per point and gives λ, edf and GCV in
closed form.

**Here GCV picks the straight line**: λ at the top of its range, edf 2.0, R² 0.091. The data do not
support any bend. Within this axis the score simply falls as the strike approaches the money, so the
1-D optimum is the deep edge, 0.80×.

## Two dimensions — the tensor-product surface

The same construction extends to both axes at once. The basis is the product of a moneyness basis
and a trigger basis, with one penalty along each axis:

<div class="math">
f(m, d) = Σ<sub>j</sub> Σ<sub>k</sub> α<sub>jk</sub> B<sub>j</sub>(m) C<sub>k</sub>(d)
<br>
minimise &nbsp; ‖ y − (B ⊙ C) α ‖<sup>2</sup> + λ<sub>m</sub> ‖ (D<sub>2</sub> ⊗ I) α ‖<sup>2</sup> + λ<sub>d</sub> ‖ (I ⊗ D<sub>2</sub>) α ‖<sup>2</sup>
<span class="where">m moneyness, d monetisation drawdown · B<sub>j</sub>, C<sub>k</sub> cubic B-spline bases, six per axis, so 36 coefficients · ⊙ the row-wise product of the two bases · two smoothing parameters, because the axes are different quantities in different units, both chosen by GCV</span>
</div>

Two cautions on reading these surfaces, both measured rather than assumed.

**The fits sit close to interpolation.** At a fixed window the simulator is deterministic, so there
is no noise for cross-validation to regularise against and it drives both smoothing parameters
towards their floor (edf 21.6 to 30.5 of 36 per window). A fitted optimum's *position* is reliable
where it agrees with the best simulated cell — it does, in every window, to within 0.02 of moneyness
— but its *height* is not: the rate-shock surface claims 3.38 where the best simulated cell scored
2.81. Positions can be quoted. Heights cannot.

**Effective degrees of freedom do not measure sharpness here.** For the same reason, edf reports how
easily the basis represents the surface rather than how sharply it is peaked. On a controlled test a
broad bowl scored 31.3 effective degrees of freedom and a narrow spike 21.9, the wrong way round.
Curvature at the optimum separates the two cleanly (−8 against −3,232) and is the quantity quoted.

## The optimum moves between regimes

| window | kind | fitted optimum: strike / trigger | curvature |
|---|---|---|---|
| GFC 2007-2009 | drawdown | 0.800 / 20% | −390 |
| COVID 2020 | drawdown | 0.855 / 20% | −7,059 |
| Rate shock 2022 | drawdown | 0.824 / 14% | −5,951 |
| 2013-2015 | calm | 0.920 / 6% | −288 |
| 2017 | calm | 0.829 / 6% | −13 |
| 2023-2024 | calm | 0.861 / 8% | −1,522 |

The *strike* now agrees across regimes far more than it did: five of six windows put it between
0.80× and 0.86×, with only 2013-2015 nearer the money. The *trigger* still splits cleanly by regime:
every crisis prefers a deep trigger (14-20%), letting the hedge run well into the fall, and every
calm window prefers the shallowest (6-8%), cashing small gains before they decay. That is the
regime-dependence that remains, and it now sits on the trigger axis rather than the strike axis.

## Pooling across windows

Fitting one surface to all 378 observations directly is dominated by which window each row came
from: crisis scores run around 2-3 and calm ones around 1. Removing each window's mean first leaves
the parameter effect common to all regimes, measured as a deviation from each window's own average.
That fit (edf 11.3, R² 0.21) puts its optimum at the **corner of the design: 0.80× with a 20%
trigger**.

The height of that surface is a deviation from a window mean, **not** a score. The optimum is an
in-sample optimum and nothing downstream consumes it. And a corner optimum is a warning rather than
an answer: within this design the surface is still rising as it reaches the deep-strike edge, so
"0.80×" partly means "the axis stopped there".

## Snapping to a tradeable strike

A fitted optimum is a real number and no exchange lists most of them. At a spot of 761.69 the
nearest listed strike to 0.800 of spot is **609**, a moneyness of 0.7995, and the fitted score
difference is zero to five decimal places.

That result is weaker than it looks this time. The optimum sits on the boundary, where the fitted
surface is clipped at its edge, so a near-zero snapping cost says nothing about how sharp the
surface is. It is reported because the question will matter again once the moneyness axis is
extended below 0.80 and the optimum, if there is one, sits in the interior.

## The surface in three dimensions

The same window-demeaned pooled surface as a rotatable 3-D object, with the continuous optimum and
the nearest listed strike marked. Rotating it needs WebGL in the browser. Where WebGL is off — an
editor's built-in preview, or a browser with graphics acceleration disabled — a static rendering of
the same figure appears here instead.

<img id="surface-3d-fallback" src="figures/pooled_surface_3d.png" alt="Pooled window-demeaned surface, static rendering" style="display:none; width:100%; margin:1.5rem 0;">
<script>
  // Show the static PNG only when this browser cannot create a WebGL context, i.e. exactly when
  // the interactive 3-D figure below would render as a blank "WebGL is not supported" panel.
  // The interactive figure itself is then hidden, since all it would show is Plotly's grey
  // "WebGL is not supported" panel; it sits later in the page, hence the wait for the DOM.
  (function () {
    var c = document.createElement("canvas");
    var gl = c.getContext("webgl") || c.getContext("experimental-webgl");
    if (gl) { return; }
    document.getElementById("surface-3d-fallback").style.display = "block";
    document.addEventListener("DOMContentLoaded", function () {
      var div = document.getElementById("fig-pooled_surface_3d");
      if (div && div.closest(".figure")) { div.closest(".figure").style.display = "none"; }
    });
  })();
</script>

## A portfolio budget, for reference

The grid fixes spend *per unit of SPY*, matched to one 0.95× put. A holder is more likely to think
in terms of a share of the portfolio: "I will spend 5% a year on insurance". That is a different
sizing rule — the number of contracts is set by portfolio value and the premium at the chosen strike
— and it was run once over the full history at 0.90× with a 10% trigger, outside the grid.

| window | score | return | max drawdown | SPY return | SPY max drawdown |
|---|---|---|---|---|---|
| GFC 2007-2009 | 1.13 | −45.0% | 53.5% | −48.5% | 56.5% |
| COVID 2020 | 1.42 | −11.1% | 26.8% | −20.5% | 34.1% |
| Rate shock 2022 | 1.38 | −13.2% | 19.7% | −19.2% | 25.4% |
| 2013-2015 | 0.89 | +28.8% | 7.9% | +40.8% | 7.7% |
| 2017 | 0.88 | +14.1% | 3.3% | +18.5% | 3.0% |
| 2023-2024 | 1.29 | +40.4% | 7.3% | +53.9% | 10.3% |

It sits a little below the budget-matched 0.90× hedge through the GFC and close to it elsewhere,
which is what a slightly smaller spend should do. The figure compares the two through the GFC.

## What this establishes, and what it does not

**Established.** Holding the premium budget fixed changes the answer. Deep out-of-the-money puts,
bought in quantity and monetised on a drawdown trigger, beat nearer strikes in two of three crises
and in one of three calm windows, and the crisis/calm trade-off that looked fundamental under
one-put-per-unit sizing largely disappears. The remaining regime-dependence is in the trigger:
crises want it deep, calm markets want it shallow. A monetisation rule is not optional for deep
strikes — blind, they are worse than no hedge.

**Not established.** Nothing here is out-of-sample. Every threshold was read on the same windows it
was scored on, across 63 trials, which is more than enough freedom to fit noise. The grid's best
cell is not a recommendation and is not described as one.

Four specific weaknesses, stated plainly:

1. **The optimum is at the edge of the axis.** The strike axis stops at 0.80× and the pooled
   surface is still rising there. Whether the true optimum is deeper, or there is no interior
   optimum at all, needs the axis extended.
2. **The budget is one choice.** Matching to the 0.95× premium spends 5.7-7.1% of equity a year.
   A smaller budget will change the calm-window numbers more than the crisis ones.
3. **No transaction costs.** Fills are at the model mark. Deep out-of-the-money options trade with
   wide spreads relative to their price, and twelve contracts per unit makes that cost twelve times
   larger. This flatters exactly the configurations that win here.
4. **Six windows.** Three crises is three observations of crisis behaviour, one of them (COVID)
   shorter than a single roll.

The outstanding question is whether any of this survives contact with data it was not fitted on.
Answering it requires a procedure that re-selects parameters on each training split and scores
them on held-out blocks, measuring the *procedure* rather than any fixed parameter set — which is
the next piece of work, and until it is done nothing on this page should be deployed.

## Reproducing this

```bash
python -m pipelines.option_grid_sweep                 # the 63-run sweep, then the page
python -m pipelines.option_grid_sweep --publish-only  # the page only, from the saved sweep
```

About four minutes for all 63 combinations over the full history. The sweep persists a per-bar
series, a trade blotter and the rule's own per-bar state for every combination, so the analysis
script re-reads them without re-simulating anything.
