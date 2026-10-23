#!/usr/bin/env python3
"""PostToolUse hook: run rustfmt on .rs files after Claude edits them.

rustfmt is fast enough to run on every edit. Clippy needs the whole crate, so
it runs once at the end of Claude's turn instead (see rust_clippy_gate.py).
Respects the repo's rustfmt.toml and the edition in the nearest Cargo.toml.
Fails open if rustfmt isn't installed (`rustup component add rustfmt`).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def crate_edition(start: Path) -> str:
    for parent in [start, *start.parents]:
        manifest = parent / "Cargo.toml"
        if manifest.is_file():
            match = re.search(r'^\s*edition\s*=\s*"(\d{4})"', manifest.read_text(), re.M)
            if match:
                return match.group(1)
    return "2021"


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path or not file_path.endswith(".rs"):
        return 0
    path = Path(file_path).resolve()
    if not path.is_file() or not shutil.which("rustfmt"):
        return 0

    result = subprocess.run(
        ["rustfmt", "--edition", crate_edition(path.parent), str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # rustfmt fails on syntax errors; tell Claude so it fixes them now.
        print(f"rustfmt could not format {file_path}:\n{result.stderr.strip()}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
