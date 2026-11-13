# PAUL Session Handoff

**Session:** 2026-10-24 → 2026-11-13
**Phase:** 13 — Walk-forward validation harness
**Plans touched:** 13-02.1 (published), 13-02.2 (partly approved and built), plus site and tooling work
outside any plan
**Context:** The published site was restructured into two hubs, every page is now edited as
Markdown with one rebuild command each, and 13-02.2 Routes A and B were built with a 2026
illustrative window and an energy diversifier. Three commits are **not pushed** (see State).

---

## Session accomplishments

### 1. The site is two hubs, both written in Markdown

| Page | Edit this | Rebuild command (repo root, venv-stats Python) |
|---|---|---|
| Portfolio Manager hub, `docs/index.html` | `research/site/portfolio_manager_hub.md` | `python -m pipelines.build_site_index` |
| Put-hedging hub, `docs/put-hedging/index.html` | `research/site/put_hedging_hub.md` | same command, builds both hubs |
| Price dynamics, `docs/probing/` | `research/validation/option_ladder_prices_probe.md` | `python -m pipelines.build_report` |
| GFC hedge structures, `docs/validation/` | `research/validation/option_hedge_structures_probe.md` | `python -m research.validation.option_hedge_structures_probe --publish` |
| Moneyness, `docs/optimisation/` | `research/validation/option_moneyness_optimisation.md` | `python -m pipelines.option_grid_sweep --publish-only` |

Deploy is `git push`: GitHub Pages serves `docs/` from `master` with no workflow. The standalone
link for the put-hedging piece is `https://danielhalm1407.github.io/Portfolio-Manager/put-hedging/`.

- A write-up's first `# ` line is now the page title (`build_report`); it used to be printed twice.
- The three study pages link back up to the put-hedging hub, which links up to the front door.
- The GFC script's exploratory cells sit behind `RUN_RUNGS`, so `--publish` re-runs only the four
  GFC configurations (about 7 s).
- Every paragraph in the three write-ups is one line (the user's own convention).
- **Every study page has a generated Contents list** (h2 and nested h3, GitHub-style anchors) and a
  "Back to contents" link under each section heading and at each section's end
  (`build_report.add_contents`). Hubs have none; the user may want them.

### 2. Writing style: two skills and a hook

- `.claude/skills/publish-writing-style/` — `SKILL.md` and `STYLE.md`, the user's voice for anything
  published. Key rules: action-title headings, "I" never "we", no capitals for emphasis in body
  text, no em dashes, full sentences with context rather than fragment lists, measured claims,
  plain-language asides for parameters, British spelling, one paragraph per line.
- `.claude/skills/learn-writing-style/SKILL.md` — learns from the user's own edits.
- `.claude/hooks/scripts/detect-hand-edits.py`, wired in `.claude/settings.json` on
  `UserPromptSubmit`, `PreToolUse` Bash and `PostToolUse` Edit/Write/Bash. It reports the user's
  uncommitted edits and their commits (no Claude trailer, or "hand edits" in the subject) once
  each. State is gitignored in `.claude/hooks/state/`.
- The user protects hand edits with **git commits**, not markers. Memory:
  `feedback_user_locked_prose.md`.

### 3. 13-02.2, partly approved and built

Approved scope and findings are recorded in `.paul/phases/13-walk-forward-validation/13-02.2-PLAN.md`
under `<approved_scope>`. Code: `src/pipelines/option_grid_extension.py` (new),
`run_config(underlying=...)`, `load_window(symbol)`, `scoring.random_windows` /
`window_kind` / `effective_window_count` (+4 tests). Run with
`python -m pipelines.option_grid_extension`, then the moneyness `--publish-only`.

- **Route B:** strikes 0.700-0.775 added; 28 new runs in `outputs/option_grid_extended/`, the 63
  guarded runs untouched. About one minute.
- **Route A:** 20 seeded random windows (seed 1302, 42-504 bars, overlap allowed, 12
  non-overlapping). 5 drawdown, 4 calm, 11 mixed.
  - Blind roll rises towards the money (0.56 at 0.70x to 1.18 at 1.00x).
  - Armed at 10%: peak 1.22 at 0.875x; 0.85-0.875x in every regime label.
  - Mean argmax 0.75x/20% and pooled corner 0.70x/20% are driven by cash banked at the
    12 March 2020 monetisation (that programme's equity goes from 0.62x to 3.7x SPY). Excluding
    the COVID window or using medians gives 0.85x with a 6-8% trigger.
- **2026 window** (2 Jan to 18 Sep 2026, after the Iran war began in February), fresh runs at
  0.95x / 0.80x / 0.70x:
  - SPY fell 9.1%, the trigger never fired, and every strike lost about 5.3%.
  - QQQ fell 11.8%, and the 0.95x hedge was monetised at 2.1x on 27 March.
- **Energy diversifier:** the same premium spent on XLE (cached from Yahoo in
  `data/processed/prices_energy.parquet`).
  - SPY + XLE: +12.4% / 8.8% max drawdown, against +11.5% / 9.1% unhedged and +6.2% / 7.6% for
    the 0.95x put.
  - For QQQ, roughly level with the put.
- New write-up sections, all after their figures:
  - "In the 2026 drawdown that followed the Iran war..."
  - "An energy diversifier funded by the same budget..."
  - "Across twenty random windows, the robust optimum lies near 0.85×..."
- The rest of the moneyness write-up was restyled ("I", no em dashes), all content kept.

### 4. Runtime: where the speed-up lives, and the hang that was fixed

- The constant-width ledger that took one full-history run from ~6.5 min to 6.3 s is documented in
  `.paul/phases/13-walk-forward-validation/13-01.1-SUMMARY.md` (lines 53 and 89). The code is
  `src/portutils/portfolio/narrow_ledger.py`; it is enforced at `option_grid_sweep.py:253`, with the
  6.3 s constant and the 30-minute budget guard at lines 379-385.
- On 2026-11-12 the moneyness publish hung for 9+ minutes in kaleido's headless Chrome while writing
  the static 3-D PNG. The simulations were not the cause. The render is now skipped when the PNG is
  newer than `scores_tidy.csv`, and capped at 60 s otherwise (`PNG_RENDER_TIMEOUT_S`). Publish times
  are now 17 s (probe), 6 s (GFC) and 40 s (moneyness).

### 5. Bugs found and fixed

- Unwrapping paragraphs collapsed the WebGL-fallback `<script>` in the moneyness write-up onto one
  line, commenting all of it out. Restored in `efa106c`, before any push.

---

## State

- **Commits:** `58ad56a`, `efa106c`, `6c42855` (and this handoff), all on `master`, **not pushed**.
  The live site still shows `df30edd`: no new sections, no contents lists.
- **Tests:** 275 passed, 1 pre-existing unrelated failure
  (`test_rebalance_live.py::test_debug_cell_script_is_disarmed_and_gated`).
- **`.paul/STATE.md` is stale.** It still describes 13-02 as "planned, not applied", and nothing
  since 13-02 is recorded in it. 13-02.1 and 13-02.2 have no UNIFY or SUMMARY.

---

## Open items for the next session

1. **Push** when the user is happy with the local page.
2. **Typos in the user's own text, left untouched for them to fix:**
   - Moneyness write-up: "demonstrateted", "days,corresponding", "trugger".
   - Put-hedging hub: "artifical", "probing window ." (stray space), "how far one should the
     market fall".
   - Portfolio Manager hub: "how far out of the money should a put should be struck".
3. **Stale numbers in the user's hub text:**
   - The put-hedging hub's moneyness card says "63 parameter combinations over six windows"; it is
     now 91 combinations, plus twenty random windows.
   - The Portfolio Manager hub's `Data:` line still says "REAL".
4. **13-02.2, not yet built:**
   - Route C: SPY back to 1990 with calibrated VIX as stand-in implied vol, from Yahoo.
   - Route D2: semiconductors.
   - Parked:
     - a refiners ETF (CRAK) as the diversifier;
     - the diversifier run over every window, not just 2026;
     - a per-underlying re-entry IV gate (0.18 suits SPY; QQQ waited 27 Mar to 18 Aug);
     - the 30-minute runtime budget check, not yet in `option_grid_extension.main`.
5. **PAUL hygiene:** UNIFY 13-02.1 and 13-02.2 (SUMMARY files), then bring `STATE.md` up to date.
6. Then 13-03: out-of-sample validation (CPCV).

## Resume

Read this file, then `13-02.2-PLAN.md` `<approved_scope>`, then
`.claude/skills/publish-writing-style/STYLE.md` before touching any published prose. Python is
`~/miniconda3/envs/venv-stats/python.exe`.
