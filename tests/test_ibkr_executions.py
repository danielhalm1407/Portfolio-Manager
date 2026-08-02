"""
Offline tests for the OUTBOUND half of ``get_executions_data`` — the ExecutionFilter it
builds and hands to ``reqExecutions``.

No TWS, no network, no sockets: the app is stubbed rather than the wire, following the
pattern ``StubApp`` already establishes in ``test_rebalance_live.py``. The stub holds the
same attributes ``IBApp`` exposes to the request helpers (``lock``, ``executions``,
``commissions``, ``executions_event``) and captures the filter object instead of
serialising it.

Why this file exists at all. The filter's ``time`` field crosses the wire as an opaque
string, so a malformed value is never rejected by the client — TWS is handed a start time it
cannot parse and answers with an ``execDetailsEnd`` and no fills. An empty frame from a bad
filter is byte-for-byte identical to an empty frame from a genuinely quiet day, so nothing
downstream can catch this. Only a test that reads the filter BEFORE it is sent can.

The format is worth pinning because the written sources disagree and only TWS settles it.
Probed against a live session, TWS's error 10314 documents two legal forms — "yyyymmdd
hh:mm:ss xx/xxxx" with an explicit timezone, or "yyyymmdd-hh:mm:ss" which IS UTC and takes
no suffix — and its error 2174 deprecates the third thing this code used to send, a space
with no timezone at all. These tests fix the UTC dash form so a well-meaning "fix" back to
the deprecated form has to argue with a red test rather than land silently.
"""

import re
import threading
from datetime import datetime, timedelta, timezone

from portutils.ingestion.ibkr_requests import get_executions_data


class StubExecApp:
    """Minimal stand-in for IBApp's executions surface. No socket, no threads.

    ``reqExecutions`` records the filter and immediately sets ``executions_event``, which is
    what the real ``execDetailsEnd`` callback does. The event must be set HERE rather than in
    the constructor because ``get_executions_data`` clears it before firing the request — a
    pre-set event would be wiped and the call would sit in ``_wait_for`` until it timed out.
    """

    def __init__(self):
        self.lock = threading.RLock()
        # The destination stores get_executions_data resets and then reads back.
        self.executions = []
        self.commissions = {}
        self.executions_event = threading.Event()
        # What we are actually here to inspect: the filter as the client would send it.
        self.captured_filter = None
        self._req_id = 0

    def next_req_id(self):
        self._req_id += 1
        return self._req_id

    def reqExecutions(self, req_id, exec_filter):
        self.captured_filter = exec_filter
        # Stand in for execDetailsEnd: TWS has said "that is all the fills there are".
        self.executions_event.set()


def test_execution_filter_time_uses_utc_dash_notation_with_no_suffix():
    """AC-1. The filter must carry ``yyyymmdd-HH:MM:SS`` — UTC dash notation, no suffix.

    Verified against live TWS, not inferred from documentation. Error 2174 deprecates a bare
    space form ("without explicit time zone ... will be removed in the next API release"),
    and error 10314 states the dash form "is in UTC" — so a trailing " UTC" is not a
    clarification but a parse error, which is exactly how a probe of that variant failed.
    """
    app = StubExecApp()
    get_executions_data(app, days_back=7, timeout=1)

    assert app.captured_filter is not None, "reqExecutions was never called"
    assert re.fullmatch(r"\d{8}-\d{2}:\d{2}:\d{2}", app.captured_filter.time), (
        f"filter time {app.captured_filter.time!r} is not UTC dash notation")


def test_execution_filter_time_anchors_at_local_midnight_converted_to_utc():
    """AC-1, second half. LOCAL midnight ``days_back`` days ago, expressed in UTC.

    Two things are pinned at once. Anchoring at midnight rather than "now minus N days" is
    what keeps a request made mid-afternoon from returning a ragged part-day at the far end.
    Converting rather than relabelling is what keeps the floor at the same INSTANT: stamping
    local midnight with a UTC label would silently move the boundary by the machine's offset,
    which off a UK summer clock is a whole hour of fills.
    """
    app = StubExecApp()
    get_executions_data(app, days_back=3, timeout=1)

    local_midnight = (datetime.now() - timedelta(days=3)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    expected = local_midnight.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S")
    assert app.captured_filter.time == expected


def test_days_back_only_lowers_the_floor_it_does_not_widen_the_window():
    """``days_back`` is a floor on the filter, never a reach back through TWS's ceiling.

    IB serves executions since midnight TODAY by default; going further needs the Trade Log
    "Show trades for ..." setting changed in the TWS GUI. All `days_back` can do is move the
    filter's lower bound, which is why a bigger value does not produce more fills. Pinning it
    here stops the parameter being re-read as a history dial — the misreading that made an
    empty frame look like a bug in this function.
    """
    near = StubExecApp()
    far = StubExecApp()
    get_executions_data(near, days_back=1, timeout=1)
    get_executions_data(far, days_back=30, timeout=1)

    # A larger days_back must produce an EARLIER floor, and nothing else about the request
    # may change — same opaque string field, same everything.
    assert far.captured_filter.time < near.captured_filter.time


def test_no_fills_returns_an_empty_frame_without_raising():
    """The quiet-week path. execDetailsEnd with zero execDetails is a legitimate answer.

    It must produce an empty DataFrame, not an exception — the caller distinguishes "no
    fills in the window TWS serves" from "the request failed" by the fact that this returned
    at all (``_wait_for`` raises TimeoutError when TWS never answers).
    """
    app = StubExecApp()
    fills = get_executions_data(app, days_back=7, timeout=1)

    assert fills.empty
