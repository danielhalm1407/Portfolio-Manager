# Archive — superseded by `.paul/`

**These 12 plans are read-only history as of 2026-08-01.** Active planning lives in
[`.paul/`](../../.paul/): `ROADMAP.md` for phase structure, `phases/{NN}-{name}/` for per-plan
`PLAN.md` and `SUMMARY.md`, `STATE.md` for the current position.

Do not edit these files. If something here is still true, it belongs in the corresponding
`.paul/phases/` record instead.

## Where each plan went

| Archived plan | Now |
|---|---|
| `pnl-accounting-extraction.md` | Phase 1, plan 01-01 |
| `rebalance-realisation.md` | Phase 2, plan 02-01 |
| `return-attribution-consolidation.md` | Phase 3, plan 03-01 |
| `ibkr-live-integration.md` | Phase 4, plan 04-01 |
| `ibkr-hist-nonus-contracts.md` | Phase 4, plan 04-02 |
| `backcast-render-when-inconsistent.md` | Phase 4, plan 04-03 |
| `rebalance-live-multicurrency.md` | Phase 5, plan 05-01 |
| `order-acknowledgement-and-debug-cells.md` | Phase 6, plan 06-01 |
| `smart-routing-and-tradeable-universe.md` | Phase 6, plan 06-02 |
| `if-you-go-to-replicated-tarjan.md` | Phase 7, plan 07-01 |
| `with-within-rebalance-realization-parallel-milner.md` | Phase 7, plan 07-02 (open) |
| `docs-hub-and-execution-stack.md` | Phase 8, plan 08-01 |

## Two Progress Logs here are wrong

Verified against the codebase and git history during migration — the `.paul/` records carry the
corrected status:

- **`docs-hub-and-execution-stack.md`** says all five steps are "not started". Every one shipped in
  commit `3701f26`; all 12 files it created are on disk.
- **`rebalance-live-multicurrency.md`** and **`order-acknowledgement-and-debug-cells.md`** say
  "pending" throughout. The code is present — `live_book` at `config/asset_universe.yaml:194`,
  `wait_for_order_ack` and `cancel_all_orders` in `src/portutils/ingestion/ibkr_requests.py`,
  `src/pipelines/cancel_orders.py`, `orders/rebalance_live_debug.py`.

This drift — a plan claiming work is undone when it shipped a week ago — is the reason the project
moved to PAUL, whose UNIFY step closes the loop rather than leaving it to memory.

## The mirror hook is gone

`.claude/hooks/scripts/mirror-plan.py` was removed from `.claude/settings.json` on 2026-08-01. It
existed only because Claude Code's plan mode pins its approval banner to `~/.claude/plans/`, a path
that cannot be redirected. PAUL writes directly into `.paul/` in-repo and does not use plan mode, so
the hook had nothing left to mirror. The script itself is still on disk, unwired.
