# Optimising put option moneyness for drawdown-adjusted returns

*SPY, 5,201 daily bars, 2006-01-09 → 2026-09-18, priced with the ticker's own measured implied
volatility. 63 parameter combinations, each simulated once over the full history and scored on six
indicative windows. Script:
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
monetisation question is asked against.

Each combination is simulated **once** over the full twenty years, and every window is then scored
by slicing the stored result. This is not only a cost saving (63 runs rather than 378); it is also
the right measurement. A standing hedge programme entering a crisis is already carrying a
position and has already paid premium for years. Scoring a slice reproduces that; restarting flat
at each window's first bar would not.

### Windows

Three drawdowns and three calm stretches:

| window | period | kind |
|---|---|---|
| GFC | 2007-10 → 2009-03 | drawdown |
| COVID | 2020-02 → 2020-03 | drawdown |
| Rate shock | 2022-01 → 2022-10 | drawdown |
| | 2013-01 → 2015-06 | calm |
| | 2017-01 → 2017-12 | calm |
| | 2023-01 → 2024-12 | calm |

Nothing starts before 2006, because that is where measured implied volatility begins.

### The scoring metric

Each combination is scored on each window by a floored Calmar ratio:

<div class="math">
score = R<sub>w</sub> / max( MDD<sub>w</sub> , 0.02 )
<span class="where">R<sub>w</sub> total return over the window · MDD<sub>w</sub> maximum peak-to-trough drawdown within it</span>
</div>

The running peak resets at each window's first bar, so a window opening mid-decline is scored on
its own terms rather than against a high set years earlier. The denominator is floored at 2%
because a calm window with a 1% drawdown would otherwise produce an enormous and meaningless
ratio. In the event the floor never binds on any of the 378 window-combination pairs — every
window here, calm ones included, has a drawdown of at least 7% — so it has no effect on any number
reported below.

**This metric has a defect that matters, and it is stated here rather than buried.** When the
window return is negative, dividing by a *larger* drawdown moves the ratio *towards* zero, so a
deeper drawdown scores better. Measured on the GFC:

| strike | return | max drawdown | score |
|---|---|---|---|
| 0.80× | −54.0% | 59.5% | **−0.908** |
| 0.95× | −42.4% | 44.5% | **−0.953** |

The 0.95× configuration lost less *and* fell less, and the metric ranked it last. All three
drawdown windows have negative returns, so every crisis-window ranking on this page inherits the
problem. Rankings within the three calm windows, where returns are positive, are unaffected.

The ratio is reported as specified rather than quietly replaced, and crisis-window results below
are read off returns and drawdowns directly rather than off the ranking.

## Nothing works in both regimes

This is the central result, and it is sharper than expected.

| | |
|---|---|
| Rank correlation between the crisis set and the calm set | **−0.637** |
| Configurations in the top ten of both | **none** |
| Best in the crisis set (1.00× / 20% trigger) — its calm rank | **63rd of 63** |
| Best in the calm set (0.93× / 6% trigger) — its crisis rank | 31st |
| Best by worst-of-two ranking (0.93× / 8%) | 21st in each |

The correlation is not merely weak, it is **negative**. Configurations are ranked within each
regime set separately and then sorted by the *worse* of the two ranks, because averaging the two
would let a configuration that tops one and bottoms the other appear as a reasonable compromise.
It is not a compromise. The crisis winner is the single worst configuration of all 63 in calm
markets.

In plain terms: **there is no setting of these two parameters that is simultaneously good at
surviving crashes and good at not bleeding money the rest of the time.** More protection wins in
a crisis and loses in calm, and the grid is dense enough to say that the trade-off is essentially
monotonic rather than having a sweet spot hiding in it.

## The decision is made by the quiet decades, not the crises

The spread of scores *within* each window says where the parameter choice actually bites:

| window | kind | spread across all 54 monetising configurations |
|---|---|---|
| GFC 2007-2009 | drawdown | **0.088** |
| COVID 2020 | drawdown | 0.631 |
| Rate shock 2022 | drawdown | 0.995 |
| 2023-2024 | calm | 2.300 |
| 2017 | calm | 3.380 |
| 2013-2015 | calm | **5.059** |

Through the GFC, every configuration from a 0.80× strike to an at-the-money one, with any trigger
depth, lands within **0.088** of floored Calmar. Through 2013-2015 the same 54 configurations span
**5.059** — nearly sixty times the dispersion.

This inverts the intuitive framing of the problem. The natural assumption is that a hedge is
calibrated for the crisis and the quiet periods are the price paid. The data says the opposite:
**in a severe enough crash, the details of the hedge barely matter — everything loses heavily
together — while in calm markets the choice of strike and trigger is the dominant driver of
outcomes.**

The practical implication is that a hedging programme should be designed primarily around its
carry cost, with crisis performance as a constraint rather than the objective. That is close to
the reverse of how such programmes are usually pitched.

## Per-window score grids

Colour is normalised *within* each window. A single scale across the whole grid would be useless:
scores run around −0.7 through the drawdown windows and around +5 through the calm ones, so a
shared scale would show only which regime was benign — which needs no grid — and would hide the
only thing the grid is for, which is which configuration did better *inside* a given window.

The printed number in each cell is the raw score.

## The fitted response surface

The grid gives 54 points per window. Fitting a smooth surface through them allows the optimum to
be read between grid points as well as on them, and — more usefully — allows the *shape* around
the optimum to be quantified.

The smoother is a penalised tensor-product B-spline with a second-difference roughness penalty,
one smoothing parameter per axis, both chosen by generalised cross-validation. A second-difference
penalty shrinks towards a plane rather than towards a constant, so the heavy-smoothing limit is a
linear trend — the honest null for "does the optimum sit in the interior or at an edge".

Two cautions on reading these surfaces, both measured rather than assumed.

**The fits sit close to interpolation.** The simulator is deterministic at a fixed window, so
there is no noise for cross-validation to regularise against and it drives the smoothing parameter
toward its floor. The consequence is that a fitted optimum's *position* is reliable where it
agrees with the raw grid's best cell — it does, in all six windows, to within one grid step — but
its *height* is not. Three windows report a fitted value above anything actually simulated; the
rate-shock window fits +0.520 where the best simulated cell scored +0.161. Positions can be
quoted. Heights cannot.

**Effective degrees of freedom do not measure sharpness here.** For the same reason — no noise to
trade against — the fitted flexibility reports how easily the basis represents the surface rather
than how sharply it is peaked. On a controlled test, a broad bowl scored 31.3 effective degrees of
freedom against a narrow spike's 21.9, the wrong way round. Curvature at the optimum separates the
two cleanly (−8 against −3,232) and is the quantity quoted.

## The optimum moves between regimes

| window | kind | fitted optimum: strike / trigger |
|---|---|---|
| GFC 2007-2009 | drawdown | 1.000 / 14% |
| COVID 2020 | drawdown | 1.000 / 20% |
| Rate shock 2022 | drawdown | 1.000 / 14% |
| 2013-2015 | calm | 0.940 / 6% |
| 2017 | calm | 0.911 / 6% |
| 2023-2024 | calm | 1.000 / 7% |

Every drawdown window pushes the strike to the at-the-money edge of the axis, and every calm
window pushes the trigger to the shallow edge. Both are *edge* solutions, which is itself
informative: within the tested range the surfaces are largely monotonic, and the crisis windows'
preference for at-the-money protection is in part the metric defect described earlier rather than
an economic result.

A single pooled optimum across all six windows is therefore the wrong object. If anything is
validated out-of-sample later, a regime-conditional rule is the more defensible candidate.

## Pooling across windows, and why the obvious approach fails

Fitting one surface to all 378 observations produces a **flat plane**: R² of 0.009 at the maximum
smoothing parameter.

That is not a failed fit. It is the correct answer to a badly posed question. Scores run around
−0.7 in drawdown windows and +5 in calm ones, so almost all variance in the pooled column is
*which window a row came from* and almost none is the two parameters. Cross-validation sees no
parameter signal and returns a plane.

Removing each window's mean first — leaving the parameter effect common across regimes, measured
as a deviation from each window's own average — gives a fit with something in it: effective
degrees of freedom 8.2, R² 0.236, optimum at a **0.873 strike with a 6% trigger**.

The height of that surface is a deviation from a window mean, **not** a Calmar ratio. The optimum
is an in-sample optimum and nothing downstream consumes it.

## Snapping to a tradeable strike

A fitted optimum is a real number — 0.873 of spot — and no exchange lists that. At a spot of
761.69 the nearest listed strike is **665**, a moneyness of 0.8731, and the fitted score
difference between the two is **zero to five decimal places**.

That is the most comfortable result on the page. The surface is flat enough near its optimum that
rounding to a tradeable strike costs nothing, which means the recommendation — whatever it turns
out to be — does not depend on precision the market cannot deliver.

## What this establishes, and what it does not

**Established.** The two parameters trade off against each other across regimes, strongly and
negatively, with no configuration good at both. The choice is dominated by calm-period carry
rather than by crisis performance, by a factor of roughly sixty in dispersion. The response
surface is broad near its optimum, so the result is robust to rounding.

**Not established.** Nothing here is out-of-sample. Every threshold was read on the same windows
it was scored on, across 63 trials, which is more than enough freedom to fit noise. The grid's
best cell is not a recommendation and is not described as one.

Three specific weaknesses, stated plainly:

1. **The metric inverts on losing windows.** Crisis-window rankings are not interpretable as
   better-or-worse. The drawdown-window conclusions rest on returns and drawdowns read directly,
   not on the ratio.
2. **Optima sit at axis edges.** Within the tested range the surfaces are largely monotonic, so
   "the optimum is at 1.000×" partly means "the axis stopped there".
3. **No transaction costs.** Fills are at the model mark. This flatters configurations that trade
   more, which is systematically the shallow-trigger ones.

The outstanding question is whether any of this survives contact with data it was not fitted on.
Answering it requires a procedure that re-selects parameters on each training split and scores
them on held-out blocks, measuring the *procedure* rather than any fixed parameter set — which is
the next piece of work, and until it is done nothing on this page should be deployed.

## Reproducing this

```bash
python -m pipelines.option_grid_sweep
```

Roughly three minutes for all 63 combinations over the full history. The sweep persists a per-bar
series, a trade blotter and the rule's own per-bar state for every combination, so the analysis
script re-reads them without re-simulating anything.
