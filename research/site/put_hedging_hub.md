# Hedging an equity portfolio with put options

This research tests put option overlays as a hedge for an equity portfolio. It sits within a wider set of strategies currently part of or planned to be incorporated into the Portfolio Manager project.

The three studies build on each other. The first establishes how the pricer behaves on a synthetic volatility surface. The second watches a monetisation policy make decisions on real implied volatility through the GFC. The third sweeps the put's strike and the monetisation trigger across three crises and three calm stretches. They read best in order.

## [Testing option price dynamics over recent drawdowns](../../docs/probing/index.html)

Foundationally, I construct an artifical options pricer and examine how an outright protective put, a put spread and collar price and behave through one probing window . In particular, I explore the pricer's own mechanics, the smirk, term structure, and the cost of carrying a hedge, worked out step by step against a synthetic implied-vol surface and with key takeaways marked in logical order as findings 1-9.

Data: synthetic vol surface (constant ATM level), plus a real-IV follow-on

## [Option hedge structures compared over the GFC](../../docs/validation/index.html)

The following stage seeks to develop a strategy tailored to work around a put option's dynamics, primarily in rolling a hedge as long as it is reasonable, closing a hedge at a drawdown trigger, banking the gain (as opposed to allowing the option to decay), and waiting for cheaper insurance before re-opening. The stage compares four option structures and one monetisation policy against each other and a blind-roll control.

Data: Historic index levels and implied vol from IBKR, one window (2007-10 to 2009-03), IN-SAMPLE

## [Optimising put option moneyness for drawdown-adjusted returns](../../docs/optimisation/index.html)

Extending this idea further, I then optimise how far out of the money a put should be struck, and how far one should the market fall before the hedge is closed. A full parameter sweep is scored on windows spanning market environments including crises and calm stretches. 

Data: Historic index levels and implied vol, 2006-2026, 63 parameter combinations over six windows, IN-SAMPLE
