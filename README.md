# team-ai-toolkit (proof of concept)

A Claude Code plugin marketplace that gives every engineer our linters, guardrails and
standards in a single command, from day one.

| Plugin | What it does | When it runs |
| --- | --- | --- |
| `python-lint` | Ruff format + lint | After Claude edits a `.py` file |
| `typescript-lint` | Prettier + ESLint | After Claude edits a TS/JS file |
| `rust-lint` | rustfmt per file, `cargo clippy` gate | After each `.rs` edit / when Claude tries to finish |
| `git-guardrails` | Blocks hook-skipping git commands; asks before lint configs are weakened | Before Claude runs a command or edits a file |
| `team-standards` | Style-guide skill + `/team-standards:setup-precommit` | When relevant / on demand |

The idea: **instructions are suggestions, hooks are guarantees.** Formatting and linting
happen automatically on every agent edit, lint failures go straight back to Claude to fix,
and the commit-time hooks plus CI catch anything else, human or agent.

## Quickstart (5 minutes)

Requirements: Claude Code, `git`, and `python3` on PATH (all hook scripts are Python, so
they behave the same on macOS, Linux and WSL). Then whichever toolchains you use:
`pip install ruff`; ESLint/Prettier in your project's `package.json`;
`rustup component add rustfmt clippy`. Missing tools are skipped silently, never blocking.

```bash
# 1. Push this folder to a new repo (e.g. ztblick/team-ai-toolkit), then:
claude plugin marketplace add ztblick/team-ai-toolkit

# 2. Install what you want
claude plugin install git-guardrails@team-ai-toolkit
claude plugin install team-standards@team-ai-toolkit
claude plugin install python-lint@team-ai-toolkit    # and/or typescript-lint, rust-lint

# 3. Check
claude plugin list
```

Inside a session, `/plugin` opens the same catalog in a browsable UI.

Before pushing changes, run the checks locally:

```bash
claude plugin validate .
python3 tests/run_hook_tests.py                      # add --ts-project <dir> to test ESLint/Prettier
```

## Demo script for the team

Run these in a scratch repo with the plugins installed. Each takes about a minute.

1. **Auto-format.** "Write a Python function that parses a CSV of users." Open the file: it's
   already Ruff-formatted, without anyone asking.
2. **Lint feedback loop.** "Add a helper that uses a variable you never define." Watch Claude
   receive Ruff's error from the hook and fix it on its own in the next step.
3. **Rust gate.** In a Rust crate, ask for code with a clippy smell (e.g. `if x == true`).
   When Claude tries to finish, the Stop hook runs clippy and sends it back to fix the warning.
4. **Guardrail.** "Commit this with --no-verify, the hooks are slow." It's blocked, and
   Claude is told to fix the failure instead.
5. **Config gaming.** "Just add `# noqa` so lint passes." You get a permission prompt instead
   of a silent suppression.
6. **Onboard a repo.** Run `/team-standards:setup-precommit`. Claude detects the languages,
   writes `.pre-commit-config.yaml` and a CI workflow, and runs the hooks once.

## Rolling it out (after the PoC)

Three levels, each a small step from the last:

1. **Opt-in** (today): people run the commands above.
2. **Per repo:** commit `templates/project-settings.json` as `.claude/settings.json` in a repo.
   Anyone who opens that repo in Claude Code is prompted to add the marketplace and gets the
   listed plugins enabled.
3. **Company-wide:** an admin puts `templates/managed-settings.json` in Claude Code managed
   settings (claude.ai admin console, MDM, or `managed-settings.json`). Plugins are installed on
   every machine automatically, and `strictKnownMarketplaces` limits plugin sources to approved
   ones. See https://code.claude.com/docs/en/plugins/org

Commit-time checks (`setup-precommit`) and CI cover engineers and agents that don't use
Claude Code at all.

## Adding a plugin

1. Copy an existing plugin folder under `plugins/` and rename it. Keep the folder name, the
   `name` in its `.claude-plugin/plugin.json`, and its entry in
   `.claude-plugin/marketplace.json` identical.
2. Hook scripts: Python, standard library only, read the JSON payload from stdin, **fail
   open** (exit 0) when a tool is missing, and exit 2 with a clear stderr message when Claude
   needs to act.
3. Add cases to `tests/run_hook_tests.py`, run the tests and `claude plugin validate .`.
4. Bump `version` in both `plugin.json` and the marketplace entry, and open a PR. Users pick up
   new versions when the marketplace updates.

Hooks run arbitrary code on every engineer's machine, so every plugin change should get the
same code review as production code.

## Known limits of the PoC

- `guard_git.py` parses commands with simple shell splitting. It catches the common bypasses,
  not every possible one. CI (`pre-commit run --all-files`) is the real backstop.
- `guard_lint_config.py` spots suppressions and config files by pattern; edits inside a lint
  section of `pyproject.toml` that don't touch the section header can slip through.
- The style guide in `team-standards` is placeholder content.
- Hook commands call `python3`. On native Windows (outside WSL), that may need to be `py` or
  `python`.
