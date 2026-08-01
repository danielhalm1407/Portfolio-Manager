# A README that is a hub, and one doc per area beneath it

> Repo copy (canonical): this file — the PostToolUse hook
> mirrors this file there with relative links. Links below are absolute `file:///C:/...` so they
> resolve from the home copy.

## Context

[`README.md`](../../README.md)
still describes the project as it was planned: research engine → theme scan → conviction → allocator
→ Dash app. Every path it lists does still exist, but its pipeline list names four of the ten
pipelines that exist, and it does not mention `orders/` at all — so the entire IBKR execution stack
(`rebalance_live.py`, `ibkr_requests.py` at ~2,700 lines, `cancel_orders.py`, the Book/P&L engine in
`portutils/portfolio`) is absent from the one document claiming to describe the architecture.

Doc coverage below the root is uneven. `src/pipelines/` and `orders/` have good per-file docs;
`portutils/portfolio` (8 modules), `portutils/ingestion/ibkr_requests.py`, `portutils/viz`, `app/`,
`config/`, `tests/` have none. Nothing states how the pieces connect end to end.

**Intended outcome:** a README that is a *hub* — headed sections explaining what each area is, each
linking down to a folder-level doc — plus one narrative document for the execution stack, which is
the part that spans folders and is therefore invisible from any single one.

## Decisions taken

- **Folder-level `README.md`**, next to the code it describes. GitHub renders it when you browse
  into the folder, and a doc that lives beside its subject rots more slowly than one in a distant
  `docs/`. Cross-cutting narratives (the execution stack) live at the repo root, because they belong
  to no single folder.
- **`CLAUDE.md` files stay untouched and separate.** They are instructions *to Claude* — conventions,
  prohibitions — not documentation for a human reading the repo. The new READMEs may reference them
  but must not absorb or duplicate them.
- **Full docs for the execution stack and `portutils`; a paragraph in the README for thin areas**
  (`models/` contains no code at all, only a CLAUDE.md; `app/`, `notebooks/`, `data/`, `outputs/`,
  `config/`, `sandbox/` are small or self-evident). Writing a page about an empty folder documents an
  intention, not a system.

## 1. `README.md` — the hub

Keep the opening intent sections (what the project aims to do, core vs extension, the five
Claude-infrastructure layers) and the setup block. Replace the single annotated tree with:

- a **shorter tree**, corrected against reality — every path verified to exist before it is written,
  and the ten pipelines and `orders/` present
- then **one `##` section per area**, each 2-5 sentences answering "what is this, why does it exist,
  what is the one thing to know", ending in a link to that area's own README:

  `## Execution stack` → [EXECUTION_STACK.md](#2) · `## src/` · `## src/portutils/` (with `###`
  subsections per subpackage) · `## src/pipelines/` · `## orders/` · `## research/` · `## models/` ·
  `## app/` · `## data/, outputs/, config/` · `## notebooks/` · `## tests/` · `## .claude/`

- a **"Where do I look for X" table** near the top: "how a trade actually reaches IBKR", "how a theme
  becomes a weight", "what a rejected order means" → the doc that answers it. The current README has
  no entry point for a reader who knows what they want but not where it lives.

## 2. `EXECUTION_STACK.md` (repo root) — the narrative that spans folders

The document this repo most lacks. Traces one rebalance end to end, naming the real functions:

```
config/settings.yaml + asset_universe.yaml   weights and guards
  → book_from_portfolio / Book               positions, base-currency equity
  → base_currency_marks                      FX: nothing IBKR returns is in one currency
  → ConstantMixRule.propose                  the studied policy, unchanged
  → build_orders                             live-only guards: whole shares, min_turnover, ceiling
  → submit_rebalance_orders                  per row: contract() + market_order() + place_order()
  → TWS                                      placeOrder writes to a socket and returns
  → check_orders                             what we sent → ack+verdict → broker view → combined
  → outputs/live/*.csv                       audit trail
```

Sections: the three sticking points already recorded in
[`orders/live_trading_notes.md`](../../orders/live_trading_notes.md)
(a ticker is not an instrument; nothing is in one currency; an order is async with errors that lie);
the verdict vocabulary (`REJECTED` / `PENDING_OPEN` / `HELD` / `WORKING` / `FILLED` / `NO_ANSWER`);
the safety model (`--live` vs dry run, `ARM_LIVE`, `max_order_value`, `min_turnover`, the pending
guard); and where each piece lives, linked.

**Absorbs the two root merge plans** (see §5), which is where the *architecture history* belongs:
why there is a single `IBApp(EWrapper, EClient)` and no base class, why the library owns all TWS
plumbing, and why `orders/kts.py` is a consumer of it rather than a second implementation.

## 3. Folder READMEs — written in full

| file | covers |
|---|---|
| `orders/README.md` | what `orders/` is *for* (interactive harnesses and GUIs, never imported), the three scripts, and links to the six existing per-file docs |
| `src/README.md` | the library/pipeline split and why side effects may not live in `portutils` |
| `src/portutils/README.md` | the five subpackages, one paragraph each, linking down |
| `src/portutils/ingestion/README.md` | `ibkr_requests.py` — connection lifecycle, the single `IBApp`, pure builders, request helpers, `OrderApp`; the callback/threading model; `reuters.py` |
| `src/portutils/portfolio/README.md` | `book.py`, `ledger.py`, `fills.py`, `rules.py`, `simulator.py`, `backcast.py`, `execution.py`, `ibkr_sync.py` — what each owns and which are pure |
| `src/portutils/analysis/README.md` | `performance.py`, `returns.py`, `factor_analysis.py`, `strategies.py`; links the existing `factor_analysis.md` |
| `src/portutils/utils/README.md` + `viz/README.md` | short: config loading is centralised and secrets never read directly; the Dash/Panel viz helpers |
| `src/pipelines/README.md` | the ten pipelines in a table — what each does, whether it touches the broker, and its doc link |
| `research/README.md` | the research stack: commentary → theme brief → scored tilt → conviction, the format rules, and the ad-hoc `.py` studies |
| `tests/README.md` | the six test modules, what each pins, and how to run them with the project env |

Each links to the per-file docs that already exist rather than restating them; the per-file docs
remain the detailed layer.

## 4. Verified, not asserted

Every path and symbol named in the new docs is checked to exist before it is written — the current
README's stale pipeline list is exactly the failure to avoid. A single script walks all markdown,
resolves relative links, and reports misses; it must return **0 broken** across the whole repo, not
just the file being edited.

## 5. The two root merge plans

[`CONN_REQUESTS_MERGE_PLAN.md`](../../archive/CONN_REQUESTS_MERGE_PLAN.md)
(marked ✅ done — `ibkr_conn.py` folded in, base class collapsed) and
[`MERGE_INFRASTRUCTURE_PLAN.md`](../../archive/MERGE_INFRASTRUCTURE_PLAN.md)
(kts.py ⇄ library; partly done, pinned by `tests/test_kts_migration.py`).

1. **Parse both and fold their durable content out** — the layering rationale and the "single IBApp,
   no base class" decision into `EXECUTION_STACK.md`; the kts-specific overlap table and what remains
   outstanding into `orders/README.md`. Step-by-step migration instructions are *not* durable and are
   not carried over.
2. Then move both into `archive/` and add `archive/` to `.gitignore`.
   **Note the consequence, since it is not obvious:** both files are tracked, so this shows up as a
   deletion in git. The content survives on disk and in history, but it leaves the repo for
   collaborators. That is what "gitignored archive" means, and it is the instruction — flagging it,
   not arguing with it.

## Verification

1. Link/anchor sweep across every `.md` in the repo (excluding `.git`, `.pytest_cache`): each
   relative target resolves and each `#L` anchor is within the file. Require **0 broken**.
2. Existence check on every path named in the new README tree and area docs.
3. Symbol check: every function named in `EXECUTION_STACK.md` is `grep`-able in the file it is
   attributed to — no describing code that has been renamed.
4. `pytest tests/ -q` — unchanged behaviour expected (docs only), still green except the known
   `test_debug_cell_script_is_disarmed_and_gated` failure while `ARM_LIVE = True`.
5. `git status` after the archive move shows the two deletions and nothing unexpected.

## Progress log

| step | state |
|---|---|
| 1. README hub: sections, corrected tree, "where do I look for X" | not started |
| 2. `EXECUTION_STACK.md` | not started |
| 3. Folder READMEs (10 files) | not started |
| 4. Fold merge plans in, then archive + gitignore | not started |
| 5. Repo-wide link and symbol verification | not started |

## Decisions

| date | decision | why |
|---|---|---|
| 2026-08-01 | Folder-level `README.md` rather than a root `docs/` | GitHub renders it on folder browse, and a doc beside its subject is the one people actually update. Cross-folder narratives stay at root because they belong to no folder. |
| 2026-08-01 | `CLAUDE.md` files untouched | They instruct Claude; the READMEs explain the system to a person. Merging the two makes both worse. |
| 2026-08-01 | Full docs for the execution stack and portutils; a README paragraph for thin areas | `models/` holds no code. A page about it would document an intention and then go stale as an intention. |
| 2026-08-01 | Every path and symbol verified before being written | The current README lists four pipelines where ten exist — the exact failure mode of documentation written from memory. |
| 2026-08-01 | Merge plans: durable content folded out, then archived to a gitignored folder | Per instruction. The step-by-step migration text has no future reader; the layering decisions do, and they belong with the stack they describe. |
