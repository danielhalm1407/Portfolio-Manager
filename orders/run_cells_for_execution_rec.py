"""Run selected `# %%` cells of a cell script in the TERMINAL, in one shared namespace.

WHAT THIS IS FOR
================
Reconciling executions against TWS — "cell 15 says 0 fills, but the Trade Log shows a week
of them, why?". Hence the name: the default run is cells **1, 2, 3, 15** of
``orders/rebalance_live_debug.py`` — import, settings, connect, and *what actually filled*.

Why it exists at all, given the cell file already runs in a notebook:

- Running the WHOLE file in a terminal is not an option. It would walk straight through the
  order-transmitting cells, and ``ARM_LIVE`` is sometimes left True.
- Running it in a notebook HIDES what you need. IB's callbacks and its ``IB Error ...``
  messages arrive on the reader thread and land on stdout; a notebook cell shows you its own
  return value and swallows the traffic that arrived while it was working. When a request
  comes back empty, that traffic IS the diagnosis — error 2174 (deprecated implied timezone)
  and 10314 (bad date format) were both found this way, and neither is visible in a notebook.

So this runner takes the middle path: exec only the cells you name, into one shared globals
dict, unbuffered, so the callbacks stream past as they land.

It REIMPLEMENTS NOTHING. The cells it runs are the cell file's own source text, read from
disk at run time — the same rule ``rebalance_live_debug.py`` itself follows with respect to
``pipelines/rebalance_live.py``. If a cell changes, this picks up the change with no edit.

USAGE
=====
    python orders/run_cells_for_execution_rec.py              # the default: cells 1 2 3 15
    python orders/run_cells_for_execution_rec.py 1 2 3 8      # any cells, in any order
    python orders/run_cells_for_execution_rec.py --list       # show every cell and its label
    python orders/run_cells_for_execution_rec.py --file other_cell_script.py 1 2

Run it with ``python -u`` if your shell buffers stdout; the script already flushes its own
banners, but ``-u`` also unbuffers whatever the cells print.

THE SAFETY GATE
===============
Cells 11 and 11c transmit real orders, and ``ARM_LIVE = True`` has been left committed in the
cell file before now. A mistyped cell number must not be a trade, so those two are REFUSED
unless ``--allow-sending`` is passed. They are identified by their `# %%` LABEL, not by
position, so inserting a cell above them cannot slide the guard onto the wrong code.

This file is a runner, not a library. It has no importable side effects, but there is nothing
in it worth importing either.
"""

import argparse
import pathlib
import re
import sys
import traceback

# Resolved from THIS file's location rather than a hardcoded path, so the script works from
# any working directory and on any clone of the repo.
HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CELL_FILE = HERE / "rebalance_live_debug.py"

# The execution-reconciliation run: import, settings, connect, "what actually filled".
# Cells 4-14 are the order-building path and are not needed to ask TWS what it filled.
DEFAULT_CELLS = ["1", "2", "3", "15"]

# Cells that can put an order on the wire. Refused unless --allow-sending.
SENDING_CELLS = {"11", "11c"}
# Cells that change broker state without placing a new order. Allowed, but announced, because
# "cancel everything" is not something to run by accident either.
MUTATING_CELLS = {"14"}

CELL_RE = re.compile(r"^# %%\s*(.*)$")


def parse_cells(path):
    """Split a cell file into a list of {label, title, src} dicts.

    The label is the token before the first '.' on the `# %%` line — "1", "11b", "15" — which
    is how the file names its own cells and how a human refers to them. Unnumbered cells (the
    "Reload custom packages" one) get a label of None: they are listed but cannot be selected,
    which is deliberate, because that cell's `importlib.reload` is only meaningful mid-session.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    cells, current = [], None

    for line in lines:
        match = CELL_RE.match(line)
        if match:
            # A new `# %%` closes the cell that was being accumulated.
            if current is not None:
                cells.append(current)
            header = match.group(1).strip()
            label = header.split(".")[0].strip() if "." in header else None
            current = {"label": label, "title": header, "src": []}
        elif current is not None:
            current["src"].append(line)

    # The last cell has no `# %%` after it to close it.
    if current is not None:
        cells.append(current)
    return cells


def list_cells(path, cells):
    """Print every cell with its label, flagging the dangerous ones."""
    print(f"cells in {path.name}:\n")
    for cell in cells:
        if cell["label"] in SENDING_CELLS:
            mark = "   <-- SENDS ORDERS"
        elif cell["label"] in MUTATING_CELLS:
            mark = "   <-- changes broker state"
        else:
            mark = ""
        print(f"  {cell['label'] or '-':<5} {cell['title']}{mark}")
    print(f"\ndefault run: {' '.join(DEFAULT_CELLS)}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run selected cells of a `# %%` cell script in the terminal.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cells", nargs="*", default=None,
                        help=f"cell labels to run, in order. Default: {' '.join(DEFAULT_CELLS)}")
    parser.add_argument("--file", default=str(DEFAULT_CELL_FILE),
                        help="cell script to run cells from")
    parser.add_argument("--list", action="store_true", help="list the cells and exit")
    parser.add_argument("--allow-sending", action="store_true",
                        help="permit the order-transmitting cells (11, 11c). Think first.")
    args = parser.parse_args(argv)

    path = pathlib.Path(args.file).resolve()
    if not path.exists():
        print(f"no such cell file: {path}")
        return 2

    cells = parse_cells(path)
    by_label = {c["label"]: c for c in cells if c["label"]}

    if args.list:
        list_cells(path, cells)
        return 0

    requested = args.cells or DEFAULT_CELLS

    # Validate the WHOLE requested list before executing anything. Discovering a typo on the
    # fourth cell, after the third already opened a TWS connection, would leave a live socket
    # behind with no cell 16 to close it.
    unknown = [c for c in requested if c not in by_label]
    if unknown:
        print(f"unknown cell(s): {', '.join(unknown)}. Run --list to see the labels.")
        return 2

    if not args.allow_sending:
        blocked = [c for c in requested if c in SENDING_CELLS]
        if blocked:
            print(f"REFUSING to run order-sending cell(s): {', '.join(blocked)}.")
            print("Those cells transmit real orders when ARM_LIVE is True.")
            print("Re-run with --allow-sending if that is genuinely what you want.")
            return 3

    # One namespace shared by every cell, exactly as a notebook kernel shares one. `__file__`
    # must be present because cell 1 derives REPO from it to put src/ on sys.path; without it
    # the cell raises NameError before a single import happens.
    namespace = {"__name__": "__cell_runner__", "__file__": str(path)}

    for label in requested:
        cell = by_label[label]
        print("\n" + "=" * 78)
        print(f" CELL {label}: {cell['title']} ".center(78, "="))
        print("=" * 78, flush=True)

        if label in MUTATING_CELLS:
            print(">>> note: this cell changes broker state.", flush=True)

        # compile() with a readable filename so a traceback points at "cell 15", not "<string>".
        code = compile("\n".join(cell["src"]), f"{path.name}:cell {label}", "exec")
        try:
            exec(code, namespace)
        except Exception:
            # Print and STOP. Continuing would run later cells against a namespace missing the
            # objects they expect, producing a second, louder error that buries the real one.
            traceback.print_exc()
            print(f"\ncell {label} raised — stopping here.", flush=True)
            return 1

    print("\n" + "=" * 78)
    print("done. NOTE: the TWS connection is still open (cell 16 disconnects).")
    print("=" * 78, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
