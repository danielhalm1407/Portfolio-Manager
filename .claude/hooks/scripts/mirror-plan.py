#!/usr/bin/env python
"""Mirror plan files written to ~/.claude/plans/ into this repo's .claude/plans/.

WHY THIS EXISTS
---------------
Claude Code pins its plan-mode approval banner to a file under ``~/.claude/plans/`` and that
path is NOT configurable — there is no settings.json key for it. But this project's
convention (see CLAUDE.md) is that plans live in ``.claude/plans/`` inside the repo, so they
are versioned with the code they describe and their markdown links resolve.

So the home-directory file is unavoidable; this hook makes it a side effect rather than a
manual step. Every time a ``.md`` under ``~/.claude/plans/`` is written, it is copied into
the repo with its links rewritten from absolute ``file:///`` form to repo-relative ``../../``
form — which is the form that actually works in a viewer opening the repo copy.

Wired from .claude/settings.json as a PostToolUse hook on Edit|Write. Receives the hook
payload as JSON on stdin; exits 0 and silently does nothing for any file that is not a plan,
because a hook that fails loudly on unrelated edits is worse than no hook.
"""
import json
import pathlib
import sys

# The repo root is two levels up from .claude/hooks/scripts/ ... which is three parents of
# this file. Derived rather than hardcoded so the hook survives the repo being moved.
REPO = pathlib.Path(__file__).resolve().parents[3]
DEST_DIR = REPO / ".claude" / "plans"
# The one directory Claude Code will write plan files to.
HOME_PLANS = pathlib.Path.home() / ".claude" / "plans"
# Absolute-link prefix to strip. Plans in the home mirror use file:/// links (the only kind
# that resolve from outside the repo); the repo copy wants them relative to .claude/plans/,
# which is two levels below the root.
ABS_PREFIX = "file:///" + str(REPO).replace("\\", "/").lstrip("/") + "/"
REL_PREFIX = "../../"


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # No payload (manual invocation, or a harness that passes the path as argv instead).
        payload = {}

    raw = (payload.get("tool_input", {}).get("file_path")
           or (sys.argv[1] if len(sys.argv) > 1 else ""))
    if not raw:
        return 0

    src = pathlib.Path(raw)
    # Only act on markdown inside the home plans directory. Everything else — source files,
    # repo plans, notes — passes straight through untouched.
    if src.suffix.lower() != ".md":
        return 0
    try:
        if src.resolve().parent != HOME_PLANS.resolve():
            return 0
    except OSError:
        return 0
    if not src.exists():
        return 0

    text = src.read_text(encoding="utf-8")
    # Rewrite absolute file:/// targets into paths relative to .claude/plans/. Case-insensitive
    # on the drive letter because Windows paths arrive spelled both ways.
    for prefix in (ABS_PREFIX, ABS_PREFIX.replace("file:///C:", "file:///c:")):
        text = text.replace("](" + prefix, "](" + REL_PREFIX)

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    dest = DEST_DIR / src.name
    # Skip the write when nothing changed, so an unrelated edit does not churn the file's
    # mtime and show up as a spurious diff in git status.
    if dest.exists() and dest.read_text(encoding="utf-8") == text:
        return 0
    dest.write_text(text, encoding="utf-8")
    # Hook stdout is surfaced to the model, so this doubles as the notification that the
    # canonical copy now exists and where it is.
    print(f"[mirror-plan] {src.name} -> .claude/plans/{dest.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
