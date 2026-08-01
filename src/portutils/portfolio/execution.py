"""
Execution backends: the thing that turns "I want to trade N units" into a ``Fill``.

Extracted from ``orders/kts.py``. The one structural change: the backend used to hold a
back-reference to the Tk app (``SimExecutionBackend(app)``) and reach into
``app.sim_realised`` before/after each fill to measure what it booked. It now holds a
``Book`` and uses the realised delta ``Book.apply_fill`` returns, plus an optional
``on_order`` callback for whoever wants to keep an order ledger. That removes the last
GUI dependency from the execution path.
"""

from .fills import Fill


# -----------------------------------------------------------------------------
# SIM EXECUTION BACKEND (plan §7.3, replay half only).
# Backend-agnostic design: the accounting engine (Book.apply_fill) only ever sees a
# Fill, never knows whether it came from IBKR or from here. This backend is the
# REPLAY/PAPER source: given a recommended trade it synthesizes a Fill at the bar's
# price (no network, no order id from TWS) and funnels it through apply_fill. The
# LiveExecutionBackend (not built here — out of scope for the simulated path) would
# instead call OrderApp.place_order and build Fills from the execDetails callbacks.
# Keeping them behind one `execute` signature means the GUI code that requests a trade
# is identical in both modes.
# -----------------------------------------------------------------------------
class SimExecutionBackend:
    def __init__(self, book, on_order=None, slippage_k=0.0):
        # The Book this backend books into — the single mutator of position/P&L. Replaces
        # the old back-reference to the owning KalmanTradingApp, so this class no longer
        # knows a GUI exists.
        self.book = book
        # Optional ledger hook, called as on_order(oid, fill, ctx, role, realised_delta)
        # right after the fill is booked. kts.py wires its _register_sim_order here; the
        # offline simulator passes None when it does not want an order ledger.
        self.on_order = on_order
        # Proportional slippage applied AGAINST the trader: buys fill above the mark,
        # sells below. Default 0.0 reproduces kts.py's frictionless fills exactly (which
        # is what the parity test pins). See portutils.analysis.strategies.calc_slippage
        # for the modelled version if you want a size-dependent estimate instead.
        self.slippage_k = float(slippage_k)
        # Local monotone order-id counter. In sim there is no TWS to hand out ids,
        # so we mint our own starting at 1 — purely a ledger key, never sent anywhere.
        self._next_id = 1

    def execute(self, side, qty, price, ts, *, symbol=None, ctx=None):
        # Synthesize an immediate fill at `price` (adjusted by slippage_k, which is 0 by
        # default — the §4c extension hook). `side` is +1/-1, `qty` is the absolute size.
        # `symbol` names the instrument (None → the Book's default_symbol, i.e. the
        # single-asset path). `ctx` is an optional ForecastView whose context we stamp
        # onto the fill (our extra fields).
        # Mint the next sim order id and advance the counter.
        oid = self._next_id
        self._next_id += 1
        # Cross the spread in the direction that hurts: +k on a buy, −k on a sell.
        fill_price = float(price) * (1.0 + self.slippage_k * (1 if int(side) > 0 else -1))
        # Build the Fill. source="sim" tags provenance so replay rows are
        # distinguishable from live ones later in the same state_df.
        fill = Fill(
            order_id=oid, ts=ts, side=int(side), qty=abs(float(qty)),
            price=fill_price, source="sim",
            symbol=(symbol or ""),
            # Stamp the forecast context (if a recommendation drove the trade) so the
            # ledger remembers WHY each fill happened — our edge over IBKR's bare prints.
            r_hat=(ctx.r_hat_h if ctx is not None else None),
            q_low=(ctx.q_low if ctx is not None else None),
            q_high=(ctx.q_high if ctx is not None else None),
            binding_cap=(ctx.binding_cap if ctx is not None else None),
        )
        # Snapshot the book BEFORE the fill so we can classify the order (did it grow the
        # exposure → OPEN, or reduce/flip it → CLOSE). The realised P&L this single fill
        # crystallised now comes back from apply_fill directly, so no before/after read.
        pos_before = self.book.position(symbol).qty
        # Route through the accounting engine — the ONLY place position/P&L move.
        # in other words, after running the line below, self.book.position(symbol).qty
        #  will be updated to reflect the fill
        realised_delta = self.book.apply_fill(fill)
        # OPEN if the trade pushed |exposure| in the same direction as (or from) flat;
        # CLOSE if it traded against an existing opposite position (reduced/flipped it).
        # pos_before == 0 → always opening; same sign as side → growing (OPEN); else CLOSE.
        if pos_before == 0 or (pos_before > 0) == (int(side) > 0):
            role = "OPEN"
        else:
            role = "CLOSE"
        # Hand the ledger owner everything it needs to build the Order record ABOVE the
        # fill (§7.5). In sim the whole order fills in one shot, so total_qty == qty_filled
        # and history is Submitted→Filled at the same timestamp. ctx (a ForecastView)
        # supplies the rationale block.
        if self.on_order is not None:
            # if the caller passed an on_order callback, call it with the order id, 
            # fill, context, role, and realised delta
            # the only reason we woudl pass an on_order callback is if we want to 
            # keep an order ledger, i.e, we want to match the order with the fill 
            # and keep track of the order history
            self.on_order(oid, fill, ctx, role, realised_delta)
        return fill
