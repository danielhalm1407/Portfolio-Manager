"""
Execution records: the atomic ``Fill`` print and the ``Order`` ledger entry above it.

Moved out of ``orders/kts.py`` (plan §7.3 / §7.5) so the accounting stack is importable
without spawning a Tk window. The dataclasses are UNCHANGED from their kts.py originals
except for one addition: ``Fill.symbol``, which lets a single ``Book`` route fills across
several instruments (the single-symbol kts.py app leaves it at its default and the
``Book``'s ``default_symbol`` absorbs it).

Nothing here has side effects on import — pure dataclasses plus one timestamp helper.
"""

from dataclasses import dataclass, field
from datetime import datetime


# -----------------------------------------------------------------------------
# FILL — one execution print, the atomic unit the accounting engine consumes
# (plan §7.3). Deliberately shaped to be a SUPERSET of what IBKR's execDetails
# returns (order_id/qty/price/side/time/perm_id) PLUS our own extras (source +
# the forecast context at decision time) so the SAME object works for a simulated
# replay fill and, later, a live IBKR fill — apply_fill never has to branch on which.
# -----------------------------------------------------------------------------
@dataclass
class Fill:
    order_id: int           # which order this fill belongs to (our counter in sim)
    ts: datetime            # fill timestamp (bar timestamp in replay)
    side: int               # +1 = BUY, -1 = SELL  (signed so apply_fill is direction-agnostic)
    qty: float              # ABSOLUTE units filled (magnitude only; sign carried by `side`)
    price: float            # fill price
    perm_id: int | None = None          # IBKR permId — None in pure sim
    source: str = "sim"                 # "sim" (replay) vs "live" (real IBKR fill) — our provenance field
    # WHICH instrument this fill belongs to. Empty string means "the book's default
    # symbol" — that is how the single-symbol kts.py path keeps working untouched while a
    # multi-asset scenario book (hedge sleeve + growth basket) routes on a real ticker.
    symbol: str = ""
    # forecast context stamped at decision time — OUR extra fields beyond IBKR (§7.3).
    r_hat: float | None = None
    q_low: float | None = None
    q_high: float | None = None
    # which risk cap (if any) was active when this trade was recommended: "none"/"idio_floor"/"factor"/"portfolio"
    binding_cap: str | None = None


# -----------------------------------------------------------------------------
# ORDER — the trade ledger record that sits ABOVE Fill (plan §7.5 / GAP A).
# Where Fill is one atomic execution print, Order is the whole order's life: its
# metadata, the recommendation rationale that justified it, its current lifecycle
# state, and an append-only event history. It is shaped to serialize EXACTLY to the
# nested structure of orders/dummy_orders.json via to_dict(), so the same record
# format works for a simulated replay order and (later) a real IBKR order.
#
# In SIM/replay we assume a single fill that fills the whole order immediately, so
# `history` collapses to a Submitted→Filled pair at one timestamp and qty_filled ==
# total_qty. The lifecycle plumbing (an order that COULD sit unfilled / partial)
# still exists unchanged for the live path to populate from orderStatus/execDetails.
#
# The dataclass holds FLAT fields (cheap to build at the execute() choke point); the
# dummy_orders.json nesting (metadata{contract, rationale}, state, history) is
# assembled only on demand in to_dict() at serialization time.
# -----------------------------------------------------------------------------
@dataclass
class Order:
    # ---- metadata (identity + the contract + how the order was specified) ----
    order_id: int                       # our ledger key (the SimExecutionBackend counter in sim)
    submitted_at: datetime              # when the order was created/submitted (bar ts in replay)
    symbol: str                         # contract symbol, e.g. "BTC" / "SMH"
    sec_type: str                       # "CRYPTO" / "STK" / "CASH"
    exchange: str                       # routing venue, e.g. "PAXOS" / "SMART"
    currency: str                       # quote currency, e.g. "USD"
    action: str                         # "BUY" / "SELL" (human-facing; sign carried by the fill side)
    order_type: str                     # "MKT" / "LMT" — sim fills are immediate market-style
    total_qty: float                    # absolute units the order is for (magnitude)
    market_price_at_submit: float       # mark/quote at submit time — the price the sim fills at
    # rationale (the ForecastView context that justified the trade) — keyed EXACTLY as the
    # `rationale` block in dummy_orders.json so to_dict() round-trips to that schema.
    # in the syntax, 'field' is a function imported from dataclasses that defines a field
    #  with a default value (in this case, an empty dict as per default_factory = dict).
    rationale: dict = field(default_factory=dict)
    # optional IBKR-side identifiers — None in pure sim (no TWS to allocate them).
    perm_id: int | None = None          # IBKR permId (stable across the order's life) — None in sim
    client_id: int | None = None        # the API client id that placed it — None/our GUI id in sim
    parent_id: int | None = None        # bracket/parent linkage — None for flat sim orders
    con_id: int | None = None           # IBKR contract id — None/0 in sim
    limit_price: float | None = None    # limit price for LMT orders — None for market-style sim fills
    tif: str = "DAY"                    # time-in-force — DAY by default
    strategy_tag: str = "ou_mean_revert_v1"  # which strategy emitted this order (provenance)
    # ---- mutable lifecycle state (overwritten as the order progresses; one shot in sim) ----
    state: dict = field(default_factory=dict)
    # ---- append-only event log (Submitted → Filled in sim, richer for live) ----
    history: list = field(default_factory=list)

    def to_dict(self):
        # Assemble the dummy_orders.json nesting from the flat fields. Called only at
        # serialization time (export_orders_json), never on the hot path.
        return {
            "metadata": {
                "order_id": self.order_id,
                "perm_id": self.perm_id,
                "client_id": self.client_id,
                "parent_id": self.parent_id,
                # ISO timestamps so the JSON matches dummy_orders.json's string form.
                "submitted_at": _iso(self.submitted_at),
                "contract": {
                    "symbol": self.symbol,
                    "sec_type": self.sec_type,
                    "exchange": self.exchange,
                    "currency": self.currency,
                    "con_id": self.con_id,
                },
                "action": self.action,
                "order_type": self.order_type,
                "total_qty": self.total_qty,
                "limit_price": self.limit_price,
                "tif": self.tif,
                "market_price_at_submit": self.market_price_at_submit,
                "strategy_tag": self.strategy_tag,
                "rationale": self.rationale,
            },
            "state": self.state,
            "history": self.history,
        }


def _iso(ts):
    # Normalize a timestamp to an ISO-8601 string for JSON. Accepts datetime (most cases)
    # or anything already string-like; None passes through so optional fields stay null.
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.isoformat()
    # pandas Timestamp / numpy datetime / str all have a sane str() fallback.
    return str(ts)
