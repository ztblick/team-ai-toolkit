# team-ai-toolkit (proof of concept)

This repo defines a marketplace for Claude plugins. A marketplace is a curated collection of plugins which can be custom designed and updated by our team.

To add the marketplace (one time command), you need to run
```bash
claude plugin marketplace add ztblick/team-ai-toolkit
```
after which you can install individual plugins:
```bash
claude plugin install git-guardrails@team-ai-toolkit
claude plugin install commit-approval@team-ai-toolkit
claude plugin install team-standards@team-ai-toolkit
claude plugin install python-lint@team-ai-toolkit
```

These can also be seen and installed in the /plugin menu in claude code.

Each plugin can contain skills and hooks that run included scripts. The scripts should probably
be written in python (that's what I did) to allow for easier cross-platform compatability.

## Quickstart (5 minutes)

Requirements: Claude Code, `git`, and `python3` on PATH.
## Shipping an update

1. Make the change. For example, `commit-approval`'s wording and thresholds are all in
   `plugins/commit-approval/config.json` (fields documented in that plugin's README).
2. Bump the version. Users keep their cached copy until the version string changes:
   ```bash
   python3 scripts/bump_version.py commit-approval          # patch; or: minor, major
   ```
   This updates `plugin.json` and the `marketplace.json` entry together so they can't drift.
3. Run the checks: `python3 tests/run_hook_tests.py` and `claude plugin validate .`
4. Commit and push to `main`.

How people get the update:

- Auto-update is **off by default** for our marketplace (Claude Code only auto-updates official
  ones). Turn it on in `/plugin` → **Marketplaces** → `team-ai-toolkit` → **Enable auto-update**,
  or an admin can set `"autoUpdate": true` on its `extraKnownMarketplaces` entry in managed
  settings. When it's on, Claude Code checks within about ten minutes of your first message in
  a session, and the new version loads next session (or after `/reload-plugins`).
- To update by hand:
  ```bash
  claude plugin marketplace update team-ai-toolkit
  claude plugin update commit-approval@team-ai-toolkit
  ```
  then restart Claude Code or run `/reload-plugins`.
