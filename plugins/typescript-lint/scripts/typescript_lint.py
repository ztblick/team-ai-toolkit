#!/usr/bin/env python3
"""PostToolUse hook: run Prettier and ESLint on TS/JS files after Claude edits them.

Behaviour:
  * Uses the project's own Prettier/ESLint from node_modules/.bin (found by
    walking up from the edited file), so each repo's pinned versions and
    configs apply. Falls back to globally installed binaries.
  * Prettier formats silently; ESLint applies --fix.
  * Remaining ESLint errors go to stderr with exit 2 so Claude fixes them.
  * Fails open if the tools aren't available.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

EXTENSIONS = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
ESLINT_CONFIGS = (
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
    ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml",
)


def find_bin(start: Path, name: str):
    """Look for node_modules/.bin/<name> from the file's dir upward, then PATH."""
    exe = name + (".cmd" if os.name == "nt" else "")
    for parent in [start, *start.parents]:
        candidate = parent / "node_modules" / ".bin" / exe
        if candidate.exists():
            return str(candidate), parent
    found = shutil.which(name)
    return (found, None) if found else (None, None)


def has_eslint_config(start: Path) -> bool:
    for parent in [start, *start.parents]:
        if any((parent / c).exists() for c in ESLINT_CONFIGS):
            return True
        if (parent / ".git").exists():
            break
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path or Path(file_path).suffix not in EXTENSIONS:
        return 0
    path = Path(file_path).resolve()
    if not path.is_file():
        return 0

    prettier, root = find_bin(path.parent, "prettier")
    if prettier:
        subprocess.run([prettier, "--write", "--log-level", "warn", str(path)],
                       cwd=root or path.parent, capture_output=True)

    eslint, root = find_bin(path.parent, "eslint")
    if not eslint or not has_eslint_config(path.parent):
        return 0  # no ESLint or no config in this repo: nothing to enforce

    cwd = root or path.parent
    subprocess.run([eslint, "--fix", str(path)], cwd=cwd, capture_output=True)
    result = subprocess.run([eslint, "--format", "stylish", str(path)],
                            cwd=cwd, capture_output=True, text=True)
    if result.returncode == 1:  # 1 = lint errors, 2 = ESLint itself crashed
        print(
            f"ESLint found issues in {file_path} that it could not auto-fix. "
            f"Please fix them (do not disable the rules):\n{result.stdout.strip()}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
