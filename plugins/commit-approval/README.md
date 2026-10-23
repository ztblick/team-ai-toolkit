# commit-approval

Makes a human approve every commit Claude makes. Right before a `git commit` that Claude runs
executes, Claude Code shows its normal permission prompt with a summary of what's about to be
committed, ending with:

> As a Lucid engineer, do you understand and approve these changes?

**Yes** runs the commit. **No** cancels it. If you choose No without a comment, Claude Code ends
Claude's turn, so Claude stops and you can say what you want changed. If you add a comment,
Claude gets the comment and is told not to retry or work around the rejection.

```bash
claude plugin install commit-approval@team-ai-toolkit
# or, in one step on Claude Code 2.1.275+:  /plugin install commit-approval --marketplace ztblick/team-ai-toolkit
```

Part of the team-ai-toolkit marketplace. See the root README for setup.

**Try it:** in a scratch repo, ask Claude "Add a `.env` with a fake API key and a small helper,
then commit." You'll get the prompt with a warning about `.env`. Choose No and nothing is
committed.

## What the prompt shows

```
Claude is about to make a git commit. Please review what will be committed:

Repository: ~/code/billing-service
Branch:     feature/csv-import
Message:
  Add CSV user import

  Adds load_users() and the users table migration.

Files (3 changed, +58 -4):
  +41 -3  src/importer.py
  +12 -1  tests/test_importer.py
   +5 -0  migrations/0002_users.sql

Always reviewed: migrations/0002_users.sql matches migrations/**.

Choosing No cancels the commit. Claude will stop and ask what you want changed.
As a Lucid engineer, do you understand and approve these changes?
```

The summary is built by running git, with no LLM involved, in the directory the commit will run
in. It follows `cd dir && ...`, `git -C dir`, `GIT_DIR=`/`GIT_WORK_TREE=` prefixes and
`bash -c "..."`. It shows:

- the message from `-m` (several `-m` flags become paragraphs), `-F file`, `-F -` with a heredoc,
  and Claude's usual `-m "$(cat <<'EOF' ... EOF)"` form
- the branch, plus the previous commit's subject for `--amend`
- each file with +/- line counts (`git diff --cached --numstat`). When the same command also
  stages changes (an earlier `git add`/`rm`/`mv`, `commit -a`, or `commit <paths>`), it shows
  everything that differs from HEAD, plus new files the `git add` will pick up, and says the
  final file set is decided at commit time
- a warning for each file that matches `sensitive_patterns`

It doesn't prompt for other git commands (`status`, `log`, `push`, ...) or `git commit --dry-run`.

## Configuration: `config.json`

Everything the user reads, and every threshold, is in [`config.json`](config.json). Edit it in
this repo and ship it as an update (see "Shipping an update" in the root README). Edits to the
installed copy under `~/.claude/plugins/cache/` are overwritten by the next update.

| Field | Default | What it does |
| --- | --- | --- |
| `header` | "Claude is about to make a git commit. ..." | First line of the prompt. |
| `question` | "As a Lucid engineer, do you understand and approve these changes?" | Last line of the prompt. |
| `rejection_note` | "Choosing No cancels the commit. ..." | Line just above the question. |
| `staging_note` | "Note: this command also stages changes, ..." | Shown when the command also stages files. |
| `summary_failed` | "Couldn't summarize the changes ..." | Shown instead of the file list when git can't be read. The prompt still appears. |
| `sensitive_warning` | "WARNING: {path} matches sensitive pattern {pattern}. ..." | One line per sensitive file. `{path}` and `{pattern}` are filled in. |
| `always_ask_note` | "Always reviewed: {path} matches {pattern}." | One line per file matching `always_ask_paths`. |
| `amend_note` | "This rewrites the previous commit (--amend): {subject}" | Shown for `--amend`. `{subject}` is the commit being replaced. |
| `multiple_commits_note` | "This command makes {count} commits. ..." | Shown when one command runs several commits. Only the first is summarized. |
| `no_message` | "(no message on the command line)" | Shown when there's no `-m`/`-F`. |
| `claude_instruction` | "Commits need human approval ... Stop and ask the user ..." | Sent to Claude, not shown to the user. Tells Claude not to work around a rejection. |
| `max_files_listed` | `15` | Files listed before "...and K more". Also caps the warning lists. At least 1. |
| `max_message_lines` | `12` | Commit message lines shown before "...and N more lines". At least 1. |
| `min_changed_lines` | `0` | Only prompt when added + deleted lines reach this. `0` means always prompt. |
| `always_ask_paths` | `migrations/**`, `*.tf`, `.github/workflows/**` | Glob patterns that force a prompt whatever the size. |
| `sensitive_patterns` | `.env`, `.env.*`, `*.pem`, `*.key`, `id_rsa`, `*.tfstate`, ... | Glob patterns that add a warning line, and also force a prompt. |

Glob patterns: a pattern without `/` matches the file name in any folder (`*.tf`). A pattern
with `/` matches that path starting from any folder, so `migrations/**` matches both
`migrations/0001.sql` and `app/migrations/0001.py`. Start it with `/` to anchor it to the repo
root. `*` stays inside one folder, `**` spans folders. Matching ignores case.

Even below `min_changed_lines`, it still prompts for `--amend`, binary files (their size can't
be measured), and any file matching `always_ask_paths` or `sensitive_patterns`.

## Fails safe

This is a safety check, so it fails the opposite way from the lint hooks:

- `config.json` missing or not valid JSON: built-in defaults are used, which always prompt, and
  the prompt says so. A single bad field falls back to its own default.
- Git can't be read (not a repo, unknown directory, slow repo): it still prompts, with the
  `summary_failed` message. Git calls share a 15-second budget.
- Unbalanced quotes in the command: it shows every change against HEAD rather than guess.
- The script crashes or can't start (for example, `python3` missing): `"onFailure": "block"` in
  `hooks/hooks.json` turns that into a denial, so the commit doesn't run. This needs Claude Code
  2.1.295 or later.

## Permission modes (tested with Claude Code 2.1.309)

| Situation | What happens |
| --- | --- |
| Normal interactive session | The prompt appears. No ends Claude's turn ("What should Claude do instead?"). |
| An allow rule such as `Bash(git commit:*)` | The commit is still held for approval. The allow rule doesn't skip it. |
| `--dangerously-skip-permissions` / `bypassPermissions` | The prompt still appears. This mode does not skip it. |
| `dontAsk` mode | The commit is denied, since nobody can be asked. |
| Headless `claude -p` (CI, scripts, Agent SDK) | The commit is denied. Claude gets the summary as the reason and didn't retry in testing. |

So Claude can't commit at all in a headless run while this plugin is enabled. Don't enable it in
CI or other automated agents that are supposed to commit.

## Known limits

- It only sees commits Claude runs through the Bash tool. A commit made from a script file,
  `python -c`, a git alias (`git ci`), or plumbing such as `git commit-tree` isn't detected.
  `claude_instruction` tells Claude not to use these to get around a rejection, but that is an
  instruction, not a guarantee.
- Pairs well with `git-guardrails`, which blocks `--no-verify` and similar.
