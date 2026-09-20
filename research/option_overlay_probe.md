# Option overlay probe — does the synthetic surface price a sane put?

**Script:** [`option_overlay_probe.py`](option_overlay_probe.py)
**Run:** 2026-08-31, on `data/processed/prices_spy_kmlm.parquet` (SPY, 250 bars,
2025-07-28 → 2026-07-24)

## The question

Before three plans of module-building ([16-01](../.paul/phases/16-strategy-architecture/CONTEXT.md)
skeleton → 16-02 instruments → 16-03 rules), is the option ECONOMICS sound? Specifically: does the
vol surface ported out of
[`12-01-PLAN.md`](../.paul/phases/12-option-overlay/12-01-PLAN.md), plus a European put, produce
protection costs and protection payoffs that look like a real option market on the real SPY path?

This is deliberately the cheap version. No roll, no sizing, no P&L — a single fixed-tenor snapshot
repriced bar by bar. That is why it needs neither Phase 11 history nor an option chain.

## What was built

Two pure modules, in the place 16-02 will find them:

- [`portutils/strategies/instruments/vol.py`](../src/portutils/strategies/instruments/vol.py) —
  `synthetic_iv_surface`, ported from `crash.py:2579` with every constant exposed as a keyword
  argument. It was previously an inline code block inside a planning document, which meant every
  consumer would have retyped it.
- [`portutils/strategies/instruments/pricing.py`](../src/portutils/strategies/instruments/pricing.py) —
  `black_scholes_put`, European, discounted-intrinsic floor, **no clamp**, per the 2026-08-26
  decision. No scipy: the normal CDF is built from `math.erf`.

## Finding 1 — the port is exact

12-01 quotes four numbers from the source function. All four reproduce to the printed precision:

| | plan | this port |
|---|---|---|
| IV, `K=90, τ=0.125, spot_ref=100` (frozen) | 0.210 | **0.2098** |
| IV, `K=90, τ=0.125, spot_ref=88` (current) | 0.176 | **0.1760** |
| put, `S=88`, frozen IV | 3.612 | **3.612** |
| put, `S=88`, current IV | 3.206 | **3.206** |

Cell 3 prints the two IV anchors on every run, so a mis-ported constant is caught immediately
rather than three cells later. A grid sweep also confirms the put never breaches its discounted
intrinsic floor — which it should not, by put-call parity, and which is the reason no clamp is
applied.

## Finding 2 — the smirk has the right shape

Cell 3's figure shows the three terms separately and correctly: IV falls as strike rises (the
skew term), longer tenors sit higher (the term-structure term), and the wings turn up rather than
running off linearly (the smile term). Nothing here is surprising; the point is that it is now
verified rather than assumed.

## Finding 3 — the cost of protection, on the only window that exists

Three-month puts struck off the period-start spot (636.94):

| strike | K/S | IV | put | as % of spot |
|---|---|---|---|---|
| 509.55 | 0.80 | 25.8% | 1.14 | **0.18%** |
| 573.25 | 0.90 | 21.9% | 5.38 | **0.85%** |
| 605.09 | 0.95 | 20.3% | 11.41 | **1.79%** |
| 636.94 | 1.00 | 19.0% | 22.52 | **3.53%** |

The headline number for v0.4: **an ATM three-month put costs 3.53% of spot, so rolling it
quarterly is roughly 14% of notional a year.** That is the drag Phase 13 has to show a benefit
against, and it is large enough that the answer is very unlikely to be "hedge at the money".
The 0.90× strike at 0.85% (≈3.4%/yr) is the one that looks like a real candidate.

## Finding 4 — the ladder never had the drawdown in front of it

**Corrected 2026-08-31.** An earlier version of this section read *"the window cannot answer the
actual question — on a window with no crash, every put is pure cost by construction."* Both halves
were wrong, and the mistake is worth recording because it is a property of the PROBE, not of the
data.

The window does contain a drawdown: **2026-01-27 (695.49) → 2026-03-30 (631.97), −9.1% over 43
bars.** What it does not contain is a drawdown that happened *in front of these strikes*. SPY
opened the window at 636.94, rallied **+9.2%** to the peak, and then gave all of it back — so at
the trough spot was **−0.8% against the window start**, within 1% of where the ladder was struck.
The whole fall took place *above* every strike in the ladder. A put struck at 636.94 therefore
watched a 9% selloff and finished essentially at the money, which reads as carry because it *is*
carry.

That is not what a hedge does. A hedge rolls: every 63 days it restrikes at the then-current spot,
so whatever happens next is measured from *there*, not from wherever the market stood a year ago.
Finding 6 strikes at the peak, which is where a rolled hedge would have been standing, and the
answer changes completely.

What genuinely does not survive the window: **one drawdown is one observation.** 14 bars below
−5%, 2 below −8%, none below −20%. The v0.4 claim is about behaviour *across regimes*, and a
single episode cannot speak to that. Phase 11 is still the binding constraint — just not for the
reason this section originally gave.

## Finding 5 — the v1/v2 gap is real, same sign, much smaller here

At the trough:

| strike | K/S *then* | IV v1 | IV v2 | put v1 | put v2 | v2 vs v1 |
|---|---|---|---|---|---|---|
| 509.55 | 0.81 | 25.8% | 25.6% | 1.32 | 1.25 | **−5.7%** |
| 573.25 | 0.91 | 21.9% | 21.6% | 6.14 | 5.96 | −2.9% |
| 605.09 | 0.96 | 20.3% | 20.1% | 12.83 | 12.60 | −1.8% |
| 636.94 | 1.01 | 19.0% | 18.8% | 24.88 | 24.65 | −0.9% |

The sign is exactly as the 2026-08-29 decision predicts — the current-spot reference makes
protection **cheaper** in the selloff — but the magnitude is 1–6%, not the 11% the plan quotes.

The reason is the same one as Finding 4, and it is mechanical. The v1/v2 gap is the difference
between evaluating the smirk at `log(K / spot_ref)` and at `log(K / S_t)`. Those two agree exactly
when `S_t == spot_ref` — and at the trough spot was within 1% of the window start the ladder was
struck against, so the two references were asking the smirk almost the same question. 12-01's
example instead moves spot 12% *below* a strike set 10% out, where they disagree substantially.
The gap is a function of distance travelled since the strike was set; **a rolling ladder would
restrike and see the full effect**, which is precisely what 16-02's `RollCalendar` provides and
cells 5-7 do not.

The decision stands: ship v1, and never enable term B (current-spot reference) without term A
(dynamic vol level).

## Finding 6 — struck at the peak, the ladder pays 1-2.6x the premium

Cell 8 strikes the same ladder at the drawdown's peak and carries **one** option through its full
63-day life, so tenor decays and theta is part of what is measured.

Struck 2026-01-27 at 695.49, marked at the trough 2026-03-30 at 631.97 (−9.1%, 43 bars):

| strike | premium | % of spot | MTM at trough | return | + vol spike | return |
|---|---|---|---|---|---|---|
| 556.39 (0.80×) | 1.25 | 0.18% | 0.66 | **−46.9%** | 5.20 | +317.0% |
| 625.94 (0.90×) | 5.88 | 0.85% | 12.18 | **+107.2%** | 24.04 | +308.8% |
| 660.72 (0.95×) | 12.46 | 1.79% | 32.54 | **+161.2%** | 43.11 | +246.0% |
| 695.49 (1.00×) | 24.59 | 3.53% | 62.98 | **+156.2%** | 68.79 | +179.8% |

Two things fall out, and they point in opposite directions:

**The mechanic works.** A 0.90× put costing 0.85% of spot roughly doubles on a nine percent dip.
That is monetisable without holding to expiry, and it is the first evidence in this project that
the structure does what the catalogue claims.

**The 0.80× strike LOSES half its value on a 9% fall.** A 20%-OTM put is not reached by a 9%
selloff, so under a frozen vol level it is pure theta and it decays. That is the honest v1 answer,
and it is also the clearest demonstration of what v1 leaves out: deep-OTM protection is almost
entirely **vega**, and a model whose ATM level never moves cannot price the thing it is bought
for.

The "+ vol spike" columns bound that gap. They apply an **illustrative** term-A response — the ATM
level rising by `VOL_BETA = 2.0` vol points per 1.0 of drawdown, so 0.16 → 0.34 at the trough —
and the 0.80× strike goes from **−47% to +317%**. Not calibrated; calibrating it is exactly what
Phase 11's vol history is for. The point is the ordering: **the vol-level term dominates the
payoff, and it dominates most on the cheapest strikes**, which are the ones a retail hedge would
actually buy.

This is the sharpest argument yet for Phase 11 being the gate on the *result* — and it also says
the 3.53% ATM cost figure is the wrong thing to design around.

## Finding 7 — rolled for real, the hedges gained mid-period and then gave it all back

**Added 2026-09-19 (plan 16-03), cell 9.** The first six findings price legs. This one runs a
hedged BOOK. Each structure is a rule in `PortfolioSimulator`: it holds one SPY unit bought on bar
0, opens its hedge on bar 1 at one option per unit, and rolls every 63 bars off the spot at that
moment. That restriking is what Finding 4 said was missing. v1 surface, and dividend yield 0.

| strategy | final value | return | max drawdown | net premium, %/yr of spot |
|---|---|---|---|---|
| SPY only | 738.93 | +16.0% | 9.1% | — |
| + protective put (0.90) | 715.87 | +12.4% | 8.4% | 3.39% |
| + put spread (0.90 / 0.80) | 720.76 | +13.2% | 8.3% | 2.67% |
| + collar (0.90 / 1.28) | 715.91 | +12.4% | 8.4% | 3.38% |

The rolls fell on 2025-07-29, 2025-10-27, **2026-01-28** and 2026-04-29. The third roll is the one
that matters. It struck one bar after the drawdown's 2026-01-27 peak, at 695.42, so the 0.90 put's
strike (625.88) sat in front of the whole fall. **By the 2026-03-30 trough, the put was worth 1.97×
what it cost, and the spread 2.38×.** Those are the same monetisation numbers cell 8 found, now
measured inside a rolling book.

**It still expired worthless.** SPY recovered to 711.58 by the 2026-04-29 roll, so the put settled
at 0. The rules hold every leg to expiry, so a hedge that nearly doubled mid-period contributed
nothing. The book paid ≈3.4% a year of drag to cut max drawdown by 0.7 points. Three observations:

1. **A roll-only rule does not monetise.** The payoff exists (the middle panel shows it), but no
   rule takes it. A monetisation trigger is the natural next rule. It is a policy question for
   Phase 13's sweep, not a pricing one: for example, close and re-strike when a hedge reaches N×
   its premium, or when spot is X% below the strike-time spot.
2. **The wide collar here is a protective put with a rounding error.** A 1.28 call struck 90 days
   out on a 16% base vol is worth ≈0.009, so it funds almost nothing. That is 12-01's cap working as
   intended: +28% a quarter is what the source ran, and it is chosen to rarely bind. On this window
   it never did, so no collar redeploy or funding ever fired. All three rolls logged "collar
   carry".
3. **The put spread was the cheapest and did best.** Its net premium was ≈21% lower. The sold 0.80
   put was never reached, so the credit was kept in full.

The usual caveats apply, and they apply more strongly here. The vol level is frozen, so every
mid-period multiple above is a floor (Finding 6). And this is one window with one drawdown: one
observation, not evidence that hedging does or does not pay.

## How a collar leg is actually priced — the call chain, and why the call is worth ~0.009

**Added 2026-09-20.** Finding 7's numbers are only readable if the path from figure to formula is
explicit. It is four hops, and nothing in it is a second implementation of anything:

| # | Where | What happens |
|---|---|---|
| 1 | [`option_probe_figures.overlay_runs`](../src/pipelines/option_probe_figures.py) | Builds one `PortfolioSimulator` per strategy: `BuyAndHoldRule({"SPY": 1.0})` plus the option rule, starting capital = the first close, so the book holds exactly one SPY unit. |
| 2 | [`PortfolioSimulator.run`](../src/portutils/portfolio/simulator.py#L47) | Per bar: `propose` → **synthetic marks** (`:72`) → fill → mark the book. The marks step is 16-03's hook; without it a leg has no price and can neither fill nor be valued. |
| 3 | [`OptionOverlayRule.propose`](../src/portutils/strategies/rules/options.py#L136) | Only acts on a ROLL bar. Closes the expiring legs, asks the subclass's `_leg_spec()` for the new ones, and prices each at birth — that price becomes the premium. `RollingCollarRule._leg_spec` (`:282`) returns `[("P", 0.90, +1), ("C", 1.28, -1)]`. |
| 4 | [`OptionOverlayRule._price`](../src/portutils/strategies/rules/options.py#L104) | The only place a number is produced: `vol_fn(strike, tau, spot_ref)` → `OptionLeg.price` → `black_scholes_put` / `black_scholes_call`. On the expiry bar it returns `intrinsic(spot)` instead. |

Between rolls `propose` returns `{}` and only `synthetic_marks` (`:202`) runs, repricing each open
leg at that bar's spot and shorter `tau`. **That is the whole dynamic:** within a period the legs
move through SPOT and TIME DECAY alone. The vol they are priced at is fixed per leg at birth (v1:
`base = 0.16`, moneyness measured against the strike-time spot), so nothing in this engine re-prices
a leg because the market got scared.

### Why the collar is a protective put with a rounding error

Measured at the first roll (spot 635.26, τ = 0.25):

| leg | strike | IV from the smirk | distance from spot | premium |
|---|---|---|---|---|
| long put 0.90× | 571.73 | 21.9% | **0.91 sd** | 5.3701 |
| short put 0.80× (spread) | 508.21 | 25.8% | 1.55 sd | 1.1391 |
| short call 1.28× (collar) | 813.13 | 14.9% | **3.76 sd** | 0.0083 |

Totals over the window's four rolls, per SPY unit:

| structure | paid for longs | received for shorts | net | shorts offset |
|---|---|---|---|---|
| put spread | 23.06 | 4.89 | 18.17 | **21.2%** |
| collar | 23.06 | 0.04 | 23.02 | **0.16%** |

So the collar's short call funds one part in six hundred of its floor, and the +12.4% it returns is
the protective put's number to three digits. Two independent reasons, and the second dominates:

1. **Skew.** The call sits on the low side of the smirk (14.9%) and the put on the high side
   (21.9%), so equal distance would already be worth less.
2. **Distance.** 1.28× is **3.76 standard deviations** away over a 63-day tenor at that vol, while
   0.90× is 0.91 sd. Almost all of the gap is here, not in the skew.

This is 12-01's cap working exactly as specified — `HEDGE_UPSIDE_CAP = 0.28` is deliberately wide,
chosen so it rarely binds — but a cap that never binds also never pays. **Whether 1.28 is the right
default for a 63-BAR roll is an open question:** the source applies +28% to its own horizon, and at
a quarterly reset the same number is nearly four sigma out. A cap nearer 1.05-1.10 (≈0.7-1.4 sd)
would fund a visible share of the floor and would genuinely cap upside — a different structure, and
exactly the kind of parameter Phase 13 should sweep rather than assume.

## Finding 8 — the real vol level nearly doubled at the trough, confirming Finding 6's guess

**Added 2026-09-20**, using 11-01's cached SPY implied vol
(`data/processed/iv_long_history.parquet`, IBKR's `OPTION_IMPLIED_VOLATILITY` for the ticker
itself, back to 2006-01-09 — not VIX, not synthetic). Finding 6's "+vol spike" columns were an
**illustrative** stand-in — `base` rising by `VOL_BETA = 2.0` points per unit of drawdown, chosen
to bound the gap v1 leaves out, not measured. This finding replaces the guess with the real
number.

Over the probe window (2025-07-28 → 2026-07-24), SPY's real IV ranged **10.5% to 26.2%**, mean
**14.7%** — sitting BELOW `base = 0.16` most of the time, not above it. Across the drawdown episode
specifically:

| | date | real IV |
|---|---|---|
| peak | 2026-01-27 | **12.9%** |
| trough | 2026-03-30 | **25.1%** |
| episode max | — | 26.2% |

The level roughly **doubled** from peak to trough — close to the illustrative 0.16 → 0.34 jump
Finding 6 assumed, arrived at independently from real market data rather than a chosen constant.

## Finding 9 — repriced with real vol AND current spot together, the cheap strikes move the most

Same ladder, same trough, now valued with `iv_paths_market` — term A (the level) from Finding 8's
real series, term B (the moneyness reference) from that bar's own spot, both dynamic together
(never term B alone, per the 2026-08-29 decision):

| strike | K/S then | IV v1 | IV market | put v1 | put market | market vs v1 |
|---|---|---|---|---|---|---|
| 509.55 | 0.81 | 25.8% | 34.6% | 1.32 | 4.72 | **+257.6%** |
| 573.25 | 0.91 | 21.9% | 30.7% | 6.14 | 13.88 | **+126.1%** |
| 605.09 | 0.96 | 20.3% | 29.2% | 12.83 | 22.89 | +78.5% |
| 636.94 | 1.01 | 19.0% | 27.9% | 24.88 | 36.09 | +45.0% |

Same ordering as Finding 6, now with real numbers instead of an illustrative one: **the cheapest,
most OTM strike gains the most from a real vol repricing** (+258% vs +45% for the ATM strike).
Finding 6's frozen-`base` run showed the 0.80× strike LOSING 46.9% over this same episode when held
to expiry with decaying tau — the two are different measurements (a fixed 63-day snapshot here vs
one option carried to expiry there) but they point at the same mechanism from opposite directions:
a model whose vol level never moves cannot price what deep-OTM protection is actually bought for,
and this finding is the first time that statement is backed by a real, measured vol series rather
than an assumption.

**The vol spike is the overriding factor, and it is not close.** Both the market series here and
the v2 series in Finding 5 measure the moneyness reference against each bar's own spot — term B is
common to both. The only thing this finding adds is term A, the real vol level. So the two tables
decompose the effect cleanly:

| strike | term B alone (Finding 5, v2 vs v1) | term B **and** term A (this finding, market vs v1) |
|---|---|---|
| 0.80× | −5.7% | **+257.6%** |
| 0.90× | −2.9% | **+126.1%** |
| 0.95× | −1.8% | **+78.5%** |
| 1.00× | −0.9% | **+45.0%** |

Sliding down the smirk — a fixed strike becoming less of a tail strike as spot falls toward it —
makes protection **cheaper by 1-6%**. The vol level rising makes it **dearer by 45-258%**. They
push in opposite directions and they are not the same order of magnitude: term A is roughly two
orders larger than term B on the same bars. Any model that freezes the vol level is therefore not
making a small approximation, and the 2026-08-29 rule ("never term B alone") is doing more work
than it appears to — enabling term B by itself would not merely under-price the hedge, it would
move the price the *wrong way* in a selloff.

The figure above is stacked for exactly this reason: the real IV panel shares its x-axis with the
put panel, so the spike and the jump in the dashed series line up vertically rather than having to
be matched by eye across two separate charts.

This is a probe-window result, not yet the walk-forward: the FULL 2006-2026 real IV series is what
Phase 13 needs to actually calibrate `vol.py`'s v2 (`base` driven bar-by-bar off a real series,
per the 2026-08-29 decision) across many regimes rather than one window. See that phase's scope
note in the roadmap.

## What this does not cover — and who owns it

| gap | owner |
|---|---|
| ~~Rolling / restriking, theta decay within a period~~ | **Done 2026-09-19** — 16-02 `RollCalendar`, 16-03 rules (Finding 7) |
| Monetising a hedge before expiry (take-profit / re-strike triggers) | Phase 13 policy sweep; a new rule |
| Delta, theta, `OptionLeg` as an object | 16-02 |
| Put spreads, collars, funding branch | 16-03 |
| Dividend yield (left at 0.0 to match the plan's worked examples; SPY is ~1.2%) | 16-02 |
| Whether hedging actually pays | Phase 13, and it needs Phase 11 first |

## Published figures

All seven figures render to standalone interactive HTML under [`docs/`](../docs/README.md), which is
the GitHub Pages root:

```bash
python -m pipelines.option_probe_figures      # or the last cell of the script
```

The builders live in
[`src/pipelines/option_probe_figures.py`](../src/pipelines/option_probe_figures.py) and are shared
between the research cells and the exporter, so the inline figure and the published page are the
same object. This is the first concrete instance of Phase 10's Track A (10-01) pattern.

## Verdict

The economics are sound and the port is exact, so 16-02 and 16-03 can be planned against verified
numbers rather than against an untested code block in a markdown file.

The payoff mechanic is **demonstrated, not merely plausible**: struck where a rolled hedge would
stand, the 0.90×-1.00× strikes return 107-161% on a nine percent dip. What is not demonstrated is
that it pays *over time* — one episode is one observation, and the frozen vol level means every
payoff figure here is a floor rather than an estimate, most severely on the cheap OTM strikes.

Two consequences for sequencing:

1. **16-02's `RollCalendar` is not cosmetic.** Restriking is what puts the next drawdown in front
   of the strike. Without it, any backtest of this measures carry and calls it a result — which is
   the exact error Finding 4 records.
2. **Phase 11 gates the vol LEVEL, and the vol level is where the payoff lives.** That is a
   stronger argument for 11-01 than the window-length one this write-up originally made.
