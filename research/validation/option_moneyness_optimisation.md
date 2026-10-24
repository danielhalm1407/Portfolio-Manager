# Optimising put option moneyness for drawdown-adjusted returns

*SPY, 5,201 daily bars, 2006-01-09 → 2026-09-18, priced with the ticker's own measured implied
volatility. 63 parameter combinations, each simulated once over the full history and scored on six
indicative windows. Every put is sized to the same premium budget. Script:
[`option_moneyness_optimisation.py`](option_moneyness_optimisation.py).*

## Previous observations motivate this analysis

We previously established, in [pricing the hedge](../../docs/probing/index.html), that the cost of
protection is steeply convex in the strike. An at-the-money put costs roughly 14% of notional a
year, a 0.90× put about 3.4%, and a 0.80× put almost nothing. We also established that a hedge
which is never closed early can double in value and still expire worthless.

We previously established, in [running the hedge structures](../../docs/validation/index.html)
through the 2007-09 crisis, that adding a monetisation rule was worth about seven percentage points
against an otherwise identical blind roll.

Both of those results used hand-picked parameters: a 0.90× strike and a 10% monetisation trigger,
chosen to exercise the mechanism rather than selected. A threshold tested on the window that
motivated it establishes nothing about the threshold. This analysis therefore asks the parameter
question directly: **how far out of the money should the put be struck, and how far should the
market fall before the hedge is closed?**

The answer must be read across both kinds of regime. In a crisis window more protection always
appears better, because the carry it cost during the preceding quiet years falls outside the
window. Scored on crises alone, the exercise would mechanically select the most expensive hedge
available. The calm windows are what turn it into a trade-off rather than a tautology.

## Design of the empirical optimisation analysis

We search the full Cartesian product of two parameters, 63 combinations in total:

| axis | values |
|---|---|
| strike, as a fraction of spot | 0.800, 0.825, 0.850, 0.875, 0.900, 0.925, 0.950, 0.975, 1.000 |
| monetisation trigger, drawdown below running peak | none (blind roll), 6%, 8%, 10%, 12%, 16%, 20% |

The tenor is held fixed at 63 trading days. The "none" column is the blind roll, the reference
against which the whole monetisation question is asked. After a monetisation the hedge waits until
implied volatility is back below 0.18 before buying again, and reopens after 126 unhedged days
regardless.

Each combination is simulated **once** over the full twenty years, and every window is then scored
by slicing the stored result. Beyond saving computation (63 runs rather than 378), this is the
correct measurement: a standing hedge programme entering a crisis already holds a position and has
already paid premium for years. Scoring a slice reproduces that; restarting flat at each window's
first day would not.

### Each strike is allocated the same premium budget

Every put is sized to an **equal premium budget**. At each roll or reopen, the hedge buys as many
contracts at its strike as the premium of one 0.95× put would buy, per unit of SPY held. A 0.95×
hedge therefore holds one put per unit, and a 0.80× hedge about twelve and a half.

This choice matters more than any other on the page. Buying one put per unit at every strike, as
the first version of this study did, compares a 0.80× hedge that cost almost nothing with a 0.95×
hedge that cost several times as much. The deep strike's value could reach seven times its premium
and still barely move the portfolio, so the strike axis was in effect a *spend* axis. Holding the
spend fixed turns the question into the one worth asking: **for the same money, which strike
protects best?** Over the full history this budget costs between 5.7% and 7.1% of equity a year,
depending on how often each configuration monetises and reopens.

### Six indicative windows span crisis and calm regimes

We score every configuration on three drawdowns and three calm stretches:

| window | period | kind | SPY return | SPY max drawdown | peak implied vol |
|---|---|---|---|---|---|
| GFC | 2007-10 → 2009-03 | drawdown | −48.5% | 56.5% | 0.76 |
| COVID | 2020-02 → 2020-03 | drawdown | −20.5% | 34.1% | 0.79 |
| Rate shock | 2022-01 → 2022-10 | drawdown | −19.2% | 25.4% | 0.31 |
| | 2013-01 → 2015-06 | calm | +40.8% | 7.7% | 0.26 |
| | 2017-01 → 2017-12 | calm | +18.5% | 3.0% | 0.13 |
| | 2023-01 → 2024-12 | calm | +53.9% | 10.3% | 0.29 |

No window starts before 2006, because that is where measured implied volatility begins.

## Deeper out-of-the-money puts carry more convexity and decay faster

Before any grid, we examine the paths. Every path figure below shares three stacked panels on one
time axis. The first shows **equity against SPY**, with every fill marked (▲ opened, ◆ rolled,
▼ monetised); hovering over a marker gives the legs traded, what they cost or returned, the number
of contracts bought, and the rule's state, drawdown, value multiple and implied vol on that day.
The second shows **the long leg's value as a multiple of the premium paid**. The third shows
**implied vol and the reentry gate**, with a band wherever a strike sat unhedged.

The second panel is where moneyness shows, and two properties of deep out-of-the-money puts govern
everything that follows.

**Convexity.** A deep out-of-the-money put is almost entirely time value: it does little until the
market falls a long way *and* implied vol jumps, and then its value multiplies far faster than a
near-the-money put's. Under equal-budget sizing those multiples apply to many more contracts, so
they reach the portfolio.

**Time decay.** The same property makes deep puts decay faster. A deep put has value only because
there is still time for the price path to travel down to its strike. As expiry approaches, the
remaining room for that path shrinks, the distance to the strike grows relative to the volatility
left in the option's life, and the value collapses towards zero. A near-the-money put keeps some
intrinsic or near-intrinsic value as expiry nears; a deep one keeps almost none. In proportional
terms, therefore, theta is much more punishing for deep strikes, and a fall that arrives late in a
roll cycle finds the deep put nearly worthless.

Whether a deep strike wins therefore depends on the *shape* and the *timing* of the fall: how deep,
how fast, how far implied vol spikes, and where in the 63-day roll cycle it arrives.

## A blind roll of deep out-of-the-money puts loses more than it protects

Below, we show four strikes, all sized to the same budget, held to expiry and re-struck every 63
days, starting without a position on the first day of the GFC window.

| strike | return | max drawdown | score |
|---|---|---|---|
| 0.80× | −66.0% | 66.2% | 0.56 |
| 0.85× | −62.0% | 62.2% | 0.67 |
| 0.90× | −53.5% | 53.8% | 0.95 |
| 0.95× | −42.7% | 43.2% | 1.46 |
| SPY | −48.5% | 56.5% | 1.00 |

Held blindly, the deep strikes are worse than holding no hedge at all. The middle panel shows why:
the 0.80× position's value rises to many times its premium into each leg of the fall and then
decays back towards zero before expiry, because nothing in the rule ever takes the gain. Twelve
contracts' worth of premium is spent every quarter and largely given back to time decay.

## Monetisation on a drawdown reverses the ordering of strikes

In the figure below, a single rule changes: the hedge is closed when SPY falls 10% below its
running peak.

| strike | return | max drawdown | score | monetisations |
|---|---|---|---|---|
| 0.80× | −36.2% | 43.3% | **1.62** | 3 |
| 0.85× | −37.8% | 44.9% | 1.52 | 3 |
| 0.90× | −39.3% | 46.6% | 1.43 | 3 |
| 0.95× | −40.1% | 47.5% | 1.38 | 3 |

The ordering reverses completely. Taken at the moment the trigger fires, the deep strike's large
multiple is realised rather than decayed, and on twelve contracts it becomes the best of the four
hedges. Every strike now beats SPY on both return and drawdown.

## Monetisation rescues deep strikes from time decay but does not help the near strike

Below, we compare the 0.85× and 0.95× strikes when rolled blindly with the same strikes when
monetised on a 10% drawdown and re-entered once implied vol falls below 0.18.

| strike | blind return | monetised return | blind max drawdown | monetised max drawdown |
|---|---|---|---|---|
| 0.85× | −62.0% | −37.8% | 62.2% | 44.9% |
| 0.95× | −42.7% | −40.1% | 43.2% | 47.5% |

For the 0.85× strike, monetisation recovers more than twenty percentage points of return and cuts
the drawdown by seventeen, despite the hedge sitting unhedged for months after each monetisation.
For the 0.95× strike the effect is small and slightly adverse on drawdown: a near-the-money put
holds its value through expiry, so cashing it early gains little, and the unhedged waits cost
protection. The two decisions are therefore **not separable**. The value of the monetisation rule
depends on the strike, so the surface below has a genuine interaction between its two axes, and
reading either axis alone would mislead.

## The drawdown trigger is restricted to hedges that have earned their premium

The full-history runs revealed a defect in the original monetisation rule. The drawdown trigger
compares SPY with its running peak, and that peak is carried across rolls. It can therefore fire at
a point in the roll cycle where the *current* puts have gained nothing.

In the standing 0.80× programme, the put was struck at the October 2007 peak (strike 125). The
trigger fired on 8 January 2008, with SPY at 138.91 and four trading days before that put expired,
and sold it for **0.009 times** its premium. The rule then sat unhedged until implied vol fell back
below 0.18 in May. A run that happened to roll on 2 January sold the same exposure that day for
1.65 times its premium. The difference was entirely where the fall arrived in the roll cycle, and
it is the time-decay property above at its most extreme.

Every result on this page therefore applies a guard: **the drawdown trigger does not monetise a
hedge worth less than its premium.** If the trigger fires while the multiple is below 1.0, the rule
keeps the position and lets the ordinary roll replace it on schedule. At the 10% trigger used in the
figures below, the guard raised the deep strikes' scores in every window (the 0.80× hedge went from
1.32 to 1.38 in the GFC and from 1.86 to 1.94 in COVID) and left the nearer strikes unchanged.
Across the whole grid its effect is not uniformly positive: at the deepest triggers it lowered the
calm-market scores (0.80× at 20%, from 1.00 to 0.81), because a hedge that is not allowed to sell at
a loss is held, and decays, for longer.

## Performance of the hedge strategy across multiple windows

Below, we show the same four strikes at the 10% trigger in every indicative window, sliced from the
full-history standing programme, so that each window opens with a hedge already in place, as it
would for a real holder. The first table gives SPY's own return and maximum drawdown for comparison;
the second gives each strike's score.

| window | SPY return | SPY max drawdown | 0.80× max drawdown | 0.95× max drawdown |
|---|---|---|---|---|
| GFC 2007-2009 | −48.5% | 56.5% | 47.9% | 47.1% |
| COVID 2020 | −20.5% | 34.1% | 21.3% | 32.1% |
| Rate shock 2022 | −19.2% | 25.4% | 13.4% | 20.3% |
| 2013-2015 | +40.8% | 7.7% | 7.7% | 7.7% |
| 2017 | +18.5% | 3.0% | 3.0% | 3.6% |
| 2023-2024 | +53.9% | 10.3% | 8.1% | 8.3% |

| window | 0.80× | 0.85× | 0.90× | 0.95× | best |
|---|---|---|---|---|---|
| GFC 2007-2009 | 1.38 | 1.30 | 1.39 | **1.42** | 0.95× |
| COVID 2020 | **1.94** | 1.67 | 1.36 | 1.10 | 0.80× |
| Rate shock 2022 | **2.16** | 1.97 | 1.63 | 1.35 | 0.80× |
| 2013-2015 | 0.89 | 0.89 | **0.91** | 0.90 | 0.90× |
| 2017 | **0.99** | 0.94 | 0.89 | 0.84 | 0.80× |
| 2023-2024 | 1.05 | 1.14 | **1.19** | 1.13 | 0.90× |

We read the panels against the hypothesis that a sharp, vol-spiking crash favours deep strikes and a
slower decline favours nearer ones.

- **COVID is the clearest case for the first half of the hypothesis.** Implied vol rose from 0.16 to
  0.79 in a month. The trigger fired on 27 February, a week into the fall, by which point the 0.80×
  position was worth **16.7 times** its premium against 5.8 for the 0.95×. Vol then stayed far above
  the reentry level, so every strike sat unhedged through the rest of March. The difference between
  strikes is almost entirely what was realised on that one day, and the score ordering follows the
  strikes exactly.
- **In the GFC at a 10% trigger, the near strike edges ahead, and the reason is the earlier,
  shallower legs of the decline.** The 0.80× hedge monetised at 1.32 times its premium in January 2008
  and 1.09 times in July; the 0.95× hedge, monetising the same falls, realised 3.60 and 2.46 times.
  A deep put that is struck near a peak and meets only a moderate fall has mostly decayed, so it
  returns little more than it cost, while the near put has gained intrinsic value. The 0.80× book
  therefore entered September 2008 lower. Its monetisation then, at **8.24 times** premium against
  3.98, did not quite close the gap. After that monetisation every strike was unhedged and fell by
  the same proportion, but the 0.80× book fell from a lower level relative to its late-2007 peak,
  which is why its drawdown is marginally deeper (47.9% against 47.1%). At an 8% trigger, which
  monetises earlier in each leg, the ordering reverses: the 0.80× hedge's drawdown is 35.4% against
  43.7% for the 0.95×.
- **The 2022 rate shock does not fit the second half of the hypothesis.** The decline was slow and
  vol never passed 0.31, yet the deepest strike won by a wide margin. Three monetisations realised
  the deep hedge's 3.8 times multiple each time the fall resumed, and at twelve contracts per unit
  that was sufficient.
- **In calm windows every strike gives up return to premium**, which is unavoidable at a fixed
  budget, and the score turns on whether the hedge also trimmed the small drawdowns there were.
  2013-2015 and 2017 never monetised, and every strike scored just below SPY.

The hypothesis therefore holds in its strong form only for the sharp crash. With the trigger armed,
the deep strike's convexity is also realised at each leg of a slow decline, so deep strikes win more
often than the hypothesis predicts, provided each leg is deep enough to give them intrinsic value
before they decay.

## Every strike reduces maximum drawdown relative to SPY in crises

The score is built from two ingredients, and we first examine each on its own. Every grid below
shows strike against window at the 10% trigger, with SPY as its own row and the 5%-a-year
portfolio-budget hedge (see the final analysis section) beneath it. Colour is normalised within
each column, so it ranks configurations inside a window; the printed number is the raw value.

The grid below shows maximum drawdown, where lower is better. Through all three crises, every strike
improved on SPY: the GFC's 56.5% came down to 47-50%, COVID's 34.1% to 21-32%, and the rate shock's
25.4% to 13-20%, with the deep strikes cutting most in the last two. In calm windows there is little
to cut: 2013-2015 matched SPY's 7.7%, 2017 came out marginally deeper than SPY's 3.0% for the nearer
strikes, and 2023-2024's 10.3% fell to around 8%.

## Hedging costs return in calm markets, and deeper strikes cost more

The grid below shows total return, where higher is better. In crises every hedge lost less than SPY.
In calm windows every hedge earned less, and the deeper the strike, the more it gave up: through
2023-2024 the 0.80× hedge returned 27.5% and the 0.95× hedge 39.9%, against SPY's 53.9%. Equal
spend does not mean equal bleed. The deep strike buys twelve times as many contracts, and in a quiet
market all of them decay to nothing, which is the time-decay property in the calm-market setting.

## Combining return and drawdown into a single ratio

The grid below combines the two as (1 + R) / MDD: the growth of a unit of wealth per unit of
drawdown suffered.

<div class="math">
(1 + R<sub>w</sub>) / max( MDD<sub>w</sub> , 0.02 )
<span class="where">R<sub>w</sub> total return over the window · MDD<sub>w</sub> maximum peak-to-trough drawdown within it, floored at 2%</span>
</div>

Unlike a plain Calmar ratio, this cannot invert. 1 + R is a wealth multiple, positive for any loss
short of total ruin, so a deeper drawdown can only lower it. It is still not comparable *across*
windows — 2017's small drawdowns put every configuration near 35, and the GFC's near 1.2 — so it
ranks within a column but cannot be pooled.

## The relative score measures performance against SPY on a common scale

Dividing through by SPY's own value on the same window gives the optimisation metric:

<div class="math">
score = [ (1 + R<sub>w</sub>) / (1 + R<sub>w</sub><sup>SPY</sup>) ] / [ max(MDD<sub>w</sub>, f) / max(MDD<sub>w</sub><sup>SPY</sup>, f) ]
<span class="where">relative wealth over relative drawdown, both measured on the same slice · f = 2% · SPY scores exactly 1.0 on every window</span>
</div>

A score above 1.0 means the configuration ended richer than SPY, fell less, or both. Because both
factors are positive, the score always rises with return and falls with drawdown, on winning and
losing windows alike. Because SPY is 1.0 everywhere, every window shares one scale, so the grid below
uses a single colour scale centred on 1.0 rather than one per column.

**This replaces the floored Calmar ratio used in the first version of this study**, which had a
material defect. When the window return is negative, dividing by a *larger* drawdown moves the ratio
*towards* zero, so a deeper drawdown scored better. On the GFC, a 0.80× hedge that lost 54.0% with a
59.5% drawdown outranked a 0.95× hedge that lost 42.4% with a 44.5% drawdown. All three crisis
windows have negative returns, so every crisis ranking inherited the inversion. The floored Calmar is
still computed on every row for comparison; nothing ranks on it. The drawdown floor never binds:
every window here, calm ones included, has a drawdown of at least 3%.

## Crisis and calm rankings no longer conflict under an equal premium budget

The first version of this study found an actively *negative* trade-off between regimes. The table
below separates the two changes made since.

| | one put per unit, floored Calmar | one put per unit, relative score | equal budget, relative score |
|---|---|---|---|
| Rank correlation, crisis set vs calm set | −0.64 | −0.43 | **+0.09** |
| Configurations in the top ten of both | 0 | 0 | **2** |
| Best crisis configuration | 1.00× | 1.00× blind | 0.80× / 20% |

The new metric alone leaves the trade-off negative and still favours the at-the-money put. **The
sizing is what changes the answer.** Once every strike spends the same money, deep strikes stop being
token hedges, and the configurations that perform well in crises are no longer systematically the
ones that perform worst in calm markets.

That is not to say deep strikes are free in calm markets: as the return grid shows, they bleed more
to time decay. The point is narrower. A deep strike's crisis performance now justifies its cost often
enough that it is no longer pushed to the bottom of the calm ranking.

We rank configurations within each regime set and sort by the *worse* of the two ranks, because
averaging would let a configuration that tops one set and bottoms the other look like a compromise.
By that measure the best is **0.85× with an 8% trigger**, 8th in the crisis set and 2nd in the calm
set, followed by 0.80× at 8% (9th and 6th). The single best crisis configuration, 0.80× at 20%, is
54th of 63 in calm markets. The blind rolls of the three deepest strikes are the three worst
configurations of all 63 in both sets.

## Parameter choice matters most in crises

The spread of scores *within* each window shows where the parameter choice has most effect:

| window | kind | spread across all 54 monetising configurations |
|---|---|---|
| GFC 2007-2009 | drawdown | 1.55 |
| COVID 2020 | drawdown | **4.26** |
| Rate shock 2022 | drawdown | 1.84 |
| 2013-2015 | calm | 0.92 |
| 2017 | calm | **0.50** |
| 2023-2024 | calm | 0.52 |

This reverses the first version of the study, where the GFC spread was 0.088 and the calm windows
spanned up to 5. Under one-put-per-unit sizing every hedge looked alike in a crash, because the cheap
ones bought almost nothing. Under equal budgets the crash is where strike and trigger matter most,
and calm markets, where every configuration pays the same premium, are where they matter least.

The figure below shows the full design: strike across, trigger down, colour the score averaged over
all six windows. Nothing here is smoothed.

## The drawdown trigger is chosen for robustness rather than for its in-sample optimum

The trigger axis is not smooth. Averaged over all six windows, the score rises and falls as the
trigger moves in steps of 2-4%:

| trigger | mean score, 0.80× | mean score, 0.80-0.85× | 0.80× in crises | 0.80× in calm |
|---|---|---|---|---|
| 6% | 1.39 | 1.39 | 1.75 | 1.02 |
| 8% | 1.60 | 1.60 | 2.03 | **1.17** |
| 10% | 1.40 | 1.36 | 1.83 | 0.98 |
| 12% | 1.62 | 1.57 | 2.23 | 1.01 |
| 16% | 1.39 | 1.36 | 1.88 | 0.91 |
| 20% | **1.67** | **1.61** | **2.54** | 0.81 |

The 20% trigger has the highest in-sample average, but almost entirely because it lets the hedge run
deep into the three crises; it is the worst trigger of all in calm markets. The alternation between
neighbouring triggers is the more important finding. It reflects the path dependence described
above: whether a given depth fires before or after a deep put has decayed, and whether it fires once
or twice in a leg of the fall. Three crises are too few observations to average that out.

A defensible choice is therefore not the argmax. **An 8% trigger** is close to the best on average,
is the best of all in calm markets, sits among the top ten in both regime sets, and fires more often,
so each monetisation depends less on the timing of a single fall. That is the trigger this analysis
would carry forward, subject to the out-of-sample test.

## Observations at a fixed trigger, before any fitting

The grid gives 54 points per window, and a smooth curve fitted through them allows the optimum to be
read between grid points as well as on them. Before any surface, we apply the same idea in one
dimension. Below, we fix the trigger at 10% and plot the score against the strike, with **one point
per strike per window**, so that six observations stand above each of the nine strikes. Filled
markers are crisis windows, open markers calm ones, and the dashed line is SPY.

Two features are visible before any fitting. The spread *between* windows at a given strike is far
larger than the change *along* the strike axis. And within most windows the points fall, or stay
flat, as the strike moves towards the money: averaged over windows, the score falls from 1.40 at
0.80× to 1.08 at 1.00×.

## A global polynomial cannot bend locally

The simplest smooth fits one polynomial to the whole axis by least squares:

<div class="math">
f(m) = β<sub>0</sub> + β<sub>1</sub> m + β<sub>2</sub> m<sup>2</sup>
&nbsp;&nbsp;&nbsp; minimising &nbsp; Σ<sub>i</sub> ( y<sub>i</sub> − f(m<sub>i</sub>) )<sup>2</sup>
<span class="where">m moneyness (strike / spot) · y<sub>i</sub> the score of observation i · three coefficients, so three degrees of freedom</span>
</div>

Its weakness is that it is *global*: one set of coefficients must fit both ends at once, so a bend
at the deep out-of-the-money end drags the fit at the at-the-money end with it. Here the data barely
curve, so the quadratic is close to a straight line (in-sample R² 0.109, peak at 0.80×).

## A regression spline bends locally but its knots are arbitrary

A spline divides the axis at **knots** and fits a separate cubic on each piece, joined so that the
curve, its slope and its curvature are all continuous at every knot. In B-spline form:

<div class="math">
f(m) = Σ<sub>j=1</sub><sup>K+4</sup> α<sub>j</sub> B<sub>j</sub>(m)
&nbsp;&nbsp;&nbsp; minimising &nbsp; Σ<sub>i</sub> ( y<sub>i</sub> − f(m<sub>i</sub>) )<sup>2</sup>
<span class="where">B<sub>j</sub> cubic B-spline basis functions, each non-zero over at most four adjacent pieces · K interior knots, here three, at 0.85, 0.90 and 0.95 (dotted verticals) · K + 4 = 7 coefficients</span>
</div>

Each piece bends independently, which resolves the polynomial's weakness but introduces another:
nothing in the data determines how many knots to use or where to place them. Too few, and the curve
cannot bend where it needs to; too many, and it follows every point. With three hand-placed knots it
fits no better than the quadratic here (R² 0.110) and uses seven coefficients to do so.

## A penalised spline lets the data choose its smoothness

The fit actually used is a **P-spline** (Eilers and Marx): many knots on an evenly spaced grid, and
a penalty on how sharply neighbouring coefficients change, so that the *penalty*, rather than the
knot count, controls how much the curve can bend.

<div class="math">
minimise &nbsp; ‖ y − Bα ‖<sup>2</sup> + λ ‖ D<sub>2</sub> α ‖<sup>2</sup>
&nbsp;&nbsp;&nbsp; so &nbsp; α̂ = ( BᵀB + λ D<sub>2</sub>ᵀD<sub>2</sub> )<sup>−1</sup> Bᵀy
<span class="where">B the n × 8 cubic B-spline basis on six evenly spaced knots from 0.80 to 1.00 (dotted verticals) · D<sub>2</sub> second differences, (D<sub>2</sub>α)<sub>j</sub> = α<sub>j</sub> − 2α<sub>j+1</sub> + α<sub>j+2</sub> · λ ≥ 0 the smoothing parameter</span>
</div>

At λ = 0 this is an unpenalised regression spline on more knots. As λ grows, the penalty flattens
the second differences, and the limit is a **straight line**, because a straight line has zero second
differences. A straight line is therefore the honest null hypothesis: the curve bends only where the
data justify the bend.

λ is chosen by **generalised cross-validation**, an approximation to leaving each point out in turn:

<div class="math">
GCV(λ) = n · RSS(λ) / ( n − edf(λ) )<sup>2</sup>
&nbsp;&nbsp;&nbsp; edf(λ) = trace[ B ( BᵀB + λ D<sub>2</sub>ᵀD<sub>2</sub> )<sup>−1</sup> Bᵀ ]
<span class="where">RSS residual sum of squares · edf effective degrees of freedom: about 2 for a straight line, up to the coefficient count for an unpenalised fit</span>
</div>

This differs from a classical **smoothing spline**, which places a knot at *every* distinct data value
and penalises the integrated squared second derivative ∫ f″(m)² dm. The two are close relatives — a
second-difference penalty on evenly spaced B-spline coefficients is a discrete version of that
integral — and with nine distinct strikes they would give nearly the same curve. The P-spline is used
because it needs eight coefficients rather than one per point, and gives λ, edf and GCV in closed
form.

**Here GCV selects the straight line**: λ at the top of its range, edf 2.0, R² 0.109. The data do not
support any bend. Within this axis the score simply falls as the strike approaches the money, so the
one-dimensional optimum is the deep edge, 0.80×. More observations, rather than a more flexible
curve, are what this fit needs, which is the subject of the next plan.

## Extending the fit to both parameters with a tensor-product surface

The same construction extends to both axes at once. The basis is the product of a moneyness basis
and a trigger basis, with one penalty along each axis:

<div class="math">
f(m, d) = Σ<sub>j</sub> Σ<sub>k</sub> α<sub>jk</sub> B<sub>j</sub>(m) C<sub>k</sub>(d)
<br>
minimise &nbsp; ‖ y − (B ⊙ C) α ‖<sup>2</sup> + λ<sub>m</sub> ‖ (D<sub>2</sub> ⊗ I) α ‖<sup>2</sup> + λ<sub>d</sub> ‖ (I ⊗ D<sub>2</sub>) α ‖<sup>2</sup>
<span class="where">m moneyness, d monetisation drawdown · B<sub>j</sub>, C<sub>k</sub> cubic B-spline bases, six per axis, so 36 coefficients · ⊙ the row-wise product of the two bases · two smoothing parameters, because the axes are different quantities in different units, both chosen by GCV</span>
</div>

Two cautions apply to reading these surfaces, both measured rather than assumed.

**The per-window fits sit close to interpolation.** At a fixed window the simulator is deterministic,
so there is no noise for cross-validation to regularise against, and it drives both smoothing
parameters towards their floor (edf 21.6 to 30.5 of 36 per window). A fitted optimum's *position* is
reliable only where it agrees with the best simulated cell, and its *height* is never reliable: the
GFC surface claims 2.80 where the best simulated cell scored 2.22. Positions can be quoted where they
agree with the grid; heights cannot.

**Effective degrees of freedom do not measure sharpness here.** For the same reason, edf reports how
easily the basis represents the surface rather than how sharply it is peaked. On a controlled test a
broad bowl scored 31.3 effective degrees of freedom and a narrow spike 21.9, the wrong way round.
Curvature at the optimum separates the two cleanly (−8 against −3,232) and is the quantity quoted.

## The fitted optimum differs between regimes

| window | kind | fitted optimum: strike / trigger | best simulated cell | curvature |
|---|---|---|---|---|
| GFC 2007-2009 | drawdown | 0.800 / 14% | 0.800 / 8% | −6,529 |
| COVID 2020 | drawdown | 0.800 / 19% | 0.875 / 20% | −7,867 |
| Rate shock 2022 | drawdown | 0.829 / 14% | 0.800 / 12% | −4,880 |
| 2013-2015 | calm | 0.919 / 6% | 0.900 / 6% | −907 |
| 2017 | calm | 0.800 / 13% | 0.925 / 6% | −452 |
| 2023-2024 | calm | 0.838 / 7% | 0.850 / 8% | −1,478 |

The fitted *strike* now agrees across regimes much more than it did, sitting between 0.80× and 0.84×
in five of six windows. The *trigger* still divides by regime: crises prefer a deep trigger (12-20%),
which lets the hedge run well into the fall, while calm windows prefer a shallow one (6-8%), which
realises small gains before they decay. Where the fit and the best simulated cell disagree by more
than one grid step, as in 2017 and in the GFC trigger, the fit is not trustworthy and the grid cell
should be read instead.

## Pooling across windows places the optimum at the edge of the design

Fitting one surface to all 378 observations directly is dominated by which window each row came
from, since crisis scores run around 2 and calm ones around 1. Removing each window's mean first
leaves the parameter effect common to all regimes, measured as a deviation from each window's own
average. With the guard in place, that fit is essentially a tilted plane (edf 4.0, R² 0.07): it
rises towards deeper strikes and deeper triggers, and its optimum is the **corner of the design,
0.80× with a 20% trigger**.

The height of that surface is a deviation from a window mean, **not** a score. The optimum is an
in-sample optimum, and a corner optimum is a warning rather than an answer: within this design the
surface is still rising where the axes stop, so "0.80×" partly means "the axis stopped there".

## Snapping the optimum to a tradeable strike

A fitted optimum is a real number, and no exchange lists most of them. At a spot of 761.69 the
nearest listed strike to 0.800 of spot is **609**, a moneyness of 0.7995, and the fitted score
difference is zero to five decimal places.

That result is weaker than it appears. The optimum sits on the boundary, where the fitted surface is
clipped at its edge, so a near-zero snapping cost says nothing about how sharp the surface is. It is
reported because the question will matter again once the moneyness axis is extended below 0.80 and
the optimum, if there is one, lies in the interior.

## The pooled surface in three dimensions

Below, we show the same window-demeaned pooled surface as a rotatable 3-D object, with the continuous
optimum and the nearest listed strike marked. Rotating it requires WebGL in the browser. Where WebGL
is unavailable — an editor's built-in preview, or a browser with graphics acceleration disabled — a
static rendering of the same figure is shown instead.

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

## A fixed share of portfolio value as an alternative budget

The main analysis fixes spend *per unit of SPY*, matched to one 0.95× put at each roll or reopen. A
holder is more likely to state the allocation as a share of the portfolio: "I will spend 5% a year
on insurance". We therefore run that rule once over the full history, at 0.90× with a 10% trigger
and the same guard, outside the grid.

The 5% is a **hard annual allowance, not a forecast**. At the first purchase of each 252-day budget
year, the allowance is set to 5% of portfolio value on that day. Every purchase in that year, whether
a scheduled roll or a reopen after a monetisation, spends the allowance remaining divided by the
number of scheduled rolls still expected before the year ends, the current one included. A year with
four quarterly rolls therefore spends a quarter at each. A reopen in the middle of a quarter spends a
share of what is *left*, never fresh money, so the year's total cannot exceed 5%. Monetisation
proceeds return to the portfolio, not to the allowance.

Measured over 21 calendar years, the median spend was 4.98% a year, and the mean 4.60%, below the cap
because a year that monetises spends part of its allowance on fewer, cheaper reopens. Single calendar
years range from 2.3% to 7.1% only because they straddle two budget years.

| window | score | return | max drawdown | SPY return | SPY max drawdown |
|---|---|---|---|---|---|
| GFC 2007-2009 | 1.13 | −44.9% | 53.3% | −48.5% | 56.5% |
| COVID 2020 | 1.40 | −11.9% | 27.0% | −20.5% | 34.1% |
| Rate shock 2022 | 1.26 | −14.8% | 21.2% | −19.2% | 25.4% |
| 2013-2015 | 0.89 | +29.9% | 8.0% | +40.8% | 7.7% |
| 2017 | 0.87 | +14.7% | 3.4% | +18.5% | 3.0% |
| 2023-2024 | 1.28 | +43.3% | 7.5% | +53.9% | 10.3% |

It sits below the budget-matched 0.90× hedge through the GFC and the rate shock and close to it
elsewhere, which is what a somewhat smaller spend should produce. The figure below compares the two
through the GFC.

## Additional analysis is needed to validate the strategy's out-of-sample performance and the robustness of its optimal parameters

**Established.** Holding the premium budget fixed changes the answer. Deep out-of-the-money puts,
bought in quantity and monetised on a drawdown trigger, beat nearer strikes in two of three crises
and reduce drawdowns relative to SPY in all three, and the trade-off between crisis and calm
performance that appeared fundamental under one-put-per-unit sizing largely disappears. Deep strikes
still bleed more to time decay in calm markets. The remaining regime dependence lies in the trigger,
which crises want deep and calm markets want shallow, and the trigger response is uneven enough that
a robust choice (8%) is preferable to the in-sample optimum (20%). A monetisation rule is not
optional for deep strikes: held blindly, they are worse than no hedge.

**Not established.** Nothing here is out-of-sample. Every threshold was read on the same windows it
was scored on, across 63 trials, which is more than enough freedom to fit noise. The grid's best
cell is not a recommendation and is not described as one.

Five specific limitations, stated plainly:

1. **Six windows are too few.** Three crises are three observations of crisis behaviour, one of them
   (COVID) shorter than a single roll. The next plan adds random windows and earlier history.
2. **The optimum lies at the edge of the strike axis.** The axis stops at 0.80× and the pooled
   surface is still rising there.
3. **Results depend on where a fall arrives in the roll cycle.** The GFC comparison turned on roll
   timing. Laddered expiries would average that timing risk away and are noted for a later plan.
4. **The budget is one choice.** Matching to the 0.95× premium spends 5.7-7.1% of equity a year. A
   smaller budget will change the calm-window results more than the crisis ones.
5. **No transaction costs.** Fills are at the model price. Deep out-of-the-money options trade with
   wide spreads relative to their price, and twelve contracts per unit makes that cost twelve times
   larger. This flatters exactly the configurations that win here.

The outstanding question is whether any of this survives contact with data it was not fitted on.
Answering it requires a procedure that re-selects parameters on each training split and scores them
on held-out blocks, measuring the *procedure* rather than any fixed parameter set. That is the next
piece of work, and until it is done nothing on this page should be deployed.

## Reproducing this analysis

```bash
python -m pipelines.option_grid_sweep                 # the 63-run sweep, then the page
python -m pipelines.option_grid_sweep --publish-only  # the page only, from the saved sweep
```

The full sweep takes about four minutes. It persists a per-day series, a trade blotter and the rule's
own per-day state for every combination, so the analysis script re-reads them without re-simulating
anything.
