#!/usr/bin/env python3
"""PreToolUse hook (Bash): a human approves every commit Claude makes.

When a Bash command would run `git commit`, this answers permissionDecision
"ask" with a plain-text summary of what is about to be committed (message,
branch, files with +/- counts, sensitive-file warnings), so Claude Code shows
its normal approval prompt. All wording and thresholds live in ../config.json.

This is a safety check, so unlike the lint hooks it fails safe: a missing or
broken config.json falls back to the built-in defaults below, and when git
can't be read the user is still asked. The prompt is skipped only when the
command clearly isn't a commit (`git commit --dry-run` included), or when
min_changed_lines says the change is too small to need review.

Only commands Claude runs are checked. Humans at a terminal are unaffected.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Keep in sync with config.json (tests/run_hook_tests.py checks this).
DEFAULTS = {
    "header": "Claude is about to make a git commit. Please review what will be committed:",
    "question": "As a Lucid engineer, do you understand and approve these changes?",
    "rejection_note": "Choosing No cancels the commit. Claude will stop and ask what you want changed.",
    "staging_note": "Note: this command also stages changes, so the exact set of files is decided at commit "
    "time. Unstaged changes to tracked files (and new files it adds) are included above.",
    "summary_failed": "Couldn't summarize the changes (git information was unavailable). Check them yourself "
    "with `git status` and `git diff --cached` before approving.",
    "sensitive_warning": "WARNING: {path} matches sensitive pattern {pattern}. Make sure it contains no secrets.",
    "always_ask_note": "Always reviewed: {path} matches {pattern}.",
    "amend_note": "This rewrites the previous commit (--amend): {subject}",
    "multiple_commits_note": "This command makes {count} commits. Only the first one is summarized.",
    "no_message": "(no message on the command line)",
    "claude_instruction": "Commits need human approval through the commit-approval prompt. If the user rejects "
    "a commit, do not retry it, reword it, or work around it (for example with a different "
    "git command, a script, or by committing again later). Stop and ask the user what "
    "they want changed.",
    "max_files_listed": 15,
    "max_message_lines": 12,
    "min_changed_lines": 0,
    "always_ask_paths": ["migrations/**", "*.tf", ".github/workflows/**"],
    "sensitive_patterns": [
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
        "*.jks",
        "*.keystore",
        "id_rsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials.json",
        ".aws/credentials",
        "secrets.*",
        "*.tfstate",
        "*.tfstate.*",
        "*.tfvars",
        ".npmrc",
        ".pypirc",
        ".netrc",
    ],
}
MINIMUMS = {"max_files_listed": 1, "max_message_lines": 1, "min_changed_lines": 0}

DEADLINE = time.monotonic() + 15  # all git calls together; the hook timeout is 30 s
MAX_REASON_CHARS = 8000  # keep the prompt readable whatever the commit
MAX_LINE_CHARS = 200

SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
WRAPPERS = {"sudo", "env", "command", "builtin", "exec", "nohup", "time", "nice"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
HEREDOC_START = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([\w.-]+)\2")
HEREDOC_TOKEN = re.compile(r"^<<__HEREDOC_(\d+)__$")
HEREDOC_SUBST = re.compile(r"^\$\(\s*cat\s*<<__HEREDOC_(\d+)__\s*\)$")
REDIRECT = re.compile(r"^(?:\d+|&)?(?:<<<|>>|>&|<&|<>|>|<)(.*)$")
COMMIT_VALUE_SHORT = set("mFCct")
COMMIT_VALUE_LONG = {
    "--message",
    "--file",
    "--reuse-message",
    "--reedit-message",
    "--template",
    "--author",
    "--date",
    "--cleanup",
    "--fixup",
    "--squash",
    "--trailer",
    "--pathspec-from-file",
}


class GitError(Exception):
    pass


# --- config -----------------------------------------------------------------


def load_config():
    """Merge config.json over DEFAULTS field by field. Bad values fall back to defaults."""
    cfg = dict(DEFAULTS)
    try:
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(user, dict):
            raise TypeError("the top level must be a JSON object")
    except FileNotFoundError:
        return cfg, [
            "Note: commit-approval's config.json is missing, so built-in defaults are used."
        ]
    except (OSError, ValueError, TypeError) as e:
        return cfg, [
            f"Note: commit-approval's config.json is invalid ({e}), so built-in defaults are used."
        ]
    problems = []
    for key, default in DEFAULTS.items():
        if key not in user:
            continue
        value = user[key]
        if isinstance(default, list):
            ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif isinstance(default, int):
            ok = (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value >= MINIMUMS[key]
            )
        else:
            ok = isinstance(value, str) and value.strip() != ""
        if ok:
            cfg[key] = value
        else:
            problems.append(
                f"Note: commit-approval's config.json has an invalid {key!r}, so the default is used."
            )
    return cfg, problems


def fill(template, **values):
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def glob_regex(pattern):
    """Glob to regex. No slash: match the file name at any depth. With a slash: match a
    path from any directory (a leading / anchors to the repo root). ** spans directories."""
    anchored, p = pattern.startswith("/"), pattern.lstrip("/")
    if p.endswith("/"):
        p += "**"
    out, i = [], 0
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        elif p[i] == "[" and "]" in p[i + 2 :]:
            j = p.index("]", i + 2)
            body = p[i + 1 : j]
            out.append(
                "["
                + ("^" + body[1:] if body.startswith("!") else body).replace(
                    "\\", "\\\\"
                )
                + "]"
            )
            i = j + 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    prefix = "^" if anchored else r"(?:^|.*/)"
    return re.compile(prefix + "".join(out) + "$", re.IGNORECASE)


def first_match(names, patterns):
    for pattern in patterns:
        regex = glob_regex(pattern)
        if any(regex.match(name) for name in names):
            return pattern
    return None


# --- shell parsing ----------------------------------------------------------


def extract_heredocs(command):
    """Cut heredoc bodies out of the command, leaving <<__HEREDOC_n__ in their place."""
    lines, out, bodies, i = command.split("\n"), [], [], 0
    while i < len(lines):
        line, pending, pieces, last = lines[i], [], [], 0
        for m in HEREDOC_START.finditer(line):
            pending.append((len(bodies), m.group(3), m.group(1) == "-"))
            pieces += [line[last : m.start()], f"<<__HEREDOC_{len(bodies)}__"]
            bodies.append("")
            last = m.end()
        out.append("".join(pieces) + line[last:])
        i += 1
        for index, delimiter, strip_tabs in pending:
            body = []
            while i < len(lines):
                line = lines[i]
                i += 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line)
            bodies[index] = "\n".join(body)
    return "\n".join(out), bodies


def split_commands(text):
    """Split on && || ; | & ( ) and newlines that are outside quotes and $(...)."""
    segments, buf, stack, i = [], [], [], 0
    while i < len(text):
        c, top = text[i], (stack[-1] if stack else None)
        if top == "'":
            if c == "'":
                stack.pop()
        elif c == "\\":
            buf.append(text[i : i + 2])
            i += 2
            continue
        elif text.startswith("$(", i):
            stack.append("(")
            buf.append("$(")
            i += 2
            continue
        elif top == '"':
            if c == '"':
                stack.pop()
        elif c in "'\"":
            stack.append(c)
        elif top == "(":
            if c == "(":
                stack.append("(")
            elif c == ")":
                stack.pop()
        elif text.startswith(("&&", "||"), i):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        elif c in ";|\n()" or (
            c == "&"
            and text[i - 1 : i] not in (">", "<")
            and text[i + 1 : i + 2] != ">"
        ):
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    segments.append("".join(buf))
    return [s for s in segments if s.strip()]


def strip_redirects(tokens, heredocs):
    """Drop redirections; return (args, text fed to stdin by a heredoc or here-string)."""
    args, stdin, i = [], None, 0
    while i < len(tokens):
        token = tokens[i]
        i += 1
        m = HEREDOC_TOKEN.match(token)
        if m:
            stdin = heredocs[int(m.group(1))]
            continue
        m = REDIRECT.match(token)
        if m:
            target = m.group(1)
            if not target and i < len(tokens):
                target = tokens[i]
                i += 1
            if token.lstrip("0123456789&").startswith("<<<"):
                stdin = target
            continue
        args.append(token)
    return args, stdin


def expand(path):
    return os.path.expanduser(os.path.expandvars(path))


def tokenize(segment):
    """shlex.split, closing an unbalanced quote if needed. Returns (tokens, exact)."""
    for suffix in ("", "'", '"'):
        try:
            return shlex.split(segment + suffix), suffix == ""
        except ValueError:
            pass
    return segment.split(), False


def walk(command, state, depth=0):
    """Follow a command line, tracking cd and git add, and record the commits it would make."""
    text, heredocs = extract_heredocs(command)
    for segment in split_commands(text):
        tokens, exact = tokenize(segment)
        tokens, stdin = strip_redirects(tokens, heredocs)
        env = {}
        while tokens:
            if ASSIGNMENT.match(tokens[0]):
                key, _, value = tokens.pop(0).partition("=")
                env[key] = value
            elif tokens[0] in WRAPPERS:
                tokens.pop(0)
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
            else:
                break
        if not tokens or tokens[0] in ("{", "}"):
            continue
        name = os.path.basename(tokens[0])
        if name in ("cd", "pushd"):
            target = tokens[1] if len(tokens) > 1 else "~"
            cwd = state["cwd"]
            state["cwd"] = (
                None
                if cwd is None or target == "-"
                else os.path.normpath(os.path.join(cwd, expand(target)))
            )
        elif name in SHELLS and depth < 3:
            flags = [
                k
                for k, t in enumerate(tokens[1:], 1)
                if re.fullmatch(r"-[a-z]*c[a-z]*", t)
            ]
            if flags and flags[0] + 1 < len(tokens):
                walk(tokens[flags[0] + 1], state, depth + 1)
        elif name == "eval" and depth < 3:
            walk(" ".join(tokens[1:]), state, depth + 1)
        elif name == "git":
            handle_git(tokens[1:], env, stdin, heredocs, state, exact)


def handle_git(args, env, stdin, heredocs, state, exact):
    global_opts, i = [], 0
    while i < len(args) and args[i].startswith("-"):
        a = args[i]
        if a in ("-C", "--git-dir", "--work-tree"):
            if i + 1 < len(args):
                global_opts += [a, expand(args[i + 1])]
            i += 2
            continue
        if a.startswith(("--git-dir=", "--work-tree=")):
            global_opts.append(a)
        elif a in ("-c", "--namespace", "--config-env"):
            i += 1
        i += 1
    if i >= len(args):
        return
    sub, rest = args[i], args[i + 1 :]
    repo = {
        "cwd": state["cwd"],
        "opts": global_opts,
        "env": {k: env[k] for k in GIT_ENV if k in env},
    }
    if sub == "commit":
        commit = parse_commit(rest)
        if not exact:  # quoting was broken: don't trust pathspecs, show every change
            commit.update(pathspecs=[], all=True, dry_run=False)
        if not commit["dry_run"]:
            commit.update(
                repo=repo, stdin=stdin, heredocs=heredocs, adds=list(state["adds"])
            )
            state["commits"].append(commit)
    elif sub in ("add", "stage", "rm", "mv"):
        state["adds"].append({"repo": repo, "sub": sub, "args": rest})


def parse_commit(args):
    c = {
        "messages": [],
        "file": None,
        "reuse": None,
        "all": False,
        "include": False,
        "pathspecs": [],
        "amend": False,
        "dry_run": False,
    }
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            c["pathspecs"] += args[i:]
            break
        if a.startswith("--"):
            name, eq, value = a.partition("=")
            if name in COMMIT_VALUE_LONG and not eq:
                value = args[i] if i < len(args) else ""
                i += 1
            commit_option(c, name, value)
        elif a.startswith("-") and len(a) > 1:
            for j, ch in enumerate(a[1:], 1):
                if ch in COMMIT_VALUE_SHORT:
                    value = a[j + 1 :]
                    if not value:
                        value = args[i] if i < len(args) else ""
                        i += 1
                    commit_option(c, "-" + ch, value)
                    break
                if ch in "uS":  # optional value attached to the flag
                    break
                commit_option(c, "-" + ch, "")
        else:
            c["pathspecs"].append(a)
    return c


def commit_option(c, name, value):
    if name in ("-m", "--message"):
        c["messages"].append(value)
    elif name in ("-F", "--file"):
        c["file"] = value
    elif name in ("-C", "-c", "--reuse-message", "--reedit-message"):
        c["reuse"] = f"(reused from commit {value})"
    elif name in ("--fixup", "--squash"):
        c["reuse"] = f"({name[2:]}! {value})"
    elif name in ("-a", "--all", "--pathspec-from-file"):
        c["all"] = True
    elif name in ("-i", "--include"):
        c["include"] = True
    elif name == "--amend":
        c["amend"] = True
    elif name == "--dry-run":
        c["dry_run"] = True


# --- git --------------------------------------------------------------------


def git(repo, *args):
    """Run a read-only git command where the commit will run. Raises GitError on failure."""
    remaining = DEADLINE - time.monotonic()
    if repo["cwd"] is None or remaining <= 0:
        raise GitError("unknown directory or out of time")
    try:
        proc = subprocess.run(
            ["git", *repo["opts"], *args],
            cwd=repo["cwd"],
            capture_output=True,
            check=False,
            timeout=remaining,
            env={**os.environ, **repo["env"], "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitError(str(e))
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip())
    return proc.stdout.decode("utf-8", "replace")


def git_ok(repo, *args):
    try:
        return git(repo, *args).strip()
    except GitError:
        return None


def numstat(repo, *args):
    """Parse `git diff --numstat -z` into file entries. Binary files get None counts."""
    fields = git(repo, "diff", "--numstat", "-z", "--no-ext-diff", *args).split("\0")
    entries, i = [], 0
    while i < len(fields):
        head = fields[i]
        i += 1
        if not head:
            continue
        added, deleted, path = head.split("\t", 2)
        names = [path]
        if not path:  # rename or copy: old and new names follow
            names = fields[i : i + 2]
            i += 2
            path = " -> ".join(names)
        binary = added == "-"
        entries.append(
            {
                "path": path,
                "names": names,
                "added": None if binary else int(added),
                "deleted": None if binary else int(deleted),
            }
        )
    return entries


def count_lines(path):
    try:
        if path.is_symlink():
            return 1
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def new_files_from_adds(adds, toplevel):
    """Untracked files that an earlier `git add` in the same command will stage."""
    entries = []
    for add in adds:
        if add["sub"] not in ("add", "stage"):
            continue
        flags = [a for a in add["args"] if a.startswith("-")]
        if any(f in ("-u", "--update") for f in flags):
            continue
        pathspecs = [a for a in add["args"] if not a.startswith("-")]
        if not pathspecs and any(
            f in ("-A", "--all", "--no-ignore-removal") for f in flags
        ):
            pathspecs = [":/"]
        if not pathspecs:
            continue
        out = git(
            add["repo"],
            "ls-files",
            "--others",
            "--exclude-standard",
            "--full-name",
            "-z",
            "--",
            *pathspecs,
        )
        for name in filter(None, out.split("\0")):
            lines = count_lines(Path(toplevel) / name)
            entries.append(
                {
                    "path": name + "  (new)",
                    "names": [name],
                    "added": lines,
                    "deleted": None if lines is None else 0,
                }
            )
    return entries


def merge(*lists):
    """Combine entry lists by path, adding counts (used when there is no HEAD to diff against)."""
    merged = {}
    for entry in (e for lst in lists for e in lst):
        old = merged.get(entry["path"])
        if old is None:
            merged[entry["path"]] = dict(entry)
        elif old["added"] is not None and entry["added"] is not None:
            old["added"] += entry["added"]
            old["deleted"] += entry["deleted"]
        else:
            old["added"] = old["deleted"] = None
    return list(merged.values())


def describe(commit):
    """Branch, repository and file list for one commit. Raises GitError."""
    repo = commit["repo"]
    toplevel = git(repo, "rev-parse", "--show-toplevel").strip()
    has_head = git_ok(repo, "rev-parse", "--verify", "-q", "HEAD") is not None
    branch = git_ok(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if not branch:
        branch = "detached HEAD at " + git(repo, "rev-parse", "--short", "HEAD").strip()
    staging = bool(commit["all"] or commit["pathspecs"] or commit["adds"])
    if not staging:
        entries = numstat(repo, "--cached")
    elif has_head:
        only = commit["pathspecs"] and not commit["include"]
        entries = (
            numstat(repo, "HEAD", "--", *commit["pathspecs"])
            if only
            else numstat(repo, "HEAD")
        )
    else:
        entries = merge(numstat(repo, "--cached"), numstat(repo))
    known = {name for e in entries for name in e["names"]}
    entries += [
        e
        for e in new_files_from_adds(commit["adds"], toplevel)
        if e["names"][0] not in known
    ]
    subject = (
        git_ok(repo, "log", "-1", "--format=%s")
        if commit["amend"] and has_head
        else None
    )
    home = str(Path.home())
    shown = (
        "~" + toplevel[len(home) :] if toplevel.startswith(home + os.sep) else toplevel
    )
    return {
        "toplevel": shown,
        "branch": branch,
        "entries": entries,
        "staging": staging,
        "subject": subject,
    }


# --- summary ----------------------------------------------------------------


def commit_message(commit, cfg):
    def resolve(value):
        m = HEREDOC_SUBST.match(value.strip())
        return (
            commit["heredocs"][int(m.group(1))]
            if m and commit.get("heredocs")
            else value
        )

    if commit["messages"]:
        return "\n\n".join(resolve(m) for m in commit["messages"])
    if commit["file"] == "-":
        return (
            commit["stdin"]
            if commit["stdin"] is not None
            else "(read from standard input)"
        )
    if commit["file"]:
        try:
            base = commit["repo"]["cwd"] or "."
            with open(
                os.path.join(base, expand(commit["file"])),
                encoding="utf-8",
                errors="replace",
            ) as f:
                return f.read(64_000)
        except OSError:
            return f"(from file {commit['file']}, which couldn't be read)"
    return commit["reuse"] or cfg["no_message"]


def clip(line):
    return line if len(line) <= MAX_LINE_CHARS else line[: MAX_LINE_CHARS - 3] + "..."


def message_lines(message, limit):
    lines = message.strip("\n").splitlines() or [""]
    out = ["  " + clip(line) if line.strip() else "" for line in lines[:limit]]
    if len(lines) > limit:
        out.append(f"  ...and {len(lines) - limit} more lines")
    return out


def file_lines(entries, limit):
    rows = []
    for e in entries[:limit]:
        if e["added"] is None:
            rows.append(("binary", "", e["path"]))
        else:
            rows.append((f"+{e['added']}", f"-{e['deleted']}", e["path"]))
    wa = max((len(r[0]) for r in rows), default=0)
    wd = max((len(r[1]) for r in rows), default=0)
    out = [f"  {a.rjust(wa)} {d.rjust(wd)}  {clip(p)}" for a, d, p in rows]
    if len(entries) > limit:
        out.append(f"  ...and {len(entries) - limit} more")
    return out


def build_reason(commits, cfg, problems):
    """Return (should_ask, reason text) for the first commit in the command."""
    commit = commits[0]
    lines = [cfg["header"], ""] + problems + ([""] if problems else [])
    if len(commits) > 1:
        lines += [fill(cfg["multiple_commits_note"], count=len(commits)), ""]
    try:
        info = describe(commit)
    except GitError:
        info = None
    if info:
        lines += [f"Repository: {info['toplevel']}", f"Branch:     {info['branch']}"]
        if commit["amend"]:
            lines.append(
                fill(cfg["amend_note"], subject=info["subject"] or "(unknown)")
            )
    lines += ["Message:"] + message_lines(
        commit_message(commit, cfg), cfg["max_message_lines"]
    )
    if not info:
        lines += ["", cfg["summary_failed"]]
        return True, finish(lines, cfg)

    entries = info["entries"]
    sizes = [e["added"] + e["deleted"] for e in entries if e["added"] is not None]
    total_added = sum(e["added"] for e in entries if e["added"] is not None)
    total_deleted = sum(e["deleted"] for e in entries if e["added"] is not None)
    lines += ["", f"Files ({len(entries)} changed, +{total_added} -{total_deleted}):"]
    lines += (
        file_lines(entries, cfg["max_files_listed"])
        if entries
        else ["  (nothing staged)"]
    )
    if info["staging"]:
        lines += ["", cfg["staging_note"]]

    warnings, always = [], []
    for e in entries:
        pattern = first_match(e["names"], cfg["sensitive_patterns"])
        if pattern:
            warnings.append(
                fill(cfg["sensitive_warning"], path=e["path"], pattern=pattern)
            )
        pattern = first_match(e["names"], cfg["always_ask_paths"])
        if pattern:
            always.append(fill(cfg["always_ask_note"], path=e["path"], pattern=pattern))
    for group in (warnings, always):
        if group:
            limit = cfg["max_files_listed"]
            lines += (
                [""]
                + group[:limit]
                + ([f"...and {len(group) - limit} more"] if len(group) > limit else [])
            )

    unmeasured = len(sizes) < len(entries)
    forced = warnings or always or unmeasured or commit["amend"] or problems
    if not forced and sum(sizes) < cfg["min_changed_lines"]:
        return False, ""
    return True, finish(lines, cfg)


def finish(lines, cfg):
    body = "\n".join(lines).rstrip()
    if len(body) > MAX_REASON_CHARS:
        body = body[:MAX_REASON_CHARS].rstrip() + "\n...(summary truncated)"
    return f"{body}\n\n{cfg['rejection_note']}\n{cfg['question']}"


def ask(reason, cfg):
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                    "additionalContext": cfg["claude_instruction"],
                }
            }
        )
    )
    return 0


def main(raw):
    payload = json.loads(raw)
    command = (payload.get("tool_input") or {}).get("command", "")
    if not isinstance(command, str) or not command.strip():
        return 0
    state = {"cwd": payload.get("cwd") or os.getcwd(), "adds": [], "commits": []}
    walk(command, state)
    if not state["commits"]:
        return 0
    cfg, problems = load_config()
    should_ask, reason = build_reason(state["commits"], cfg, problems)
    return ask(reason, cfg) if should_ask else 0


if __name__ == "__main__":
    raw = sys.stdin.read()
    try:
        sys.exit(main(raw))
    except (ValueError, LookupError, TypeError, AttributeError, OSError, RuntimeError):
        # Fail safe: if anything unexpected breaks on what may be a commit, still ask.
        # Anything else exits non-zero, which hooks.json's onFailure "block" turns into a denial.
        if "commit" in raw:
            ask(
                f"{DEFAULTS['header']}\n\n{DEFAULTS['summary_failed']}\n\n"
                f"{DEFAULTS['rejection_note']}\n{DEFAULTS['question']}",
                DEFAULTS,
            )
        sys.exit(0)
