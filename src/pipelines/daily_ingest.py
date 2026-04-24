"""Daily ingestion pipeline — fetches market data and commentary."""

import pandas as pd
import plotly.io as pio
from portutils.ingestion.ibkr_requests import get_equity_data
from portutils.viz.dash_timeseries_app import make_level_figure

pio.renderers.default = 'browser'  # open charts in browser, not inline IPython


def main():
    results = get_equity_data('AAPL')  # returns dict[symbol, DataFrame]
    df = results['AAPL']

    # make_level_figure requires a 'time' column of datetime64 dtype.
    # get_equity_data returns a string column named 'datetime' — rename and convert.
    df = df.rename(columns={'datetime': 'time'})
    df['time'] = pd.to_datetime(df['time'])

    fig = make_level_figure(df, cols_of_interest=['close'], figure_title='AAPL Close Price')
    fig.show()  # writes a temp HTML file and opens in the default browser — no Dash server needed


if __name__ == "__main__":
    main()
