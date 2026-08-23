---
description: "Turn qualitative market narratives into portfolio tilts, and execute the result against a real Interactive Brokers account"
type: Project
about: "Portfolio-Manager"
---

# Portfolio-Manager

## What This Is

A thematic-fundamental research engine that converts qualitative market commentary into portfolio
tilts, feeds those tilts into a disciplined allocator, and executes the resulting target weights as
real orders against an Interactive Brokers account. An extension layer builds structural conviction
in the gas / power / electrification value chain.

## Core Value

A market narrative becomes a defensible target weight, and that target weight becomes a real
position — with the accounting, attribution and order-verification needed to trust each step.

## Current State

| Attribute | Value |
|-----------|-------|
| Type | Application |
| Version | 0.2.0 |
| Status | Active — execution half built and live-tested; research half scaffolded |
| Last Updated | 2026-08-01 |

## Requirements

### Core Features

- **Accounting engine** — multi-symbol, GUI-free realised/unrealised P&L, average cost, positions
- **Rebalancing** — constant-mix and rule-driven rebalancing as the natural source of realised P&L
- **Return attribution** — within/between decomposition over weights and returns
- **IBKR integration** — account snapshot, historical data, position logging, order submission
- **Live rebalancer** — multi-currency target weights to acknowledged orders, dry-run gated
- **Order verification** — per-order verdicts, full message capture, cancellation recovery path
- **Research half** — theme extraction from commentary into scored tilts and conviction briefs

### Validated (Shipped)

- P&L accounting extracted to `portutils.portfolio`, pinned by golden-fixture parity tests
- Rebalancing study, return attribution consolidation, figure/theme export path
- IBKR read path: executions, sync, backcast, position logging, non-US contract resolution
- Live multi-currency rebalancer with contract specs and base-currency valuation
- Order acknowledgement, per-order verdicts, SMART routing, cancellation pipeline
- Documentation hub: root README plus one README per area
- Executions request hardened: non-deprecated UTC time format, the midnight-today ceiling
  documented, and a terminal cell runner that makes IB callback traffic visible

### Active (In Progress)

- Staged live verification runbook — the TWS-dependent legs (see Phase 7)

### Planned (Next)

- Research half: theme briefs, conviction write-ups, allocator wiring (Phase 9)

### Out of Scope

- SPY and KMLM in the live book — rejected on client eligibility (PRIIPs/KID); UCITS equivalents
  are a user decision, not an automatic substitution
- Direct order routing — listing venue belongs in `primaryExchange`, never `exchange`

## Constraints

### Technical Constraints

- Python >= 3.11; package `portutils` installed editable via `pip install -e .`
- Interactive Brokers TWS must be running for every live leg. As of 2026-08-02 it starts and
  answers read-only API requests, so this is no longer a blocker
- **`reqExecutions` serves only the current day (since midnight).** Verified against live TWS: an
  unfiltered `ExecutionFilter()` returns 0 fills with `execDetailsEnd` received while the TWS Trade
  Log shows a week of them. Genuine execution history requires IBKR's Flex Web Service, not this
  API path
- IB date-time request fields must carry an explicit timezone. UTC dash notation
  (`yyyymmdd-HH:MM:SS`, no suffix) is the form this repo sends; the implied-timezone form is
  deprecated and TWS flags it with error 2174
- `data/raw/` is append-only — transformations happen in code and land in `data/processed/`
- Library code in `src/portutils/` must be import-safe; side effects belong in `src/pipelines/`
- Secrets live in `.env`, loaded once by `portutils.utils.config`; never `os.getenv()` directly

### Business Constraints

- Live orders touch a real brokerage account — every live path is gated and requires explicit
  human approval of a dry-run table first

## Key Decisions

| Decision | Rationale | Date | Status |
|----------|-----------|------|--------|
| Listing venue in `primaryExchange`, never `exchange` | Direct routing was an accident of copying the account snapshot; it cost a manual-confirmation hold on every foreign leg | 2026-07-26 | Active |
| An untransmitted order is not an order | It exists only in the TWS client, is invisible to `reqAllOpenOrders`, and cannot be cancelled via the API | 2026-07-26 | Active |
| Order classification believes the status over the code | A code list fails silently as IB adds warnings; `PreSubmitted` is unambiguous | 2026-07-26 | Active |
| Advisory messages are recorded, not discarded | "Not terminal" and "not worth keeping" are different claims; conflating them made five held orders look lost | 2026-07-26 | Active |
| Cancellation lives in its own pipeline behind `--cancel` | The recovery tool must not be reachable by accident from the tool being recovered from | 2026-07-26 | Active |
| Comments travel with their code, verbatim | Retention is mandatory in this repo; refactors must not silently drop rationale | Standing | Active |
| The running TWS outranks every written IB source | On the ExecutionFilter time format, the field reference, ib_insync and the ibapi method docstring each gave a different, incomplete answer; one live probe produced the real spec from TWS's own error text | 2026-08-02 | Active |
| Execution history comes from Flex Web Service, not `reqExecutions` | The API path serves only the current day, proven by an unfiltered request returning nothing while the Trade Log showed a week | 2026-08-02 | Active |

## Success Metrics

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Test suite green | 130 / 130 | 129 pass, 1 fail by design (harness armed) | Near |
| Live round trip verified against TWS | Complete | Unblocked — TWS runs and answers read-only requests; round trip not yet done | In progress |
| Theme briefs written | >= 1 conviction brief | 0 | Not started |

## Tech Stack / Tools

| Layer | Technology | Notes |
|-------|------------|-------|
| Language | Python >= 3.11 | env `~/miniconda3/envs/venv-stats` — only env with `portutils` + pytest |
| Package | `portutils` | editable install; `analysis`, `ingestion`, `portfolio`, `utils`, `viz` |
| Broker API | `ibapi` / TWS | reader thread + event-driven callbacks |
| Data | pandas, parquet | `data/processed/prices_*.parquet` |
| Viz | Plotly | HTML figure export with a shared theme |
| Testing | pytest | stub the app, not the network |
| Config | YAML + `.env` | `config/settings.yaml`, `config/asset_universe.yaml` |

---
*Created: 2026-08-01 — migrated from 12 pre-existing plans in `.claude/plans/`*
