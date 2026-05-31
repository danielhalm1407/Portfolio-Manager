# Imports

# standard library imports
from itertools import product
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.io import loadmat


class QuantileForecastsMerger:
    """
    Load S&P 500 returns, clean them, load BJQTS forecast quantiles from MAT,
    and merge both datasets for backtesting.

    Typical usage in a notebook can stay concise:

    merger = QuantileForecastsMerger()
    merged_returns = merger.run()

    After running, intermediate outputs are also available on the instance:
    - merger.raw_returns
    - merger.returns
    - merger.quantile_forecasts_df
    - merger.merged_returns
    """

    def __init__(
        self,
        label_format: str = "%y-%m-%d",
        returns: Optional[pd.DataFrame] = None,
        returns_path: str | Path = "data/intermediate_input/sp500_interday_returns.xlsx",
        mat_path: str | Path = "data/intermediate_input/q_minus_all_BJSAV_5_norm_SP500.mat",
        mat_key: str = "q_minus_all_select",
        date_format: str = "%d-%b-%Y",
        drop_zero: bool = True,
        burn_in: int = 2520,
        label: bool = False,
    ) -> None:
        self.label_format = label_format
        self.raw_returns: Optional[pd.DataFrame] = None
        self.returns = returns
        self.returns_path = returns_path
        self.mat_path = mat_path
        self.mat_key = mat_key
        self.date_format = date_format
        self.drop_zero = drop_zero
        self.burn_in = burn_in
        self.label = label
        self.quantile_forecasts_df: Optional[pd.DataFrame] = None
        self.merged_returns: Optional[pd.DataFrame] = None

    def _resolve_path(self, path_value: str | Path, *, path_label: str) -> Path:
        """
        Resolve a file path robustly across notebook/module execution contexts.

        Resolution order:
        1) Absolute path as provided.
        2) Relative to current working directory.
        3) Relative to project root (derived from this file location).
        """
        p = Path(path_value)
        if p.is_absolute():
            if not p.exists():
                raise FileNotFoundError(f"{path_label} not found: {p}")
            return p

        cwd_candidate = Path.cwd() / p
        if cwd_candidate.exists():
            return cwd_candidate

        project_root = Path(__file__).resolve().parents[2]
        root_candidate = project_root / p
        if root_candidate.exists():
            return root_candidate

        raise FileNotFoundError(
            f"Could not resolve {path_label}. "
            f"Tried: {cwd_candidate} and {root_candidate}"
        )

    def load_returns_from_file(
        self,
        returns_path: Optional[str | Path] = None,
    ) -> pd.DataFrame:
        """
        Load raw returns data from CSV or Excel and store it on the instance.
        """
        resolved_path = self._resolve_path(
            returns_path or self.returns_path,
            path_label="returns file",
        )

        suffix = resolved_path.suffix.lower()
        if suffix == ".csv":
            raw_returns = pd.read_csv(resolved_path)
        elif suffix in {".xls", ".xlsx"}:
            raw_returns = pd.read_excel(resolved_path)
        else:
            raise ValueError(
                f"Unsupported returns file type '{suffix}'. Use CSV or Excel."
            )

        self.raw_returns = raw_returns
        return raw_returns

    def clean_returns(
        self,
        returns: Optional[pd.DataFrame] = None,
    ) -> pd.DataFrame:
        """
        Clean a returns dataframe and store the cleaned result on the instance.
        """
        source_returns = returns
        if source_returns is None:
            source_returns = self.raw_returns if self.raw_returns is not None else self.returns

        if source_returns is None:
            raise ValueError(
                "No returns DataFrame available. Load one from file or pass `returns`."
            )

        cleaned_returns = source_returns.copy()
        cleaned_returns.columns = ["time", "return"]
        cleaned_returns["time"] = pd.to_datetime(
            cleaned_returns["time"],
            format=self.date_format,
            errors="coerce",
        )

        if self.drop_zero:
            cleaned_returns = cleaned_returns[cleaned_returns["return"] != 0]

        cleaned_returns = cleaned_returns[cleaned_returns["return"].notna()]

        if self.burn_in:
            cleaned_returns = cleaned_returns.iloc[self.burn_in :].reset_index(drop=True)

        if self.label:
            cleaned_returns["time_label"] = cleaned_returns["time"].dt.strftime(
                self.label_format
            )

        self.returns = cleaned_returns
        return cleaned_returns

    def load_quantile_forecasts_from_mat(
        self,
        mat_path: Optional[str | Path] = None,
        mat_key: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Load quantile forecasts from a MATLAB .mat file and return a DataFrame.
        """
        resolved_path = self._resolve_path(
            mat_path or self.mat_path,
            path_label="MAT file",
        )
        data = loadmat(resolved_path)

        resolved_key = mat_key or self.mat_key

        if resolved_key not in data:
            available = sorted(k for k in data.keys() if not k.startswith("__"))
            raise KeyError(
                f"Key '{resolved_key}' not found in MAT file. "
                f"Available keys: {available}"
            )

        quantile_forecasts = np.asarray(data[resolved_key])
        if quantile_forecasts.ndim != 2:
            raise ValueError(
                f"Expected a 2D array for '{resolved_key}', got shape {quantile_forecasts.shape}."
            )

        self.quantile_forecasts_df = pd.DataFrame(
            quantile_forecasts,
            columns=[f"q_{i + 1}" for i in range(quantile_forecasts.shape[1])],
        )
        return self.quantile_forecasts_df

    def merge_returns_with_quantiles(
        self,
        returns: Optional[pd.DataFrame] = None,
        quantile_forecasts_df: Optional[pd.DataFrame] = None,
        *,
        mat_path: Optional[str | Path] = None,
        mat_key: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Merge returns with quantile forecasts on index.

        If quantile_forecasts_df is not provided, forecasts are loaded from MAT.
        """
        resolved_returns = returns if returns is not None else self.returns
        if resolved_returns is None:
            raise ValueError(
                "No returns DataFrame provided. Pass `returns` or set it on QuantileForecastsMerger(...)"
            )

        if quantile_forecasts_df is None:
            quantile_forecasts_df = self.load_quantile_forecasts_from_mat(
                mat_path=mat_path,
                mat_key=mat_key,
            )

        self.merged_returns = resolved_returns.merge(
            quantile_forecasts_df,
            left_index=True,
            right_index=True,
        )
        return self.merged_returns

    def run(
        self,
        returns: Optional[pd.DataFrame] = None,
        quantile_forecasts_df: Optional[pd.DataFrame] = None,
        *,
        clean_loaded_returns: bool = True,
    ) -> pd.DataFrame:
        """
        End-to-end pipeline for backtest inputs.

        If `returns` is supplied, it is treated as already prepared returns data.
        Otherwise returns are loaded from `returns_path`, optionally cleaned, and
        then merged with the quantile forecasts loaded from `mat_path`.
        """
        if returns is not None:
            self.returns = returns
        elif self.returns is None:
            loaded_returns = self.load_returns_from_file()
            if clean_loaded_returns:
                self.clean_returns(loaded_returns)
            else:
                self.returns = loaded_returns.copy()

        return self.merge_returns_with_quantiles(
            returns=self.returns,
            quantile_forecasts_df=quantile_forecasts_df,
        )


class IntradayIndexLevelsCleaner:
    """
    Load and clean intraday S&P index levels into a single tidy dataframe.

    The default pipeline mirrors the notebook/script workflow:
    1) Read the raw Excel file.
    2) Promote row 6 to header and drop metadata rows.
    3) Standardize column names.
    4) Parse the effective date column to datetime and drop invalid rows.
    5) Keep only the date and S&P columns, renaming levels with a _level suffix.
    6) Coerce level columns to numeric.
    """

    def __init__(
        self,
        input_path: str | Path = "data/input/intraday_indices_levels.xlsx",
        header_row_idx: int = 5,
        sheet_name: str | int | None = 0,
        use_existing_header: bool = False,
        date_column: str = "effective_date_",
        sp_column_token: str = "sp",
    ) -> None:
        self.input_path = input_path
        self.header_row_idx = header_row_idx
        self.sheet_name = sheet_name
        self.use_existing_header = use_existing_header
        self.date_column = date_column
        self.sp_column_token = sp_column_token

        self.raw_intraday_index_levels: Optional[pd.DataFrame] = None
        self.cleaned_intraday_index_levels: Optional[pd.DataFrame] = None
        self.merged_intraday_index_levels: Optional[pd.DataFrame] = None

    def _resolve_path(self, path_value: str | Path, *, path_label: str) -> Path:
        """
        Resolve a path robustly across notebook/module execution contexts.
        """
        p = Path(path_value)
        if p.is_absolute():
            if not p.exists():
                raise FileNotFoundError(f"{path_label} not found: {p}")
            return p

        cwd_candidate = Path.cwd() / p
        if cwd_candidate.exists():
            return cwd_candidate

        project_root = Path(__file__).resolve().parents[2]
        root_candidate = project_root / p
        if root_candidate.exists():
            return root_candidate

        raise FileNotFoundError(
            f"Could not resolve {path_label}. "
            f"Tried: {cwd_candidate} and {root_candidate}"
        )

    def load(
        self,
        input_path: Optional[str | Path] = None,
        sheet_name: Optional[str | int] = None,
    ) -> pd.DataFrame:
        """
        Load the raw intraday index level file from Excel.
        """
        resolved_path = self._resolve_path(
            input_path or self.input_path,
            path_label="intraday index levels file",
        )
        resolved_sheet = self.sheet_name if sheet_name is None else sheet_name
        self.raw_intraday_index_levels = pd.read_excel(
            resolved_path,
            sheet_name=resolved_sheet,
        )
        return self.raw_intraday_index_levels

    def clean(self, intraday_index_levels: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Apply the full cleaning pipeline and return tidy index levels.
        """
        source_df = intraday_index_levels
        if source_df is None:
            source_df = self.raw_intraday_index_levels

        if source_df is None:
            raise ValueError(
                "No intraday index levels dataframe available. "
                "Load from file or pass `intraday_index_levels`."
            )

        out = source_df.copy()

        if not self.use_existing_header:
            if self.header_row_idx >= len(out):
                raise ValueError(
                    f"header_row_idx={self.header_row_idx} is out of bounds for dataframe with {len(out)} rows."
                )

            # Promote the configured row to header and remove pre-header metadata rows.
            out.columns = out.iloc[self.header_row_idx]
            out = out.drop(index=list(range(self.header_row_idx + 1))).reset_index(drop=True)

        out.columns = [str(col).lower().replace(" ", "_") for col in out.columns]
        out.columns = [col.replace("&", "") for col in out.columns]
        out.columns = [col.replace("(", "") for col in out.columns]
        out.columns = [col.replace(")", "") for col in out.columns]

        if self.date_column not in out.columns:
            raise KeyError(
                f"Date column '{self.date_column}' not found after normalization. "
                f"Available columns: {list(out.columns)}"
            )

        out[self.date_column] = pd.to_datetime(
            out[self.date_column],
            errors="coerce",
        )
        out = out.dropna(subset=[self.date_column])

        sp_columns = [col for col in out.columns if self.sp_column_token in col]

        renamed_columns = {self.date_column: "time"}
        renamed_columns.update({col: f"{col}_level" for col in sp_columns})
        out = out[[self.date_column] + sp_columns].rename(columns=renamed_columns)

        level_columns = [col for col in out.columns if "level" in col]
        for col in level_columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        self.cleaned_intraday_index_levels = out
        return out

    def merge_with_existing(
        self,
        existing_intraday_index_levels: pd.DataFrame,
        new_intraday_index_levels: Optional[pd.DataFrame] = None,
        *,
        time_col: str = "time",
        stitch_using_returns: bool = True,
        column_pairs: Optional[list[tuple[str, str]]] = None,
    ) -> pd.DataFrame:
        """
        Merge cleaned intraday levels with an existing dataframe.

        Rules:
        - Keep existing values when present.
        - Fill missing existing values from new data where available.
        - Include any columns that only exist in new data.
        - Include union of dates from both sources.
        - If stitch_using_returns=True (default), shared numeric level columns are
          filled using return-based stitching so base-level differences do not
          create jumps at transition points.

        Parameters
        ----------
        column_pairs : list of (existing_col, new_col) tuples, optional
            Explicit column pairings for columns whose names differ between the two
            DataFrames.  Each tuple ``(existing_col, new_col)`` means: fill gaps in
            ``existing_col`` using return-based stitching from ``new_col``.  The
            output column keeps the ``existing_col`` name; ``new_col`` is NOT
            included as a separate column in the result.

            Example — CRSP price-return index merged with LSEG total-return index::

                column_pairs=[("s&p_500_index_level", "spx_level")]

            This works even though the two series are on completely different scales
            (~6k vs ~14k) because stitching is return-ratio based, not level based.
        """
        if existing_intraday_index_levels is None:
            raise ValueError("existing_intraday_index_levels must be provided")

        incoming = new_intraday_index_levels
        if incoming is None:
            incoming = self.cleaned_intraday_index_levels

        if incoming is None:
            raise ValueError(
                "No cleaned new data available. Run clean()/run() first or pass new_intraday_index_levels."
            )

        existing = existing_intraday_index_levels.copy()
        new = incoming.copy()

        if time_col not in existing.columns:
            raise KeyError(f"'{time_col}' not found in existing_intraday_index_levels")
        if time_col not in new.columns:
            raise KeyError(f"'{time_col}' not found in new_intraday_index_levels")

        existing[time_col] = pd.to_datetime(existing[time_col], errors="coerce")
        new[time_col] = pd.to_datetime(new[time_col], errors="coerce")

        existing = existing.dropna(subset=[time_col]).set_index(time_col).sort_index()
        new = new.dropna(subset=[time_col]).set_index(time_col).sort_index()

        union_index = existing.index.union(new.index).sort_values()

        # Build a dict {existing_col: new_col} from the explicit pairings.
        # These new_col names are "claimed" and excluded from the new-only pass.
        paired_map: dict[str, str] = {}
        claimed_new_cols: set[str] = set()
        if column_pairs:
            for e_name, n_name in column_pairs:
                if e_name not in existing.columns:
                    raise KeyError(
                        f"column_pairs: '{e_name}' not found in existing DataFrame. "
                        f"Available: {list(existing.columns)}"
                    )
                if n_name not in new.columns:
                    raise KeyError(
                        f"column_pairs: '{n_name}' not found in new DataFrame. "
                        f"Available: {list(new.columns)}"
                    )
                paired_map[e_name] = n_name
                claimed_new_cols.add(n_name)

        # Start with existing columns order, then append any new-only columns
        # (excluding those claimed by column_pairs).
        merged_cols = list(existing.columns) + [
            c for c in new.columns if c not in existing.columns and c not in claimed_new_cols
        ]
        merged = pd.DataFrame(index=union_index)

        for col in merged_cols:
            e_series = existing[col].reindex(union_index) if col in existing.columns else pd.Series(index=union_index, dtype=float)

            # Resolve the new-side series: use the paired column if specified,
            # otherwise fall back to the same-name column in the new DataFrame.
            new_col_name = paired_map.get(col, col)
            n_series = new[new_col_name].reindex(union_index) if new_col_name in new.columns else pd.Series(index=union_index, dtype=float)

            is_level_col = col.endswith("_level") or new_col_name.endswith("_level")
            if (
                stitch_using_returns
                and pd.api.types.is_numeric_dtype(e_series)
                and pd.api.types.is_numeric_dtype(n_series)
                and is_level_col
                and (col in existing.columns or col in paired_map)
                and (new_col_name in new.columns)
            ):
                merged[col] = self._stitch_level_series(e_series, n_series)
            else:
                merged[col] = e_series.combine_first(n_series)

        merged = merged.reset_index()
        self.merged_intraday_index_levels = merged
        return merged

    @staticmethod
    def _stitch_level_series(existing_col: pd.Series, new_col: pd.Series) -> pd.Series:
        """
        Fill missing values in an existing level series using a new level series,
        preserving continuity by applying new-series returns relative to overlap anchors.
        """
        e = pd.to_numeric(existing_col, errors="coerce")
        n = pd.to_numeric(new_col, errors="coerce")
        out = e.copy()

        if e.notna().sum() == 0:
            return n
        if n.notna().sum() == 0:
            return e

        overlap = e.notna() & n.notna()
        if not overlap.any():
            # No common anchor date available; fall back to simple gap fill.
            return out.combine_first(n)

        # the .where method keeps values where the condition is True and where false, 
        # replaces with NaN (but keeps the same length)
        e_anchor = e.where(overlap) 
        n_anchor = n.where(overlap).replace(0, np.nan)

        # Project from nearest overlap anchors in both directions.
        # ffill takes the last valid observation and fills forward until the next valid observation, 
        # bfill does the opposite
        prev_e = e_anchor.ffill()
        prev_n = n_anchor.ffill()
        next_e = e_anchor.bfill()
        next_n = n_anchor.bfill()

        missing_existing = out.isna() & n.notna()
        # The projection moves with the new series returns, anchored to the existing level scale.
        # As n becomes increasingly above prev_n, so does our projection of e
        projected_from_prev = prev_e * (n / prev_n)
        projected_from_next = next_e * (n / next_n)

        out.loc[missing_existing] = projected_from_prev.loc[missing_existing]

        still_missing = out.isna() & n.notna()
        out.loc[still_missing] = projected_from_next.loc[still_missing]

        # Final fallback in edge cases where anchor projection is unavailable.
        return out.combine_first(n)

    def run(
        self,
        intraday_index_levels: Optional[pd.DataFrame] = None,
        *,
        input_path: Optional[str | Path] = None,
        sheet_name: Optional[str | int] = None,
    ) -> pd.DataFrame:
        """
        Execute load (if needed) + clean in one call.
        """
        if intraday_index_levels is not None:
            self.raw_intraday_index_levels = intraday_index_levels.copy()
        elif self.raw_intraday_index_levels is None:
            self.load(input_path=input_path, sheet_name=sheet_name)

        return self.clean(self.raw_intraday_index_levels)

    

class QuantileRiskControlStrategy:
    # add a mapping of quantile numbers to quantile levels
    # 8 to 1 map to 0.0556, 0.1111, 0.1667, 0.2222, 0.2778, 0.3333, 0.3889, 0.4444 respectively
    quantile_level_map = {
        1: 0.4444,
        2: 0.3889,
        3: 0.3333,
        4: 0.2778,
        5: 0.2222,
        6: 0.1667,
        7: 0.1111,
        8: 0.0556,
    }

    # Set maximum drawdown/quantile target
    def __init__(
        self,
        r: float = 0.0063,
        spec: int = 2,
        q_level: int = 8,
        model_trades: bool = True,
        underlying: str = "s&p 500",
        replication_instrument: str = "futures",
        contract_multiplier: float = 50, # for ES futures, each contract is worth 50 times the index level
        initial_margin_pct: float = 0.05, # assume a 5% initial margin requirement for futures
        cash_buffer_pct: float = 0.1, # keep a 10% cash buffer to avoid margin calls and allow for slippage
        starting_capital: float = 1_000_000,
        Min_turnover: float = 0.02,
        slippage_k: float = 0.1,
        level_col: Optional[str] = None,
        underlying_return_col: Optional[str] = None,
    ) -> None:
        self.r = r
        self.spec = spec
        self.q_level = q_level
        self.model_trades = model_trades # allows the option to track units in index and trades, but requires price and not just returns data
        self.underlying = underlying # the name of the underlying index/asset we are trying to replicate
        self.starting_capital = starting_capital
        self.replication_instrument = replication_instrument
        self.contract_multiplier = contract_multiplier
        self.initial_margin_pct = initial_margin_pct
        self.cash_buffer_pct = cash_buffer_pct
        self.Min_turnover = Min_turnover
        self.slippage_k = slippage_k # calibraton constant for slippage costs as a function of order size

        # Explicit column-name overrides; when None, auto-construct from underlying/replication_instrument.
        # Spaces in 'underlying' are replaced with underscores to match typical DataFrame column conventions.
        _base = underlying.replace(" ", "_")
        self.level_col = level_col if level_col is not None else f"{_base}_{replication_instrument}_level"
        self.underlying_return_col = underlying_return_col if underlying_return_col is not None else f"{_base}_return"
        self.period_volume = 50000 # assume a period volume of 50k units for the replication instrument     
                                    # based upon the typical lower bound of morning hours trading, which will be primary execution window

    # Note that the target probability level will be given by the quantile forecast that we compare this to, in this case q_8

    # define a function calc_targ_weight that calculates the target weight in the market index based on the quantile forecast
    # this takes as input a the forecast quantile we are comparing and the maximum drawdown/quantile target
    def calc_targ_weight(self, q_forecast: float) -> float:
        """
        Calculate the target weight in the market index based on the quantile forecast.

        Args:
            q_forecast (float): The forecasted quantile value.
            r (float): The maximum drawdown/quantile target.
        Returns:
            float: The target weight in the market index.
        """
        if self.spec == 1:
            # For the first specification, the weight is set to 1 when the quantile forecast is above the target
            if q_forecast >= -self.r:
                w_targ = 1
            else:
                # If the quantile forecast is below the target, we set the weight to 0
                w_targ = 0
            return w_targ

        # For the second specification, the weight changes linearly with the quantile forecast
        if q_forecast == 0:
            return 1

        w_targ = max(min(1, (-self.r / q_forecast)), 0)
        return w_targ

    def calc_bounded_weights(
            self,
            target_weights: pd.Series,
        ) -> pd.Series:
        """
        Calculate the number of units to hold based on the target weights and current price.

        Args:
            q_forecast (float): The forecasted quantile value.
            twap (float): The time-weighted average price for the period.
        """

        # Create a delayed weight series that is the target weight column shifted by one row
        # this is to reflect a 1-day trading delay
        delayed_weights = target_weights.shift(1)

        # Only convert these then to bounded weights if the weight change/turnover exceeds 2pp
        turnover = (delayed_weights - delayed_weights.shift(1)).abs()
        bounded_weights = delayed_weights.where(turnover > 0.02, other=delayed_weights.shift(1))
        
        return bounded_weights


    def calc_units(
            self,
            prev_units: float,
            bounded_weight: float,
            ref_price: float,
            capital_level: float,
        ):
        """
        For a given point in time, calculate the number of units to hold based on 
        the capital_level, target weights and current price.

        Args:
            prev_units (float): The number of units held in the previous period.
            bounded_weight (float): The bounded weight for the current period.
            ref_price (float): The reference price for the replication instrument.
            capital_level (float): The total capital available for investment.
        """

        # calculate the number of notional units to hold in the replication instrument
        notional_units = bounded_weight * (capital_level / ref_price)

        # calculate the actual number of units to hold
        # divide the notional units by the contract multiplier to get the number of contracts to hold 
        # even if futures are used as a replicaton instrument
        units = notional_units / self.contract_multiplier

        # calculate the change in units from the previous period, and thus the implied slippage costs
        # when further accounting for notional
        unit_change = units - prev_units

        return units, unit_change
    def calc_slippage(
        self,
        unit_change: float,
        n_blocks: int,
        twap: float,
        period_volume: float,
        period_vol: float = 0.14, # assume a daily volatility of 14% for the replication instrument, which is in line with the historical volatility of the S&P 500
    ) -> pd.Series:
        """
        Calculate slippage costs based on the change in units held.

        Args:
            unit_change (float): The change in the number of units held from the previous period.
            n_blocks (int): The number of blocks/child orders the total order is split into,
                used to model the impact of order splitting on slippage
                (higher order size per unit time = higher bps of slippage).
            twap (float): The time-weighted average price for the period,
                used to convert unit changes to notional values if needed.
            period_vol (float): The volatility of the replication instrument during the period.
                Default is 0.14 to correspond with the daily volatility of the S&P 500,
                used to model the impact of market conditions on slippage
                (higher volatility = higher bps of slippage).
            period_volume (float): The volume of the replication instrument during the period,
                used to model the impact of market conditions on slippage
                (higher volume = lower bps of slippage).
        Returns:
            pd.Series: The slippage cost for each period.
        """
        slippage_bps = period_vol * self.slippage_k * np.sqrt((unit_change.abs() / period_volume) * n_blocks)
        slippage_prop = slippage_bps/(100**2)
        slippage_cost = (slippage_prop) * unit_change.abs() * twap

        return slippage_prop, slippage_cost


    def calc_funding_cost(
            self,
            capital_value: float,
            units: float,
            exec_price_prev: float,
            exec_price_curr: float,
            underlying_ret: float = 0.0, 
            daily_funding_rate: float = 0.03,
        ) -> float:
        """
        Calculate funding costs based on the number of units held.
        Targetted specifically at futures replication

        Args:
            capital_value (float): The total capital available for investment.
            units (float): The number of units held during the period.
            exec_price_prev (float): The execution price for the previous period.
            exec_price_curr (float): The execution price for the current period.
            daily_funding_rate (float): The daily funding rate.
        Returns:
            pd.Series: The funding cost for each period.
        """
        
        
        # calculate total margin requirement based on the notional exposure and a margin rate (assume 5% margin requirement for futures)
        total_margin = notional_value * self.initial_margin_pct
        # set asside a cash buffer
        cash_buffer = capital_value * self.cash_buffer_pct
        # whatever is left, we can invest in short term lending instruments
        surplus_cash = max(capital_value - total_margin - cash_buffer, 0)

        # find the gross funding benefit that can be achieved from holding this cash
        interest_earned = surplus_cash * (daily_funding_rate / 252) # assume 252 trading days in a year

        # find the interest paid on the margin requirement, which is a cost that reduces the net funding benefit.
        interest_paid = total_margin * (daily_funding_rate / 252)   

        # find the net funding benefit after accounting for the interest paid on the margin requirement
        net_interest_earned = interest_earned - interest_paid

        # find the funding cost from this period to the next implied by the difference in the return of 
        # the underlying and the return in the instrument. for futures, will be hampered by the extent of
            # the decay in the net funding benefit priced into the futures price
            # or, upon contract expiry, the cost of rolling the futures forward,
            # which accounts for the loss we make in 
                # selling the current front month (whose futures price would have largely 
                # converged to that of the underlying)
                # buying the next front month (whose futures price will add the net funding 
                # benefit as a premium to the spot)
        instrument_ret = (exec_price_curr - exec_price_prev) / exec_price_prev
        ret_diff = instrument_ret - underlying_ret

        funding_cost = notional_value * ret_diff

        net_funding_cost = funding_cost - net_interest_earned
    
        return [total_margin, cash_buffer, surplus_cash, net_interest_earned, funding_cost, net_funding_cost]

    # A function that calculates the target weights and actual weights for each period
    # based on the quantile forecasts and the maximum drawdown/quantile target
    def calc_strat_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate target weights and actual weights based on quantile forecasts.

        Args:
            df (pd.DataFrame): DataFrame containing quantile forecasts and returns.

        Returns:
            pd.DataFrame: DataFrame with added target and actual weights.
        """
        out = df.copy()  # Prevent modifying the original DataFrame
        # Calculate target weights based on the quantile forecast for q_8
        out["target_weight"] = out[f"q_{self.q_level}"].apply(self.calc_targ_weight)

        if self.model_trades:
            # if user wants to model trades, calculate actual weights and returns one period at a time
            for i in range(len(out)):
                if i == 0:
                    prev_units = 0
                    capital_level = self.starting_capital
                else:
                    prev_units = out.loc[i-1, "units"]
                    capital_level = out.loc[i-1, "capital_level"]

                # calculate the bounded weight
                bounded_weight = self.calc_bounded_weights(out["target_weight"].iloc[:i+1]).iloc[-1]

                ref_price = out[self.level_col].iloc[i]

                # calculate the number of units needed to achieve the bounded weight, and the change in units from the previous period
                units, unit_change = self.calc_units(
                    prev_units=prev_units,
                    bounded_weight=bounded_weight,
                    ref_price=ref_price,
                    capital_level=capital_level,
                )
                out.loc[i, "units"] = units                
                out.loc[i, "unit_change"] = unit_change

                # taking these units, unit change and reference price, find the slippage costs
                slippage_prop, slippage_cost = self.calc_slippage(
                    unit_change=unit_change,
                    n_blocks=5, # assume orders are split into 5 child orders
                    twap=ref_price,
                    period_volume=self.period_volume,
                )

                #calculate the adjusted execution price accounting for slippage
                exec_price_curr = ref_price + np.sign(unit_change) * slippage_prop * ref_price
                out.loc[i, "exec_price"] = exec_price_curr
                exec_price_prev = out.loc[i-1, "exec_price"] if i > 0 else exec_price_curr

                # calculate notional exposure based on last notional value and change based upon the price returns at execution

                if i == 0:
                    notional_value = units * self.contract_multiplier * exec_price_curr
                else:
                    price_return_amount = prev_units * (exec_price_curr - exec_price_prev)
                    notional_value = out.loc[i-1, "notional_value"] + price_return_amount    

                # calculate actual weight based on notional value as a proportion of capital
                out.loc[i, "notional_value"] = notional_value
                out.loc[i, "actual_weight"] = notional_value / capital_level

                # calculate the price return of the replication instrument from the previous period 
                # to the current period based on the adjusted execution price
                price_return = price_return_amount / capital_level - 1 if i > 0 else 0

                # if we are working with futures, calculate the relevant additional values:
                #   margin, buffer, surplus cash, net interest earned (funding benefit)
                #   funding cost from the return difference between the underlying and the replication instrument,
                #   and net funding cost after accounting for the funding benefit
                if self.replication_instrument == "futures":

                    funding_info = self.calc_funding_cost(
                        capital_value=capital_level,
                        units=units,
                        exec_price_prev=exec_price_prev,
                        exec_price_curr=exec_price_curr,
                        underlying_ret=out[self.underlying_return_col].iloc[i], # assume the return of the underlying is the return of the S&P 500 index
                    )

                    # add entries into columns for each of these values
                    out.loc[i, "total_margin"] = funding_info[0]
                    out.loc[i, "cash_buffer"] = funding_info[1]
                    out.loc[i, "surplus_cash"] = funding_info[2]
                    out.loc[i, "net_interest_earned"] = funding_info[3]
                    out.loc[i, "funding_cost"] = funding_info[4]
                    out.loc[i, "net_funding_cost"] = funding_info[5]

                    # calculate the additional cash return as a proportion of the previous period's capital level
                    cash_return = funding_info[3] / capital_level

                    # calculate the total return for the period as the sum of the price return and the cash return from funding
                    total_return = price_return + cash_return         

                    # add the price return amount and the cash return amount to the capital level to get the new
                    #  capital level for the next period           
                    out.loc[i, "capital_level"] = capital_level + price_return_amount + funding_info[3]

                    # alternately, could just apply the total return to the capital level to get the new
                    # capital level for the next period
                    # out.loc[i, "capital_level"] = capital_level * (1 + total_return)
                    

        else:
            # Create an actual_weight column that is the target weight column shifted by one row
            # this is to reflect a 1-day trading delay
            out["actual_weight"] = out["target_weight"].shift(1)
            # Now calculate the actual, risk control returns based on the actual weight
            return_col = [col for col in out.columns if "return" in col and "risk_control_return" not in col]
            out["risk_control_return"] = out[return_col[0]] * out["actual_weight"]
            out["capital_level"] = self.starting_capital * (1 + out["risk_control_return"].fillna(0)).cumprod()

        


        return out

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.calc_strat_returns(df)


class QuantileRiskControlGrid:
    """
    Run a grid of risk-control strategies and append each strategy's results
    as additional columns to the input dataframe.
    """

    def __init__(
        self,
        r_list: list[float],
        q_level_list: list[int],
        spec_list: list[int],
        model_trades_list: Optional[list[bool]] = None,
        level_col: Optional[str] = None,
        underlying_return_col: Optional[str] = None,
    ) -> None:
        self.r_list = r_list
        self.q_level_list = q_level_list
        self.spec_list = spec_list
        # Default to [True] to preserve prior behaviour when caller omits it.
        self.model_trades_list = [True] if model_trades_list is None else model_trades_list
        self.level_col = level_col
        self.underlying_return_col = underlying_return_col

    def _strategy_suffix(self, r: float, q_level: int, spec: int, model_trades: bool) -> str:
        # format r in terms as a percentage
        r_label = f"{r*100:.2f}%"
        suffix = f"q_{q_level}_target_at_{r_label}_spec_{spec}"
        # Only append model_trades tag when grid spans both True and False, to
        # avoid renaming columns in the common single-value case.
        if len(self.model_trades_list) > 1:
            suffix += f"_mt_{str(model_trades).lower()}"
        return suffix

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        For each (r, q_level, spec, model_trades) in the Cartesian product of
        parameter lists, calculate target weight, actual weight, and risk-control
        return and append these as new columns.
        """
        out = df.copy()

        for r, q_level, spec, model_trades in product(
            self.r_list, self.q_level_list, self.spec_list, self.model_trades_list
        ):
            strategy = QuantileRiskControlStrategy(
                r=r,
                spec=spec,
                q_level=q_level,
                model_trades=model_trades,
                level_col=self.level_col,
                underlying_return_col=self.underlying_return_col,
            )
            strat_df = strategy.run(df)
            suffix = self._strategy_suffix(r=r, q_level=q_level, spec=spec, model_trades=model_trades)

            # Add strategy-specific columns for target and actual weights and returns.
            out[f"target_weight__{suffix}"] = strat_df["target_weight"]
            out[f"actual_weight__{suffix}"] = strat_df["actual_weight"]
            out[f"risk_control_return__{suffix}"] = strat_df["risk_control_return"]

        return out


class StrategyReturnsMerger:
    """
    Merge strategy returns from a grid DataFrame with benchmark/index returns
    from an index levels DataFrame on a shared time column.

    Optionally combines two versions of a benchmark column: the primary column
    keeps all its existing values; any missing rows are back-filled from a
    secondary column, after which the secondary column is dropped.

    Typical usage::

        merger = StrategyReturnsMerger(
            benchmark_fill=("spx_return", "spx_period_return_period_return")
        )
        merged_strat_returns = merger.run(grid_df, intraday_index_levels_v2)

    Parameters
    ----------
    strategy_regex : str
        Regex passed to ``DataFrame.filter(regex=...)`` on the strategy
        DataFrame.  Should match all return columns and the time column.
    index_regex : str
        Regex passed to ``DataFrame.filter(regex=...)`` on the index levels
        DataFrame.  Should match all period-return columns and the time column.
    time_col : str
        Column name used as the merge key.
    how : str
        Merge type forwarded to ``pd.merge`` (default ``"outer"``).
    benchmark_fill : tuple[str, str] | None
        Optional ``(primary_col, fill_col)`` pair.  After merging, any NaN
        values in *primary_col* will be filled with values from *fill_col*,
        and *fill_col* will then be dropped.  Pass ``None`` to skip this step.
    """

    def __init__(
        self,
        strategy_regex: str = "return|time",
        index_regex: str = "period_return|time",
        time_col: str = "time",
        how: str = "outer",
        benchmark_fill: tuple[str, str] | None = None,
    ) -> None:
        self.strategy_regex = strategy_regex
        self.index_regex = index_regex
        self.time_col = time_col
        self.how = how
        self.benchmark_fill = benchmark_fill

    def run(
        self,
        strategy_df: pd.DataFrame,
        index_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Filter, merge, and optionally combine benchmark columns.

        Parameters
        ----------
        strategy_df : pd.DataFrame
            Grid output (e.g. from ``QuantileRiskControlGrid.run``).
        index_df : pd.DataFrame
            Index levels DataFrame that also contains period-return columns
            (e.g. produced by ``ReturnsCalculator.transform``).

        Returns
        -------
        pd.DataFrame
            Merged returns DataFrame.
        """
        strategies_returns = strategy_df.filter(regex=self.strategy_regex)
        index_returns = index_df.filter(regex=self.index_regex)

        merged = pd.merge(
            strategies_returns,
            index_returns,
            on=self.time_col,
            how=self.how,
        )

        if self.benchmark_fill is not None:
            primary_col, fill_col = self.benchmark_fill
            if primary_col not in merged.columns:
                raise KeyError(
                    f"benchmark_fill primary column '{primary_col}' not found "
                    f"in merged DataFrame. Available columns: {merged.columns.tolist()}"
                )
            if fill_col not in merged.columns:
                raise KeyError(
                    f"benchmark_fill fill column '{fill_col}' not found "
                    f"in merged DataFrame. Available columns: {merged.columns.tolist()}"
                )
            merged[primary_col] = merged[primary_col].fillna(merged[fill_col])
            merged = merged.drop(columns=[fill_col])

        return merged



