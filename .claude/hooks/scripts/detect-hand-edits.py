#!/usr/bin/env python
"""Tell Claude when the user has hand-edited a published write-up, so the style guide learns from it.

WHY THIS EXISTS
---------------
The ``learn-writing-style`` skill updates ``.claude/skills/publish-writing-style/STYLE.md`` from the
user's own edits to the published write-ups. A skill only loads when Claude judges a request to match
it, and nothing watches the files: an edit the user makes in a new session, and never mentions, would
otherwise go unlearnt. This hook makes the detection deterministic. On every prompt it checks the
write-ups and, when it finds the USER's changes (not Claude's), adds one line to Claude's context
naming the files and saying to run the skill.

TELLING THE USER'S EDITS FROM CLAUDE'S
----------------------------------------
An uncommitted change can be either party's. Claude's writes are recorded as they happen:

* ``record`` (PostToolUse on Edit|Write) — the file Claude just wrote is hashed and remembered;
* ``pre`` / ``post`` (PreToolUse / PostToolUse on Bash) — every write-up is hashed before and after a
  shell command, and any file whose content changed DURING the command is remembered as Claude's.
  Shell edits (sed, a Python script) never pass through Edit/Write, so without this pair Claude's own
  bulk edits would be reported back to it as the user's.

``check`` (UserPromptSubmit) then flags a file only if its content differs from HEAD, differs from the
last content Claude wrote, and differs from what was last reported — so one edit is reported once,
not on every prompt. Commits are handled the same way: a commit touching a write-up with NO
``Co-Authored-By: Claude`` trailer, or with "hand edits" in its subject, is the user's, and each is
reported once.

Every mode exits 0 and prints nothing when there is nothing to say: a hook that fails loudly or
chatters on every prompt is worse than no hook.
"""
import hashlib
import json
import pathlib
import subprocess
import sys

# The repo root is three parents of this file (.claude/hooks/scripts/). Derived rather than
# hardcoded so the hook survives the repo being moved — same reasoning as mirror-plan.py.
REPO = pathlib.Path(__file__).resolve().parents[3]
# The published write-ups: the two hub pages and the three study write-ups. Globs, so a new
# write-up in either directory is covered without editing this file.
WATCHED = ("research/site/*.md", "research/validation/*.md")
# Hook state. Gitignored: it describes this working copy, not the project.
STATE = REPO / ".claude" / "hooks" / "state" / "hand-edits.json"
# Where the style guide records the newest commit it has learnt from. Commits older than this were
# already processed and are never reported.
STYLE = REPO / ".claude" / "skills" / "publish-writing-style" / "STYLE.md"


def _git(*args):
    # Run git in the repo and return stdout, or "" on any failure — a missing git or a detached
    # state must never break the user's prompt.
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout
    except Exception:
        return ""


def _files():
    # Every watched write-up on disk, as repo-relative posix paths (the form git and the state use).
    return sorted({p.relative_to(REPO).as_posix() for g in WATCHED for p in REPO.glob(g)})


def _hash(rel):
    # Content hash with line endings normalised, so git's CRLF/LF conversion on checkout is not
    # mistaken for an edit.
    try:
        data = (REPO / rel).read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return None
    return hashlib.sha1(data).hexdigest()


def _head_hash(rel):
    # The same hash of the committed version, or None for a file that is not in HEAD yet.
    out = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=REPO, capture_output=True)
    if out.returncode != 0:
        return None
    return hashlib.sha1(out.stdout.replace(b"\r\n", b"\n")).hexdigest()


def _load():
    # State shape: claude = {path: hash Claude last wrote}, notified = {path: hash last reported},
    # commits = [hashes already reported], pre = {path: hash} snapshot taken before a Bash call.
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"claude": {}, "notified": {}, "commits": [], "pre": {}}


def _save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _payload():
    # The hook payload arrives as JSON on stdin; an empty or malformed one is treated as {}.
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def record(state):
    # PostToolUse on Edit|Write: remember the hash of the write-up Claude just wrote. Any other
    # file is ignored.
    path = (_payload().get("tool_input") or {}).get("file_path", "")
    try:
        rel = pathlib.Path(path).resolve().relative_to(REPO).as_posix()
    except Exception:
        return
    if rel in _files():
        state["claude"][rel] = _hash(rel)


def pre(state):
    # PreToolUse on Bash: snapshot every write-up so `post` can see what the command changed.
    _payload()
    state["pre"] = {rel: _hash(rel) for rel in _files()}


def post(state):
    # PostToolUse on Bash: a write-up whose hash changed during the command was changed by Claude.
    _payload()
    before = state.get("pre", {})
    for rel in _files():
        now = _hash(rel)
        if before.get(rel) != now:
            state["claude"][rel] = now
    state["pre"] = {}


def _processed_commit():
    # The commit STYLE.md says it has learnt up to, or None if the line is missing.
    try:
        for line in STYLE.read_text(encoding="utf-8").splitlines():
            if line.startswith("Evidence processed through commit:"):
                return line.split("`")[1]
    except Exception:
        pass
    return None


def check(state):
    _payload()
    edited = []
    for rel in _files():
        now = _hash(rel)
        # Gate 1: identical to HEAD means nothing uncommitted to learn from.
        if now == _head_hash(rel):
            continue
        # Gate 2: identical to what Claude last wrote means the change is Claude's own.
        if now == state["claude"].get(rel):
            continue
        # Gate 3: already reported at exactly this content, so stay quiet until it changes again.
        if now == state["notified"].get(rel):
            continue
        edited.append(rel)
        state["notified"][rel] = now

    # Commits since the style guide's last processed one that the user made by hand.
    commits = []
    since = _processed_commit()
    if since:
        log = _git("log", "--format=%h%x1f%s%x1f%b%x1e", f"{since}..HEAD", "--", *WATCHED)
        for entry in filter(None, (e.strip() for e in log.split("\x1e"))):
            short, subject, body = (entry.split("\x1f") + ["", ""])[:3]
            theirs = "Co-Authored-By: Claude" not in body or "hand edits" in subject.lower()
            if theirs and short not in state["commits"]:
                commits.append(f"{short} ({subject})")
                state["commits"].append(short)

    if not (edited or commits):
        return
    # One line of context for Claude. UserPromptSubmit stdout is added to the conversation.
    parts = []
    if edited:
        parts.append("uncommitted hand edits by the user in " + ", ".join(edited))
    if commits:
        parts.append("user commits " + "; ".join(commits))
    print("Writing-style hook: detected " + " and ".join(parts) + ". Run the learn-writing-style "
          "skill (read the diffs with git diff / git show) before editing these files, and keep "
          "the user's wording.")


def seed(state):
    # One-off: mark the CURRENT content of every write-up as already seen, so installing the hook
    # does not report edits that predate it and have already been learnt from.
    for rel in _files():
        state["notified"][rel] = _hash(rel)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    state = _load()
    try:
        {"record": record, "pre": pre, "post": post, "check": check, "seed": seed}[mode](state)
        _save(state)
    except Exception:
        # Never block a prompt or a tool call over this hook.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
