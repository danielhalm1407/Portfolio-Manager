# Testing option price dynamics over recent drawdowns

*SPY, 250 daily bars, 2025-07-28 → 2026-07-24. Script:
[`option_ladder_prices_probe.py`](option_ladder_prices_probe.py).*

## Why this exists

This project does not buy option prices. There is no historical chain behind it, so every option
it values is **modelled** — from the underlying's price, a time to expiry, and an implied
volatility read off a parametric surface. Everything downstream depends on that: if the pricer
misrepresents how a put behaves in a selloff, then every hedging result built on top of it is
measuring the model rather than the market.

So before any strategy work, the pricer has to earn trust. The test used here is whether it
reproduces the **stylised facts** of a real equity option market — the regularities that hold
across venues and decades and that any usable model must show:

1. Out-of-the-money puts carry higher implied volatility than at-the-money puts (the skew, or
   smirk), and the curve turns up at the wings rather than running off linearly.
2. Longer tenors carry slightly higher implied volatility, with the term structure steep at the
   front and flattening out.
3. Protection gets cheaper, fast, as the strike falls — and the relationship is strongly convex.
4. A put's value responds to the underlying falling, to time decaying, and to volatility
   repricing — and in a genuine selloff the volatility term is not a second-order correction.

Those are also, not coincidentally, the facts the hedging strategy itself rests on. A rolling
out-of-the-money put programme is only worth considering if cheap protection is genuinely cheap,
if it genuinely pays when the market falls through it, and if the payoff is large enough to be
banked. Each section below therefore does double duty: it checks the pricer against a known
regularity, and it reads off what that regularity implies for the strategy.

The window is deliberately small and the setup deliberately cheap — a single fixed-tenor snapshot
repriced bar by bar, no sizing, no portfolio accounting. The question here is whether the
machinery behaves, not whether hedging pays.

## The pricing model

Two modules do the work:
[`vol.py`](../../src/portutils/strategies/instruments/vol.py) supplies the volatility surface,
[`pricing.py`](../../src/portutils/strategies/instruments/pricing.py) the option value.

### The volatility surface

Implied volatility is a parametric function of log-moneyness and tenor:

<div class="math">
σ(K, τ) = clip[ σ₀ + c<sub>term</sub>·√τ + c<sub>skew</sub>·ln(K/S<sub>ref</sub>) + c<sub>smile</sub>·ln²(K/S<sub>ref</sub>) ,  σ<sub>min</sub> , σ<sub>max</sub> ]
<span class="where">K strike · τ years to expiry · S<sub>ref</sub> the spot that moneyness is measured against<br>
σ₀ = 0.16 (at-the-money level) · c<sub>term</sub> = 0.06 · c<sub>skew</sub> = −0.24 · c<sub>smile</sub> = 0.30 · σ<sub>min</sub> = 0.10 · σ<sub>max</sub> = 0.85</span>
</div>

Three things are worth stating plainly about its shape.

**The skew is additive in log-moneyness, not multiplicative.** A wing strike is priced at the
at-the-money level *plus* a spread, not at a multiple of it. At K/S = 0.80 and τ = 0.25, the terms
contribute +3.0 volatility points (term structure), +5.4 (skew) and +1.5 (smile), so a 16% base
becomes 25.8% — roughly ten points of uplift, not a scaling. This matters for interpretation: when
the base level later moves, it moves the *whole* surface up in parallel rather than fanning it, so
a volatility spike lifts cheap wing strikes by far more in percentage terms than it lifts
at-the-money ones. That asymmetry drives most of what follows.

**The sign conventions produce the right curve.** `c_skew` is negative, so lower strikes carry
higher volatility. `c_smile` is positive, so the wings turn up and deep strikes do not extrapolate
to nonsense. `c_term` scales with √τ, so the term structure is steep at the front and flattens —
which is how real term structures behave.

**It is not calibrated.** The coefficients are plausible equity-index values, not a fit to a
quoted surface, and no attempt is made here to match a particular day's market. That is a real
limitation and it bounds what this page can claim: nothing here is a statement about the *level*
of option prices in any specific period. What it does retain is the **dynamics** — the ordering
of strikes, the convexity of cost in moneyness, the relative sensitivity of wing strikes to a
volatility move. Those are the properties the strategy work depends on, and they are structural
consequences of the functional form rather than of the particular constants. The level question
is answered later on this page, where the synthetic surface is replaced with the ticker's actual
measured implied volatility.

### The option value

European Black-Scholes with a continuous dividend yield:

<div class="math">
P = K e<sup>−rτ</sup> Φ(−d₂) − S e<sup>−qτ</sup> Φ(−d₁)
<span class="where">d₁ = [ ln(S/K) + (r − q + σ²/2)τ ] / (σ√τ) &nbsp;·&nbsp; d₂ = d₁ − σ√τ &nbsp;·&nbsp; Φ the standard normal CDF</span>
</div>

A European put is bounded below by its *discounted* intrinsic value, not by its intrinsic value:

<div class="math">
P ≥ max( K e<sup>−rτ</sup> − S e<sup>−qτ</sup> , 0 )
</div>

This follows from put-call parity and is the reason the implementation applies **no clamp**. A
naive floor at `max(K − S, 0)` would be wrong — it would force a positive value onto deep
in-the-money puts that are correctly worth slightly less than their undiscounted intrinsic,
because exercise cannot happen until expiry. The bound is instead asserted as a property and
checked across a strike-and-tenor grid on every run, so a mis-signed term shows up as a failure
rather than as a quietly distorted price. The normal CDF is built from `math.erf` rather than
pulled from a numerical library, which keeps the pricer dependency-free and auditable in one
screen of code.

That is as far as the engineering goes on this page. The rest is what the model says.

## The surface reproduces the smirk, the term structure and the wings

Fixing spot and sweeping the strike separates the three terms visually. All three are present and
correctly signed: implied volatility falls as the strike rises, longer tenors sit above shorter
ones, and the wings curve upward instead of running off in a straight line.

This is the least surprising result on the page and it is still worth having. The surface was
previously an untested expression; it is now a verified one, and every number further down
inherits that.

## Protection costs fall sharply as the strike falls

Three-month puts struck off the period-start spot of 636.94:

| strike | K/S | implied vol | put | % of spot |
|---|---|---|---|---|
| 509.55 | 0.80 | 25.8% | 1.14 | **0.18%** |
| 573.25 | 0.90 | 21.9% | 5.38 | **0.85%** |
| 605.09 | 0.95 | 20.3% | 11.41 | **1.79%** |
| 636.94 | 1.00 | 19.0% | 22.52 | **3.53%** |

The cost relationship is steeply convex: moving the strike down 20% cuts the premium by a factor
of twenty, even though implied volatility is *rising* along the way. Delta falls faster than
volatility rises.

The practical reading is the one that shapes everything afterwards. **An at-the-money three-month
put costs 3.53% of spot, so rolling it quarterly costs roughly 14% of notional a year.** No
equity risk premium supports that, which effectively rules out an at-the-money hedge before any
backtest is run. The 0.90× strike at 0.85% per quarter — about 3.4% annually — is the first
candidate that looks economically plausible, and the 0.80× strike at 0.18% is cheap enough to be
almost free. Whether either *pays* is a separate question; what this establishes is which part of
the strike range is even worth testing.

## A drawdown that ends above the strike pays nothing

The window contains a real selloff: **2026-01-27 (695.49) → 2026-03-30 (631.97), −9.1% over 43
bars.** A ladder struck at the window's start, however, barely registers it.

The reason is positional rather than statistical. SPY opened at 636.94, rallied **+9.2%** to the
peak, then gave all of it back — so at the trough spot sat **0.8% below the window start**, within
one percent of where the ladder was struck. The entire fall happened *above* every strike. A put
struck at 636.94 watched a nine percent selloff and finished roughly at the money, which reads as
pure carry because that is exactly what it was.

This is the central mechanical insight of the page, and it generalises well beyond this window:

> A put only converts a drawdown into cash if the drawdown carries spot **through its strike**.
> A hedge struck once and held measures the market's distance from a stale reference point, not
> its recent fall.

Which is the argument for **rolling**. Restriking every quarter at the prevailing spot keeps the
strike tracking the underlying's level, so each new drawdown is measured from where the market
actually is rather than from where it was a year ago. Against an index that trends upward over
time, a static strike drifts ever further below spot and becomes progressively less likely to be
reached — protection that quietly turns into a donation. Rolling resets that distance on a
schedule.

The same logic motivates **monetising**. If a put is sold while it is in the money and the
position is reopened at a lower strike afterwards, the gain is banked and the new strike is set
against the reduced spot level, rather than the whole position being carried back to zero as the
market recovers. Both mechanisms exist to keep the strike meaningfully close to spot.

One limitation genuinely does survive here, and it is about the data rather than the method: **one
drawdown is one observation.** The window has 14 bars below −5%, two below −8%, and none below
−20%. Nothing about behaviour across regimes can be read off it.

## How the surface is anchored, and what happens when it moves

Pricing a put through time requires a decision that is easy to make silently: *which* spot does
moneyness get measured against?

The baseline convention is the conservative one. Implied volatility is fixed per leg at the moment
it is struck — both the at-the-money level σ₀ and the reference spot S_ref in the formula above are
frozen at strike time. Under that convention the surface is never consulted again, and a put's
value moves through **spot and time decay alone**. It captures the most basic channel, a falling
underlying lifting a fixed strike's value, and nothing else.

Two refinements can then be layered on, and the distinction between them matters:

- **Moving the moneyness reference** (measuring against each bar's own spot) means that as spot
  falls toward a fixed strike, that strike becomes *less* of a tail strike and slides down the
  smirk to a lower implied volatility. On its own, this makes protection **cheaper** in a selloff.
- **Moving the volatility level** (letting σ₀ follow the market) captures the volatility spike
  that accompanies a real decline, lifting the whole surface in parallel and making protection
  **dearer**.

Measured at the trough with only the moneyness reference moving:

| strike | K/S then | vol, frozen | vol, current-spot | put, frozen | put, current-spot | change |
|---|---|---|---|---|---|---|
| 509.55 | 0.81 | 25.8% | 25.6% | 1.32 | 1.25 | **−5.7%** |
| 573.25 | 0.91 | 21.9% | 21.6% | 6.14 | 5.96 | −2.9% |
| 605.09 | 0.96 | 20.3% | 20.1% | 12.83 | 12.60 | −1.8% |
| 636.94 | 1.01 | 19.0% | 18.8% | 24.88 | 24.65 | −0.9% |

The effect is small here, and for a mechanical reason: the two references agree exactly when
current spot equals the strike-time spot, and at this trough spot had round-tripped to within one
percent of where the ladder was struck. The gap scales with distance travelled since the strike
was set, so a *rolling* ladder — which restrikes and then watches spot move away — would see
considerably more of it.

The ordering of these two effects is established further down, and it is not close. The
consequence for the engine is a firm rule: **the moneyness reference is never made dynamic without
the volatility level being made dynamic at the same time.** Enabling the cheaper-in-a-selloff term
on its own would not merely understate the hedge, it would move its price in the wrong direction
during precisely the event it exists for.

## Struck where a rolling hedge would stand

Striking the same ladder at the drawdown's **peak** — where a quarterly roll would have placed it
— and carrying one option through its full 63-day life, so that time decay is part of the
measurement:

Struck 2026-01-27 at 695.49, marked at the trough 2026-03-30 at 631.97 (−9.1%, 43 bars):

| strike | premium | % of spot | value at trough | return | with vol response | return |
|---|---|---|---|---|---|---|
| 556.39 (0.80×) | 1.25 | 0.18% | 0.66 | **−46.9%** | 5.20 | +317.0% |
| 625.94 (0.90×) | 5.88 | 0.85% | 12.18 | **+107.2%** | 24.04 | +308.8% |
| 660.72 (0.95×) | 12.46 | 1.79% | 32.54 | **+161.2%** | 43.11 | +246.0% |
| 695.49 (1.00×) | 24.59 | 3.53% | 62.98 | **+156.2%** | 68.79 | +179.8% |

Two results, pointing in different directions.

**The payoff mechanic works, and it is large.** A 0.90× put costing 0.85% of spot roughly doubles
on a nine percent dip. That is a gain big enough to be worth banking rather than carrying, which
is the quantitative version of the rolling-and-monetising argument made above: the strike was
close enough to spot for the fall to reach it, and the resulting move in the put is a multiple of
its cost rather than a fraction.

**The 0.80× strike loses half its value on the same fall.** A 20%-out-of-the-money put is simply
not reached by a nine percent selloff, so with a frozen volatility level it is pure time decay. On
its own that would suggest deep-out-of-the-money protection is not worth buying.

It is the wrong conclusion, and the final columns show why. Applying an illustrative volatility
response — the at-the-money level rising from 0.16 to 0.34 across the drawdown — turns the 0.80×
strike from **−47% to +317%**. Deep-out-of-the-money protection is almost entirely a **volatility**
position, not a directional one, so a model whose volatility level never moves cannot price the
thing such a put is actually bought for. The understatement is worst precisely on the cheapest
strikes.

That illustrative response is assumed, not measured. It is replaced with the real series below.

## Running the structures as a rolled book

Moving from pricing single legs to holding a hedged portfolio: one SPY unit, with a hedge opened
alongside it and rolled every 63 bars at the prevailing spot — the restriking the stale-strike
problem above calls for. Frozen volatility level throughout.

| strategy | final value | return | max drawdown | net premium, %/yr of spot |
|---|---|---|---|---|
| SPY only | 738.93 | +16.0% | 9.1% | — |
| + protective put (0.90) | 715.87 | +12.4% | 8.4% | 3.39% |
| + put spread (0.90 / 0.80) | 720.76 | +13.2% | 8.3% | 2.67% |
| + collar (0.90 / 1.28) | 715.91 | +12.4% | 8.4% | 3.38% |

The rolls landed on 2025-07-29, 2025-10-27, **2026-01-28** and 2026-04-29. The third is the
interesting one: it struck a single bar after the drawdown's peak, at 695.42, placing the 0.90 put's
strike at 625.88 — in front of the entire subsequent fall. **By the trough the put was worth 1.97×
what it cost, and the put spread 2.38×.**

**Both expired worthless.** SPY recovered to 711.58 by the next roll, and because these rules hold
every leg to expiry, a hedge that nearly doubled mid-period contributed nothing at all. The book
paid roughly 3.4% a year to reduce maximum drawdown by 0.7 percentage points.

This is the clearest possible statement of the case for a monetisation rule. The payoff was
genuinely there and was genuinely available — it simply was not taken, because nothing in the
rule set was looking for it. Rolling fixes *where* the strike sits; it does nothing about *when*
to realise a gain. Closing a position at some multiple of premium paid, or once the underlying has
fallen a given distance below its peak, is a separate policy decision, and quantifying it is the
subject of the other two sections of this site.

Two secondary observations:

**The wide collar is a protective put with a rounding error.** A 1.28× call struck 90 days out on
a 16% base volatility is worth about 0.009, so it funds almost nothing; the collar's return
matches the protective put's to three significant figures. The next section works through why.

**The put spread was the cheapest and performed best.** Its net premium was about 21% lower
because the sold 0.80 put was never reached, so the credit was kept in full. Selling the wing you
have just established is nearly worthless is an efficient trade on this window.

## How a collar leg is priced, and why the call is worth so little

Measured at the first roll (spot 635.26, τ = 0.25):

| leg | strike | vol from the surface | distance from spot | premium |
|---|---|---|---|---|
| long put 0.90× | 571.73 | 21.9% | **0.91 sd** | 5.3701 |
| short put 0.80× (spread) | 508.21 | 25.8% | 1.55 sd | 1.1391 |
| short call 1.28× (collar) | 813.13 | 14.9% | **3.76 sd** | 0.0083 |

Across the window's four rolls, per SPY unit:

| structure | paid for longs | received for shorts | net | shorts offset |
|---|---|---|---|---|
| put spread | 23.06 | 4.89 | 18.17 | **21.2%** |
| collar | 23.06 | 0.04 | 23.02 | **0.16%** |

The collar's short call funds one part in six hundred of its floor. Two reasons, and the second
dominates overwhelmingly:

1. **Skew.** The call sits on the low side of the smirk at 14.9% while the put sits on the high
   side at 21.9%, so equal distance would already be worth less.
2. **Distance.** 1.28× is **3.76 standard deviations** away over a 63-day tenor at that volatility,
   against 0.91 sd for the 0.90× put. Almost the whole gap is here.

A +28% cap is a sensible annual figure and a nearly meaningless quarterly one — at a 63-day reset
it is almost four sigma out. A cap that never binds also never pays for anything. Somewhere near
1.05–1.10 (roughly 0.7–1.4 sd) would fund a visible share of the floor and would genuinely cap
upside, which makes it a different structure and a parameter worth sweeping rather than assuming.

## What implied volatility actually did

Replacing the assumed volatility response with the measured one: SPY's own implied volatility, as
recorded by the broker for the ticker itself — not VIX, and not synthetic.

Over the window, real implied volatility ranged **10.5% to 26.2%**, mean **14.7%** — sitting
*below* the model's 16% base most of the time rather than above it. Across the drawdown:

| | date | implied vol |
|---|---|---|
| peak | 2026-01-27 | **12.9%** |
| trough | 2026-03-30 | **25.1%** |
| episode maximum | — | 26.2% |

The level roughly **doubled** from peak to trough, arriving independently at close to the
illustrative 0.16 → 0.34 response assumed earlier. The stylised fact that volatility spikes as
equities fall is reproduced in the data, and its magnitude on a merely nine percent dip is
substantial.

## Repricing with the real volatility level

The same ladder at the same trough, now valued with both the measured volatility level and the
current-spot moneyness reference:

| strike | K/S then | vol, frozen | vol, market | put, frozen | put, market | change |
|---|---|---|---|---|---|---|
| 509.55 | 0.81 | 25.8% | 34.6% | 1.32 | 4.72 | **+257.6%** |
| 573.25 | 0.91 | 21.9% | 30.7% | 6.14 | 13.88 | **+126.1%** |
| 605.09 | 0.96 | 20.3% | 29.2% | 12.83 | 22.89 | +78.5% |
| 636.94 | 1.01 | 19.0% | 27.9% | 24.88 | 36.09 | +45.0% |

**The cheapest, furthest out-of-the-money strike gains the most** — +258% against +45% for the
at-the-money strike — which is the additive structure of the surface showing through. A parallel
lift in the level is a far larger proportional change to a put worth 1.32 than to one worth 24.88.

Because both this table and the earlier one measure moneyness against each bar's own spot, the
two decompose the effect cleanly:

| strike | moneyness reference alone | moneyness reference **and** volatility level |
|---|---|---|
| 0.80× | −5.7% | **+257.6%** |
| 0.90× | −2.9% | **+126.1%** |
| 0.95× | −1.8% | **+78.5%** |
| 1.00× | −0.9% | **+45.0%** |

Sliding down the smirk makes protection cheaper by 1–6%. The volatility level rising makes it
dearer by 45–258%. The two push in opposite directions and differ by roughly two orders of
magnitude. **Freezing the volatility level is therefore not a small approximation**, and the rule
stated earlier — never move the moneyness reference without moving the level — is doing
considerably more work than it appears to.

## Limitations

**The surface is uncalibrated.** Coefficients are plausible rather than fitted. Price *levels*
from the synthetic surface should not be read as market levels; the dynamics and the orderings
are what this page supports.

**One window, one drawdown.** A −9.1% dip is a single observation, and a shallow one. Nothing here
speaks to behaviour across regimes.

**Frozen-volatility payoffs are floors, not estimates.** Every figure computed with a fixed
volatility level understates the hedge, most severely on the cheap out-of-the-money strikes that a
retail programme would actually buy.

**No dividend yield.** Left at zero; SPY's is approximately 1.2%.

**No transaction costs.** Every price is a model mark — no bid/ask spread, no slippage. This
flatters any strategy that trades frequently.

## What this establishes

The pricer reproduces the stylised facts it needs to: a correctly signed smirk with convex wings,
a flattening term structure, steeply convex protection costs, and a volatility response that
dominates the payoff of cheap strikes. It can be used for strategy work, with the level caveat
above held in view.

For the strategy itself, three things follow:

1. **At-the-money hedging is uneconomic** at roughly 14% of notional a year. The plausible range
   is 0.80×–0.95×.
2. **Rolling is structural, not cosmetic.** A static strike measures distance from a stale
   reference and drifts out of reach of exactly the falls it was bought for.
3. **Rolling alone is not enough.** A hedge that reached 1.97× its premium still expired
   worthless, because no rule was looking to realise it. Monetisation is a separate decision and
   needs its own evidence.

The [next section](../../docs/validation/index.html) takes those three structures through a real
crisis with a monetisation policy attached.

## Reproducing this

```bash
python -m pipelines.option_probe_figures
```

Figure builders live in
[`option_probe_figures.py`](../../src/pipelines/option_probe_figures.py) and are shared between the
interactive research script and the page exporter, so what is published and what is inspected are
the same objects.
