---
phase: 11-long-history-data
plan: 01
task: 1
type: ProbeEvidence
about: "Portfolio-Manager"
description: "What long-dated daily history and volatility data are actually obtainable, and from where — raw observations, 2026-08-24"
---

# 11-01 Task 1 — Probe findings

Raw observations, not design. Produced by
[`src/pipelines/probe_history_sources.py`](../../../src/pipelines/probe_history_sources.py);
machine-readable evidence in `data/raw/probes/history_sources_2026-08-24.json`.

**Where this was run matters.** This run happened in a remote Linux container, not on the dev
machine. That decides what half of the probe could be answered — see A below.

---

## A. IBKR is UNMEASURED in this run

No listener on 7497 (TWS paper), 7496 (TWS live), 4002 or 4001 (Gateway). `ibapi` is not even
installed in the container. Every IBKR question in this plan — the per-request bar ceiling, the
pacing rate, the earliest `1 day` bar for SPY — is therefore **unanswered**, not answered
negatively.

Per the plan's own instruction ("Where TWS is unavailable, record that and run the free-source
half only — a partial probe with its limits stated is a valid result; a guessed limit is not"),
this is the stated limit of the run. Re-running `probe_history_sources.py` on the machine with
TWS open is what closes it.

## B. The free source silently changes granularity — it does not refuse

This is the finding the whole task existed to catch, and it is the Phase 4 executions failure
wearing a different costume: **a coarsened result that looks exactly like a complete one.**

| Request for SPY | HTTP | Bars | Granularity Yahoo actually served | Median bar spacing |
|---|---|---|---|---|
| `range=max&interval=1d` | **200** | 404 | **`1mo`** | **31.0 days** |
| `period1=1990-01-01&period2=now&interval=1d` | 200 | **8448** | `1d` | 1.0 day |
| `period1=now-10y` | 200 | 2510 | `1d` | 1.0 day |
| `period1=now-5y` | 200 | 1253 | `1d` | 1.0 day |
| `period1=now-1y` | 200 | 250 | `1d` | 1.0 day |

Asking for daily bars over `range=max` returns **monthly** bars, with HTTP 200, no warning, and
no error. The only signal is `meta.dataGranularity`, which nothing forces a caller to read. A
naive long-history fetch would have succeeded and been wrong.

**Consequences for the design:**

1. **Never address a series by `range=`. Always use explicit `period1`/`period2` epochs.**
2. **Assert the granularity that came back**, both from `meta.dataGranularity` and from the
   observed median bar spacing. Metadata alone is not enough — a provider that mislabels its
   own output would slip past a metadata-only check.
3. **No pagination is required for the free source.** One epoch-addressed request returned SPY's
   entire 33-year daily history — 8448 bars, 1993-01-29 to date — in a single call. AC-1's
   paginated-stitch machinery is an IBKR-path requirement, not a universal one. If the source
   split lands free-source-primary, AC-1 has far less to build than the plan assumed.

## C. Earliest available bar, per symbol

Every symbol in `config/asset_universe.yaml`, daily bars, epoch-addressed:

| Symbol | Resolved listing | Bars | First bar | Ccy | Exchange |
|---|---|---:|---|---|---|
| SPY | `SPY` | 8448 | 1993-01-29 | USD | NYSEArca |
| JPM | `JPM` | 9227 | 1990-01-02 | USD | NYSE |
| BARC | `BARC.L` | 9391 | ≤1990-01-01 | **GBp** | LSE |
| HSBA | `HSBA.L` | 9391 | ≤1990-01-01 | **GBp** | LSE |
| LNG | `LNG` | 8151 | 1994-04-04 | USD | NYSE |
| RNR | `RNR` | 7819 | 1995-07-27 | USD | NYSE |
| DBB | `DBB` | 4938 | 2007-01-05 | USD | NYSEArca |
| ACWX | `ACWX` | 4628 | 2008-04-01 | USD | NasdaqGM |
| MNA | `MNA` | 4215 | 2009-11-17 | USD | NYSEArca |
| CSPX | `CSPX.L` | 4027 | 2010-09-15 | USD | LSE |
| CPER | `CPER` | 3712 | 2011-11-15 | USD | NYSEArca |
| DVYE | `DVYE` | 3644 | 2012-02-24 | USD | NYSEArca |
| DGRE | `DGRE` | 3284 | 2013-08-01 | USD | NasdaqGM |
| EQLT | `EQLT` | 3142 | 2014-02-25 | USD | Cboe US |
| FTXL | `FTXL` | 2493 | 2016-09-21 | USD | NasdaqGM |
| QDIV | `QDIV` | 2036 | 2018-07-17 | USD | NYSEArca |
| FLSP | `FLSP` | 1674 | 2019-12-23 | USD | NYSEArca |
| KMLM | `KMLM` | 1436 | 2020-12-02 | USD | NYSEArca |
| AINF | `AINF.L` | 431 | 2024-12-09 | GBP | LSE |
| 5MVL | — | 0 | — | — | **no listing found** |

Regime reach, against the plan's stated success measure:

- **2008:** 8 of 20 symbols — SPY, JPM, BARC, HSBA, LNG, RNR, DBB, and ACWX (from 2008-04, i.e.
  it starts *inside* the GFC year and misses the run-up).
- **2020 and 2022:** 18 of 20 — everything except AINF and 5MVL.

**The binding constraint is the hedge sleeve, not the index leg.** The plan's number requires
2008/2020/2022 "for at least the index leg", and SPY clears that with 1993. But **KMLM begins
2020-12-02** and **FLSP begins 2019-12-23** — so the crisis hedge that the whole v0.4 claim
rests on *has no 2008 history at all, and no pre-COVID history*. Of the hedge role, only MNA
(2009-11) reaches back meaningfully, and even it misses the GFC.

This does not block the plan, but it materially narrows what v0.4 can claim, and Phase 13's
walk-forward design has to be built knowing it. A 2008 test of *these* hedge instruments is not
available at any price; it would require proxying the sleeve with something longer-lived (a
managed-futures index rather than the ETF wrapper), which is a research decision, not an
ingestion one.

## D. A bare ticker can silently resolve to the WRONG instrument

`AINF` returns HTTP 200 for two entirely different instruments:

| Query | Instrument | Exchange | Ccy | First bar |
|---|---|---|---|---|
| `AINF` | Defiance Inference AI Chip ETF | NasdaqGM | USD | 2026-08-18 |
| `AINF.L` | iShares III plc — iShares AI Infrastructure UCITS ETF | LSE | GBP | 2024-12-09 |

The line held in DUP102412 is the LSE one. A sweep trusting the bare ticker would have loaded a
six-day-old, unrelated US ETF into the panel — with no error anywhere.

Four symbols resolve to more than one listing and are flagged `needs_explicit_mapping` in the
JSON: **AINF, CPER, EQLT, QDIV**. A first version of the probe broke the tie by "longest history
wins" and got it wrong three times out of three, so the probe now **picks nothing** and reports
every candidate. `EQLT` also disagrees with the YAML on identity — the YAML calls it *Xtrackers
MSCI USA Quality ESG*, the bare Yahoo listing answers *iShares MSCI Emerging Markets Quality* on
Cboe US. That needs a human eye before EQLT enters any panel.

**Side finding, and it closes an open repo question.** `config/asset_universe.yaml` carries AINF
under "HOLDINGS AWAITING CLASSIFICATION" with `name: "VERIFY — unconfirmed"`. It is now
identified: **iShares AI Infrastructure UCITS ETF, LSE, GBP, listed 2024-12-09.** `5MVL` remains
unidentified — neither `5MVL` nor `5MVL.L` resolves.

**Consequence for the design:** the universe needs an explicit per-symbol Yahoo mapping (a
`yahoo_symbol` field alongside `role` and `sleeve`), not a suffix heuristic. Currency belongs
with it — BARC and HSBA come back in **GBp (pence)**, the same factor-of-100 trap the live book
already documents.

## E. Volatility: an index level with deep history, a surface only as of today

| Capability | Reachable? | Evidence |
|---|---|---|
| `^VIX` daily **history** | **Yes** | 9560 daily bars, 1990-01-02 → 2026-08-24 |
| Live option **chain** with per-contract IV | **Yes** | SPY: 32 expiries (2026-08-24 → 2028-12-15), 158 strikes in the nearest, 124/124 calls carrying `impliedVolatility` |
| **Historical** option chains / IV surfaces | **No** | The endpoint exposes no as-of date parameter — only currently listed contracts |

The chain endpoint is crumb-guarded. Worth recording precisely, because it produced a *false
negative* on the first run: the crumb call returns **HTTP 406** when the session sends
`Accept: application/json` (it answers `text/plain`), which then surfaces on the options call as
**401 "Invalid Crumb"** — i.e. as *"no implied-vol data exists"* rather than *"bad header"*. With
`Accept: */*` on that one call, it works.

**This answers AC-5, and it determines Phase 12.** A historical implied-vol *surface* is not
obtainable free. Back-testing a rolling collar over multiple regimes therefore has three routes:

1. **Synthesise from VIX** — price options off the VIX level as an ATM 30-day vol input, with a
   modelled skew. Covers 1990→now, matching the longest equity history available. Cheapest, and
   the skew assumption is the weak joint.
2. **Synthesise from realised vol** — no external dependency at all, but it discards the
   volatility risk premium, which is precisely what a collar is harvesting or paying. For a study
   of whether hedging *pays*, that is close to assuming the answer.
3. **Buy a surface** — OptionMetrics / ORATS / CBOE DataShop, or an IBKR market-data
   subscription. Out of scope without an explicit decision from the user (the plan's boundaries
   forbid a new paid subscription).

Route 1 is the only one that is both free and honest about the vol premium. The live chain
remains useful as a *calibration check*: today's real surface can be compared against what the
VIX-driven model would have produced for today, which turns the skew assumption into something
measurable rather than asserted.

---

## What this changes in the plan

| Plan assumption | What the probe found |
|---|---|
| AC-1 needs paginated stitching | True for IBKR (unmeasured); **not needed for the free source** — one request returns 33 years |
| The risk is a hard request ceiling | The real risk is **silent granularity downgrade** at HTTP 200 |
| Symbols identify themselves | **Four symbols are ambiguous**; one resolves to the wrong instrument entirely |
| 2008/2020/2022 for the index leg | Achieved for the index leg (SPY 1993) — but **the hedge sleeve does not reach 2008 at all** |
| AC-5 open | **Answered:** VIX history yes, live surface yes, historical surface no |

## Open decision

The source-split checkpoint is blocking, and the probe deliberately does not pre-empt it. Note
that it now carries an extra wrinkle: **the IBKR half is unmeasured**, so choosing
`ibkr-primary` means choosing it without evidence, or re-running this probe on the TWS machine
first.
