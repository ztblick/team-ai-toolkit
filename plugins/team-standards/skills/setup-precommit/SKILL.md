---
name: setup-precommit
description: Set up our standard git pre-commit hooks (Ruff, Prettier/ESLint, rustfmt/clippy) and the matching CI check in the current repository. Use when the user asks to add pre-commit hooks, onboard a repo to team standards, or make commits run our linters.
disable-model-invocation: true
---

# Set up team pre-commit hooks

Install our standard commit-time checks in the current repo. The editor hooks
from the `*-lint` plugins only run on Claude's edits; these run on **every**
commit, human or agent, and CI re-runs them so nothing slips through.

## Steps

1. **Detect languages.** Look at the repo root (and workspace members):
   - Python: `pyproject.toml`, `setup.py`, `requirements*.txt` or `*.py` files
   - TypeScript/JS: `package.json`
   - Rust: `Cargo.toml`

2. **Write `.pre-commit-config.yaml`** at the repo root from
   `templates/pre-commit-config.yaml` in this skill's directory. Keep only the
   sections for languages detected in step 1. If the file already exists, merge
   our hooks in and keep the team's existing ones; show the user the diff.

3. **Add the CI backstop.** If the repo uses GitHub Actions
   (`.github/workflows/` exists or the remote is GitHub), copy
   `templates/pre-commit-ci.yml` to `.github/workflows/pre-commit.yml`.
   For other CI systems, tell the user the one command CI must run:
   `pre-commit run --all-files`.

4. **Install the git hook.** Run `pre-commit install`. If `pre-commit` isn't
   installed, tell the user to run `pip install pre-commit` (or
   `uv tool install pre-commit`) and stop there rather than installing it
   yourself.

5. **First run.** Run `pre-commit run --all-files`. If it reformats files,
   summarize what changed. If lint errors remain, list them and ask whether to
   fix them now or in a separate change.

6. **Report.** Tell the user which hooks were added, and remind them that the
   first commit may be slower while pre-commit downloads tool environments.

Never pass `--no-verify` or set `SKIP=` to get a commit through.
