# `tests/` — what is pinned, and why

126 tests, **none of which need a TWS connection**. Everything that talks to a broker is stubbed, so
the whole suite runs offline in about twelve seconds.

```bash
~/miniconda3/envs/venv-stats/python.exe -m pytest tests/ -q
```

That environment is the one with `portutils` installed plus `pytest`.

## The modules

| file | tests | pins |
|---|---|---|
| [`test_rebalance_live.py`](test_rebalance_live.py) | 86 | the live rebalancer's decision logic: order sizing, the guards, currency conversion, order acknowledgement, verdicts, and the safety properties of the cell scripts |
| [`test_ibkr_sync.py`](test_ibkr_sync.py) | 18 | the broker→book bridge and the policy-anchored backcast |
| [`test_book.py`](test_book.py) | 12 | the average-cost accounting rules in `portutils.portfolio.book` |
| [`test_simulator.py`](test_simulator.py) | 5 | the rules + simulator that answer the hedge-sleeve question |
| [`test_kts_migration.py`](test_kts_migration.py) | 3 | `kts.py` still behaves identically after delegating its accounting to the library |
| [`test_book_parity.py`](test_book_parity.py) | 2 | **golden parity** — the extracted `Book` reproduces kts.py's numbers exactly |

## The ones that are not really unit tests

Three assertions in `test_rebalance_live.py` read the *source* of the cell scripts rather than
calling anything. They exist because the failure they prevent is not a wrong number, it is a real
order:

- **the committed debug script is disarmed** — `ARM_LIVE = False` in the file as committed;
- **the live submit is gated** — an `assert ARM_LIVE` precedes it, so "Run All" stops rather than
  trading, and no `dry_run=False` call appears above that gate;
- **no module under `src/` imports the cell scripts** — importing one transmits its module-level
  orders.

> **Expect one failure while you are working live.** Arming the debug harness (`ARM_LIVE = True`)
> makes `test_debug_cell_script_is_disarmed_and_gated` fail *by design*. It is a reminder that the
> file is armed, not a broken test. Set it back to `False` when you are done and the suite is green.

## Writing new ones

- **Stub the app, not the network.** The pattern is a small `StubApp` holding the same dicts
  `IBApp` fills from callbacks (`order_status`, `open_orders`, `req_error_log`, …). No sockets, no
  threads, no sleeping beyond a few milliseconds.
- **Use the real strings.** The rejection and warning texts in these tests are verbatim from live
  runs — the KID refusal, the "will not be placed at the exchange until 09:00:00 MET" warning. A
  test against an invented message proves the parser handles invented messages.
- **Say what breaks in the docstring**, not what the function does. Most tests here open with the
  failure they were written after; that is what makes them worth keeping when the code moves.
