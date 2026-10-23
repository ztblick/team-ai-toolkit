#!/usr/bin/env python3
"""Smoke tests for every hook script in the marketplace.

Feeds each script the same JSON payload Claude Code sends on stdin and checks
the exit code / output. Language tests are skipped when that toolchain isn't
installed. Run from the repo root:

    python3 tests/run_hook_tests.py
    python3 tests/run_hook_tests.py --ts-project /path/to/project/with/eslint
"""

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
results = []


def run_hook(plugin, script, payload):
    proc = subprocess.run(
        [sys.executable, str(PLUGINS / plugin / "scripts" / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def check(name, condition, detail=""):
    results.append(condition)
    print(
        f"  {'PASS' if condition else 'FAIL'}  {name}"
        + (f"\n        {detail}" if detail and not condition else "")
    )


def skip(name, why):
    print(f"  SKIP  {name} ({why})")


def edit_payload(path, cwd):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "cwd": str(cwd),
        "tool_input": {"file_path": str(path), "old_string": "", "new_string": ""},
    }


def bash_payload(cmd, cwd):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "cwd": str(cwd),
        "tool_input": {"command": cmd},
    }


def test_python(tmp):
    print("python-lint")
    if not (shutil.which("ruff") or shutil.which("uvx")):
        return skip("all", "ruff not installed")
    f = tmp / "messy.py"
    f.write_text("import os\nimport sys\ndef add( a,b ):\n  return a+b\n")
    code, _, err = run_hook("python-lint", "python_lint.py", edit_payload(f, tmp))
    check(
        "auto-formats and removes unused imports",
        code == 0
        and "def add(a, b):" in f.read_text()
        and "import os" not in f.read_text(),
        f"exit={code} err={err} file={f.read_text()!r}",
    )

    bad = tmp / "bad.py"
    bad.write_text("def f():\n    return undefined_name\n")
    code, _, err = run_hook("python-lint", "python_lint.py", edit_payload(bad, tmp))
    check(
        "reports unfixable errors to Claude (exit 2)",
        code == 2 and "F821" in err,
        f"exit={code} err={err}",
    )

    other = tmp / "notes.txt"
    other.write_text("x")
    code, *_ = run_hook("python-lint", "python_lint.py", edit_payload(other, tmp))
    check("ignores non-Python files", code == 0)


def test_typescript(tmp, project):
    print("typescript-lint")
    if project is None:
        return skip(
            "all", "pass --ts-project <dir with eslint+prettier in node_modules>"
        )
    src = Path(project) / "src"
    src.mkdir(exist_ok=True)
    (Path(project) / "eslint.config.mjs").write_text(
        'import js from "@eslint/js";\nimport tseslint from "typescript-eslint";\n'
        "export default tseslint.config(js.configs.recommended, ...tseslint.configs.recommended);\n"
    )
    f = src / "messy.ts"
    f.write_text("export const add=(a:number,b:number)=>{return a+b}\n")
    code, _, err = run_hook(
        "typescript-lint", "typescript_lint.py", edit_payload(f, project)
    )
    check(
        "Prettier formats the file",
        code == 0 and "(a: number, b: number)" in f.read_text(),
        f"exit={code} err={err} file={f.read_text()!r}",
    )

    bad = src / "bad.ts"
    bad.write_text(
        "export function f(): number {\n  const unused = 1;\n  return 2;\n}\n"
    )
    code, _, err = run_hook(
        "typescript-lint", "typescript_lint.py", edit_payload(bad, project)
    )
    check(
        "reports unfixable ESLint errors (exit 2)",
        code == 2 and "no-unused-vars" in err,
        f"exit={code} err={err}",
    )


def test_rust(tmp):
    print("rust-lint")
    if not shutil.which("cargo") or not shutil.which("rustfmt"):
        return skip("all", "cargo/rustfmt not installed")
    crate = tmp / "demo_crate"
    subprocess.run(
        ["cargo", "new", "--lib", "--quiet", "--vcs", "git", str(crate)], check=True
    )
    lib = crate / "src" / "lib.rs"
    lib.write_text("pub fn add(a:i32,b:i32)->i32{a+b}\n")
    code, _, err = run_hook("rust-lint", "rust_fmt.py", edit_payload(lib, crate))
    check(
        "rustfmt formats the file",
        code == 0 and "pub fn add(a: i32, b: i32) -> i32" in lib.read_text(),
        f"exit={code} err={err} file={lib.read_text()!r}",
    )

    broken = crate / "src" / "broken.rs"
    broken.write_text("fn x( {\n")
    code, _, err = run_hook("rust-lint", "rust_fmt.py", edit_payload(broken, crate))
    check("reports syntax errors (exit 2)", code == 2, f"exit={code} err={err}")
    broken.unlink()

    lib.write_text(
        "pub fn is_on(b: bool) -> bool {\n    if b == true { true } else { false }\n}\n"
    )
    stop = {"hook_event_name": "Stop", "cwd": str(crate), "stop_hook_active": False}
    code, _, err = run_hook("rust-lint", "rust_clippy_gate.py", stop)
    check(
        "clippy gate blocks stop on warnings (exit 2)",
        code == 2 and "clippy" in err.lower(),
        f"exit={code} err={err[:300]}",
    )
    code, *_ = run_hook(
        "rust-lint", "rust_clippy_gate.py", {**stop, "stop_hook_active": True}
    )
    check("clippy gate doesn't loop when stop_hook_active", code == 0)


def test_git_guard(tmp):
    print("git-guardrails: guard_git")
    blocked = [
        'git commit -m "wip" --no-verify',
        "git commit -nm wip",
        "git push --no-verify origin feature",
        "SKIP=ruff git commit -m x",
        "HUSKY=0 git commit -m x",
        "git -c core.hooksPath=/dev/null commit -m x",
        "git push --force origin main",
        "git push -f origin HEAD:main",
        "npm test && git commit --no-verify -m done",
    ]
    allowed = [
        'git commit -m "add feature"',
        "git push origin feature/x",
        "git push --force-with-lease origin feature/x",
        "git log -n 5",
        "git push -n origin main",  # -n on push is --dry-run
        "echo --no-verify",
    ]
    for cmd in blocked:
        code, _, err = run_hook(
            "git-guardrails", "guard_git.py", bash_payload(cmd, tmp)
        )
        check(f"blocks: {cmd}", code == 2 and "BLOCKED" in err, f"exit={code}")
    for cmd in allowed:
        code, *_ = run_hook("git-guardrails", "guard_git.py", bash_payload(cmd, tmp))
        check(f"allows: {cmd}", code == 0, f"exit={code}")


def test_lint_config_guard(tmp):
    print("git-guardrails: guard_lint_config")

    def decision(tool_input):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Edit",
            "cwd": str(tmp),
            "tool_input": tool_input,
        }
        code, out, _ = run_hook("git-guardrails", "guard_lint_config.py", payload)
        try:
            return json.loads(out)["hookSpecificOutput"]["permissionDecision"]
        except (ValueError, KeyError, TypeError):
            return "allow" if code == 0 else f"exit {code}"

    check(
        "asks before editing ruff.toml",
        decision(
            {
                "file_path": str(tmp / "ruff.toml"),
                "old_string": "",
                "new_string": "ignore=['E501']",
            }
        )
        == "ask",
    )
    check(
        "asks before editing eslint.config.mjs",
        decision(
            {
                "file_path": str(tmp / "eslint.config.mjs"),
                "old_string": "a",
                "new_string": "b",
            }
        )
        == "ask",
    )
    check(
        "asks when adding # noqa",
        decision(
            {
                "file_path": str(tmp / "a.py"),
                "old_string": "x = 1",
                "new_string": "x = 1  # noqa",
            }
        )
        == "ask",
    )
    check(
        "asks when adding #[allow(...)]",
        decision(
            {
                "file_path": str(tmp / "a.rs"),
                "old_string": "fn a()",
                "new_string": "#[allow(dead_code)]\nfn a()",
            }
        )
        == "ask",
    )
    check(
        "asks when adding eslint-disable",
        decision(
            {
                "file_path": str(tmp / "a.ts"),
                "old_string": "",
                "new_string": "// eslint-disable-next-line",
            }
        )
        == "ask",
    )
    check(
        "allows normal code edits",
        decision(
            {
                "file_path": str(tmp / "a.py"),
                "old_string": "x = 1",
                "new_string": "x = 2",
            }
        )
        == "allow",
    )

    pyproject = tmp / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\ndependencies = []\n\n[tool.ruff]\nline-length = 100\n'
    )
    check(
        "allows pyproject writes that leave [tool.ruff] alone",
        decision(
            {
                "file_path": str(pyproject),
                "content": '[project]\nname = "x"\ndependencies = ["requests"]\n\n[tool.ruff]\nline-length = 100\n',
            }
        )
        == "allow",
    )
    check(
        "asks on pyproject writes that change [tool.ruff]",
        decision(
            {
                "file_path": str(pyproject),
                "content": '[project]\nname = "x"\ndependencies = []\n\n[tool.ruff]\nline-length = 200\n',
            }
        )
        == "ask",
    )


def git_repo(path):
    path.mkdir(parents=True)
    for cmd in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "Test"],
    ):
        subprocess.run(["git", *cmd], cwd=path, check=True)
    (path / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def plugin_copy(tmp, name, config_text):
    """A copy of commit-approval with its own config.json, so tests can change settings."""
    dest = tmp / name
    shutil.copytree(PLUGINS / "commit-approval", dest)
    (dest / "config.json").write_text(config_text)
    return dest


def test_commit_approval(tmp):
    print("commit-approval")
    question = "As a Lucid engineer, do you understand and approve these changes?"

    def approval(cmd, cwd, plugin_dir=PLUGINS / "commit-approval"):
        proc = subprocess.run(
            [sys.executable, str(plugin_dir / "scripts" / "commit_approval.py")],
            input=json.dumps(bash_payload(cmd, cwd)),
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            out = json.loads(proc.stdout)["hookSpecificOutput"]
            return out["permissionDecision"], out["permissionDecisionReason"]
        except (ValueError, KeyError, TypeError):
            return (
                "none"
                if proc.returncode == 0 and not proc.stdout.strip()
                else f"exit {proc.returncode}"
            ), proc.stderr

    repo = git_repo(tmp / "approval_repo")
    (repo / "app.py").write_text("print('hello')\nprint('world')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    decision, reason = approval('git commit -m "Say world"', repo)
    check(
        "asks on a small staged change, with question and file names",
        decision == "ask"
        and question in reason
        and "app.py" in reason
        and "Say world" in reason
        and "+1 -0" in reason,
        f"{decision}: {reason}",
    )

    (repo / "sub").mkdir()
    for cmd, cwd in (  # tmp itself is not a git repo
        ("cd approval_repo/sub && git commit -m x", tmp),
        (f"git -C {repo} commit -m x", tmp),
        ("GIT_AUTHOR_NAME=bot git commit -m x", repo),
        ("bash -c 'git commit -m x'", repo),
        ("git commit -m x 2>&1 | tail -1", repo),
    ):
        decision, reason = approval(cmd, cwd)
        check(
            f"detects: {cmd.replace(str(repo), '<repo>')}",
            decision == "ask" and "app.py" in reason,
            f"{decision}: {reason}",
        )

    heredoc_cmd = (
        'git commit -m "$(cat <<\'EOF\'\nAdd world\n\nExplains "why".\nEOF\n)"'
    )
    decision, reason = approval(heredoc_cmd, repo)
    check(
        "shows a heredoc commit message",
        decision == "ask"
        and "Add world" in reason
        and 'Explains "why".' in reason
        and "EOF" not in reason,
        reason,
    )
    decision, reason = approval(
        "git commit -m 'unclosed\n\nCo-Authored-By: Bot <bot@example.com>", repo
    )
    check(
        "broken quoting still shows the staged file",
        decision == "ask" and "app.py" in reason,
        reason,
    )

    (repo / "app.py").write_text("print('hello')\nprint('world')\nprint('again')\n")
    (repo / "new_module.py").write_text("x = 1\ny = 2\n")
    decision, reason = approval('git add . && git commit -m "x"', repo)
    check(
        "`git add . && git commit` includes working-tree changes",
        decision == "ask"
        and "+2 -0  app.py" in reason
        and "new_module.py" in reason
        and "decided at commit time" in reason,
        reason,
    )
    decision, reason = approval("git commit -am x", repo)
    check(
        "`commit -a` includes unstaged tracked changes",
        decision == "ask"
        and "+2 -0  app.py" in reason
        and "new_module.py" not in reason,
        reason,
    )

    for cmd in (
        "git status",
        "git log --oneline -5",
        "git push origin main",
        "git commit --dry-run -m x",
        "echo git commit",
        "git log --grep commit",
    ):
        check(f"doesn't prompt: {cmd}", approval(cmd, repo)[0] == "none")

    (repo / ".env").write_text("API_KEY=abc\n")
    subprocess.run(["git", "add", ".env"], cwd=repo, check=True)
    decision, reason = approval("git commit -m secrets", repo)
    check(
        "warns about sensitive files",
        "WARNING: .env matches sensitive pattern .env" in reason,
        reason,
    )
    subprocess.run(["git", "reset", "-q"], cwd=repo, check=True)

    config = json.loads((PLUGINS / "commit-approval" / "config.json").read_text())
    thresholded = plugin_copy(
        tmp, "approval_threshold", json.dumps({**config, "min_changed_lines": 50})
    )
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    check(
        "doesn't prompt below min_changed_lines",
        approval("git commit -m x", repo, thresholded)[0] == "none",
    )
    (repo / "migrations").mkdir()
    (repo / "migrations" / "0001_init.sql").write_text("CREATE TABLE t (id int);\n")
    subprocess.run(["git", "add", "migrations"], cwd=repo, check=True)
    decision, reason = approval("git commit -m x", repo, thresholded)
    check(
        "always_ask_paths overrides the threshold",
        decision == "ask"
        and "migrations/0001_init.sql matches migrations/**" in reason,
        reason,
    )

    broken = plugin_copy(tmp, "approval_broken", "{ this is not json")
    decision, reason = approval("git commit -m x", repo, broken)
    check(
        "invalid config.json still prompts, with the default question",
        decision == "ask" and question in reason and "config.json is invalid" in reason,
        reason,
    )

    outside = tmp / "not_a_repo"
    outside.mkdir()
    decision, reason = approval("git commit -m x", outside)
    check(
        "still prompts when git info is unavailable",
        decision == "ask" and "Couldn't summarize the changes" in reason,
        reason,
    )

    spec = importlib.util.spec_from_file_location(
        "commit_approval",
        PLUGINS / "commit-approval" / "scripts" / "commit_approval.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    check(
        "built-in defaults match config.json",
        module.DEFAULTS == config,
        f"differs in: {sorted(k for k in config if module.DEFAULTS.get(k) != config[k])}",
    )


def test_versions(tmp):
    print("marketplace versions")
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for entry in marketplace["plugins"]:
        manifest = json.loads(
            (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").read_text()
        )
        check(
            f"{entry['name']}: plugin.json and marketplace.json versions match",
            manifest.get("version") == entry.get("version"),
            f"plugin.json={manifest.get('version')} marketplace.json={entry.get('version')}",
        )

    copy = tmp / "bump_copy"
    (copy / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "bump_version.py", copy / "scripts")
    shutil.copytree(ROOT / ".claude-plugin", copy / ".claude-plugin")
    shutil.copytree(
        PLUGINS / "commit-approval" / ".claude-plugin",
        copy / "plugins" / "commit-approval" / ".claude-plugin",
    )
    old = json.loads(
        (
            copy / "plugins" / "commit-approval" / ".claude-plugin" / "plugin.json"
        ).read_text()
    )["version"]
    proc = subprocess.run(
        [
            sys.executable,
            str(copy / "scripts" / "bump_version.py"),
            "commit-approval",
            "minor",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    major, minor, _ = map(int, old.split("."))
    new = json.loads(
        (
            copy / "plugins" / "commit-approval" / ".claude-plugin" / "plugin.json"
        ).read_text()
    )["version"]
    entry = next(
        p
        for p in json.loads((copy / ".claude-plugin" / "marketplace.json").read_text())[
            "plugins"
        ]
        if p["name"] == "commit-approval"
    )
    check(
        "bump_version.py bumps both files",
        proc.returncode == 0 and new == entry["version"] == f"{major}.{minor + 1}.0",
        f"exit={proc.returncode} {proc.stderr} plugin.json={new} marketplace={entry['version']}",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ts-project",
        help="directory with eslint, prettier and typescript-eslint installed",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_python(tmp)
        test_typescript(tmp, args.ts_project)
        test_rust(tmp)
        test_git_guard(tmp)
        test_lint_config_guard(tmp)
        test_commit_approval(tmp)
        test_versions(tmp)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
