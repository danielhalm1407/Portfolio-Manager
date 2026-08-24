"""
Probe what long-dated daily history and volatility data are ACTUALLY obtainable, and from where.

Why this exists: plan 11-01 (milestone v0.4) has to reach back across several market regimes,
and the standing project decision after Phase 4 is that a published limit is not evidence — the
running system is. Phase 4 spent two plans on an executions ceiling taken from documentation
that turned out to be wrong. So nothing about pagination, window size or the IBKR/free-source
split is designed until this script has produced observations to design against.

Side-effecting by design (network + a written evidence file), hence a pipeline and not library
code (see src/CLAUDE.md). It answers, as recorded evidence:

  A. Is IBKR/TWS reachable from this machine at all, and how far back does it serve daily bars?
  B. Does the free source silently CHANGE GRANULARITY on a long request instead of refusing?
  C. What request window actually returns true daily bars, and where is the cap?
  D. What is the earliest available bar per symbol in the project's universe?
  E. For volatility: is an implied surface reachable, only a VIX-style index level, or neither?
  F. Does a BARE ticker resolve to the listing we actually hold, or to a same-named one elsewhere?

Usage:
    python src/pipelines/probe_history_sources.py                  # full probe
    python src/pipelines/probe_history_sources.py --skip-universe  # quick pass, no per-symbol sweep
"""

import argparse
import datetime as dt
import json
import pathlib
import socket
import sys
import time

# Repo root is two levels up from src/pipelines/, so data/ resolves regardless of the working
# directory the script is launched from. Same convention as cache_prices.py.
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

PROBE_DIR = REPO / "data" / "raw" / "probes"

import requests  # noqa: E402  (kept below the sys.path insert for symmetry with cfg)

from portutils.utils import config as cfg  # noqa: E402

# ---------------------------------------------------------------------------------------
# WHY PLAIN `requests` AND NOT yfinance's OWN CLIENT.
# yfinance >=0.2.5x fetches through curl_cffi, which deliberately IMPERSONATES a Chrome TLS
# fingerprint to get past Yahoo's bot screening. Any TLS-terminating middlebox (the agent
# proxy here, a corporate proxy elsewhere) sees a handshake that does not match the client
# it is proxying for and RESETS the connection — surfacing as
# `SSLError('curl: (35) Recv failure: Connection reset by peer')`, which reads like a
# certificate problem and is not one. The same endpoint over ordinary `requests` with a
# browser User-Agent returns 200. The probe therefore talks to the Yahoo chart API directly:
# it is the transport yfinance itself uses, minus the fingerprint that the proxy rejects.
# ---------------------------------------------------------------------------------------
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_OPTIONS = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
# The options endpoint (unlike the chart endpoint) is crumb-guarded: it answers 401
# {"code":"Unauthorized","description":"Invalid Crumb"} to a cookieless caller. The crumb is
# issued against a session cookie, so both calls below must share one Session.
YAHOO_COOKIE_SEED = "https://fc.yahoo.com"
YAHOO_CRUMB = "https://query1.finance.yahoo.com/v1/test/getcrumb"

# ---------------------------------------------------------------------------------------
# EXCHANGE-QUALIFIED SYMBOLS. Yahoo namespaces non-US listings with a market suffix, and a
# BARE ticker does not fail when the suffix is missing — it matches whatever else carries
# that ticker. Probed 2026-08-24: bare `AINF` returns the Defiance Inference AI Chip ETF
# (NasdaqGM, USD, first bar 2026-08-18); `AINF.L` returns the iShares AI Infrastructure
# UCITS ETF actually held in DUP102412. Both answer HTTP 200. A sweep that trusted the bare
# ticker would have silently loaded an unrelated instrument into the panel, so the London
# lines are qualified here and the resolved listing is recorded for every symbol.
# ---------------------------------------------------------------------------------------
LSE_SUFFIX_CANDIDATES = ("", ".L")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Yahoo rate-limits an unauthenticated caller with HTTP 429 fairly readily. Space the requests
# and retry a 429 rather than recording it as "no data" — a throttle mistaken for an absent
# series would poison every conclusion drawn from this probe.
REQUEST_SPACING_S = 1.2
MAX_429_RETRIES = 3


def _session():
    # One session for the whole probe so the cookie Yahoo sets on the first call is reused;
    # a fresh connection per request is what tends to draw the 429.
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json"})
    return s


def _crumb(sess):
    # Seed the session cookie, then exchange it for a crumb. Only the options endpoint needs
    # this; the chart endpoint is open. Returns None on failure rather than raising, so a
    # crumb problem degrades the volatility probe instead of aborting the whole run.
    try:
        sess.get(YAHOO_COOKIE_SEED, timeout=30)
        # Override Accept for this ONE call. The crumb endpoint answers text/plain and returns
        # HTTP 406 Not Acceptable to the session-wide `Accept: application/json` the chart
        # endpoint needs — which then surfaces downstream as a misleading 401 "Invalid Crumb"
        # on the options call, i.e. as "no implied vol available" rather than "bad header".
        r = sess.get(YAHOO_CRUMB, timeout=30, headers={"Accept": "*/*"})
        if r.status_code == 200 and r.text.strip():
            return r.text.strip()
        return None
    except Exception:
        return None


def _daily_params(start_year):
    # ALWAYS address a daily series by explicit epoch bounds, NEVER by `range=`. Probed
    # 2026-08-24: `range=max&interval=1d` on SPY answers HTTP 200 with 404 bars at a median
    # spacing of 31 days — Yahoo silently downgrades to MONTHLY and says so only in
    # `meta.dataGranularity`. The same request expressed as period1/period2 returns 8448 true
    # daily bars. This is the executions failure of Phase 4 in a different costume: a capped
    # or coarsened result that looks exactly like a complete one.
    return {
        "period1": int(dt.datetime(start_year, 1, 1, tzinfo=dt.timezone.utc).timestamp()),
        "period2": int(time.time()),
        "interval": "1d",
    }


def _get(sess, url, params, label):
    # Single choke point for every outbound call, so the retry/backoff policy and the
    # verbatim failure text are recorded identically for each probe step.
    for attempt in range(MAX_429_RETRIES + 1):
        time.sleep(REQUEST_SPACING_S)
        try:
            r = sess.get(url, params=params, timeout=60)
        except Exception as exc:                      # network-level failure, not an HTTP status
            return {"ok": False, "label": label, "error": f"{type(exc).__name__}: {exc}"[:400]}
        if r.status_code == 429 and attempt < MAX_429_RETRIES:
            # Exponential backoff on the throttle only. Any other status is returned as-is:
            # a 404 is a real answer about the symbol and must not be retried into a timeout.
            time.sleep(2 ** attempt * 2)
            continue
        if r.status_code != 200:
            return {"ok": False, "label": label, "status": r.status_code, "body": r.text[:300]}
        try:
            return {"ok": True, "label": label, "json": r.json()}
        except ValueError:
            return {"ok": False, "label": label, "error": "response was not JSON",
                    "body": r.text[:300]}
    return {"ok": False, "label": label, "error": "429 after retries"}


def _summarise_chart(payload):
    # Reduce a Yahoo chart response to the four facts the plan actually needs: how many bars
    # came back, the span they cover, and — the point of the exercise — what granularity Yahoo
    # DECIDED to serve, which is not necessarily the one that was asked for.
    result = (payload.get("json", {}).get("chart", {}).get("result") or [None])[0]
    if not result:
        err = payload.get("json", {}).get("chart", {}).get("error")
        return {"bars": 0, "error": str(err)[:300]}
    meta = result.get("meta", {})
    stamps = result.get("timestamp") or []
    out = {
        "bars": len(stamps),
        # The resolved listing, recorded for every series: a bare ticker can match a different
        # instrument on a different exchange in a different currency, and only these fields
        # make that visible. GBp here means the line is quoted in PENCE, not pounds.
        "resolved_symbol": meta.get("symbol"),
        "resolved_name": meta.get("longName") or meta.get("shortName"),
        "resolved_exchange": meta.get("fullExchangeName"),
        "resolved_currency": meta.get("currency"),
        # `dataGranularity` is Yahoo's own statement of what it served. When it disagrees with
        # the requested interval, that IS the silent-truncation finding — recorded, not inferred.
        "granularity_served": meta.get("dataGranularity"),
        "range_served": meta.get("range"),
        "has_adjclose": "adjclose" in (result.get("indicators") or {}),
    }
    if stamps:
        out["first_bar"] = dt.datetime.fromtimestamp(stamps[0], dt.timezone.utc).date().isoformat()
        out["last_bar"] = dt.datetime.fromtimestamp(stamps[-1], dt.timezone.utc).date().isoformat()
        # Median spacing in calendar days is the cross-check on `granularity_served`: a true
        # daily series over a trading calendar sits at 1 day, a monthly one near 30. Trusting
        # the metadata alone would miss a provider that mislabels its own output.
        gaps = sorted(stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1))
        if gaps:
            out["median_spacing_days"] = round(gaps[len(gaps) // 2] / 86400.0, 2)
            out["max_spacing_days"] = round(gaps[-1] / 86400.0, 2)
    return out


def probe_ibkr():
    # ---------------------------------------------------------------------------------
    # PROBE A — is TWS/Gateway reachable AT ALL from wherever this is running?
    # A TCP connect settles it without needing ibapi installed and without opening a client
    # session: if nothing is listening, no amount of request-shaping reaches IBKR history.
    # 7497 = TWS paper, 7496 = TWS live, 4002/4001 = IB Gateway paper/live.
    # ---------------------------------------------------------------------------------
    ports = {7497: "TWS paper", 7496: "TWS live", 4002: "Gateway paper", 4001: "Gateway live"}
    listening = {}
    for port, what in ports.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect(("127.0.0.1", port))
            listening[port] = {"open": True, "what": what}
        except Exception as exc:
            listening[port] = {"open": False, "what": what, "error": type(exc).__name__}
        finally:
            sock.close()
    reachable = any(v["open"] for v in listening.values())
    return {
        "reachable": reachable,
        "ports": listening,
        # Stated rather than implied: with no listener there is no IBKR evidence in this run,
        # and the plan's source-split decision must be taken knowing that half is missing.
        "note": ("A listener was found — the IBKR half of this probe can be run." if reachable
                 else "No IB listener on this host. The IBKR ceiling is UNMEASURED in this run; "
                      "it must be probed from the machine running TWS before any claim is made "
                      "about how far back IBKR reaches."),
    }


def probe_granularity(sess):
    # ---------------------------------------------------------------------------------
    # PROBE B/C — the silent-truncation question, which is the whole reason Task 1 exists.
    # Ask for daily bars three different ways over the same long span and compare what comes
    # back. If a request for '1d' over 'max' returns monthly bars with HTTP 200, then a
    # naive long-history fetch SUCCEEDS and is WRONG — the exact failure mode Phase 4 hit
    # with executions, where a capped result looked like a complete one.
    # ---------------------------------------------------------------------------------
    now = int(time.time())
    year = 365 * 86400
    probes = [
        # (label, params) — the same daily interval, addressed by range vs by explicit epochs.
        ("range=max&interval=1d", {"range": "max", "interval": "1d"}),
        ("period1=1990&interval=1d", {"period1": int(dt.datetime(1990, 1, 1).timestamp()),
                                      "period2": now, "interval": "1d"}),
        ("window=10y&interval=1d", {"period1": now - 10 * year, "period2": now, "interval": "1d"}),
        ("window=5y&interval=1d", {"period1": now - 5 * year, "period2": now, "interval": "1d"}),
        ("window=1y&interval=1d", {"period1": now - year, "period2": now, "interval": "1d"}),
    ]
    findings = {}
    for label, params in probes:
        payload = _get(sess, YAHOO_CHART.format(symbol="SPY"), {**params, "events": "div,split"},
                       label)
        findings[label] = (_summarise_chart(payload) if payload["ok"]
                           else {"bars": 0, "failed": payload})
    return findings


def probe_universe(sess, symbols):
    # ---------------------------------------------------------------------------------
    # PROBE D/F — earliest available bar per symbol, and WHICH LISTING answered.
    #
    # Earliest bar decides whether a symbol can appear in a 2008 or 2020 test at all: an ETF
    # launched in 2019 cannot be back-tested through the GFC no matter which provider serves
    # it, and a study that quietly starts each series wherever it happens to begin would
    # report a survivorship-flattered result.
    #
    # The listing matters just as much, and for a subtler reason — see LSE_SUFFIX_CANDIDATES
    # above. Each symbol is tried bare first, then with the London suffix, and BOTH answers
    # are kept when both resolve, because the disagreement between them is the evidence.
    # ---------------------------------------------------------------------------------
    out = {}
    for sym in symbols:
        candidates = {}
        for suffix in LSE_SUFFIX_CANDIDATES:
            qualified = f"{sym}{suffix}"
            payload = _get(sess, YAHOO_CHART.format(symbol=qualified),
                           _daily_params(1990), f"earliest:{qualified}")
            summary = (_summarise_chart(payload) if payload["ok"]
                       else {"bars": 0, "failed": payload})
            if summary.get("bars"):
                candidates[qualified] = summary
        if not candidates:
            out[sym] = {"resolved": None, "bars": 0,
                        "note": "No Yahoo listing answered for this ticker, bare or .L-suffixed."}
            continue
        # DO NOT GUESS which listing is the right one. A first pass ranked candidates by bar
        # count, and it was wrong three times out of three: it chose QDIV.L (iShares MSCI USA
        # Quality Dividend, LSE) over the Global X S&P 500 Quality Dividend ETF the YAML names,
        # and EQLT on Cboe US (an iShares EM Quality line) over the Xtrackers MSCI USA Quality
        # ESG ETF. Longer history is not the same claim as "the line we actually hold", and a
        # wrong pick here is invisible downstream — the panel just quietly describes a
        # different instrument. So the bare ticker is reported as the default resolution, every
        # candidate is listed, and anything with more than one match is flagged for a human.
        # The durable fix is an explicit per-symbol Yahoo mapping in config/asset_universe.yaml.
        default = sym if sym in candidates else next(iter(candidates))
        out[sym] = dict(candidates[default])
        out[sym]["resolved"] = default
        if len(candidates) > 1:
            out[sym]["needs_explicit_mapping"] = True
            out[sym]["candidates"] = {k: {"bars": v["bars"], "first_bar": v.get("first_bar"),
                                          "exchange": v.get("resolved_exchange"),
                                          "currency": v.get("resolved_currency"),
                                          "name": v.get("resolved_name")}
                                      for k, v in candidates.items()}
    return out


def probe_volatility(sess):
    # ---------------------------------------------------------------------------------
    # PROBE E — the question Phase 12 cannot start without: can the option overlay be priced
    # off a real implied-vol surface, or must it synthesise vol from an index level or from
    # realised vol? AC-5 requires this be answered with evidence, not assumption.
    #
    # Three things are checked, because they are three different capabilities:
    #   1. ^VIX daily HISTORY  — an implied-vol LEVEL through time, but only 30-day ATM S&P.
    #   2. a live option chain — strikes and per-contract impliedVolatility, but AS OF NOW.
    #   3. historical chains   — what a back-test actually needs, and what nobody free offers.
    # ---------------------------------------------------------------------------------
    findings = {}

    # Epoch-addressed, not `range=max` — the first run of this probe returned 440 MONTHLY
    # bars for the VIX under `range=max&interval=1d`, for exactly the reason _daily_params
    # documents. 1985 is simply earlier than the index exists; Yahoo clamps to its own start.
    vix = _get(sess, YAHOO_CHART.format(symbol="^VIX"), _daily_params(1985), "vix-history")
    findings["vix_index_history"] = (_summarise_chart(vix) if vix["ok"]
                                     else {"bars": 0, "failed": vix})

    # The chain is crumb-guarded; without it the endpoint answers 401 and the probe would
    # have wrongly concluded that no implied-vol data is reachable at all.
    crumb = _crumb(sess)
    chain = _get(sess, YAHOO_OPTIONS.format(symbol="SPY"),
                 {"crumb": crumb} if crumb else {}, "spy-option-chain")
    if chain["ok"]:
        result = (chain["json"].get("optionChain", {}).get("result") or [None])[0] or {}
        expirations = result.get("expirationDates") or []
        calls = ((result.get("options") or [{}])[0]).get("calls") or []
        with_iv = [c for c in calls if c.get("impliedVolatility") is not None]
        findings["live_option_chain"] = {
            "reachable": True,
            "expirations_offered": len(expirations),
            "first_expiry": (dt.datetime.fromtimestamp(expirations[0], dt.timezone.utc)
                             .date().isoformat() if expirations else None),
            "last_expiry": (dt.datetime.fromtimestamp(expirations[-1], dt.timezone.utc)
                            .date().isoformat() if expirations else None),
            "strikes_in_nearest_expiry": len(result.get("strikes") or []),
            "calls_in_nearest_expiry": len(calls),
            "contracts_carrying_implied_vol": len(with_iv),
            # A single sample is kept as proof the field is populated rather than present-and-null.
            "sample": ({k: with_iv[0].get(k) for k in
                        ("strike", "impliedVolatility", "lastPrice", "bid", "ask", "openInterest")}
                       if with_iv else None),
        }
    else:
        findings["live_option_chain"] = {"reachable": False, "crumb_obtained": bool(crumb),
                                         "failed": chain}

    # There is no request to make for #3 — the endpoint does not exist. Recording the absence
    # explicitly is the point: the decision it forces is what Phase 12 has to be designed around.
    findings["historical_option_chains"] = {
        "reachable": False,
        "why": "The Yahoo options endpoint serves only CURRENTLY LISTED contracts. It exposes no "
               "as-of date parameter, so a chain observed today cannot be re-observed for any "
               "past date. Historical implied-vol surfaces are a paid product (OptionMetrics, "
               "ORATS, CBOE DataShop) or an IBKR market-data subscription.",
    }
    return findings


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-universe", action="store_true",
                    help="skip the per-symbol earliest-bar sweep (one request per ticker)")
    ap.add_argument("--portfolio", default=None,
                    help="probe only the tickers of a named vector in config/asset_universe.yaml; "
                         "default is the whole universe catalogue")
    args = ap.parse_args()

    sess = _session()
    started = dt.datetime.now(dt.timezone.utc)

    # Symbols resolve from the YAML, never from a literal list here — the repo rule that one
    # file decides what the universe is applies to a probe as much as to a study.
    if args.portfolio:
        symbols = list(cfg.portfolio_weights(args.portfolio))
    else:
        symbols = sorted(cfg.ASSET_UNIVERSE.get("universe", {}))

    evidence = {
        "probe": "11-01 Task 1 — long-history source limits",
        "run_at_utc": started.isoformat(),
        "host_note": "Run location matters: the IBKR half is only meaningful where TWS runs.",
        "ibkr": probe_ibkr(),
        "free_source_granularity": probe_granularity(sess),
        "volatility": probe_volatility(sess),
    }
    if not args.skip_universe:
        evidence["free_source_earliest_bar"] = probe_universe(sess, symbols)

    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    out = PROBE_DIR / f"history_sources_{started.date().isoformat()}.json"
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    # ASCII only in console output — the Windows console on the dev machine is cp1252 and
    # raises UnicodeEncodeError on anything fancier (same reason as cache_prices.py).
    print(f"wrote {out}")
    print(f"IBKR reachable: {evidence['ibkr']['reachable']}")
    for label, f in evidence["free_source_granularity"].items():
        print(f"  {label:32s} bars={f.get('bars'):>6}  served={f.get('granularity_served')}  "
              f"first={f.get('first_bar')}  spacing_d={f.get('median_spacing_days')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
