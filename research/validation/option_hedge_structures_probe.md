# Option hedge structures compared over the GFC

*SPY, 377 trading bars, 2007-10-01 → 2009-03-31, priced with the ticker's own measured implied
volatility. Script:
[`option_hedge_structures_probe.py`](option_hedge_structures_probe.py).*

## Why this exists

The [pricing work](../../docs/probing/index.html) ended on an uncomfortable result. A rolled protective
put reached **1.97× the premium paid** at the bottom of a drawdown and then expired worthless,
because the rule holding it had no instruction to do anything other than wait for expiry. Rolling
determines *where* a strike sits; it says nothing about *when* to take a gain.

That suggests an obvious addition — close the hedge once the market has fallen far enough, bank
the proceeds, and wait for insurance to get cheap again before re-opening. It also raises the
obvious objection: a policy that goes **unhedged** after a crash begins is doing exactly the wrong
thing if the crash continues.

So the question is not whether such a policy is implementable. It is whether, on a real crisis
deep enough and long enough to test it, the policy helps or hurts.

The 2007-09 window is the natural test. It contains a drawdown severe enough to trigger
monetisation several times over, a volatility spike large enough to keep a re-entry gate shut for
months, and enough bars for several quarterly rolls. Crucially it is also the hardest case for the
policy: going flat in late 2008 means standing aside during the worst of the fall.

Four structures run through an identical policy and an identical harness, so the only difference
between any two panels is the one named in its heading.

**The policy.** Close the hedge once the underlying is 10% below its running peak; stay unhedged
until implied volatility falls back under 0.18, or 126 bars pass, whichever comes first; then
re-open and repeat. These levels are illustrative — chosen to exercise the mechanism, not fitted.
Finding good levels is the [moneyness optimisation](../../docs/optimisation/index.html) work.

## Reading the panels

Each panel shares a three-row layout. It repays reading once, since the same structure then holds
for all four.

**Top — equity and the market.** SPY and the strategy's equity, both rebased to 1.0 at the first
bar, so a widening gap is the thing the hedge exists to produce. Every fill is marked on the
equity line: a triangle up where a leg is struck, a diamond where a roll closes one leg and opens
the next, a triangle down where the policy closes the hedge and banks its value. Hovering a marker
reports the drawdown and the implied volatility the rule saw on that bar, which distinguishes "a
trigger fired" from "the trigger fired for the reason it was built to fire for".

**Middle — value as a multiple of premium paid.** The long leg's current value divided by its
cost. This is the number that answers *was it worth closing here*. A dashed line marks break-even;
the exit markers are redrawn on this panel so a claimed exit multiple can be checked against the
curve rather than taken from a tooltip. Gaps are unhedged stretches, where no premium is
outstanding and the ratio is undefined rather than zero.

**Bottom — implied volatility and the re-entry gate.** The volatility path the rule priced
against, with unhedged stretches shaded and the re-entry level drawn in. This panel answers *why
is nothing hedged right now* — a question no table of final values can address.

## Protective put — the baseline structure

A 0.90 floor rolled every 63 bars, with the monetisation policy armed. The simplest structure the
policy can run on, and the reference for the other three.

Final value 86.72 against a starting 100 — a −43.8% return, against a deeper fall in SPY itself.
Three monetisations, one roll, and a best exit at **5.26×** the premium paid: a put struck near the
top and closed once the crash had done its work.

The book spent **64.7% of the window with no hedge at all.** That is the policy's cost stated
honestly. Waiting for cheap insurance means standing aside, and in this window standing aside
covers some of the steepest declines in the sample. The result below is what that trade-off
actually produced, not what it was hoped to produce.

[Open this figure on its own](../../docs/validation/figures/protective_put.html)

## Put spread 0.90/0.80 — part-financed protection

The same 0.90 long put, with a short put at 0.80 sold against it to reduce the cost. The point of
this panel is not the put spread as such — it is that the policy's behaviour is not an artefact of
the plain long put.

Three monetisations, one roll, the same 5.26× best exit — the short leg does not change what the
long leg was worth when it was closed — and a −45.2% return, slightly worse than the plain put.
The financing cost shows up exactly where it should: in the final number, not in the trigger's
behaviour.

<!--STAT:put_spread_financing-->

[Open this figure on its own](../../docs/validation/figures/put_spread.html)

## Rolling collar 0.90/1.28 — financed by capping upside

The same 0.90 floor with a call sold at 1.28 to fund it. Same policy, same three monetisations,
same 5.26× best exit on the put leg.

The collar is the worst of the three hedged structures at −46.0%, which is the mirror image of the
put spread's result: the short call surrenders part of the rebound between declines. As before,
the cost appears in the final number and nowhere else — the trigger did not behave differently for
carrying a capped upside.

<!--STAT:collar_financing-->

[Open this figure on its own](../../docs/validation/figures/collar.html)

## Blind roll — the control, with monetisation switched off

The same 0.90 protective put rolled every 63 bars, with the monetisation policy removed and
nothing else changed.

Five rolls rather than one — a book that has closed its hedge is not there to be rolled — zero
monetisations by construction, and a **−50.8% return**, markedly worse than any of the three
monetising structures.

This is the comparison the page is built to support. Same rule, same strike, same roll cadence,
same window, same harness, with exactly one thing removed. On this window the monetisation policy
is worth roughly **seven percentage points** against an otherwise identical blind roll — and it
earned that while spending nearly two thirds of the window unhedged.

[Open this figure on its own](../../docs/validation/figures/blind_roll_control.html)

## Summary

<!--TABLE:summary-->

`rolls` counts closed legs immediately replaced; `monetisations` counts times the policy banked a
hedge's value and went flat; `best_multiple` is the richest such exit, value over premium paid;
`flat_share` is the fraction of the window spent with no hedge in place. The control's
`best_multiple` is blank because a hedge that is never closed early has no exit to measure.

## What this supports, and what it does not

**Supported.** A monetisation policy can be seen deciding, on real data, over the window a hedge
exists for. It opens near the top, survives several rolls, closes at multiples of its cost, and
waits for cheaper insurance before re-opening. Against an otherwise identical blind roll it left
the book materially better off here — and it did so in the window most hostile to it, where going
flat meant standing aside during a historic decline. The objection that monetising must be
self-defeating in a sustained crash is not supported by this run.

The three hedged structures differ mainly in **financing cost**, not in how the trigger behaves.
That is a useful negative result: the monetisation decision appears largely separable from the
choice of structure, which means the two can reasonably be studied one at a time.

**Not supported.** That these levels are good levels — they were hand-picked, and a 10% trigger
tested on the window that motivated it proves nothing about 10%. That this window is
representative; it is one regime, chosen precisely because it is extreme. That any one structure
should be preferred to the others on a single window's final values. Nothing here is out-of-sample.

Fills are at the model mark, with no bid/ask spread and no slippage, which flatters every hedged
row by an unmeasured amount — and flatters the monetising rows more than the control, since they
trade more.

The obvious next question is what the right levels are. That is the
[moneyness optimisation](../../docs/optimisation/index.html) section, which sweeps strike and trigger
depth across six windows covering both crises and calm periods.
