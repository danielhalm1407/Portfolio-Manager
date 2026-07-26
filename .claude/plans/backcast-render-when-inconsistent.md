# Render cells 9-11 regardless of verdict, marked per symbol

Repo copy (canonical): `.claude/plans/backcast-render-when-inconsistent.md` — rename the mirrored file to that.

## Context

Cells 9, 10 and 11 of [research/check_existing_port.py](../../research/check_existing_port.py) all open with the same gate:

```python
if bc is not None and bc["verdict"] != "INCONSISTENT":
```

`verdict` is a **single global string** ([backcast.py L301](../../src/portutils/portfolio/backcast.py#L301) — `"consistent" if ok else "INCONSISTENT"`, where `ok` is `rec["qty_ok"].all() and rec["avg_ok"].all()`). So one bad symbol out of five blanks all three cells and every chart in the file. In the current run that is `5MVL` (no price data at all — a missing subscription, not a falsified assumption) plus `LNG` (start-date error, ~3%). Three symbols reconstruct fine and the user sees nothing.

The all-or-nothing gate was the wrong instrument. `bc["check"]` **already carries a per-symbol `verdict` column** ([backcast.py L229](../../src/portutils/portfolio/backcast.py#L229)), so the information needed to show the trustworthy part and mark the rest is sitting there unused.

Intended outcome: the reconstruction always renders, and **which symbols to believe is visible on the chart itself** rather than enforced by hiding it. That keeps the file's stated discipline (header, L12-L31: never blend measured and modelled into one unmarked line) while ending the blackout.

## Changes — all in `research/check_existing_port.py`

### 1. Header block (L12-L31) — restate the policy

The header currently implies INCONSISTENT means nothing downstream is shown. Rewrite that paragraph: the verdict now governs **labelling, not visibility**; every figure carries its per-symbol status, and a symbol that failed is drawn with an explicit marker rather than dropped. Keep the three-verdict table verbatim.

### 2. Cell 2 settings — one flag

`SHOW_SUSPECT_BACKCAST = True`. Default on (the requested behaviour); set False to restore the old hide-on-failure gate. Commented with why the default is "show, marked" rather than "hide".

### 3. New cell 8c — classify once, reuse three times

Immediately after 8b, derive from `bc["check"]` and `prices.columns`:

- `sym_verdict` — dict symbol → `"consistent"` / `"INCONSISTENT"` / `"no price data"` (the third is the 5MVL case: present in `check` via `reconcile`'s union but absent from `prices.columns`, so it never entered `anchor_qty` at all — [backcast.py L288](../../src/portutils/portfolio/backcast.py#L288)).
- `trusted` / `suspect` — symbol lists.
- `label_for(s)` — `f"{s} ({cfg.asset_role(s)})"` for trusted, with `" ⚠ UNVERIFIED"` appended for suspect. One helper, used by every `label_map` below so the marking cannot drift between charts.
- `banner` — one string naming the counts and the failing symbols, printed above each figure.

Reuses `cfg.asset_role` and `study.TICKER_COLOURS` already imported.

### 4. Cells 9, 10, 11 — swap the gate

`if bc is not None and (SHOW_SUSPECT_BACKCAST or bc["verdict"] != "INCONSISTENT"):`, then print `banner` before rendering. Titles already interpolate `bc['verdict']`; extend to name the failing count, e.g. `... [INCONSISTENT — 2 of 5 symbols unverified]`.

Per-cell additions beyond the gate:

- **Cell 9** — the plotted split comes from `TOTAL_*`, which sums trusted *and* suspect symbols. Add a small table beside it: all-symbol total vs total recomputed over `trusted` only (sum the `{s}_realised_pnl` / `{s}_unrealised_pnl` columns), so the reader can see how much of the headline number rests on unverified reconstruction. Table only — no second figure.
- **Cell 10** — add a `verdict` column to `per_ticker` from `sym_verdict`, and stop `.dropna()`-ing unpriced holdings out of existence: keep the row with NaN P&L and its `no price data` verdict. Use `label_for` in `label_map`.
- **Cell 11** — weights are normalised over `path.columns` only, so an unpriced holding is silently excluded and every plotted weight is overstated. Compute the excluded share from `marks` and the position (`qty * marketPrice / net_liq`) and print it above the chart. Use `label_for` in `label_map`.

Comments in the `orders/kts.py` house style throughout; no existing comment deleted.

## Verification

1. `~/miniconda3/envs/venv-stats/python.exe -m py_compile research/check_existing_port.py`.
2. Offline dry-run of the new classification against a synthetic `check` frame + the cached `data/processed/prices_holdings.parquet`: assert `5MVL` → `no price data`, `LNG` → `INCONSISTENT`, `AINF`/`BARC`/`HSBA` → `consistent` after the cell-7b rescale, and that `label_for` marks exactly the suspect set.
3. TWS running: re-run cells 3-11. Expect all three figures to render, each preceded by the banner, with `LNG` and `5MVL` marked `⚠ UNVERIFIED` in the legend and `5MVL` present as a NaN row in cell 10's table.
4. Set `SHOW_SUSPECT_BACKCAST = False`, re-run — cells 9-11 fall silent exactly as today (regression on the old behaviour).
5. `python -m pytest tests/ -q` — nothing here touches `portutils`, so this only guards against an accidental import-time change.

## Progress Log

| # | Step | Status |
|---|------|--------|
| 1 | Header block restated | done — 2026-07-26 |
| 2 | `SHOW_SUSPECT_BACKCAST` flag | done — 2026-07-26 |
| 3 | Cell 8c classification (`sym_verdict` / `label_for` / `banner` / `title_tag`) | done — 2026-07-26 |
| 4 | Cell 9 gate + trusted-only exposure table | done — 2026-07-26 |
| 5 | Cell 10 gate + `verdict` column + keep unpriced rows | done — 2026-07-26 |
| 6 | Cell 11 gate + excluded-weight share | done — 2026-07-26 |
| 7 | Verification 1, 2, 5 | done — 2026-07-26 |
| 8 | Verification 3, 4 (live TWS re-run, flag-off regression) | **pending — needs TWS** |

### Verification results (offline, 2026-07-26)

- Classification on the real cached panel + a `check` frame matching the live run:
  `5MVL → no price data`, `LNG → INCONSISTENT`, `AINF/BARC/HSBA → consistent`.
  `label_for` marks exactly the suspect set (`NO DATA` vs `UNVERIFIED`, nothing on the rest).
- Exposure table arithmetic: trusted 600 vs all 1000 → `difference (unverified) = 400`.
- `per_ticker` keeps `5MVL` as a NaN row carrying `no price data`; role totals skip it via `sum()`.
- Excluded-weight share computes (e.g. 6.9% of NetLiq for a 99-share `5MVL`).
- `py_compile` clean; `pytest tests/ -q` → **52 passed**.
- **Changed from the plan:** the `⚠` glyph is `(!)` in every *printed* string — a Windows
  console on cp1252 raises `UnicodeEncodeError` on it, which the dry-run hit immediately.

## Decisions

- 2026-07-26 — The verdict governs **labelling, not visibility**. Hiding a reconstruction because one symbol failed threw away three that did not; marking each symbol on the chart carries strictly more information than a blank cell.
- 2026-07-26 — `no price data` is kept **distinct** from `INCONSISTENT`. A missing subscription is a data gap, not a falsified assumption, and collapsing the two is what made the original output misleading.
- 2026-07-26 — Suspect symbols are drawn **mixed into** the stacks, not split into a separate figure, with a trusted-only total alongside as a table. The user asked to see the whole decomposition; the table supplies the "how much of this is unverified" answer without doubling the chart count.
