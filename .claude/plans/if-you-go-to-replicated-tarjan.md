# Cell-label banners in `check_existing_port.py` (+ Stage 2 run)

## Context

User is at **Stage 2** of
[.claude/plans/with-within-rebalance-realization-parallel-milner.md](../../.claude/plans/with-within-rebalance-realization-parallel-milner.md)
— proving the IBKR read path — and wants to run
[research/check_existing_port.py](../../research/check_existing_port.py)
cell by cell in the VS Code interactive window.

Problem: `# %% N. Title` headers are visible in the **editor** only. Once cells run, the
interactive window output is an undifferentiated stream of tables and prints with no marker
saying which cell produced what. Fix: emit the cell label as runtime output too.

Per the parent plan's decision, **the user runs every TWS-dependent command**; Claude only
edits the script and interprets output.

## Change

One file: `research/check_existing_port.py`. Nothing else.

Under **every labelled `# %%` line, on the literally next line**, insert a banner print of that
cell's exact label text:

```python
# %% 4. Mirror the account in our accounting engine
print("=== 4. Mirror the account in our accounting engine " + "=" * 20)
#
# book_from_portfolio seeds a Book straight from IBKR's position + averageCost. Note the
# realised figure is deliberately NOT seeded from IB: theirs is session-scoped, ours is
```

Rules:

- **Label text copied verbatim** from the `# %%` header — no rewording, no renumbering.
- **ASCII `=` rule, not box-drawing characters.** The file is also runnable as a plain
  `python research/check_existing_port.py`, and a Windows console at cp1252 raises
  `UnicodeEncodeError` on `─`. The existing `print("=" * 70)` at
  [line 192](../../research/check_existing_port.py#L192)
  already establishes `=` as this file's separator.
- Banner goes **at module level**, above any `if` — so cells 9/10/11, which are wholly wrapped
  in `if bc is not None and ...`, still announce themselves when the guard is false.
- **Comment blocks are not touched, moved, or shortened.** The insert pushes them down one
  line; every existing comment survives verbatim (project comment-retention rule).

### Cells to cover (13)

`1. Import libraries` · `Reload custom packages` · `2. Settings` · `3. Connect and pull the
account` · `4. Mirror the account in our accounting engine` · `5. Roles and current weights` ·
`6. The real executions window (exact data)` · `7. Price history for the held symbols` ·
`8. Policy-anchored backcast — THE FALSIFICATION CHECK COMES FIRST` · `9. Reconstructed P&L
path` · `10. Realised P&L by ticker and by role` · `11. Weight path vs target` · `12. Disconnect`

The **trailing bare `# %%`** at
[line 304](../../research/check_existing_port.py#L304)
has no label — skipped, left as is.

Cell 8's label contains an em-dash (`—`). Same cp1252 hazard as above, so that one banner uses
`--` in the printed string while the `# %%` header keeps its em-dash unchanged.

## Verification

1. `python -c "import ast,pathlib; ast.parse(pathlib.Path('research/check_existing_port.py').read_text(encoding='utf-8'))"`
   — parses.
2. Count check: 13 `print("===` lines, one per labelled `# %%`.
3. Diff review: every insertion is a pure `+` line; **zero `-` lines** in the diff. Any deleted
   line means a comment was clobbered.

## Then — Stage 2 (user runs, TWS on `127.0.0.1:7497`, paper `DUP102412`)

**2a.** `python src/pipelines/log_positions.py --account DUP102412 --client-id 131`
→ `data/raw/ibkr/positions_<today>.parquet` written; holdings match TWS on screen.

**2b.** `research/check_existing_port.py` cell by cell in the interactive window.
Settings already correct at §2 (`CLIENT_ID = 141`, `EXEC_DAYS_BACK = 7`,
`BACKCAST_POLICY = backcast.BUY_AND_HOLD`) — no edit needed. Read in this spirit:

- §4 `reconciliation vs IBKR` must print **ALL MATCH**. `*** DRIFT ***` invalidates everything
  below it — stop there rather than reading on.
- §6 empty executions means "no fills in the ~7 days TWS serves", **never** "no trades ever".
  Raising `EXEC_DAYS_BACK` does not defeat that ceiling.
- §8 prints its verdict **before** its numbers, deliberately. `INCONSISTENT` means the backcast
  policy is wrong for this account, not that the code is broken.

Paste output back; Claude interprets and updates the Stage 2 rows of the parent plan's
Progress Log.

## Progress Log

| Step | Status | Notes |
|------|--------|-------|
| Insert 13 cell-label banners | pending | |
| Parse + diff verification | pending | zero `-` lines required |
| 2a. `log_positions.py` | pending | user runs, needs TWS |
| 2b. `check_existing_port.py` cells | pending | user runs, needs TWS |

## Decisions

- **Banner on the literally next line after `# %%`**, splitting the header from its comment
  block — user's explicit choice over the tidier after-the-block placement.
- **`=` rule rather than `─`**, so the file survives a plain `python` run on a cp1252 Windows
  console; matches the file's existing `"=" * 70` separator.
- **Banners are unconditional**, outside the `if bc is not None` guards, so a skipped cell still
  announces itself instead of silently producing nothing.
