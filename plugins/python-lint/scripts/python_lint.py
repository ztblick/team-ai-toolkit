#!/usr/bin/env python3
"""PostToolUse hook: format and lint Python files with Ruff after Claude edits them.

Behaviour:
  * Auto-fixes what Ruff can fix (formatting, safe lint fixes) silently.
  * If lint errors remain, prints them to stderr and exits 2 so Claude sees
    them immediately and fixes them in its next step.
  * Fails open: if Ruff isn't installed or anything unexpected happens, exit 0
    so a broken tool never blocks someone's work.

Ruff reads the repo's own pyproject.toml / ruff.toml, so teams keep control of
their rules. Install Ruff with `pip install ruff` or `uv tool install ruff`.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

EXTENSIONS = {".py", ".pyi"}


def ruff_cmd():
    if shutil.which("ruff"):
        return ["ruff"]
    if shutil.which("uvx"):
        return ["uvx", "ruff"]
    return None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path or Path(file_path).suffix not in EXTENSIONS:
        return 0
    if not Path(file_path).is_file():
        return 0

    ruff = ruff_cmd()
    if ruff is None:
        return 0  # tool not installed: fail open

    cwd = payload.get("cwd") or None
    subprocess.run(ruff + ["format", "--quiet", file_path], cwd=cwd, capture_output=True)
    subprocess.run(ruff + ["check", "--fix", "--quiet", file_path], cwd=cwd, capture_output=True)

    result = subprocess.run(
        ruff + ["check", "--output-format", "concise", file_path],
        cwd=cwd, capture_output=True, text=True,
    )
    if result.returncode != 0:
        issues = (result.stdout or result.stderr).strip()
        print(
            f"Ruff found lint issues in {file_path} that it could not auto-fix. "
            f"Please fix them (do not disable the rules):\n{issues}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
