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

## Finding 4 — the window cannot answer the actual question

SPY rose **+16.0%** across these 250 bars with a maximum drawdown of **−9.1%** (2026-03-30). On a
window with no crash, every put is pure cost by construction. This is not a result about hedging;
it is the restatement of the constraint Phase 11 exists to remove — one regime, 250 rows.

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

The reason is a property of this probe, not a contradiction of the decision: the ladder is struck
**once at inception and never rolled**, so by the time of the trough the market had round-tripped
and `K/S` was 0.81–1.01, i.e. spot was almost back at `spot_ref`. The gap is a function of
distance travelled since the strike was set, and 12-01's example moves spot 12% below a strike set
10% out. **A rolling ladder would restrike and see the full effect** — which is precisely what
16-02's `RollCalendar` provides and this script does not.

The decision stands: ship v1, and never enable term B (current-spot reference) without term A
(dynamic vol level).

## What this does not cover — and who owns it

| gap | owner |
|---|---|
| Rolling / restriking, theta decay within a period | 16-02 `RollCalendar`, 16-03 |
| Delta, theta, `OptionLeg` as an object | 16-02 |
| Put spreads, collars, funding branch | 16-03 |
| Dividend yield (left at 0.0 to match the plan's worked examples; SPY is ~1.2%) | 16-02 |
| Whether hedging actually pays | Phase 13, and it needs Phase 11 first |

## Verdict

The economics are sound and the port is exact, so 16-02 and 16-03 can be planned against verified
numbers rather than against an untested code block in a markdown file. The cost figures above are
the ones to design the structures around; the payoff figures are not usable until Phase 11 supplies
a window containing an actual crash.
