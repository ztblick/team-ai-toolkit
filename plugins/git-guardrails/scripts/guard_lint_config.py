#!/usr/bin/env python3
"""PreToolUse hook (Write/Edit): stop agents from "fixing" lint by weakening it.

Agents sometimes silence a linter instead of fixing the code. This hook asks
the human to approve (permissionDecision "ask") when Claude tries to:
  * edit a linter / formatter / pre-commit config file, or
  * add a new inline suppression (# noqa, eslint-disable, #[allow(...)], ...)

It asks rather than hard-blocking, because sometimes the change is legitimate.
"""
import json
import re
import sys
from pathlib import Path

CONFIG_NAMES = re.compile(
    r"^(ruff\.toml|\.ruff\.toml|\.flake8|\.pylintrc|mypy\.ini"
    r"|\.eslintrc(\..+)?|eslint\.config\.[cm]?[jt]s|\.prettierrc(\..+)?|prettier\.config\.[cm]?js"
    r"|biome\.jsonc?|tsconfig(\..+)?\.json"
    r"|rustfmt\.toml|\.rustfmt\.toml|clippy\.toml|\.clippy\.toml"
    r"|\.pre-commit-config\.yaml|lefthook\.ya?ml)$"
)
# Lint sections inside shared manifests
MANIFEST_SECTIONS = {
    "pyproject.toml": re.compile(r"\[tool\.(ruff|mypy|pylint|black)"),
    "Cargo.toml": re.compile(r"\[(workspace\.)?lints"),
    "package.json": re.compile(r'"(eslintConfig|prettier)"'),
}
SUPPRESSIONS = re.compile(
    r"#\s*noqa|#\s*type:\s*ignore|#\s*pylint:\s*disable|#\s*ruff:\s*noqa"
    r"|eslint-disable|@ts-ignore|@ts-nocheck|@ts-expect-error|prettier-ignore"
    r"|#!?\[allow\(|#\[rustfmt::skip\]"
)


def ask(reason: str) -> int:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


def lint_section(text: str, header: re.Pattern) -> str:
    """Return the lint-related section(s) of a manifest, for before/after comparison."""
    if header.pattern.startswith('"'):  # package.json: compare whole file's matching keys
        return "\n".join(line.strip() for line in text.splitlines() if header.search(line))
    out, inside = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            inside = bool(header.search(line))
        if inside:
            out.append(line.rstrip())
    return "\n".join(out)


def new_and_old_text(tool_input: dict, path: Path):
    if "content" in tool_input:  # Write
        old = path.read_text(errors="ignore") if path.is_file() else ""
        return tool_input.get("content") or "", old
    if "edits" in tool_input:  # MultiEdit
        edits = tool_input.get("edits") or []
        return ("\n".join(e.get("new_string", "") for e in edits),
                "\n".join(e.get("old_string", "") for e in edits))
    return tool_input.get("new_string") or "", tool_input.get("old_string") or ""


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")
    if not file_path:
        return 0
    path = Path(file_path)
    new, old = new_and_old_text(tool_input, path)

    if CONFIG_NAMES.match(path.name):
        return ask(f"Claude wants to change lint/format config {path.name}. "
                   "Approve only if the rule change itself is intended.")

    section = MANIFEST_SECTIONS.get(path.name)
    if section:
        if "content" in tool_input:  # whole-file write: compare the lint sections
            changed = lint_section(new, section) != lint_section(old, section)
        else:  # edit fragment that touches a lint section header
            changed = bool(section.search(new) or section.search(old))
        if changed:
            return ask(f"Claude wants to change lint settings inside {path.name}.")

    added = len(SUPPRESSIONS.findall(new)) - len(SUPPRESSIONS.findall(old))
    if added > 0:
        return ask(f"Claude is adding {added} lint suppression comment(s) to {path.name} "
                   "instead of fixing the code. Approve only if it's justified.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
