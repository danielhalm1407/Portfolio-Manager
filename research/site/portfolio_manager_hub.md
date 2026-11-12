# Portfolio Manager

Portfolio Manager is a comprehensive project combining research and execution with the aim of shaping an investment portfolio that produces sustained returns across market environments. 

A key defining principle supporting this outcome is growth under the guardrails of diversification and hedging. At the core, it aims to convert a range of market narratives into portfolio tilts, allocating greater weight to areas of higher conviction while conscious of risk impact across the wider portfolio, and executes within an Interactive Brokers account.

This site publishes select parts of the research behind it. Each piece has its own landing page and can be read on its own.

## [Hedging an equity portfolio with put options](../../docs/put-hedging/index.html)

Investigates the effectiveness of using put options to hedge a portfolio against market risk, depending on their contractual design to more reliably offset drawdowns in an underlying equity index compared to diversification alone, which can suffer from assets becoming more correlated as conditions evolve.

Of particular focus in this section are the cost of protection, the structures carrying a put-based hedge (e.g. outright put, spread, or collar), and how far out of the money should a put should be struck to maximise its protective impact.

This research contains three studies, from the mechanics of an option pricing function on a synthetic volatility surface to a full parameter sweep over twenty years of historic levels and implied volatility for a market index underlying.

Data: synthetic vol surface, then REAL implied vol from IBKR, 2006-2026, IN-SAMPLE
