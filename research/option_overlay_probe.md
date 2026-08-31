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

## What this does not cover — and who owns it

| gap | owner |
|---|---|
| Rolling / restriking, theta decay within a period | 16-02 `RollCalendar`, 16-03 |
| Delta, theta, `OptionLeg` as an object | 16-02 |
| Put spreads, collars, funding branch | 16-03 |
| Dividend yield (left at 0.0 to match the plan's worked examples; SPY is ~1.2%) | 16-02 |
| Whether hedging actually pays | Phase 13, and it needs Phase 11 first |

## Published figures

All four figures render to standalone interactive HTML under [`docs/`](../docs/README.md), which is
the GitHub Pages root:

```bash
python -m pipelines.option_probe_figures      # or cell 9 of the script
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
