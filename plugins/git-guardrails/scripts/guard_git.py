#!/usr/bin/env python3
"""PreToolUse hook (Bash): block git commands that bypass our quality gates.

Blocked (exit 2, the command never runs and Claude is told why):
  * --no-verify on commit/push/merge/rebase/cherry-pick/am, and -n on commit
  * Env vars that silently skip hook frameworks (SKIP=, HUSKY=0, LEFTHOOK=0)
  * Overriding core.hooksPath for a single command
  * Force pushes to protected branches (main/master/release/*)

These hooks only fire on commands Claude runs. Humans at a terminal are
unaffected. CI still re-runs every check as the backstop.
"""
import json
import re
import shlex
import subprocess
import sys

PROTECTED = re.compile(r"^(main|master|release/.+)$")
NO_VERIFY_SUBCOMMANDS = {"commit", "push", "merge", "rebase", "cherry-pick", "am"}
SKIP_ENV = re.compile(r"^(SKIP|HUSKY|LEFTHOOK|PRE_COMMIT_ALLOW_NO_CONFIG)=")


def segments(command: str):
    """Split a shell line into simple commands on && || ; | and newlines."""
    for part in re.split(r"&&|\|\||[;|\n]", command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        if tokens:
            yield tokens


def current_branch(cwd):
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             cwd=cwd, capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return ""


def check(tokens, cwd):
    env_prefix = []
    while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
        env_prefix.append(tokens.pop(0))
    if not tokens or tokens[0] != "git":
        return None

    args = tokens[1:]
    # Skip global options like `-C path` or `-c key=value` to find the subcommand.
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "-c" and i + 1 < len(args) and args[i + 1].lower().startswith("core.hookspath"):
            return "Overriding core.hooksPath skips the repository's git hooks."
        i += 2 if args[i] in ("-C", "-c") else 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]

    if any(SKIP_ENV.match(e) for e in env_prefix) and sub in NO_VERIFY_SUBCOMMANDS:
        return f"Setting {', '.join(env_prefix)} skips pre-commit hooks."
    if sub in NO_VERIFY_SUBCOMMANDS and "--no-verify" in rest:
        return "--no-verify skips the repository's pre-commit checks."
    if sub == "commit" and any(re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", a) for a in rest):
        return "`git commit -n` is short for --no-verify and skips pre-commit checks."
    if sub == "push" and any(a in ("--force", "-f") or a.startswith("--force-with-lease") or
                             re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", a) for a in rest):
        refs = [a.split(":")[-1].lstrip("+") for a in rest if not a.startswith("-")][1:]
        targets = refs or [current_branch(cwd)]
        if any(PROTECTED.match(t) for t in targets):
            return f"Force-pushing to a protected branch ({', '.join(targets)}) is not allowed."
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if "git" not in command:
        return 0
    for tokens in segments(command):
        reason = check(tokens, payload.get("cwd"))
        if reason:
            print(
                f"BLOCKED by git-guardrails: {reason}\n"
                "If a hook is failing, fix the underlying problem and commit normally. "
                "If you believe the hook itself is broken, stop and ask the user.",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
