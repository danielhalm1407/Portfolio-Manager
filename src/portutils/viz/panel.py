"""
Reusable data-loading and Plotly theming helpers for portfolio notebooks.

PanelBuilder centralises the CSV-loading, normalisation, and dark-theme
styling logic that was duplicated across every code cell in ff.ipynb.
"""

# %%
# file paths and reloading
import pathlib

# dataframes and maths
import numpy as np
import pandas as pd

# plotting
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# %%

# ── 1. Module-level config constants ────────────────────────────────────────────
# These are shared across notebooks — import them directly:
#   from portutils.viz.panel import SECTOR_MAP, SECTOR_COLORS, LINE_STYLES

SECTOR_MAP = {
    'Tech': ['AAPL', 'MSFT', 'AVGO'],
    'Healthcare': ['UNH', 'JNJ', 'AMGN'],
    'Consumer Staples': ['WMT', 'COST', 'PG'],
    'Energy': ['CVX', 'XOM', 'LNG'],
}

# -- 1.1 Colours and colour scales ---

SECTOR_COLORS = {
    'Tech': '#1f77b4',
    'Healthcare': '#2ca02c',
    'Consumer Staples': '#d62728',
    'Energy': '#ff7f0e',
}

SECTOR_COLOUR_SCALES = {
    'Tech': 'Blues',
    'Healthcare': 'Greens',
    'Consumer Staples': 'Reds',
    'Energy': 'Oranges',
}

EIGENVEC_STYLES = {
    'Eigenvalue':{'colour': '#00d1ff', 'opacity': 0.7, 'yaxis': 'y2'},
    'Cumulative Variance':{'colour': "#ffffff", 'size': 8, 'width': 2, 'yaxis': 'y2'}
}

LINE_STYLES = ['solid', 'solid', 'dash', 'dot']

OPACITIES = [1, 0.5 , 0.5 , 0.5]

WIDTHS = [3, 1, 1, 1]


# default data directory: three levels up from this file, then "data/raw/market"
_DEFAULT_DATA_DIR = (
    pathlib.Path(__file__).resolve().parent / ".." / ".." / ".." / "data" / "raw" / "market"
).resolve()



# %%
class PanelBuilder:
    """Load close-price CSVs and expose derived DataFrames + Plotly helpers.

    Parameters
    ----------
    tickers : list[str]
        Ticker symbols to load (expects ``{ticker}.csv`` in cwd).
    start_date : str
        ISO date string; rows before this date are dropped.
    """

    def __init__(
                self,
                df_all = None, # we allow an optional df_all to be passed in, but if it's None, we will load it from the
                tickers = [t for tickers in SECTOR_MAP.values() for t in tickers], # by default, we will load all the tickers in the sector map, but we allow an override to this if the user only wants to load a subset of tickers
                start_date='2024-01-01',
                data_dir=None,
                ncols = 2, # number of columns to have in a panle
                nrows = None, # number of rows to have in a panel; 
                # if None, will be calculated based on the number 
                # of sectors and ncols
                ):
        self.tickers = list(tickers)
        self.start_date = start_date
        self.data_dir = pathlib.Path(data_dir) if data_dir else _DEFAULT_DATA_DIR
        self.ncols = ncols
        self.nrows = nrows
        # the below attribute will be in the form of {'ticker': ticker, 'col': col_idx}
        # where col_idx is the number assigned to that particular sector or theme etc
        # it is only appended to once that ticker's trace has been added
        self.trace_configs = []

        # below, we initialise the datframe storing all the plotting data
        # by reading in each of the individual stock files and compacting them into one df
        # alternately, a user can input a readily made dataframe
        self.df_all = self._load() if df_all is None else df_all
        # as per the ._load() callm the index of df_all will be a DatetimeIndex 
        self.dates = self.df_all.index

        # initialise a df_norm, which is a normalised version of the main dataframe
        self.df_norm = self._normalise()

        # initialise a df_ret, which is the daily returns version of the main dataframe, 
        # which we can use for plotting returns and correlations
        self.df_ret = self._daily_returns()

        # initialise a df_plot, which is the dataframe we will actually use for plotting and animation;
        # and will often be the same as df_norm
        self.df_plot = self.df_norm.copy()

    # ── data loading ─────────────────────────────────────────────────────

    def _load(self, 
              debug_print = False # extra optionality to print loading in each and every dataframe
              ):
        df_all = pd.DataFrame()

        # loop through each ticker
        for ticker in self.tickers:
            filename = self.data_dir / f"{ticker}.csv"
            if debug_print:
                print(f"Loading {ticker} from {filename}...")
            try:
                df = pd.read_csv(filename)
                if debug_print:
                    print(f"Successfully loaded {ticker} with shape {df.shape}.")
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

    def _normalise(self, df = None, base=100):
        """Wealth index: ``(price / first_price) * base``."""
        # we allow an external df to be passed in, but if it's None,
        #  we default to using self.df_all; this way, we can reuse
        #  this method for any DataFrame with the same structure 
        # (e.g. returns) without having to duplicate the logic for normalizing it
        df = self.df_all if df is None else df
        return (df / df.iloc[0]) * base

    def _pct_returns(self, df=None):
        """Cumulative percentage returns from first observation."""
        df = self.df_all if df is None else df
        return ((df / df.iloc[0]) - 1) * 100

    def _daily_returns(self, df=None):
        """Simple daily percentage returns (first row dropped)."""
        df = self.df_all if df is None else df
        return df.pct_change().dropna()

    def _log_returns(self, df=None):
        """Daily log returns (first row dropped)."""
        df = self.df_all if df is None else df
        return np.log(df / df.shift(1)).dropna()

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

    # -- subplot panel construction ─────────────────────────────────────────────

    def make_panel_subplots(self, 
                            sector_map=SECTOR_MAP, 
                            horizontal_spacing=0.05,
                            # we allow an override to the default number of columns and rows, especially
                            # since these will probably most typically be defined here and not elsewhere
                            ncols = 2, 
                            nrows = None,
                            overall_per_sector = True, # whether we have an overall line per sector  
                            df_norm = None # we allow an optional df_norm to be passed in, but if it's None, we will calculate it from the df_all attribute of the class; this is useful for flexibility in case the user wants to use a different dataframe for calculating the overall sector composites
                            ):
        """Make a subplot grid with one sector per subplot."""
        # we create a 2x2 subplot figure with titles for each sector;
        #  the horizontal_spacing is set to 0.05 to provide a bit of separation between the columns,
        #  which helps visually distinguish the different sectors while still keeping them close
        #  enough for easy comparison
        # this returns a Figure object (just like go.Figure()) that we can then add traces to
        # and customize the layout of;
        n_sectors = len(sector_map)
        ncols = ncols if ncols is not None else self.ncols
        nrows = self.nrows or ((n_sectors - 1) // ncols + 1)

        # initialise empty set of titles
        titles = []

        # we also need to update the titles to show overall returns per group/sector if the user
        #  has chosen to include these overall lines; 
        if overall_per_sector:
            # Calculate Sector Composites (Equally Weighted)

            # check if a df_norm is provided, else, we can calculate it from the df_all attribute of the class;
            # we use a try-except block here to handle the case where df_norm is not yet 
            # calculated and is None, in which case we will calculate it from df_all; 
            # if df_norm is already calculated and stored as an attribute of the class, we can just use that;
            #  this way, we avoid unnecessary recalculations of the normalized dataframe if it's already available, while still allowing for flexibility in case the user wants to pass in a different dataframe for calculating the sector composites
            if df_norm is not None:                                                    
                pass  # use as-is
            elif hasattr(self, 'df_norm') and self.df_norm is not None:                
                df_norm = self.df_norm
            elif hasattr(self, 'df_all') and self.df_all is not None:                  
                df_norm = self._normalise(self.df_all)      
            else:
                raise ValueError("Please provide a normalised dataframe for overall returns per sector calculation.")
            
            # actually calculate the composite returns
            for sector, tickers in sector_map.items():
                df_norm[f"{sector}_Composite"] = df_norm[tickers].mean(axis=1)


            
            for sector in sector_map.keys():
                # Calculate Total Return: (End Value - 100)
                final_val = df_norm[f"{sector}_Composite"].iloc[-1]
                total_ret = final_val - 100
                titles.append(f"{sector} | {total_ret:+.1f}%")

        # depending on whether we are including overall lines per sector or not, we will have different 
        # titles for the subplots; 
        # if we are including overall lines, we want to include the total return for each sector
        #  in the title, which we can calculate from the normalized dataframe; 
        # if we are not including overall lines, we can just use the sector names as titles without the returns
        subplot_titles_vals = titles if titles else list(sector_map.keys())

        fig = make_subplots(
                            rows=nrows, 
                            cols=ncols, 
                            subplot_titles=subplot_titles_vals,  # automatically uses the sector names as titles for each subplot
                            horizontal_spacing = horizontal_spacing # controls the horizontal space between the columns of subplots; 
                            )
        return fig

    def add_sector_traces(self, 
                          fig, 
                          df=None, 
                          sector_map = SECTOR_MAP, # a dictionary which had a key
                          # for each sector, and each key's values is a list 
                          # of the tickers in that sector
                          colours = SECTOR_COLORS, # a dictionary mapping each group (usually sector)#
                          line_styles = LINE_STYLES,
                          opacities = OPACITIES,
                          widths = WIDTHS,
                          # to a specific color for plotting
                          normalise = True,
                          overall_per_sector = True # whether we have an overall line per sector                          
                          ):
        
        """Add traces for each ticker in *df* to the appropriate subplot in *fig*."""
        # get the dataframe used for plotting, and normalise if chosen to be the case,
        # or extract the already normalised dataframe
        if df is not None:
            df_plot = self._normalise(df) if normalise else df                      
        elif normalise:                                
            if hasattr(self, 'df_norm') and self.df_norm is not None:              
                df_plot = self.df_norm                 
            elif hasattr(self, 'df_all') and self.df_all is not None:              
                df_plot = self._normalise(self.df_all)
            else:
                raise ValueError("Please provide a dataframe for normalised/indexed traces.")
        else:
            if hasattr(self, 'df_all') and self.df_all is not None:
                df_plot = self.df_all
            else:
                raise ValueError("Please provide a dataframe for asset traces.")

        # extract the x axis dates which we should be using: usually the dates of the
        # dataframe stored as an attirbute of this class, but else,
        # we can just use the index of whatever plot we have passed in (hopefully it is a 
        # datetime index as well)
        dates = self.dates if self.dates is not None else df_plot.index

        # --- 4. Add Initial Traces ---
        # we iterate through each sector and its corresponding tickers in the sector map,
        #  and we also keep track of the column index (starting from 1) for plotting
        # note that the enumerate function allows us to loop over the items (key-value pairs)in the sector_map
        #  while also keeping track of an index (col_idx) that increments with each iteration; this is useful for assigning each sector to a specific column in the subplot grid
        for col_idx, (sector, tickers) in enumerate(sector_map.items(), start=1):
            # colurs will be specific to each sector/group, so we can begin defining them here
            colour = colours[sector]

            # if we want the overall line per sector, we need to calculate the composite returns for that sector,
            #  which is usually the mean of the normalized returns of the tickers in that sector; 
            # we can add this as a new column to the df_plot dataframe, which we can then use for plotting later;
            df_plot[f"{sector}_Composite"] = df_plot[tickers].mean(axis=1) if overall_per_sector else None
            
            
            # the below // syntax represents integer division, which means 
            # that we divide and then round down to the nearest whole number;
            #  this is important for determining the row index in the subplot grid
            #  based on the column index; 
            # since we want to fill the subplots left-to-right, top-to-bottom,
            #  we can calculate the row index by taking the column index minus 1
            #  (to convert from 1-based to 0-based indexing), dividing by the number
            #  of columns, and then adding 1 back to convert to 1-based indexing for Plotly's subplot referencing
            row_num_fig = (col_idx - 1) // self.ncols + 1

            # note that the % syntax here means modulus, which gives us the remainder
            #  after dividing (col_idx - 1) by self.ncols;
            # for example, if self.ncols is 2, then the column indices will cycle as follows:
            # col_idx: 1 -> col_idx - 1: 0 -> 0 % 2: 0 -> col_num_fig: 0 + 1: 1 (first column)
            # col_idx: 2 -> col_idx - 1: 1 -> 1 % 2: 1 -> col_num_fig: 1 + 1: 2 (second column)
            # col_idx: 3 -> col_idx - 1: 2 -> 2 % 2: 0 -> col_num_fig: 0 + 1: 1 (first column, next row)
            # col_idx: 4 -> col_idx - 1: 3 -> 3 % 2: 1 -> col_num_fig: 1 + 1: 2 (second column, next row)
            col_num_fig = ( (col_idx - 1) % self.ncols ) + 1

            for i, ticker in enumerate(tickers):
                # note that the % syntax here is used to cycle through the line styles
                # since we have more tickers than line styles, we want to repeat the line
                # styles in order;
                style = line_styles[(i+1) % len(line_styles)]
                opacity_val = opacities[(i+1) % len(opacities)]
                width_val  = widths[(i+1) % len(widths)]

                # print(row_num_fig, col_num_fig, ticker, style, opacity)
                # note, this only plots the very first trace, since later, we add every additional trace as part of the
                # animation functionality
                fig.add_trace(go.Scatter(
                    x=[dates[0]], 
                    y=[df_plot[ticker].iloc[0]],
                    mode='lines',
                    name=ticker,
                    opacity = opacity_val,
                    line=dict(color=colour, width=width_val, dash=style),
                    legendgroup=sector,
                    showlegend=True
                ), row=row_num_fig, col=col_num_fig)
                
                # we append the trace configuration for this ticker to the trace_configs list,
                # to confirm the fact that this trace has been added, but also to access 
                # later for updating each of the frames we create for animation
                self.trace_configs.append(
                        {
                        'name': ticker, 'row': row_num_fig, 'col': col_num_fig,
                        'colour': colour, 'style': style, 'opacity_val': opacity_val, 'width_val': width_val
                        }
                    )
    
            if overall_per_sector:
                style = line_styles[0] # the first style is reserved for the overall sector composite line
                opacity_val = opacities[0] # the first opacity is reserved for the overall sector composite line
                width_val = widths[0] # the first width is reserved for the overall sector composite line

                # B. Composite Index (Solid, Medium Thickness)
                fig.add_trace(go.Scatter(
                    x=[dates[0]], 
                    y=[df_plot[f"{sector}_Composite"].iloc[0]],
                    mode='lines',
                    name=f"{sector} Index",
                    opacity=opacity_val,
                    line=dict(color=colour, width=width_val, dash=style), # Thinner than before (was 5)
                    legendgroup=sector,
                    showlegend=True
                ), row=row_num_fig, col=col_num_fig)
                # similarly, append this to the configurations of the traces we wish to animate
                self.trace_configs.append(
                        {
                        'name': f"{sector}_Composite", 'row': row_num_fig, 'col': col_num_fig,
                        'colour': colour, 'style': style, 'opacity_val': opacity_val, 'width_val': width_val
                        }
                    )
            
            # reassign this back to df_plot so that we can use it for plotting and animation later
            # without having to recalculate it
            self.df_plot = df_plot

            # if we are normalising, the composite returns we added to df.plot should be reassigned back to df_norm
            if normalise:
                self.df_norm = df_plot

        return fig
            
    def add_sector_correlation_heatmaps(self,
                                        fig, 
                                        ret_df=None, 
                                        sector_map = SECTOR_MAP, # a dictionary which had a key
                                        # for each sector, and each key's values is a list 
                                        # of the tickers in that sector
                                        colours = SECTOR_COLORS, # a dictionary mapping each group (usually sector)#
                                        colour_scales = SECTOR_COLOUR_SCALES
                                                                 
                                        ):
        """Add traces for pairwise correlations between tickers in each sector."""
        # --- 1. Get, extract or calculate the dataframe for plotting ---
        # get the dataframe used for covariance matrix plotting, or
        # extract it, or calculate it as necessary, using the same logic as in the add_sector_traces method;
        if ret_df is not None:
            df_plot = ret_df                  
        else:                                
            if hasattr(self, 'df_ret') and self.df_ret is not None:              
                df_plot = self.df_ret                 
            elif hasattr(self, 'df_all') and self.df_all is not None:              
                df_plot = self._daily_returns(self.df_all)
            else:
                raise ValueError("Please provide a dataframe for normalised/indexed traces.")
        # --- 2. Add Initial Traces ---
        # we iterate through each sector and its corresponding tickers in the sector map,
        #  and we also keep track of the column index (starting from 1) for plotting
        # note that the enumerate function allows us to loop over the items (key-value pairs)in the sector_map
        #  while also keeping track of an index (col_idx) that increments with each iteration; this is useful for assigning each sector to a specific column in the subplot grid
        for col_idx, (sector, tickers) in enumerate(sector_map.items(), start=1):
            # colurs will be specific to each sector/group, so we can begin defining them here
            valid_tickers = [t for t in tickers if t in df_plot.columns]
            sector_rets = df_plot[valid_tickers]
            corr_matrix = sector_rets.corr()
            
            # the below // syntax represents integer division, which means 
            # that we divide and then round down to the nearest whole number;
            #  this is important for determining the row index in the subplot grid
            #  based on the column index; 
            # since we want to fill the subplots left-to-right, top-to-bottom,
            #  we can calculate the row index by taking the column index minus 1
            #  (to convert from 1-based to 0-based indexing), dividing by the number
            #  of columns, and then adding 1 back to convert to 1-based indexing for Plotly's subplot referencing
            row_num_fig = (col_idx - 1) // self.ncols + 1
            # note that the % syntax here means modulus, which gives us the remainder
            #  after dividing (col_idx - 1) by self.ncols;
            # for example, if self.ncols is 2, then the column indices will cycle as follows:
            # col_idx: 1 -> col_idx - 1: 0 -> 0 % 2: 0 -> col_num_fig: 0 + 1: 1 (first column)
            # col_idx: 2 -> col_idx - 1: 1 -> 1 % 2: 1 -> col_num_fig: 1 + 1: 2 (second column)
            # col_idx: 3 -> col_idx - 1: 2 -> 2 % 2: 0 -> col_num_fig: 0 + 1: 1 (first column, next row)
            # col_idx: 4 -> col_idx - 1: 3 -> 3 % 2: 1 -> col_num_fig: 1 + 1: 2 (second column, next row)
            col_num_fig = ( (col_idx - 1) % self.ncols ) + 1


            # main working horse: add a separate heatmap for each sector/group showing the correlation
            # between stocks within the group
            fig.add_trace(go.Heatmap(
                z=corr_matrix.values,
                x=corr_matrix.columns,
                y=corr_matrix.index,
                colorscale=colour_scales[sector],
                zmin=0, zmax=1,
                showscale=False,
                text=corr_matrix.values,
                texttemplate="%{z:.2f}",
            ), row=row_num_fig, col=col_num_fig)
        
        return fig

    def add_pca_waterfall(self,
                            pca_df=None, 
                            styles_dict = EIGENVEC_STYLES
                        ):
        """Add traces for PCA eigenvalues and cumulative variance."""   
        # --- 1. add a Scree Plot (Bar Chart of Eigenvalues)

        # initialise a figure
        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=pca_df["PC"],
            y=pca_df["eigenvalue"],
            name='Eigenvalue',
            marker_color=styles_dict['Eigenvalue']['colour'],
            opacity=styles_dict['Eigenvalue']['opacity'],
        ))

        # B. Cumulative Variance (Line Chart)
        # Map to secondary Y-axis? Or just show % on hover.
        # Standard scree plot plots Eigenvalues. 
        # Sometimes people plot Explained Variance % instead.
        # Let's plot Explained Variance % (Scree) and Cumulative % (Line).
        # The user asked for "Spectral Decay", which usually refers to the Eigenvalues themselves.
        # I will stick to Eigenvalues on the left, but maybe add text for %.

        # Let's do a Dual Axis: Left=Eigenvalue, Right=Cumulative %
        colour_val = styles_dict['Cumulative Variance']['colour']
        size_val = styles_dict['Cumulative Variance']['size'] 
        width_val = styles_dict['Cumulative Variance']['width']
        yaxis_val = styles_dict.get('Cumulative Variance', {}).get('yaxis',  "y2") # default to "y2" if not specified


        fig.add_trace(go.Scatter(
            x=pca_df["PC"],
            y=pca_df["cumulative_variance_ratio"],
            name='Cumulative Variance',
            mode='lines+markers',
            marker=dict(color=colour_val, size=size_val),
            line=dict(color=colour_val, width=width_val),
            yaxis=yaxis_val
        ))

        # Update layout for dual y-axes
        fig = self.apply_dark_theme(
            fig = fig,
            second_axis = True,
            title=dict(
                    text="<b>Spectral Decomposition of Portfolio Returns</b><br><sup>Eigenvalue Decay (Scree Plot)</sup>",
                    font=dict(color="#e0e0e0", size=22),
                    x=0.5, xanchor='center'
                ),
            height = 600,
            margin=dict(t=100, b=50, r=50),
            showlegend=True,
            legend=dict(
                orientation='h',
                y=1.02, x=0.5, xanchor='center'
            ),
            
            # Axes
            xaxis=dict(
                title="Principal Component (Mode)",
                showgrid=False
            ),
            yaxis=dict(
                title="Eigenvalue Magnitude",
            ),
            yaxis2=dict(
                title="Cumulative Explained Variance",
                overlaying='y',# 
                side='right',
                range=[0, 1.1],
                tickformat='.0%',
                showgrid=False,
                tickfont=dict(color='white'),
                title_font=dict(color='white')
            )

        )

        return fig
                            

        
    
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

    def add_in_frames(
                        self, # the instance of the class is useful as it will track which configs we have plotted
                        # since we only want to add frames for the configs (sector/group ticker pairs) that we have already plotted
                        fig,
                        df=None,
                        trace_configs = None, # we allow an optional trace_configs to be passed in, but if it's None, we will use the trace_configs attribute of the class, which should have been populated as we added traces to the figure; this is important because we only want to add frames for the traces that we have actually plotted and styled, and this way, we can ensure that the frames we create for animation are consistent with the initial traces we added to the figure
                        # to a specific color for plotting
                        normalise = True,
                        overall_per_sector = True, # whether we have an overall line per sector  
                        num_frames=60
                    ):
        """
        Add frames for animating traces from *df*.
        Frames are sampled evenly across the date range using ``animation_indices()``.
        """
        # get the dataframe used for plotting, and normalise if chosen to be the case,
        # or extract the already normalised dataframe
        if df is not None:
            df_plot = self._normalise(df) if normalise else df                      
        elif normalise:                                
            if hasattr(self, 'df_norm') and self.df_norm is not None:              
                df_plot = self.df_norm                 
            elif hasattr(self, 'df_all') and self.df_all is not None:              
                df_plot = self._normalise(self.df_all)
            else:
                raise ValueError("Please provide a dataframe for normalised/indexed traces.")
        else:
            if hasattr(self, 'df_all') and self.df_all is not None:
                df_plot = self.df_all
            else:
                raise ValueError("Please provide a dataframe for asset traces.")


        # similarly, extract the dates for the x axis: usually from the class attribute,
        #  but else from the index of the dataframe we are plotting if different
        dates = self.dates if self.dates is not None else df_plot.index

        # we can call on the static method we defined above to get the
        # indices of the dates that we want to use as frames for the animation; 
        indices = self.animation_indices(dates, num_frames)

        # before we start creating the frames, we need to check if we already have a set of trace configurations
        # passed in as an argument, or if we should use the trace_configs attribute of the class,
        # and if neither exist, we should raise a valueerror to alert the user that we don't have any
        #  trace configurations to work with for creating the frames;
        trace_configs = trace_configs if trace_configs is not None else self.trace_configs
        if not trace_configs or trace_configs == []:
            raise ValueError("No trace configurations found for animation frames.")
        
        # initialise empty frames object which we can use to update the figure with
        #  each frame's data; each frame will represent a snapshot of the plot at 
        # a particular point in time,
        frames = []
        # for each of the indices for which we want a 'frame' (just like in a film reel)
        # we plot the y series data up to that point
        for idx in indices:
            # for each animation frame/snapshot in time, we start with an empty set of
            #  frames data
            frame_data = []
            # we only start filling this up for each frame by iterating over each configuration
            # (group/sector -ticker pair) that we have already plotted
            for config in self.trace_configs:
                name = config['name']
                row_idx = config['row']
                col_idx = config['col']

                # since we had stored all styling attributes as we initialised the traces,
                # we can now easily access the colour, style, and opacity for each trace
                #  from the config dictionary
                colour = config['colour']
                style = config['style']
                opacity_val = config['opacity_val']
                width_val = config['width_val']

                # print(name, row_idx, col_idx, style)
                # create a scatter trace for this ticker using the data up to the current index (idx);
                frame_data.append(go.Scatter(
                                    # note that each frame will contain the traces for all tickers up to that point in time,
                                    # so when the animation plays, it will show all the lines for all the tickers gradually tracing out
                                    x=dates[:idx],
                                    y=df_plot[name].iloc[:idx],
                                    mode='lines',
                                    # usually the below will automatically trigger to be 
                                    # the right colour withd and style of the trace we already plotted
                                    opacity=opacity_val,
                                    line=dict(color=colour, width=width_val, dash=style),
                                    showlegend=False
                                    ))
            # once we are done with appending the data of all the traces for that particular frame
            # we append it to the frames list, as a go.Frame object with the data for that frame,
            #  and we can also give it a name which is just the index of the date it represents
            #  (this will be useful for debugging and understanding which frame corresponds to which date)
            frames.append(go.Frame(data=frame_data, name = str(idx)))

        # finally, we need to assign these frames that we have created bag to the figure so that
        # they can be animated within the figure
        fig.frames = frames

        return fig

    # ── Plotly theming ───────────────────────────────────────────────────

    OFF_WHITE = "#e0e0e0"
    OFF_BLACK = "#222222"

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
    
    # ── subplot axis helpers ─────────────────────────────────────────────

    @classmethod
    def fix_axes(cls,
                 fig,
                 df=None,
                 sector_map=SECTOR_MAP,
                 dates=None,
                 *,
                 ncols=None,
                 buffer=0.05,
                 normalise=True,
                 y_label="Wealth Index",
                 square_cells=False,
                 ):
        """Set x/y-axis ranges for a sector-per-subplot grid.

        Iterates over *sector_map* and computes y-axis limits from *df_norm*
        with a symmetric *buffer* (default 5%).  Sectors are laid out
        left-to-right, top-to-bottom across *ncols* columns (defaults to
        all sectors in a single row).

        The first column of each row gets a "Wealth Index" y-axis title.
        Subplot title annotations are re-coloured to ``OFF_WHITE``.

        square_cells=True: skips range logic and instead anchors each subplot's
        y-axis to its own x-axis so heatmap cells render as squares. Use for
        correlation matrix grids.
        """
        if ncols is None:
            ncols = len(sector_map) // 2 + 1 # default to 2 columns and however many rows needed

        if square_cells:
            # each subplot has its own x-axis ref: x, x2, x3, ...
            for i in range(len(sector_map)):
                axis_idx = "" if i == 0 else str(i + 1)
                fig_row_num = ((i) // ncols) + 1
                fig_col_num = (i % ncols) + 1
                # print(f"dividing subplot index {i+1} by ncols {ncols} gives us {(i+1) // ncols}, which plus 1 gives us the row number {fig_row_num}")
                
                # print(f"Anchoring y-axis of subplot ({fig_row_num}, {fig_col_num}) to x-axis '{axis_idx}' for square cells.")
                fig.update_xaxes(constrain="domain", row=fig_row_num, col=fig_col_num)
                fig.update_yaxes(
                    scaleanchor=f"x{axis_idx}",
                    scaleratio=1,
                    constrain="domain",
                    row=fig_row_num, col=fig_col_num
                )
        else:
            if normalise:
                plot_df = cls._normalise(df)
            else:
                plot_df = df

            if dates is None:
                dates = plot_df.index

            for i, (sector, tickers) in enumerate(sector_map.items()):
                row = i // ncols + 1
                col = i % ncols + 1

                sector_data = plot_df[tickers]
                y_min = sector_data.min().min() * (1 - buffer)
                y_max = sector_data.max().max() * (1 + buffer)

                fig.update_xaxes(range=[dates[0], dates[-1]], row=row, col=col)
                y_kwargs = dict(range=[y_min, y_max], row=row, col=col)
                if col == 1:
                    y_kwargs['title_text'] = y_label
                fig.update_yaxes(**y_kwargs)

        for annotation in fig['layout']['annotations']:
            annotation['font'] = dict(color=cls.OFF_WHITE, size=16)

        return fig




    @classmethod
    def apply_dark_theme(cls, # the class itself (inherits methods but not attributes of an instance)
                         fig, # important: usually a now enriched figure object that we have created
                         height=700, # increased height to accomodate the larger bottom margin
                         width=1200,
                         # animation-label-related-parrameters
                         play_label=None, play_y=-0.25, play_duration=20,
                         second_axis = False, # whether to apply the standard axis styling to a secondary y-axis (useful for the PCA waterfall plot where we have a secondary y axis for the cumulative variance)

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
        # ---- 1. Base Layout ----
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

        # The below allows us to override any of the base layout settings by passing additional keyword 
        # arguments when calling apply_dark_theme.
        # note that base is a dictionary and using the .attribute() method allows us to update it with
        # the key-value pairs from layout_overrides, (e.g., if we have showlegend=True, the base dict 
        # will now have 'showlegend': True, added onto the end
        # so if we pass in something like title="My Title" when calling apply_dark_theme, it will add 'title': 'My Title' to the base dictionary, which will then be applied to the figure's layout; this way, we can easily customize the layout of the figure while still applying the standard dark theme settings as a base
        base.update(layout_overrides) 

        # in the belwo syntax, we apply the possibly overridden base layout settings to the figure
        # even though base is a dict, and fig_update_layout() accepts keyword arguments, the ** syntax allows us
        #  to unpack the key-value pairs in the base dictionary and pass them as keyword arguments to 
        # fig.update_layout(); 
        fig.update_layout(**base) 

        # ----- 2. Axis Styling ----

        # apply the standard dark axis styling to both x and y axes using the dark_axis_style method;
        #  this ensures that the axes have the consistent off-white color and grid styling defined in that method
        axis_style = cls.dark_axis_style()
        fig.update_xaxes(axis_style)
        fig.update_yaxes(axis_style, overwrite = False)

        if second_axis:
            merged = {**axis_style, **layout_overrides.get('yaxis2', {})}
            fig.update_layout(yaxis2=merged)


        return fig

    