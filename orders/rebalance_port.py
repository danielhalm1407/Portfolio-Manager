# %% 1. Import libraries

# 1. Import libraries
# Imports

import importlib
import os
import pathlib
import pandas as pd
from IPython.display import display
from portutils.ingestion import ibkr_requests
from portutils.ingestion.ibkr_requests import IBApp, get_account_data, OrderApp


# %% Reload custom package

# Reload custom package

# Reload module during development (re-import names after reload)
importlib.reload(ibkr_requests)
from portutils.ingestion.ibkr_requests import IBApp, get_account_data, OrderApp, connection_status

# %% 2. Initialise connection and order app

# 2. Initialise connection and order app


# initialise an instance of the IBApp class, which is our wrapper around the IBKR API.
ib_app = IBApp()
# call the start method on that instance of the IBApp, which starts the connection 
# and the background thread that listens for messages from IBKR.
ib_app.start(client_id = 125)

# %% 2.1 Input into our custom maed OrderApp class, which is a wrapper around the IBApp that provides some convenience methods for submitting orders.

# 2.1 Input into our custom maed OrderApp class, which is a wrapper around the IBApp that provides some convenience methods for submitting orders.
order_app = OrderApp(ib_app)

# %% 2.2 Check connection status

# 2.2 Check connection status
connection_status(ib_app)

# %% 3.1 Get portfolio info and value

# 3.1 Get portfolio info and value

# Define  function that can:
# get account data, figure out our portfolio value and how much we should have 
# in each position based on the weights, and then calculate how many units of
# each stock we should have based on the current price of each stock.
def get_portfolio_info_and_val(
                            app=ib_app,
                            account="DUP102412",# paper trading account
                            useful_cols = ['symbol', 'currency', 'position', 'marketPrice',
                            'marketValue', 'unrealizedPnL','realizedPnL'],
                            print_statements = True
                            ):
    # get the portfolio info for the account
    portfolio_info = ibkr_requests.get_account_updates(app=app, account=account)

    mkt_val = portfolio_info['marketValue'].sum()
    
    # select then only the useful columns from the portfolio info dataframe
    portfolio_info = portfolio_info[useful_cols]

    # get the total portfolio liquidation value from the account summary data, which is tagged as 'NetLiquidation'.
    account_df = ibkr_requests.get_account_data(app=app)
    port_val = account_df.loc[account_df['tag'] == 'NetLiquidation', 'value'].iloc[0]
    if print_statements:
        print(f"Total portfolio market value from positions: {mkt_val}")
        print(f"Total portfolio liquidation value from account summary: {port_val}")


    return portfolio_info, float(port_val), float(mkt_val)

# %% 3.2 Run function to get portfolio info and value

# 3.2 Run function to get portfolio info and value

port_df, port_val, mkt_val = get_portfolio_info_and_val()

# %% 3.3 Display portfolio info

# 3.3 Display portfolio info
port_df

# %% 3.4 Update porfolio value with 10% of the remaining cash in the account

# 3.4 Update porfolio value with 10% of the remaining cash in the account
# basically just return the cash value upon requesting it from IBKR
acct = get_account_data(app=ib_app)
cash = float(acct.loc[acct['tag'] == 'TotalCashValue', 'value'].iloc[0])

# %% 4.5 Display new portfolio value

# 4.5 Display new portfolio value
# This allows us to keep accumulating/depositing in and rebalancing
print(
      f"Cash available in the account: {cash}\n"
      f"Adding 10% of this to the portfolio value would add {cash * 0.1}\n"
      f"to the portfolio value of {port_val}, taking it to {port_val + cash * 0.1}, "
      f"or increasing total 'available weight' to  {(port_val + cash * 0.1) / (port_val):.8f}"
  )
# %% 4.1 Rebalancing

# 4.1 Rebalancing

# %% 4.1 Calculate new weights for the portfolio

# For the sake of illustration and experimentation, let's always assign target weights that are slightly different from the current weights,
# and also clear some predefined 'minimum turnover'

min_turnover = 0.02

# create some dummy weights just for experimentation
# weights = pd.Series(1/len(port_df), index=port_df.index)

# create weights closer to those that we really want
target_weights = pd.Series([0.45, 0.4, 0.05,0.05,0.05], index=port_df.index)



weights

# %% Calculate what position sizes to take based upon portfolio weights

# Calculate what position sizes to take based upon portfolio weights
def calculate_target_positions(
        port_df, 
        target_weights, 
        port_val, 
        min_turnover=0.02,
        prop_exposure=0.1 # just to control us having still a fair amount of cash left
        ):
    # calculate target value for each position based on the weights and total portfolio value
    port_df["target_weight"] = target_weights
    port_df["target_value"] = port_df["target_weight"] * port_val
    port_df["current_value"] = port_df["marketValue"]
    port_df["current_weight"] = port_df["current_value"] / port_val
    port_df["weight_diff"] = port_df["target_weight"] - port_df["current_weight"]
    port_df["clears_min_turnover"] = port_df["weight_diff"].abs() > min_turnover
    port_df["value_diff"] = port_df["target_value"] - port_df["current_value"]
    port_df["units_diff"] = round(port_df["value_diff"] / port_df["marketPrice"])
    return port_df

target_positions = calculate_target_positions(port_df, target_weights, mkt_val)

# %%

display(target_positions)



# %%

# Submit orders

# for each of the tickers, we have a units_diff, which is + or - depending
# on how many units we need to buy or sell to get to our target position.  We can loop through
# the tickers and submit market orders for the units_diff amount, with the appropriate buy/sell direction.

def submit_rebalance_orders(
    order_app,
    target_positions_df,
    *,
    symbol_col='symbol',
    currency_col='currency',
    units_col='units_diff',
    dry_run=True,
):
    """Submit a batch of market orders implied by a rebalance table.

    How order IDs are handled
    -------------------------
    You do NOT need to manually increment order IDs.

    Each call to `order_app.submit_market_order(...)` funnels into
    `OrderApp.place_order(...)`, which calls `ib_app.reserve_order_id()`.
    That method returns the next available orderId (from the last `nextValidId`
    callback) and increments it under a lock.

    Parameters
    ----------
    order_app : OrderApp
        An instance of `portutils.ingestion.ibkr_requests.OrderApp` that wraps
        a connected `IBApp`. This is the object that actually sends orders via
        `order_app.submit_market_order(...)`.
    target_positions_df : pd.DataFrame
        The rebalance table (typically the output of `calculate_target_positions`).
        Must contain at least:
        - a symbol column (default `symbol`)
        - a currency column (default `currency`, optional)
        - a units delta column (default `units_diff`), where:
            * positive = BUY that many units
            * negative = SELL that many units
            * zero     = no trade
    symbol_col : str
        Column name in `target_positions_df` containing the ticker/symbol.
    currency_col : str
        Column name in `target_positions_df` containing the currency. If
        missing/NaN for a row, we default to 'USD'.
    units_col : str
        Column name in `target_positions_df` containing the unit difference
        (signed). This is typically computed as value_diff / marketPrice.
    dry_run : bool
        When True, prints the intended orders but does not send them.
        This is a safety switch while iterating on the rebalance logic.
    """
    submitted = []

    # Iterate row-by-row so the action and quantity can be derived from the
    # signed `units_diff`. This keeps the logic explicit and debuggable.
    # an itterows object is two values, the index and the row, we can ignore 
    # the index with _ and just use the row
    for _, row in target_positions_df.iterrows():
        symbol = row.get(symbol_col)
        currency = row.get(currency_col, 'USD')
        units = row.get(units_col)

        # Guard against missing/NaN rows.
        # - If the rebalance logic produced NaNs (e.g., missing marketPrice)
        #   then we simply skip those lines here rather than submitting
        #   incorrect orders.
        if pd.isna(symbol) or pd.isna(units):
            continue

        # `units_diff` often comes out as numpy scalars (e.g., np.float64) or
        # as a float because of division/rounding earlier. We normalize to an
        # int so order quantities are valid.
        units_int = int(units)
        if units_int == 0:
            # Nothing to do for this symbol.
            continue

        # Convert the signed delta into (action, quantity).
        # - Positive means we need to increase the position => BUY
        # - Negative means we need to reduce the position   => SELL
        action = 'BUY' if units_int > 0 else 'SELL'
        quantity = abs(units_int)

        if dry_run:
            # Print exactly what would be sent. Keeping this as a single line
            # makes it easy to scan the proposed rebalance in the console.
            print(f"DRY RUN: {action} {quantity} {symbol} ({currency})")
            submitted.append({'symbol': symbol, 'currency': currency, 'action': action, 'quantity': quantity, 'orderId': None})
            continue

        # This call reserves and increments `orderId` internally.
        # Internally:
        # - submit_market_order() creates a contract object with the specifications of the asset we are buying/selling
        # e.g. symbol, currency, exchange, etc., and then calls place_order() with that contract object
        # - in turn, it calls market_order to OrderApp.place_order() which creates the specifications of that order,
        # under ibkr's 'order' object class, importantly specifying buy, market and of what quantity,
        # - in tuurn, it calls ou own OrderApp's place_order() method with the contract object and order object
        # - in turn, the place_order() method calls calls ib_app.reserve_order_id()
        # - in turn, reserve_order_id() returns the next valid orderId and increments it so that 
        # - the next time we send an order and do the same thing, we have a valid id to use
        # - in turn, once it has the valid id, it calls the IBAPi's built-in app.placeOrder() method, 
        # (or rather, Eclient.placeOrder(), sinc our app object will be an instance of ECLient)
        # which sends the order to IBKR with the contract, order specifications and orderId.
        # through the TWS gateway
        # this naturally returns an order_id that ibkr assigns to the order, 
        # This means you can submit multiple orders in a loop without manually
        # managing order IDs.
        order_id = order_app.submit_market_order(symbol, action, quantity, currency=currency)

        # confirms that the order was submitted with the given specifications and the order ID that
        #  IBKR assigned to it. This is useful for tracking and debugging.
        print(f"Submitted: orderId={order_id} {action} {quantity} {symbol} ({currency})")

        # we append this to a self-created list of submitted orders, which we can then convert to a 
        # dataframe at the end. This is useful for tracking what we intended to submit, especially
        #  in dry run mode where we don't have actual order IDs from IBKR.
        submitted.append({'symbol': symbol, 'currency': currency, 'action': action, 'quantity': quantity, 'orderId': order_id})

    return pd.DataFrame(submitted)

#%%

# Start with a dry run. 
orders_submitted = submit_rebalance_orders(order_app, target_positions, dry_run=True)
display(orders_submitted)

# %%

orders_submitted = submit_rebalance_orders(order_app, target_positions, dry_run=False)
display(orders_submitted)

# %%
ib_app.reserve_order_id()

# %%
# Start with dummy illustrative order for 10 units of Barclays


order_app.submit_market_order('5mvl', 'BUY', 10, currency='GBP')
# %%
# check if in the open orders
ibkr_requests.get_open_orders_data(ib_app)



# %%

# check order status
ib_app.orderStatus(orderid=1)
# %%
