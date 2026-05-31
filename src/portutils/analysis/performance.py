# performance.py


# Import modules needed for the py file to work
from typing import Any, Dict, List, Optional
import re
import numpy as np
import pandas as pd


class PerformanceSummary:
    """
    Compute performance summary statistics for multiple return series.

    Usage:
        ps = PerformanceSummary(df, ret_cols, kind="log")
        summary = ps.run()
    """

    DEFAULT_STRESS_WINDOWS = [
        {"period": "1973-74 Oil Shock", "start": "1973-10-01", "end": "1974-12-31"},
        {"period": "1987 Black Monday Crash", "start": "1987-10-01", "end": "1987-11-30"},
        {"period": "Dot-Com Bust", "start": "2000-03-01", "end": "2002-10-31"},
        {"period": "2008 GFC", "start": "2007-10-01", "end": "2009-03-31"},
        {"period": "2020 COVID Crash", "start": "2020-02-19", "end": "2020-03-23"},
        {"period": "2021-22 Rate Hikes", "start": "2021-11-01", "end": "2022-12-31"},
        {"period": "2025 Liberation Day Shock", "start": "2025-04-02", "end": "2025-04-30"},
    ]

    def __init__(
        self,
        df: pd.DataFrame,
        ret_cols,
        kind: str = "simple",
        annualise: List = ["resampled","windows"], # which returns to annualise
        resample_freq: Any = "YE", # controls frequency of the resampled periods; can be str or list of str
        periods_per_year: float = 252.0, # default 252 for interday data,
        include_stress_windows: bool = False,
        custom_windows: Optional[List] = None,
        rolling_lookback_years: Optional[List[int]] = None,
        rolling_anchor_freq: str = "YE",
        align_end_dates: bool = True,
        *, 
        # the * makes the following arguments keyword-only, 
        # which means they must be specified by name
        include_vol: bool = False,
        include_drawdown: bool = False
        
    ):
        """
        Initialize with DataFrame, return columns, and return type.
        Done to make attributes retrievable later and clearly associated with the instance.
        """
        # stop if returns argument is invalid
        if kind not in ("simple", "log"):
            raise ValueError("kind must be 'simple' or 'log'")
        
        self.df = df # dataframe input
        self.x = None  # prepared dataframe (filled in _prepare_returns)
        self.R = None # returns dataframe (filled in _prepare_returns)
        # list of return columns to analyze (can be just one)
        self.ret_cols = ret_cols if isinstance(ret_cols, list) else [ret_cols]
        self.kind = kind
        self.annualise = annualise
        self.resample_freqs = self._normalise_resample_freqs(resample_freq)
        self._resample_specs = [
            {
                "input": f,
                "resample_rule": self._to_resample_rule(f),
                "period_rule": self._to_period_rule(f),
            }
            for f in self.resample_freqs
        ]

        # Keep backward compatibility for code paths that expect single-frequency attributes.
        self.resample_freq = self.resample_freqs[0]
        self._resample_rule = self._resample_specs[0]["resample_rule"]
        self._period_rule = self._resample_specs[0]["period_rule"]
        self.periods_per_year = periods_per_year
        self.include_stress_windows = include_stress_windows
        self.custom_windows = custom_windows or []
        # length of windows to use in rolling lookbacks
        self.rolling_lookback_years = sorted(
            {int(w) for w in (rolling_lookback_years or []) if int(w) > 0}
        )
        # frequency for rolling lookback anchors (e.g., "YE" for year-end, "QE" for quarter-end)    
        # this is done to see if long term performance would have looked the same in the past
        self.rolling_anchor_freq = str(rolling_anchor_freq).strip().upper()
        self._rolling_anchor_rule = self._to_resample_rule(self.rolling_anchor_freq)
        self.align_end_dates = align_end_dates
        self.include_vol = include_vol
        self.include_drawdown = include_drawdown
        self.common_end = None
        
        # filled during run()
        self.summary = None



    @staticmethod
    def _split_freq_multiplier(freq: str) -> tuple[int, str]:
        """Split frequencies like '5YE' into (5, 'YE')."""
        m = re.match(r"^(\d+)([A-Z].*)$", freq)
        if m:
            return int(m.group(1)), m.group(2)
        return 1, freq

    @staticmethod
    def _to_resample_rule(freq: str) -> str:
        """Map user aliases to pandas resample-safe offsets."""
        mult, base = PerformanceSummary._split_freq_multiplier(freq)

        def with_mult(token: str) -> str:
            return f"{mult}{token}" if mult != 1 else token

        if base == "Q":
            return with_mult("QE")
        if base.startswith("Q-"):
            return with_mult(f"QE-{base.split('-', 1)[1]}")
        if base in ("Y", "A"):
            return with_mult("YE")
        if base.startswith("Y-") or base.startswith("A-"):
            return with_mult(f"YE-{base.split('-', 1)[1]}")
        if base == "QE" or base.startswith("QE-"):
            return with_mult(base)
        if base == "YE" or base.startswith("YE-"):
            return with_mult(base)
        return freq

    @staticmethod
    def _to_period_rule(freq: str) -> str:
        """Map resample aliases to period-safe frequency strings."""
        mult, base = PerformanceSummary._split_freq_multiplier(freq)

        def with_mult(token: str) -> str:
            return f"{mult}{token}" if mult != 1 else token

        if base == "QE":
            return with_mult("Q")
        if base.startswith("QE-"):
            return with_mult(f"Q-{base.split('-', 1)[1]}")
        if base == "YE":
            return with_mult("Y")
        if base.startswith("YE-"):
            return with_mult(f"Y-{base.split('-', 1)[1]}")
        if base == "Q" or base.startswith("Q-"):
            return with_mult(base)
        if base == "Y" or base.startswith("Y-"):
            return with_mult(base)
        if base == "A":
            return with_mult("Y")
        if base.startswith("A-"):
            return with_mult(f"Y-{base.split('-', 1)[1]}")
        return freq

    @staticmethod
    def _normalise_resample_freqs(resample_freq: Any) -> List[str]:
        """Normalize a frequency input into an ordered unique list of uppercase strings."""
        if isinstance(resample_freq, str):
            raw = [resample_freq]
        elif isinstance(resample_freq, (list, tuple, set)):
            raw = list(resample_freq)
        else:
            raise ValueError("resample_freq must be a string or a list/tuple/set of strings")

        normalised = []
        seen = set()
        for f in raw:
            ff = str(f).strip().upper()
            if not ff:
                continue
            if ff not in seen:
                seen.add(ff)
                normalised.append(ff)

        if not normalised:
            raise ValueError("At least one non-empty resample frequency must be provided")

        return normalised

    def _is_quarterly_freq(self, period_rule: Optional[str] = None) -> bool:
        rule = self._period_rule if period_rule is None else period_rule
        return rule.startswith("Q")

    @staticmethod
    def _normalise_window_item(item: Any) -> Optional[Dict[str, pd.Timestamp]]:
        """Normalise one user-provided window spec into a dict."""
        if isinstance(item, dict):
            if not {"period", "start", "end"}.issubset(item.keys()):
                raise ValueError("custom_windows dict items must contain 'period', 'start', and 'end'")
            label = str(item["period"])
            start = pd.to_datetime(item["start"])
            end = pd.to_datetime(item["end"])
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            label = str(item[0])
            start = pd.to_datetime(item[1])
            end = pd.to_datetime(item[2])
        else:
            raise ValueError("custom_windows items must be dicts or 3-tuples: (label, start, end)")

        if pd.isna(start) or pd.isna(end):
            return None
        if end < start:
            raise ValueError(f"custom window '{label}' has end < start")
        return {"period": label, "start": start, "end": end}

    def _collect_window_specs(self) -> List[Dict[str, pd.Timestamp]]:
        """Build final list of stress/custom windows to evaluate."""
        specs = []
        # add stressed windows
        if self.include_stress_windows:
            specs.extend(self.DEFAULT_STRESS_WINDOWS)

        # then add custom windows
        specs.extend(self.custom_windows)

        normalised = []
        for item in specs:
            spec = self._normalise_window_item(item)
            if spec is not None:
                normalised.append(spec)
        return normalised

    def _prepare_returns(self) -> pd.DataFrame:
        """
        Prepare the returns DataFrame for analysis.
        Convert the 'time' column to datetime, sort by time, and set it as the index.
        Return only the specified return columns as floats.
        """
        # create an attribute self.x to hold the prepared DataFrame
        self.x = self.df.copy() # make a copy to avoid modifying original
        self.x["time"] = pd.to_datetime(self.x["time"])
        self.x = self.x.sort_values("time").set_index("time")
        # return only the return columns as float type,
        # now indexed by time thanks to the above code
        R = self.x[self.ret_cols].astype(float)

        if self.align_end_dates:
            # Use the earliest column-specific last-valid timestamp as a common endpoint.
            # This prevents comparing "Last N Years" where some strategies include newer data.
            last_valid = R.apply(pd.Series.last_valid_index)
            if last_valid.isna().any():
                missing = list(last_valid[last_valid.isna()].index)
                raise ValueError(
                    f"No non-null observations for return columns: {missing}"
                )

            self.common_end = min(last_valid.tolist())
            R = R.loc[:self.common_end]
        else:
            self.common_end = R.index.max()

        if R.empty:
            raise ValueError("No data available after end-date alignment")

        return R

    def _total_return(self, frame: pd.DataFrame, annualise: bool = False) -> pd.Series:
        """Calculate total return  over the given DataFrame frame."""
        # using log returns 
        if self.kind == "log":
            total_return = np.exp(frame.sum()) - 1
        # using simple returns
        else:
            total_return = (1 + frame).prod() - 1
        if annualise:
            # annualise the return
            elapsed_years = (frame.index[-1] - frame.index[0]).days / 365.25
            return (1 + total_return) ** (1 / elapsed_years) - 1
        else:
            return total_return

    def _total_return_resampled(self, resampler, annualise: bool = False, period_rule: Optional[str] = None) -> pd.DataFrame:
        """
        Compute total returns for each resample bin, returning a DataFrame
        indexed by bin end timestamps, columns=strategies.
        """
        if self.kind == "log":
            tr = np.exp(resampler.sum()) - 1
        else:
            tr = (1 + resampler).prod() - 1

        if not annualise:
            return tr

        # Annualise each bin using that bin's actual elapsed years
        # Bin start/end per row:
        # tr is a DataFrame whose INDEX is the resample bin label (e.g., each quarter-end),
        # and whose COLUMNS are your strategies. Each ROW corresponds to one resample window.
        # Example: index = [2024-03-31, 2024-06-30, ...] for quarterly.

        # Convert each bin label into a Period (e.g., "2024Q1"), then get the start timestamp
        # of that period. This gives a vector of start dates, one per row/bin.
        p_rule = self._period_rule if period_rule is None else period_rule
        start = tr.index.to_period(p_rule).start_time

        # Same idea: get the end timestamp of each period/bin, one per row/bin.
        end = tr.index.to_period(p_rule).end_time

        # Compute elapsed time per bin in years.
        # (end - start) is a TimedeltaIndex; .days gives integer day counts per row/bin.
        elapsed_years = (end - start).days / 365.25

        # broadcast elapsed_years across columns
        # We want an annualisation exponent per row/bin: exponent = 1 / elapsed_years.
        # Make it a Series indexed by the same index as tr so pandas can ALIGN by row.
        ann_factor = 1 / pd.Series(elapsed_years, index=tr.index)

        # Annualise each cell in tr using the exponent for its ROW/bin:
        # - (1 + tr) makes gross returns (e.g., 1.05 instead of 0.05)
        # - .pow(ann_factor, axis=0) raises each ROW to the power for that row.
        #
        # IMPORTANT: axis=0 means "align ann_factor on the index (rows)".
        # So for each row i (each resample window):
        #   for every strategy column j:
        #       result[i, j] = (1 + tr[i, j]) ** ann_factor[i]
        #
        # This is the "iterating over windows" part, but it happens in vectorised form
        # across all rows at once (no Python for-loop).
        #
        # Finally, subtract 1 to go back to simple returns.
        return (1 + tr).pow(ann_factor, axis=0) - 1

        
    def _equity_curve(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Equity curve starting at 1.0 for each column."""
        if self.kind == "log":
            return np.exp(frame.cumsum())
        else:
            return (1 + frame).cumprod()
        
    def _annualised_vol(self, frame: pd.DataFrame) -> pd.Series:
        """Annualised volatility per column."""
        # the ddof=0 gives population std dev, ddof=1 gives sample std dev
        if self.kind == "log":
            r = frame
        else:
            r = np.log1p(frame)  # log(1+r)
        return r.std(ddof=0) * np.sqrt(self.periods_per_year)

    def _max_drawdown(self, frame: pd.DataFrame) -> pd.Series:
        """Max drawdown (most negative) per column over the window."""
        # start with equity curve
        eq = self._equity_curve(frame)
        # then calculate the drawdown: the largest % drop in equity from a prior peak
        dd = eq / eq.cummax() - 1.0
        return dd.min()
    
    def _add_extra_stats(self, row: dict, frame: pd.DataFrame, pre_annualised: bool = True) -> dict:
        """Add extra statistics like volatility and drawdown to the stats Series."""
        # add extra stats if requested by user
        if self.include_vol:
            # find the value of annualised volatility
            vol = self._annualised_vol(frame)
            
            # store as e.g. "spx_vol", "es_vol" etc.
            row.update({f"{c}_volatility": float(vol[c]) for c in self.ret_cols})
            # might as well add the sharpe ratio while we're at it
            # taking advantage of the fact that annualised return for a given col
            # is stored in row[c], since we called _total_return earlier
            sharpe = {}
            for c in self.ret_cols:
                if pre_annualised:
                    annualised_return = row[c]
                else:
                    # annualise the return if not already done (as is usually the case for YTD)
                    elapsed_years = (frame.index[-1] - frame.index[0]).days / 365.25
                    annualised_return = (1 + row[c]) ** (1 / elapsed_years) - 1
                sharpe[c] = annualised_return / vol[c] if vol[c] != 0 else np.nan
            row.update({f"{c}_sharpe": float(sharpe[c]) for c in self.ret_cols})
        if self.include_drawdown:
            # find the value of max drawdown
            mdd = self._max_drawdown(frame) 
            # add to the row as e.g. "spx_max_drawdown", "es_max_drawdown" etc.
            row.update({f"{c}_max_drawdown": float(mdd[c]) for c in self.ret_cols})
        return row

    def _build_resampled_df_for_spec(self, spec: Dict[str, str]) -> pd.DataFrame:
        """Build period-resampled section for a single frequency spec."""
        annualised_value = "resampled" in self.annualise
        # this gives us a DataFrame indexed by the resample bin end timestamps, with one column per strategy,
        # containing the total return for that strategy during that resample bin (e.g., quarter), annualised if requested
        resampled = self._total_return_resampled(
            self.R.resample(spec["resample_rule"]),
            annualise=annualised_value,
            period_rule=spec["period_rule"],
        )

        # derive period labels from the index *before* reset_index so we have a true
        # PeriodIndex (supports .start_time / .end_time), mirroring _total_return_resampled
        period_idx = resampled.index.to_period(spec["period_rule"])

        # now we need to add a "period" column that labels each row/bin with the corresponding quarter or year,
        out = resampled.reset_index()
        # convert the time index into a period label (e.g., "2024Q1" or "2024"), and store in a new "period" column
        if self._is_quarterly_freq(spec["period_rule"]):
            out["period"] = period_idx.astype(str)
        elif spec["period_rule"].startswith("Y"):
            out["period"] = out["time"].dt.year.astype(str)
        else:
            out["period"] = period_idx.astype(str)

        # also add explicit start_date and end_date columns for each resampled period,
        # which we can use later to calculate extra stats like volatility and drawdown over the same period
        out["start_date"] = period_idx.start_time.date.astype(str)
        out["end_date"] = period_idx.end_time.date.astype(str)

        # if the user has requested extra stats, we need to loop through each row/bin and calculate those
        # stats over the original returns DataFrame self.R for the corresponding time window
        if self.include_vol or self.include_drawdown:
            out_rows = out.to_dict(orient="records")
            out_rows_new = []
            for row in out_rows:
                frame = self.R.loc[row["start_date"]:row["end_date"]]
                if frame.empty:
                    continue
                row = self._add_extra_stats(row, frame, pre_annualised=annualised_value)
                out_rows_new.append(row)
            out = pd.DataFrame(out_rows_new)

        if out.empty:
            return out

        out = out.sort_values("period", ascending=False)
        return out.drop(columns=["time"])

    def _build_resampled_df(self) -> pd.DataFrame:
        """Build and combine resampled sections across all requested frequencies."""
        parts = []
        for spec in self._resample_specs:
            part = self._build_resampled_df_for_spec(spec)
            if not part.empty:
                parts.append(part)

        if not parts:
            return pd.DataFrame()

        out = pd.concat(parts, ignore_index=True)
        if "end_date" in out.columns:
            out = out.sort_values(["end_date", "period"], ascending=[False, False])
        return out

    def _build_window_row(
        self,
        period_label: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        *,
        annualise_key: str,
    ) -> Optional[dict]:
        """Build one row for any explicit date window, skipping empty overlap windows."""
        annualised_value = annualise_key in self.annualise
        frame = self.R.loc[start:end]
        if frame.empty:
            return None

        stats = self._total_return(frame, annualise=annualised_value)
        row = {
            "period": period_label,
            "start_date": str(frame.index.min().date()),
            "end_date": str(frame.index.max().date()),
            **stats.to_dict(),
        }
        return self._add_extra_stats(row, frame, pre_annualised=annualised_value)

    def _build_ytd_df(self) -> pd.DataFrame:
        """Build YTD section as a one-row DataFrame."""
        last_date = self.R.index.max()
        start = pd.Timestamp(f"{last_date.year}-01-01")
        row = self._build_window_row("YTD", start, last_date, annualise_key="YTD")
        return pd.DataFrame([row]) if row is not None else pd.DataFrame()

    def _build_rolling_windows_df(self) -> pd.DataFrame:
        """Build standard rolling multi-year windows section."""
        last_date = self.R.index.max()
        rows = []
        for window in (1, 3, 5, 10):
            start = max(last_date - pd.DateOffset(years=window), self.R.index.min())
            row = self._build_window_row(
                f"Last {window} Years",
                start,
                last_date,
                annualise_key="windows",
            )
            if row is not None:
                rows.append(row)
        return pd.DataFrame(rows)

    def _build_rolling_lookback_history_df(self) -> pd.DataFrame:
        """Build rolling lookback snapshots (e.g., 5Y as-of each anchor date)."""
        if not self.rolling_lookback_years:
            return pd.DataFrame()

        anchor_dates = self.R.resample(self._rolling_anchor_rule).last().index
        rows = []

        for lookback in self.rolling_lookback_years:
            for anchor in anchor_dates[::-1]:
                start = max(anchor - pd.DateOffset(years=lookback), self.R.index.min())
                label = f"Rolling {lookback}Y As Of {anchor.date()}"
                row = self._build_window_row(
                    label,
                    start,
                    anchor,
                    annualise_key="rolling_lookbacks",
                )
                if row is not None:
                    rows.append(row)

        return pd.DataFrame(rows)

    def _build_stress_windows_df(self) -> pd.DataFrame:
        """Build predefined/custom stress window rows."""
        rows = []
        for spec in self._collect_window_specs():
            row = self._build_window_row(
                spec["period"],
                spec["start"],
                spec["end"],
                annualise_key="stress_windows",
            )
            if row is not None:
                rows.append(row)
        return pd.DataFrame(rows)

    def run(self) -> pd.DataFrame:
        """
        Compute performance summary statistics by calling on the above functions
        """
        # Expected input shape: one row per timestamp, with a `time` column and
        # one or more return columns listed in self.ret_cols.
        # prepare returns DataFrame by calling _prepare_returns
        # which sets up the time index and selects return columns
        self.R = self._prepare_returns()

        # build each section independently to keep run() small and testable
        sections = [
            self._build_ytd_df(),
            self._build_rolling_windows_df(),
            self._build_rolling_lookback_history_df(),
            self._build_stress_windows_df(),
            self._build_resampled_df(),
        ]
        sections = [s for s in sections if not s.empty]
        summary = pd.concat(sections, ignore_index=True) if sections else pd.DataFrame()

        # formatting
        # ensure the numeric cols are stored as floats but rounded to 4 decimal places
        if not summary.empty:
            for c in [col for col in summary.columns if col not in ["period", "start_date", "end_date"]]:
                summary[c] = summary[c].astype(float).round(4)

            # format the summary columns neatly in title format without underscores
            summary.columns = [c.replace("_", " ").title() for c in summary.columns]

        self.summary = summary
        return summary

    def save_to_excel(self, filepath: str) -> None:
        """
        Write self.summary to an Excel workbook with one sheet per metric.

        Sheet layout
        ------------
        Each sheet contains:  Period | Start Date | End Date | <strategy 1> | <strategy 2> | ...
        The best-performing strategy in every row is written in bold.

        Metric → bold direction
        -----------------------
        Period Return   : higher is better
        Volatility      : lower is better
        Sharpe          : higher is better; row is skipped if the best value is negative
        Max Drawdown    : higher is better (least negative = smallest drawdown)

        File-A / File-B workflow
        ------------------------
        Keep this file as "file A" (plain data, always overwritten).
        Create "file B" separately, connecting to file A via Excel's
        Data → Get Data → From Workbook (Power Query).  File B can then hold colour
        scales and any formatting you want without ever being overwritten by this code.
        """
        if self.summary is None:
            raise RuntimeError("Call run() before save_to_excel()")

        summary = self.summary
        id_cols = [c for c in summary.columns if c in ("Period", "Start Date", "End Date")]
        data_cols = [c for c in summary.columns if c not in id_cols]

        # Group data columns by metric suffix, preserving insertion order
        metric_groups: dict = {}
        for col in data_cols:
            col_lower = col.lower()
            if col_lower.endswith(" volatility"):
                # if the column name ends with " volatility", we consider it a volatility metric 
                # and group it under "Volatility". the setdefault is just a way to initialise the
                # list if the key "Volatility" is not already in the dict, and then we append 
                # the column name to that list
                metric_groups.setdefault("Volatility", []).append(col)
            elif col_lower.endswith(" sharpe"):
                metric_groups.setdefault("Sharpe", []).append(col)
            elif col_lower.endswith(" max drawdown"):
                metric_groups.setdefault("Max Drawdown", []).append(col)
            else:
                metric_groups.setdefault("Period Return", []).append(col)

        with pd.ExcelWriter(filepath, engine="xlsxwriter") as writer:
            workbook = writer.book
            bold = workbook.add_format({"bold": True})

            for metric, strat_cols in metric_groups.items():
                sheet_df = summary[id_cols + strat_cols].copy()
                sheet_name = metric[:31]  # Excel tab name limit is 31 chars
                sheet_df.to_excel(writer, sheet_name=sheet_name, index=False)
                worksheet = writer.sheets[sheet_name]

                metric_lower = metric.lower()
                if "volatility" in metric_lower:
                    direction = "lower"
                elif "sharpe" in metric_lower:
                    direction = "sharpe"
                else:
                    direction = "higher"  # Period Return and Max Drawdown

                n_id = len(id_cols)

                for row_idx in range(len(sheet_df)):
                    values = [sheet_df.iloc[row_idx][c] for c in strat_cols]
                    numeric = [(i, v) for i, v in enumerate(values) if pd.notna(v)]
                    if not numeric:
                        continue

                    if direction == "lower":
                        best_i, best_val = min(numeric, key=lambda x: x[1])
                    elif direction == "sharpe":
                        positive = [(i, v) for i, v in numeric if v >= 0]
                        if not positive:
                            continue
                        best_i, best_val = max(positive, key=lambda x: x[1])
                    else:
                        best_i, best_val = max(numeric, key=lambda x: x[1])

                    # row_idx + 1 shifts past the header row in the Excel sheet
                    worksheet.write(row_idx + 1, n_id + best_i, best_val, bold)





class PerformanceCompare:
    """
    Compare existing summary statistics of different return series to see which is better.
    """

    def __init__(
        self,
        summary_df: pd.DataFrame,
        rank_by: list[str] = ["Total Return","Sharpe"],
        rank_periods: list[str] = ["Last 5 Years","Last 10 Years"],
        *,
        periods_per_year: float = 252.0 # default 252 for interday data,
    ):
        """
        Initialize with summary DataFrame and periods per year.
        """
        self.summary_df = summary_df
        self.rank_by = rank_by
        self.rank_periods = rank_periods

    def _transform_summary_df(self):
        """
        Transform the summary DataFrame into a format suitable for comparison.
        """
        # Assumes summary_df columns follow the naming pattern produced by
        # PerformanceSummary.run(), e.g. "My Strategy Period Return",
        # "My Strategy Period Return Sharpe", etc.
        # first, trasform the summary_df into a long format for easier manipulation
        df_long = self.summary_df.melt(
            id_vars=["Period", "Start Date", "End Date"],
            var_name="metric",
            value_name="value"
        )
        # create a column called "strategy_name" that extracts the strategy name 
        # from the Metric column: everything before Period Return comes up in the
        # string in the Metric column
        # basically, find where "Period Return" comes up and take all the text before that

        # Regex explanation:
        # ^                 -> start of string
        # (.*)              -> capture group 1: "everything up to the next part" (greedy, so it will take as much as it can)
        # \s+               -> one or more spaces before the literal phrase
        # Period Return     -> literal text we anchor on
        # (?:\s+.*)?        -> OPTIONAL non-capturing group:
        #                      \s+.* means "a space followed by anything"
        #                      the trailing ? makes the whole group optional, which is what allows
        #                      anything (or nothing) to follow "Period Return"
        # $                 -> end of string
        df_long["strategy_name"] = df_long["metric"].str.extract(
            r"^(.*)\s+Period Return(?:\s+.*)?$"
        )[0] # this 0 makes sure we only xtract the first group captured ( the part before "Period Return")

        # then, add a separate column for the metric type, which is whatever came after
        # period return in the metric column, and if nothing did, this would correspond to
        # total return (annualised, save for YTD)

        # Regex explanation:
        # ^                 -> start of string
        # .*?               -> any characters (non-greedy) up to the first match of the next literal phrase
        # \s+               -> one or more spaces before the literal phrase
        # Period Return     -> literal text we anchor on
        # (.*)              -> capture group 1: everything after "Period Return" (can be empty)
        # $                 -> end of string
        df_long["metric_type"] = (
            df_long["metric"]
            .str.extract(r"^.*?\s+Period Return(.*)$")[0]
            .str.lstrip()   # remove whitespace only at the start of the string
        )

        # fill the "" (empty string) values in this col with "Total Return"
        # (note: extract returns "" rather than NaN when it matches but captures nothing)
        df_long["metric_type"] = df_long["metric_type"].replace("", "Total Return")

        # drop the start and end date columns as no longer needed

        # transform the datframe again so that each row is a strategy_name, and each column is 
        # a metric during a given period
        df_long = df_long.pivot_table(
            index=["strategy_name"],
            columns=["Period","metric_type"],
            values="value"
        ).reset_index()

        return df_long

    def _flag_frequent_bottom_performers(self,
        df: pd.DataFrame,
        metric: str = "Total Return",
        bottom_frac: float = 0.05,
        min_fraction_periods: float = 0.5,
        out_col: tuple = ("flag", "bottom_5pct_half_periods"),
        ) -> pd.DataFrame:
        """
        Adds a column that flags strategies that fall in the bottom `bottom_frac`
        of performers for at least `min_fraction_periods` of the periods (years)
        for the specified `metric`.

        Returns a copy with the extra column appended.
        """
        out = df.copy()

        # 1) pick (year, metric) columns only
        cols = out.columns
        if not isinstance(cols, pd.MultiIndex):
            raise TypeError("df.columns must be a MultiIndex")

        # Keep columns where level0 is not a strategy_name (will be a metric period)
        period_metric_cols = [c for c in cols if c[0] != "strategy_name"]
        # usually, level0 looks like a time period AND level1 equals metric
        

        X = out[period_metric_cols].astype(float)  # rows=strategy, cols=years

        # 2) bottom-k per year (k = max(1, ceil(frac * n_strategies)))
        n = X.shape[0] # n corresponds to the first dimension: the number of rows = n strategies
        # the below will give us the rank below which a strategy is in the bottom bottom_frac percent
        #  of performers for a given year/metric. 
        # We take the ceiling to ensure we always flag at least one strategy, even if bottom_frac * n is less than 1.
        k = max(1, int(np.ceil(bottom_frac * n)))

        # Rank ascending: rank 1 = worst (smallest return)
        # axis = 0 within each other dimension (cols, axis = 1), we sum across all rows (axis = 0).
        # this creates a DataFrame of the same shape as X where each cell contains the rank of that
        #  strategy for that year/metric,
        ranks = X.rank(axis=0, method="min", ascending=True)

        # the below creates a boolean DataFrame of the same shape as X where each cell is True 
        # if that strategy's rank for that year/metric is less than or equal to k (i.e., in the bottom bucket),
        #  and False otherwise
        bottom_mask = ranks <= k  # True if strategy is in bottom bucket for that year

        # 3) count how often bottom, require at least half the years (or your threshold)
        # here, axis = 1 means that within each other dimension (rows, axis = 0), we sum
        #  across all columns (axis = 1). This returns an n_rows x 1 column vector where 
        # each row gives the count of how many times that strategy was in the bottom bucket across all periods
        hits = bottom_mask.sum(axis=1)
        periods = bottom_mask.shape[1]
        threshold = int(np.ceil(min_fraction_periods * periods))

        out[out_col] = (hits >= threshold).astype(int)

        return out

    def run(self) -> pd.DataFrame:
        """
        Compare the performance summaries and determine which return series is better.
        """
        df_transformed = self._transform_summary_df()

        df_flagged = self._flag_frequent_bottom_performers(df_transformed)

        # filter out frequent bottom performers
        df_filtered = df_flagged[df_flagged[("flag", "bottom_5pct_half_periods")] == 0].copy()

              
        # first, initialise a dataframe to hold the rankings, with the same number of rows as the filtered df
        rankings_df = pd.DataFrame(index=df_filtered.index)

        # now,  for each of the metrics and periods specified in self.rank_by and self.rank_periods,
        # create a column where the highest ranked strategy is at the top, followed by the column 
        # used to rank that strategy, also in top to bottom rank order
        # Output format is side-by-side ranking tables (one table per
        # metric/period pair) concatenated horizontally.
        for metric in self.rank_by:
            for period in self.rank_periods:
                col = (period, metric)
                if col not in df_filtered.columns:
                    raise ValueError(f"Column {col} not found in DataFrame for ranking.")

                # rank the strategies in descending order (highest value = rank 1)
                df_ranked = df_filtered.sort_values(by=col, ascending=False).reset_index(drop=True)

                # now that it is ranked, take only the strategy_name and the column used for ranking
                df_ranked_subset = df_ranked[[('strategy_name', ''), col]].copy()
                # rename the strategy_name column to indicate the metric and period used for ranking
                # flatten MultiIndex columns -> single strings
                df_ranked_subset.columns = [
                    f"{a} {b}".strip() if b else str(a)   # join levels; drop empty second level
                    for a, b in df_ranked_subset.columns # a and b are usually ('period', 'metric')
                ]

                # now rename using single-level column names
                df_ranked_subset = df_ranked_subset.rename(columns={
                    "strategy_name": f"Ranked by {metric} ({period})",
                    f"{col[0]} {col[1]}".strip(): f"{metric} ({period})",
                })

                # append/concatenate to thee right of the rankings_df
                rankings_df = pd.concat([rankings_df, df_ranked_subset], axis=1)
             


        return rankings_df


class ReturnsToLevels:
    """
    Convert return series into level indices that all start at 100.

    The common start date is defined as the latest first-valid timestamp across
    all selected return columns, so every output series begins together.
    """

    def __init__(
        self,
        df: pd.DataFrame,
        ret_cols,
        kind: str = "simple",
        *,
        time_col: str = "time",
        keep_time_col: bool = True,
        align_common_calendar: bool = True,
    ):
        if kind not in ("simple", "log"):
            raise ValueError("kind must be 'simple' or 'log'")

        self.df = df
        self.ret_cols = ret_cols if isinstance(ret_cols, list) else [ret_cols]
        self.kind = kind
        self.time_col = time_col
        self.keep_time_col = keep_time_col
        self.align_common_calendar = align_common_calendar

        self.common_start = None
        self.levels = None

    def _prepare_returns(self) -> pd.DataFrame:
        """Prepare return matrix indexed by time and sorted chronologically."""
        x = self.df.copy()
        x[self.time_col] = pd.to_datetime(x[self.time_col])
        x = x.sort_values(self.time_col).set_index(self.time_col)
        R = x[self.ret_cols].astype(float)

        first_valid = R.apply(pd.Series.first_valid_index)
        if first_valid.isna().any():
            missing = list(first_valid[first_valid.isna()].index)
            raise ValueError(
                f"No non-null observations for return columns: {missing}"
            )

        common_start = max(first_valid.tolist())
        R = R.loc[common_start:]

        if self.align_common_calendar:
            R = R.dropna(how="any")

        if R.empty:
            raise ValueError("No overlapping observations after applying common start alignment")

        self.common_start = common_start
        return R

    def run(self) -> pd.DataFrame:
        """
        Return level-indexed series starting at 100 at the common start date.
        """
        R = self._prepare_returns()

        if self.kind == "log":
            gross = np.exp(R.cumsum())
        else:
            gross = (1 + R).cumprod()

        levels = 100.0 * gross.div(gross.iloc[0])

        if self.keep_time_col:
            out = levels.reset_index().rename(columns={levels.index.name: self.time_col})
        else:
            out = levels

        self.levels = out
        return out