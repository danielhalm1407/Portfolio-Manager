# Option monetisation over the GFC

## The question

13-01 built a monetisation policy: hold a protective hedge, close it and bank the gain once a
drawdown trigger fires, then wait for cheaper insurance (a real-IV re-entry gate) before opening the
next one. 13-01.1 proved the mechanism runs correctly on synthetic, hand-constructed paths. Neither
proves it does anything sensible on a real market.

This write-up is that proof, read as a picture rather than a table. The window is 2007-10-01 to
2009-03-31 — 377 trading bars — priced with REAL IBKR implied vol, not a synthetic surface: the one
window in the available history where every mechanism fires in the same run — a drawdown deep enough
to trigger monetisation, a vol spike that holds the re-entry gate shut, and enough bars for several
rolls. Four structures are run through the identical policy and the identical harness
(`research/validation/option_ladder_probe.run_one`), so the only thing that differs between any two
panels below is the one parameter named in its heading.

**What this is not.** IN-SAMPLE, one window, one regime. The monetisation levels
(`monetise_drawdown=0.10`, `reentry_iv=0.18`, `max_flat_bars=126`) are illustrative — hand-picked to
exercise the mechanism, not fitted, not swept, not claimed to be good numbers. Fills are at the model
mark, with no bid/ask spread and no slippage, which flatters every hedged row here by some amount
that has not been measured. Nothing below ranks a structure against another on these numbers; that is
13-02's work, on evidence this plan does not provide.

## How to read the figure

Every panel below shares the same three-row layout, and it repays reading once, since the same
argument then holds for all four.

**Top: equity and the market.** SPY and the strategy's equity, both rebased to 1.0 at the window's
first bar, so a widening gap is the thing the hedge is FOR. Every fill is marked on the equity
line — a triangle up where a leg is struck, a diamond where a roll closes one leg and opens the next,
a triangle down where the policy monetises (closes the hedge and banks the value). Hovering any
marker shows the drawdown and the real IV level the rule saw on that exact bar, which is what turns
"a trigger fired" into "the trigger fired for the reason it was built to fire for."

**Middle: value as a multiple of premium paid.** The long leg's current value divided by what was
paid for it — the ratio the monetisation trigger can be armed on, and the number that answers "was it
worth closing here." A dashed line marks 1.0x (break-even); the monetise markers are re-drawn on this
panel so a reader can check "closed at 5.3x" against the curve rather than take it from a hover box.
Gaps in the line are FLAT stretches — no premium is outstanding, so the ratio is undefined rather
than zero.

**Bottom: implied vol and the re-entry gate.** The real IV path the rule priced against, with the
FLAT stretches shaded and the re-entry level drawn as a dashed line. This is the panel that answers
"why is nothing hedged right now" — a question a table of final values cannot answer at all.

## Protective put — the headline structure

`ProtectivePutRule`, floor 0.90, rolled every 63 bars, monetising at a 10% drawdown and re-entering
once real IV falls back under 0.18 (or after 126 bars flat, whichever comes first). This is the
simplest structure the policy can be run on, and the one the other three are compared against.

Over the window: final value 86.72 against the window's starting 100, a −43.8% return against SPY's
own deeper fall, three monetisations, one roll, and a best exit at **5.26x** the premium paid — the
put that was struck near the top and closed once the crash had done its work. The book spent 64.7% of
the window with no hedge on at all, which is the visible cost of the policy: waiting for cheaper
insurance is not free, and this is what "not free" measures to in one concrete run.

[Open this figure on its own](../../docs/validation/figures/protective_put.html)

## Put spread 0.90/0.80 — the same policy, a capped structure

Identical monetisation policy, on a `PutSpreadRule` instead: the same 0.90 long put, financed by a
short put struck at 0.80. The point of this panel is not the put spread itself — it is that the
mechanism's behaviour is not an artefact of the plain long put above. Three monetisations, one roll,
the same 5.26x best exit (the short leg does not change what the LONG leg was worth when it was
closed), and a −45.2% return — slightly worse than the plain put, which is the financing cost of the
short leg showing up exactly where it should: in the final number, not in the trigger's behaviour.

<!--STAT:put_spread_financing-->

[Open this figure on its own](../../docs/validation/figures/put_spread.html)

## Rolling collar 0.90/1.28 — the same policy, a capped upside

`RollingCollarRule`: the same 0.90 floor, plus a call sold at 1.28 to fund it further. Same
monetisation policy, same three monetisations, same 5.26x best exit on the put leg. The collar comes
out worst of the three hedged structures at −46.0%, which is the mirror image of the put spread's
result: the short call gives back some of the equity rebound between crashes, and that cost is
visible in the final number and nowhere else — the monetisation trigger itself did not behave any
differently for carrying a capped upside.

<!--STAT:collar_financing-->

[Open this figure on its own](../../docs/validation/figures/collar.html)

## The control — blind roll, no monetisation

`ProtectivePutRule` again, floor 0.90, rolled every 63 bars — with the monetisation policy switched
OFF. This is the control the other three rows are measured against: same structure as the
protective put row above, same rule class, same strike, same roll cadence, with exactly one thing
removed.

Five rolls (against one, above — a monetised book that has closed its hedge is not there to be
rolled), zero monetisations by construction, and a −50.8% return: markedly worse than any of the
three monetising rows. **The difference between this row and the protective-put row above is the
monetisation policy and nothing else** — same rule class, same strike, same window, same harness.
That is the one comparison this write-up is built to support.

[Open this figure on its own](../../docs/validation/figures/blind_roll_control.html)

## Summary

<!--TABLE:summary-->

`rolls` counts roll events (a closed leg immediately replaced); `monetisations` counts times the
policy banked a hedge's value and went flat; `best_multiple` is the richest of those exits, value
over premium paid; `flat_share` is the fraction of the window spent with no hedge in place, waiting
for the re-entry gate. The blind-roll control's `best_multiple` is blank because a hedge that is
never monetised has no exit to measure a multiple at.

## What this does and does not support

This write-up shows a monetisation policy DECIDING, on real data, over the one window a hedge is
built for: it opens near the top, it survives several rolls, it closes at multiples of the premium it
cost, and it waits for cheaper insurance before re-opening — and doing all of that against a plain
blind roll leaves the book meaningfully better off in this one run.

It does not show that these levels are good levels, that this window is representative, or that any
one of the three hedged structures should be preferred to the others — the three differ mainly in
financing cost, not in how the trigger behaves, and 13-02 is where a claim like that would need to be
earned rather than read off four panels. It does not include transaction costs beyond the model mark.
It does not cover any window other than the one shown. Every number above is IN-SAMPLE.
