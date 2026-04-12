"""Reusable data-loading and Plotly theming helpers for portfolio notebooks.

PanelBuilder centralises the CSV-loading, normalisation, and dark-theme
styling logic that was duplicated across every code cell in ff.ipynb.
"""

import pathlib

import numpy as np
import pandas as pd

# ── Module-level config constants ────────────────────────────────────────────
# These are shared across notebooks — import them directly:
#   from portutils.viz.panel import SECTOR_MAP, SECTOR_COLORS, LINE_STYLES

SECTOR_MAP = {
    'Tech': ['AAPL', 'MSFT', 'AVGO'],
    'Healthcare': ['UNH', 'JNJ', 'AMGN'],
    'Consumer Staples': ['WMT', 'COST', 'PG'],
    'Energy': ['CVX', 'XOM', 'LNG'],
}

SECTOR_COLORS = {
    'Tech': '#1f77b4',
    'Healthcare': '#2ca02c',
    'Consumer Staples': '#d62728',
    'Energy': '#ff7f0e',
}

LINE_STYLES = ['solid', 'dash', 'dot']

# default data directory: three levels up from this file, then "data/raw/market"
_DEFAULT_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent / ".." / ".." / ".." / "data" / "raw" / "market"
).resolve()


class PanelBuilder:
    """Load close-price CSVs and expose derived DataFrames + Plotly helpers.

    Parameters
    ----------
    tickers : list[str]
        Ticker symbols to load (expects ``{ticker}.csv`` in cwd).
    start_date : str
        ISO date string; rows before this date are dropped.
    """

    def __init__(self, tickers, start_date='2024-01-01', data_dir=None):
        self.tickers = list(tickers)
        self.start_date = start_date
        self.data_dir = pathlib.Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
        self.df_all = self._load()
        self.dates = self.df_all.index

    # ── data loading ─────────────────────────────────────────────────────

    def _load(self):
        df_all = pd.DataFrame()

        # loop through each ticker
        for ticker in self.tickers:
            filename = self.data_dir / f"{ticker}.csv"
            try:
                df = pd.read_csv(filename)
                df.columns = [c.lower() for c in df.columns]
                # data from ibkr will typically have a "datetime" column instead of "date"
                if 'datetime' in df.columns:
                    df.rename(columns={'datetime': 'date'}, inplace=True)
                # convert the date column to datetime and set it as the index
                # note that the original format is usually 20250414, AKA YYYYMMDD without delimiters
                # so we need to specify the format to avoid pandas treating it as a number and losing leading zeros 
                df['date'] = pd.to_datetime(df['date'].astype(str), format='%Y%m%d')
                df.set_index('date', inplace=True)
                series = df['close'].rename(ticker).copy()

                # if this is the first dataframe, initialize df_all with it; otherwise, join on the index
                if df_all.empty:
                    df_all = pd.DataFrame(series)
                else:
                    df_all = df_all.join(series, how='outer')
            # if we can't load this ticker for any reason, print a warning and skip it 
            # (e.g. file not found, missing columns, parse errors)
            except Exception as e:
                print(f"Skipping {ticker}: {e}")
        # once we have loaded all the data and combined into a single df_all,
        # we then filter by the start date, forward-fill missing values, and drop any remaining NaNs
        # note that forward-filling is a simple way to handle non-trading days and ensure we have a continuous time series for plotting;
        # the way it works is that it fills any missing values with the last known price, 
        # which is a common convention in financial data analysis 
        df_all = df_all[df_all.index >= self.start_date].copy()
        df_all.ffill(inplace=True)
        df_all.dropna(inplace=True)

        # if after all that we end up with an empty DataFrame, we raise an error to 
        # alert the user that something went wrong with loading/filtering the data
        if df_all.empty:
            raise ValueError("No data found. Check dates/files.")

        return df_all

    # ── derived DataFrames ───────────────────────────────────────────────

    def normalize(self, base=100):
        """Wealth index: ``(price / first_price) * base``."""
        return (self.df_all / self.df_all.iloc[0]) * base

    def pct_returns(self):
        """Cumulative percentage returns from first observation."""
        return ((self.df_all / self.df_all.iloc[0]) - 1) * 100

    def daily_returns(self):
        """Simple daily percentage returns (first row dropped)."""
        return self.df_all.pct_change().dropna()

    def log_returns(self):
        """Daily log returns (first row dropped)."""
        return np.log(self.df_all / self.df_all.shift(1)).dropna()

    # ── portfolio simulation ────────────────────────────────────────────

    def simulate_weights(self, rebal_freq='QE'):
        """Simulate drifting equal-weights with periodic rebalancing.

        Parameters
        ----------
        rebal_freq : str
            Pandas offset alias for rebalance dates (e.g. 'QE' for quarter-end,
            'ME' for month-end).  Weights are snapped back to 1/N on these dates.

        Returns
        -------
        pd.DataFrame
            Same shape as ``daily_returns()``, holding beginning-of-period weights.
        """
        rets = self.daily_returns()
        n = len(rets.columns)

        # start with equal weights by default
        # equal_w is a numpy array of length n (number of assets) where each element is 1/n,
        # which has dimensions: n x 1 (a column vector), and it represents the equal weight for each asset in the portfolio;
        equal_w = np.ones(n) / n

        # dates on which we rebalance back to equal weight
        # Pandas < 2.2 uses 'Q'/'M'; >= 2.2 uses 'QE'/'ME' for offsets,
        # but to_period() always needs the legacy short form.
        period_freq = rebal_freq.replace('QE', 'Q').replace('ME', 'M')
        # we convert the index of the returns DataFrame to a PeriodIndex with the specified frequency,
        # and then we take the end_time of each period, normalize it to midnight, and
        #  create a set of these rebalance dates for quick lookup; this way we can easily check if a
        #  given date is a rebalance date during our iteration over the returns
        rebal_dates = set(rets.index. # take the index of the returns DataFrame, which is a DatetimeIndex representing the dates of the returns
                          to_period(period_freq). # convert it to a PeriodIndex with the specified frequency (e.g. quarterly or monthly), which groups the dates into periods based on that frequency
                          end_time. # take the end time of each period, which gives us the last date of each quarter or month (depending on the frequency we specified); this is important because we want to rebalance at the end of each period
                          normalize() # normalize the timestamps to midnight (00:00:00) to ensure that we are comparing dates without time components when we check for rebalance dates during our iteration; this is important because the original timestamps might have time components that could cause mismatches when we check if a date is in the rebal_dates set
                          )

        # initialize a DataFrame to hold the weights, with the same index and columns as the returns DataFrame
        # and we use dtype=float to ensure that the weights are stored as floating-point numbers for calculation ease
        weights = pd.DataFrame(index=rets.index, columns=rets.columns, dtype=float)
        w = equal_w.copy()

        for date in rets.index:
            weights.loc[date] = w  # beginning-of-period weight (will carry through the drift from the previous period)
            # drift weights by that day's return
            # this multiplies the current weights of dimension n x 1 elementwise by (1 + daily return) for each asset,
            #  which gives us the new weights (as a proportion of the starting value of that portfolio)
            #   (if was matrix multiplication, we would need to use np.dot or the @ operator)
            w = w * (1 + rets.loc[date].values)
            w = w / w.sum()  # renormalise, since overall, will be some proportion of the starting value of the portfolio, 
            # but we want to keep it as weights that sum to 1

            # snap back to equal weight at period boundaries
            if date.normalize() in rebal_dates:
                w = equal_w.copy()

        return weights

    def attribution(self, weights, sector_map):
        """Compute sector-level return attribution.

        Parameters
        ----------
        weights : pd.DataFrame
            Beginning-of-period weights for each period (from ``simulate_weights``).
        sector_map : dict[str, list[str]]
            Mapping of sector name to list of ticker columns.

        Returns
        -------
        sector_contrib : pd.DataFrame
            Cumulative contribution per sector (wealth-index style, base 100).
        portfolio_series : pd.Series
            Total portfolio wealth index (base 100).
        """
        rets = self.daily_returns()
        # per-asset weighted contribution each day
        #   (elementwise multiplication of the weights DataFrame and the returns DataFrame)
        contrib = weights * rets
        # roll up to sector level
        sector_contrib = pd.DataFrame(index=rets.index)
        for sector, tickers in sector_map.items():
            sector_contrib[sector] = contrib[tickers].sum(axis=1)

        # cumulative portfolio return -> wealth index (base 100)
        portfolio_daily = sector_contrib.sum(axis=1)
        portfolio_series = (1 + portfolio_daily).cumprod() * 100
        portfolio_series_change = portfolio_series - 100

        # absolute sector contributions: scale each day's percentage contribution
        # by the portfolio value at the *start* of that day, so the units are
        # portfolio-value units (not percentages).  This ensures that the
        # cumulative sector contributions sum exactly to portfolio_series_change.
        portfolio_prev = portfolio_series.shift(1).fillna(100)  # V_{t-1}
        sector_abs = pd.DataFrame(index=rets.index)
        for sector, tickers in sector_map.items():
            sector_abs[sector] = portfolio_prev * contrib[tickers].sum(axis=1)
        sector_cum = sector_abs.cumsum()

        return sector_contrib, portfolio_daily, sector_cum, portfolio_series_change, portfolio_series

    # ── animation helpers ────────────────────────────────────────────────

    @staticmethod
    def animation_indices(dates, num_frames=60):
        """
        Return evenly-spaced index positions for Plotly animation frames.
        Defined as a static method since it doesn't depend on any instance state and can
        Be reused independently.
        """
        # we want to sample approximately num_frames frames from the full date range,
        # so we calculate a step size by dividing the total number of dates by num_frames
        # to find how many dates to skip between each frame; we use max(1, ...) to ensure 
        # we don't get a step of 0 if there are fewer dates than num_frames
        # and we use // for integer division to get an integer step size: integer division means
        # that we divide and then round down to the nearest whole number, which is important for indexing
        step = max(1, len(dates) // num_frames) 
        indices = list(range(1, len(dates), step))
        if indices[-1] != len(dates) - 1:
            indices.append(len(dates) - 1)
        return indices

    # ── Plotly theming ───────────────────────────────────────────────────

    OFF_WHITE = "#e0e0e0"
    OFF_BLACK = "#222222"

    @classmethod
    def dark_axis_style(cls):
        """
        Return the standard axis-style dict
        We use a class method here so that we can reference the OFF_WHITE color constant defined at the class level,
        and so that we can call this method directly on the class without needing an instance (e.g. PanelBuilder.dark_axis_style())
        """
        return dict(
            showgrid=True,
            gridcolor='rgba(255,255,255,0.1)', # light transparent grid lines
            tickfont=dict(color=cls.OFF_WHITE), # tick labels in off-white
            linecolor=cls.OFF_WHITE, # axis lines in off-white, just like the ticks and labels
            zeroline=False,
            title_font=dict(color=cls.OFF_WHITE), # axis title in off-white as well
        )

    @classmethod
    def play_button(
            cls, # cls represents the class itself and allows us to access class-level constants like OFF_WHITE and OFF_BLACK
            label='▶ Play', # the text that will appear on the button; you can customize this when calling the method
            y=-0.25, # Usually -0.25 works well for the y-coordinate to place it just below the x-axis, but this can be adjusted as needed.
            duration=20 # duration in milliseconds for each frame; adjust as needed to control animation speed
            ):
        """
        Return an ``updatemenus`` list with a single animate button.
        The button is styled with the standard off-white/black colors and positioned centered below the plot at the specified y-coordinate. 
        
        """
        # returning a list of dictionaries that defines the updatemenus for Plotly; in this case, 
        # we have a single menu with one button that triggers the animation
        return [{
            'type': 'buttons',
            'x': 0.5, 'y': y, 'xanchor': 'center',
            'font': {'color': cls.OFF_BLACK}, # button text in off-black
            'bgcolor': cls.OFF_WHITE,
            'buttons': [{
                'label': label,
                'method': 'animate',
                'args': [None, {
                    'frame': {'duration': duration, 'redraw': False},
                    'fromcurrent': True,
                }],
            }],
        }]

    @classmethod
    def apply_dark_theme(cls, fig, height=700, width=1200,
                         play_label=None, play_y=-0.25, play_duration=20,
                         **layout_overrides 
                         # note that the ** syntax allows us to accept any number of additional keyword
                         #  arguments that will be collected into a dictionary called layout_overrides;
                         #  this is useful for allowing users to override or add to the base layout
                         #  settings when calling this method, without having to explicitly define every
                         #  possible layout parameter in the method signature
                         ):
        """
        Apply the standard dark transparent theme to *fig*.

        We use a class method here so that we can call it directly on the class without needing an instance (e.g. PanelBuilder.apply_dark_theme(fig)),
        and so that we can reference class-level constants like OFF_WHITE and OFF_BLACK for consistent styling

        If *play_label* is provided, a play/animate button is added.
        Extra keyword arguments are forwarded to ``fig.update_layout()``.
        """
        # we start by defining a base layout dict with the standard dark theme settings: we use the "plotly_dark" 
        # template for overall styling,
        # the 'theme' being dark means that the default colors for text, axes, and gridlines will be light/off-white,
        #  which provides good contrast against the dark background;
        # and we set both the paper and plot background colors to transparent so that it can blend seamlessly 
        # when embedded in the portfolio site, which has its own dark background 
        #   (the portfolio site only has this dark background because the plots have transparent backgrounds while the 
        #   rest of the site design is light on dark (due to the fact that we specify the theme as "plotly_dark"))
        # we also set the height and 
        # width to the provided values (defaulting to 700x1200)
        base = dict(
            template="plotly_dark",
            paper_bgcolor='rgba(0,0,0,0)', # transparent background for embedding in the portfolio site, which has its own dark background; this way we get a seamless look without a black box around the plot
            plot_bgcolor='rgba(0,0,0,0)',
            height=height,
            width=width,
        )

        # if a play_label is provided, we add an "updatemenus" entry to the layout using the play_button method defined above; this will add a play button to the plot that triggers the animation when clicked
        if play_label:
            base['updatemenus'] = cls.play_button(
                label=play_label, y=play_y, duration=play_duration,
            )

        # finally, we update the figure's layout with our base settings, and then apply the standard axis styling to 
        # both x and y axes using the dark_axis_style method defined above; 
        # this ensures that the axes have the consistent off-white color and grid styling defined in that method
        base.update(layout_overrides) # this allows us to override any of the base layout settings by passing additional keyword arguments when calling apply_dark_theme
        fig.update_layout(**base) # apply the possibly overridden base layout settings to the figure

        # apply the standard dark axis styling to both x and y axes using the dark_axis_style method;
        #  this ensures that the axes have the consistent off-white color and grid styling defined in that method
        axis_style = cls.dark_axis_style()
        fig.update_xaxes(axis_style)
        fig.update_yaxes(axis_style)

        return fig

    # ── subplot axis helpers ─────────────────────────────────────────────

    @classmethod
    def fix_axes(cls, fig, df_norm, sector_map, dates, *, ncols=None, buffer=0.05):
        """Set x/y-axis ranges for a sector-per-subplot grid.

        Iterates over *sector_map* and computes y-axis limits from *df_norm*
        with a symmetric *buffer* (default 5%).  Sectors are laid out
        left-to-right, top-to-bottom across *ncols* columns (defaults to
        all sectors in a single row).

        The first column of each row gets a "Wealth Index" y-axis title.
        Subplot title annotations are re-coloured to ``OFF_WHITE``.
        """
        if ncols is None:
            ncols = len(sector_map)

        for i, (sector, tickers) in enumerate(sector_map.items()):
            row = i // ncols + 1
            col = i % ncols + 1

            # y-axis range from the data with a buffer on each side
            sector_data = df_norm[tickers]
            y_min = sector_data.min().min() * (1 - buffer)
            y_max = sector_data.max().max() * (1 + buffer)

            fig.update_xaxes(range=[dates[0], dates[-1]], row=row, col=col)
            y_kwargs = dict(range=[y_min, y_max], row=row, col=col)
            if col == 1:
                y_kwargs['title_text'] = "Wealth Index"
            fig.update_yaxes(**y_kwargs)

        # style subplot title annotations to match the dark theme
        for annotation in fig['layout']['annotations']:
            annotation['font'] = dict(color=cls.OFF_WHITE, size=16)

        return fig
