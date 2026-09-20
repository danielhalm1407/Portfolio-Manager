# returns.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Literal

import numpy as np
import pandas as pd


ReturnKind = Literal["log", "simple"]


@dataclass(frozen=True)
class ReturnsConfig:
    """
    Configuration for return calculations.

    Attributes
    ----------
    level_contains : str
        Substring used to identify "level" columns to compute returns for.
    level_suffix : str
        Suffix used to convert a level column name to derived return columns.
        Example: spx_level -> spx_period_return, spx_total_return
    period_suffix : str
        Suffix for period returns column.
    total_suffix : str
        Suffix for total returns column.
    return_kind : {"log","simple"}
        log: log(P_t/P_{t-1})
        simple: P_t/P_{t-1} - 1
    inplace : bool
        If True, mutate the input dataframe. If False, work on a copy.
    start_index : int
        Row index from which returns start being defined (first row is NaN).
    """
    level_contains: str = "level"
    level_suffix: str = "_level"
    period_suffix: str = "_period_return"
    total_suffix: str = "_total_return"
    return_kind: ReturnKind = "log"
    inplace: bool = False # control whether to modify input df or return a copy
    start_index: int = 1
    df: Optional[pd.DataFrame] = None


class ReturnsCalculator:
    """
    Calculate period returns and total returns for all "level" columns.

    Usage
    -----
    calc = ReturnsCalculator(ReturnsConfig(return_kind="log"))
    out = calc.transform(df)
    """

    def __init__(self, config: Optional[ReturnsConfig] = None):
        """
        Initialize the ReturnsCalculator class with given configuration.
        """
        self.cfg = config or ReturnsConfig()

    def select_level_cols(self, df: pd.DataFrame, cols: Optional[Sequence[str]] = None) -> List[str]:
        """
        Pick which columns to treat as level series., and outputs them
        """
        if cols is not None:
            missing = [c for c in cols if c not in df.columns]
            if missing:
                # detect if one of the columns in the user-provided list is missing from df
                raise ValueError(f"Requested cols not in df: {missing}")
            return list(cols)
        # if no columns given, automatically select all columns containing the level substring
        # (self.config.level_contains is usually just "level", so this just checks if "level" is in the column name)
        return [c for c in df.columns if self.cfg.level_contains in c]

    def _validate(self, df: pd.DataFrame, level_cols: Sequence[str]) -> None:
        if not isinstance(df, pd.DataFrame):
            raise TypeError("df must be a pandas DataFrame.")
        if len(df) < 2:
            raise ValueError("df must have at least 2 rows to compute returns.")
        if not level_cols:
            raise ValueError("No level columns found (or provided).")

        # Check numeric
        non_numeric = [c for c in level_cols if not pd.api.types.is_numeric_dtype(df[c])]
        if non_numeric:
            raise TypeError(f"These level columns are not numeric: {non_numeric}")

    def _make_return_col(self, level_col: str) -> str:
        """
        Replaces level suffix with period return suffix, or appends if not found.
        Usually, basically changes a column name from xyz_level -> xyz_period_return
        """
        if self.cfg.level_suffix in level_col:
            return level_col.replace(self.cfg.level_suffix, self.cfg.period_suffix)
        return f"{level_col}{self.cfg.period_suffix}"

    def _make_total_return_col(self, level_col: str) -> str:
        """
        Replaces level suffix with total return suffix, or appends if not found.
        Usually, basically changes a column name from xyz_level -> xyz_total_return
        """
        if self.cfg.level_suffix in level_col:
            return level_col.replace(self.cfg.level_suffix, self.cfg.total_suffix)
        return f"{level_col}{self.cfg.total_suffix}"

    def _compute_period_returns(self, values: np.ndarray) -> np.ndarray:
        """
        Return array of same length with first element NaN and the rest returns.
        """
        # Initialize output with NaN for the first row, since returns are undefined
        # for the first observation
        out = np.full(values.shape[0], np.nan, dtype=float)

        # Avoid division by zero / invalid values quietly exploding
        prev = values[:-1]
        curr = values[1:]

        if self.cfg.return_kind == "log":
            out[1:] = np.log(curr / prev)
        else:
            out[1:] = (curr / prev) - 1.0

        return out

    def _compute_total_returns_from_period(self, period: np.ndarray) -> np.ndarray:
        """
        Total return from period returns.

        If log returns: total = exp(cumsum(log_r)) - 1
        If simple returns: total = (1+r).cumprod() - 1
        """
        total = np.full(period.shape[0], np.nan, dtype=float)

        if self.cfg.return_kind == "log":
            total = np.exp(np.nancumsum(period)) - 1.0
        else:
            # For simple returns, treat NaN as 0 for cumprod logic
            r = np.where(np.isnan(period), 0.0, period)
            total = np.cumprod(1.0 + r) - 1.0
            total[0] = np.nan

        return total

    def transform(
        self,
        df: Optional[pd.DataFrame] = None,
        *,
        cols: Optional[Sequence[str]] = None,
    ) -> pd.DataFrame:
        """
        Add period and total return columns for each selected level column.

        If `df` is not provided, this method uses `self.cfg.df`.
        """
        # Default to dataframe stored in config unless caller provides one.
        if df is None:
            if self.cfg.df is None:
                raise ValueError("No DataFrame provided. Pass `df` or set `ReturnsConfig(df=...)`.")
            df = self.cfg.df

        level_cols = self.select_level_cols(df, cols=cols)
        self._validate(df, level_cols)

        out = df if self.cfg.inplace else df.copy()

        for col in level_cols:
            period_col = self._make_return_col(col)
            total_col = self._make_total_return_col(col)

            values = out[col].to_numpy(dtype=float)
            period = self._compute_period_returns(values)
            total = self._compute_total_returns_from_period(period)

            out[period_col] = period
            out[total_col] = total

        return out


# ============================================================================
# WIDE-MATRIX HELPERS — price panel in, derived panel out.
#
# These are the primary definitions of "normalise" / "daily return" / "log return"
# for the repo. They used to live as private methods on ``PanelBuilder`` in
# ``portutils/viz/panel.py``, which meant a plotting module owned the definition of
# what a return is, and anything else that wanted one had to construct a PanelBuilder
# to get at it. They are plain functions here; ``PanelBuilder`` now calls them.
#
# Deliberately NOT merged with ``ReturnsCalculator`` above. That class solves a
# different problem: ONE frame carrying suffix-named columns (``spx_level`` ->
# ``spx_period_return``, ``spx_total_return``), selected by name and written back
# alongside the levels. These take a WIDE T x N matrix (one column per instrument,
# no naming convention) and return a wide T x N matrix. Neither contract subsumes the
# other, so both live in this module rather than one being bent into the other.
#
# Every function is pure: it never mutates the frame handed in.
# ============================================================================


# ============================================================================
# RAGGED-FRAME ANCHOR POLICY — added 2026-09-20 (Phase 11, Task 4, Step 2).
# `normalise` and `pct_returns` both divide by a single anchor row. On a ragged
# frame (series whose listing histories start years apart, e.g. SPY 1993 beside
# a vol series starting 2005) the OLD unconditional `df.iloc[0]` divides a
# late-starting column's row 0 (NaN, since it has no observation yet) through
# NaN, and the ENTIRE column silently becomes NaN — an invisible trace, no
# error raised. `anchor` makes that choice explicit instead of accidental.
# ============================================================================


def _resolve_anchor(df: pd.DataFrame, anchor):
    """Resolve the anchor ROW used to rebase a frame. Shared by `normalise` and
    `pct_returns`; `anchor="self"` is handled separately by the callers because it
    anchors PER COLUMN rather than on one shared row.
    """
    if anchor == "first_row":
        # Unchanged current behaviour — every existing caller keeps this default.
        return df.iloc[0]
    if anchor == "common":
        # The first date where EVERY series has data. A series that starts later
        # is still shown before that date (divided by this same anchor row), which
        # is meaningful information about where it stood relative to the common
        # baseline — not an artefact to be trimmed.
        common = df.dropna()
        if common.empty:
            # `.index[0]` on an empty frame raises a bare IndexError that names no
            # series — useless when the whole point is to find WHICH series never
            # overlap. Name them explicitly instead.
            coverage = ", ".join(
                f"{c}: {df[c].first_valid_index()} to {df[c].last_valid_index()}"
                for c in df.columns
            )
            raise ValueError(
                f"anchor='common' found no date where every series has data "
                f"(no overlapping window across the panel). Per-series coverage: {coverage}"
            )
        return common.iloc[0]
    if isinstance(anchor, pd.Timestamp):
        # An explicit anchor date. Exists because one late-starting series can drag
        # "common" forward and discard the comparability of everything before it —
        # e.g. a vol series starting 2005 beside SPX from 1990 makes "common" 2005.
        return df.loc[anchor]
    raise ValueError(
        f"unknown anchor {anchor!r}; expected 'first_row', 'common', 'self', or a pd.Timestamp"
    )


def _self_anchor_row(df: pd.DataFrame) -> pd.Series:
    # Each column anchored on ITS OWN first valid observation, not a shared row —
    # right for "growth of 100 since inception" per series, but the resulting
    # levels are NOT comparable across series: a series that started in 2020 and
    # one that started in 1993 both read `base` at their own start, which says
    # nothing about where either stood relative to the other on any shared date.
    # Use anchor="common" for a cross-series comparison chart.
    return df.apply(
        lambda s: s.loc[s.first_valid_index()] if s.first_valid_index() is not None else np.nan
    )


def normalise(df: pd.DataFrame, base: float = 100.0, anchor: str = "first_row") -> pd.DataFrame:
    """Wealth index: ``(price / anchor_price) * base``.

    anchor : "first_row" (default, unchanged), "common", "self", or a pd.Timestamp.
        See the RAGGED-FRAME ANCHOR POLICY block above for what each means on a
        ragged (non-overlapping-coverage) frame. Existing callers pass nothing and
        get exactly today's `df.iloc[0]` behaviour.
    """
    anchor_row = _self_anchor_row(df) if anchor == "self" else _resolve_anchor(df, anchor)
    return (df / anchor_row) * base


def pct_returns(df: pd.DataFrame, anchor: str = "first_row") -> pd.DataFrame:
    """Cumulative percentage returns from the anchor observation.

    Same rebasing as `normalise`, expressed as a percentage change from the anchor
    rather than an index level — i.e. ``normalise(df, 100, anchor) - 100``. See
    `normalise` for what `anchor` means.
    """
    anchor_row = _self_anchor_row(df) if anchor == "self" else _resolve_anchor(df, anchor)
    return ((df / anchor_row) - 1) * 100


def daily_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Simple period-over-period returns (first row dropped)."""
    # The first row is dropped, not zero-filled: there is no prior observation, so a
    # return is genuinely undefined there. Callers that multiply weights by these
    # returns therefore need weights aligned to the SHORTENED index.
    # NO anchor parameter, unlike normalise/pct_returns above: pct_change() is PER-SERIES
    # (each row divides by that same column's PREVIOUS row, not a shared anchor row), so it
    # is already correct on a ragged frame — a late-starting column just yields extra leading
    # NaNs, not a silently-NaN'd column. There is no anchor choice to make here.
    return df.pct_change().dropna()


def log_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Log returns, ``log(P_t / P_{t-1})`` (first row dropped)."""
    # Log returns add across time, which is why they are preferred for aggregation and
    # for anything assuming normality; simple returns add across assets instead, which
    # is why `daily_returns` is what attribution consumes.
    return np.log(df / df.shift(1)).dropna()


