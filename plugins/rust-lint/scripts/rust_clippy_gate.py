#!/usr/bin/env python3
"""Stop hook: before Claude says it's done, run clippy if Rust code changed.

If clippy reports warnings, the hook blocks the stop (exit 2) and hands the
findings back to Claude so it keeps working. To avoid infinite loops it only
blocks once per stop attempt (Claude Code sets stop_hook_active on retries).
Fails open when cargo/clippy isn't installed or the project isn't Rust.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

MAX_OUTPUT = 4000  # keep the feedback short enough to be useful in context


def find_crate_root(start: Path):
    for parent in [start, *start.parents]:
        if (parent / "Cargo.toml").is_file():
            return parent
    return None


def rust_files_changed(root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "*.rs", "Cargo.toml", "Cargo.lock"],
        cwd=root, capture_output=True, text=True,
    )
    # Not a git repo: assume yes so clippy still runs.
    return result.returncode != 0 or bool(result.stdout.strip())


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("stop_hook_active"):
        return 0  # already asked Claude to fix once; don't loop forever

    root = find_crate_root(Path(payload.get("cwd") or ".").resolve())
    if root is None or not shutil.which("cargo"):
        return 0
    if not rust_files_changed(root):
        return 0

    result = subprocess.run(
        ["cargo", "clippy", "--quiet", "--all-targets", "--message-format", "short",
         "--", "-D", "warnings"],
        cwd=root, capture_output=True, text=True,
    )
    if result.returncode == 0:
        return 0
    output = (result.stderr or result.stdout).strip()
    if "no such command" in output or "not installed" in output:
        return 0  # clippy component missing
    print(
        "cargo clippy reported problems in the Rust code you changed. Fix them "
        "before finishing (don't add #[allow] unless there's a real reason):\n"
        + output[:MAX_OUTPUT],
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
