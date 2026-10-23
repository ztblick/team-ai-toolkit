#!/usr/bin/env python3
"""Bump a plugin's version in its plugin.json and its marketplace.json entry together.

    python3 scripts/bump_version.py commit-approval          # patch: 0.1.0 -> 0.1.1
    python3 scripts/bump_version.py commit-approval minor    # 0.1.0 -> 0.2.0

Installed users only receive a plugin update when its version string changes, and
Claude Code reads plugin.json first, so the two copies must never drift apart. The
files are edited in place (only the version string changes) to keep their formatting.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def bumped(version, part):
    m = SEMVER.match(version)
    if not m:
        sys.exit(f"Version {version!r} is not MAJOR.MINOR.PATCH; fix it by hand first.")
    major, minor, patch = map(int, m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def replace_version(text, start, old, new):
    """Replace the first `"version": "<old>"` at or after `start`."""
    pattern = re.compile(r'("version"\s*:\s*")' + re.escape(old) + '"')
    m = pattern.search(text, start)
    if not m:
        sys.exit(f'Couldn\'t find "version": "{old}" to replace.')
    return text[: m.start()] + m.group(1) + new + '"' + text[m.end() :]


def main():
    if len(sys.argv) not in (2, 3) or (
        len(sys.argv) == 3 and sys.argv[2] not in ("major", "minor", "patch")
    ):
        sys.exit("usage: scripts/bump_version.py <plugin> [major|minor|patch]")
    plugin, part = sys.argv[1], (sys.argv[2] if len(sys.argv) == 3 else "patch")

    market_text = MARKETPLACE.read_text(encoding="utf-8")
    entries = [p for p in json.loads(market_text)["plugins"] if p["name"] == plugin]
    if not entries:
        sys.exit(f"No plugin named {plugin!r} in {MARKETPLACE.relative_to(ROOT)}.")
    manifest = ROOT / entries[0]["source"] / ".claude-plugin" / "plugin.json"
    manifest_text = manifest.read_text(encoding="utf-8")

    old = json.loads(manifest_text).get("version")
    if entries[0].get("version") != old:
        sys.exit(
            f"Versions already differ: plugin.json has {old!r}, marketplace.json has "
            f"{entries[0].get('version')!r}. Make them match by hand, then bump."
        )
    new = bumped(old, part)

    entry_start = re.search(
        r'"name"\s*:\s*"' + re.escape(plugin) + '"', market_text
    ).start()
    market_text = replace_version(market_text, entry_start, old, new)
    manifest_text = replace_version(manifest_text, 0, old, new)

    # Check the result before writing anything.
    entry = next(p for p in json.loads(market_text)["plugins"] if p["name"] == plugin)
    if entry.get("version") != new or json.loads(manifest_text).get("version") != new:
        sys.exit("Bump failed a consistency check; nothing was written.")
    manifest.write_text(manifest_text, encoding="utf-8")
    MARKETPLACE.write_text(market_text, encoding="utf-8")
    print(f"{plugin}: {old} -> {new} (plugin.json and marketplace.json)")


if __name__ == "__main__":
    main()
