# Hedging an equity portfolio with put options

This research tests option overlays as a hedge for an equity portfolio. It is the hedging half of Portfolio Manager's structural commodity and hedging extension.

The three studies build on each other. The first establishes how the pricer behaves on a synthetic volatility surface. The second watches a monetisation policy make decisions on real implied volatility through the GFC. The third sweeps the put's strike and the monetisation trigger across three crises and three calm stretches. They read best in order.

## [Testing option price dynamics over recent drawdowns](../../docs/probing/index.html)

How does a protective put, put spread and collar price and behave through one probe window — the pricer's own mechanics, the smirk, term structure, and the cost of carrying a hedge, worked out step by step against a synthetic implied-vol surface. Findings 1-9.

Data: synthetic vol surface (constant ATM level), plus a real-IV follow-on

## [Option hedge structures compared over the GFC](../../docs/validation/index.html)

Can a monetisation policy be seen DECIDING on real data — closing a hedge at a drawdown trigger, banking the gain, and waiting for cheaper insurance before re-opening? Four structures, one policy, every fill marked on the equity path, against a blind-roll control.

Data: REAL implied vol from IBKR, one window (2007-10 to 2009-03), IN-SAMPLE

## [Optimising put option moneyness for drawdown-adjusted returns](../../docs/optimisation/index.html)

How far out of the money should the put be struck, and how far should the market fall before the hedge is closed? A full parameter sweep scored on three crises and three calm stretches. The two regimes rank configurations in OPPOSITE directions, and the choice turns out to be decided by carry in quiet markets rather than by performance in a crash.

Data: REAL implied vol, 2006-2026, 63 parameter combinations over six windows, IN-SAMPLE
