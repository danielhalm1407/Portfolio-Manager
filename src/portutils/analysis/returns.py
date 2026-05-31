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


