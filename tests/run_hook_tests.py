#!/usr/bin/env python3
"""Smoke tests for every hook script in the marketplace.

Feeds each script the same JSON payload Claude Code sends on stdin and checks
the exit code / output. Language tests are skipped when that toolchain isn't
installed. Run from the repo root:

    python3 tests/run_hook_tests.py
    python3 tests/run_hook_tests.py --ts-project /path/to/project/with/eslint
"""
import argparse
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
        input=json.dumps(payload), capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def check(name, condition, detail=""):
    results.append(condition)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"\n        {detail}" if detail and not condition else ""))


def skip(name, why):
    print(f"  SKIP  {name} ({why})")


def edit_payload(path, cwd):
    return {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(cwd),
            "tool_input": {"file_path": str(path), "old_string": "", "new_string": ""}}


def bash_payload(cmd, cwd):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(cwd),
            "tool_input": {"command": cmd}}


def test_python(tmp):
    print("python-lint")
    if not (shutil.which("ruff") or shutil.which("uvx")):
        return skip("all", "ruff not installed")
    f = tmp / "messy.py"
    f.write_text("import os\nimport sys\ndef add( a,b ):\n  return a+b\n")
    code, _, err = run_hook("python-lint", "python_lint.py", edit_payload(f, tmp))
    check("auto-formats and removes unused imports", code == 0 and "def add(a, b):" in f.read_text()
          and "import os" not in f.read_text(), f"exit={code} err={err} file={f.read_text()!r}")

    bad = tmp / "bad.py"
    bad.write_text("def f():\n    return undefined_name\n")
    code, _, err = run_hook("python-lint", "python_lint.py", edit_payload(bad, tmp))
    check("reports unfixable errors to Claude (exit 2)", code == 2 and "F821" in err, f"exit={code} err={err}")

    other = tmp / "notes.txt"
    other.write_text("x")
    code, *_ = run_hook("python-lint", "python_lint.py", edit_payload(other, tmp))
    check("ignores non-Python files", code == 0)


def test_typescript(tmp, project):
    print("typescript-lint")
    if project is None:
        return skip("all", "pass --ts-project <dir with eslint+prettier in node_modules>")
    src = Path(project) / "src"
    src.mkdir(exist_ok=True)
    (Path(project) / "eslint.config.mjs").write_text(
        'import js from "@eslint/js";\nimport tseslint from "typescript-eslint";\n'
        "export default tseslint.config(js.configs.recommended, ...tseslint.configs.recommended);\n")
    f = src / "messy.ts"
    f.write_text("export const add=(a:number,b:number)=>{return a+b}\n")
    code, _, err = run_hook("typescript-lint", "typescript_lint.py", edit_payload(f, project))
    check("Prettier formats the file", code == 0 and "(a: number, b: number)" in f.read_text(),
          f"exit={code} err={err} file={f.read_text()!r}")

    bad = src / "bad.ts"
    bad.write_text("export function f(): number {\n  const unused = 1;\n  return 2;\n}\n")
    code, _, err = run_hook("typescript-lint", "typescript_lint.py", edit_payload(bad, project))
    check("reports unfixable ESLint errors (exit 2)", code == 2 and "no-unused-vars" in err, f"exit={code} err={err}")


def test_rust(tmp):
    print("rust-lint")
    if not shutil.which("cargo") or not shutil.which("rustfmt"):
        return skip("all", "cargo/rustfmt not installed")
    crate = tmp / "demo_crate"
    subprocess.run(["cargo", "new", "--lib", "--quiet", "--vcs", "git", str(crate)], check=True)
    lib = crate / "src" / "lib.rs"
    lib.write_text("pub fn add(a:i32,b:i32)->i32{a+b}\n")
    code, _, err = run_hook("rust-lint", "rust_fmt.py", edit_payload(lib, crate))
    check("rustfmt formats the file", code == 0 and "pub fn add(a: i32, b: i32) -> i32" in lib.read_text(),
          f"exit={code} err={err} file={lib.read_text()!r}")

    broken = crate / "src" / "broken.rs"
    broken.write_text("fn x( {\n")
    code, _, err = run_hook("rust-lint", "rust_fmt.py", edit_payload(broken, crate))
    check("reports syntax errors (exit 2)", code == 2, f"exit={code} err={err}")
    broken.unlink()

    lib.write_text("pub fn is_on(b: bool) -> bool {\n    if b == true { true } else { false }\n}\n")
    stop = {"hook_event_name": "Stop", "cwd": str(crate), "stop_hook_active": False}
    code, _, err = run_hook("rust-lint", "rust_clippy_gate.py", stop)
    check("clippy gate blocks stop on warnings (exit 2)", code == 2 and "clippy" in err.lower(),
          f"exit={code} err={err[:300]}")
    code, *_ = run_hook("rust-lint", "rust_clippy_gate.py", {**stop, "stop_hook_active": True})
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
        code, _, err = run_hook("git-guardrails", "guard_git.py", bash_payload(cmd, tmp))
        check(f"blocks: {cmd}", code == 2 and "BLOCKED" in err, f"exit={code}")
    for cmd in allowed:
        code, *_ = run_hook("git-guardrails", "guard_git.py", bash_payload(cmd, tmp))
        check(f"allows: {cmd}", code == 0, f"exit={code}")


def test_lint_config_guard(tmp):
    print("git-guardrails: guard_lint_config")

    def decision(tool_input):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": str(tmp), "tool_input": tool_input}
        code, out, _ = run_hook("git-guardrails", "guard_lint_config.py", payload)
        try:
            return json.loads(out)["hookSpecificOutput"]["permissionDecision"]
        except Exception:
            return "allow" if code == 0 else f"exit {code}"

    check("asks before editing ruff.toml",
          decision({"file_path": str(tmp / "ruff.toml"), "old_string": "", "new_string": "ignore=['E501']"}) == "ask")
    check("asks before editing eslint.config.mjs",
          decision({"file_path": str(tmp / "eslint.config.mjs"), "old_string": "a", "new_string": "b"}) == "ask")
    check("asks when adding # noqa",
          decision({"file_path": str(tmp / "a.py"), "old_string": "x = 1", "new_string": "x = 1  # noqa"}) == "ask")
    check("asks when adding #[allow(...)]",
          decision({"file_path": str(tmp / "a.rs"), "old_string": "fn a()", "new_string": "#[allow(dead_code)]\nfn a()"}) == "ask")
    check("asks when adding eslint-disable",
          decision({"file_path": str(tmp / "a.ts"), "old_string": "", "new_string": "// eslint-disable-next-line"}) == "ask")
    check("allows normal code edits",
          decision({"file_path": str(tmp / "a.py"), "old_string": "x = 1", "new_string": "x = 2"}) == "allow")

    pyproject = tmp / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\ndependencies = []\n\n[tool.ruff]\nline-length = 100\n')
    check("allows pyproject writes that leave [tool.ruff] alone",
          decision({"file_path": str(pyproject),
                    "content": '[project]\nname = "x"\ndependencies = ["requests"]\n\n[tool.ruff]\nline-length = 100\n'}) == "allow")
    check("asks on pyproject writes that change [tool.ruff]",
          decision({"file_path": str(pyproject),
                    "content": '[project]\nname = "x"\ndependencies = []\n\n[tool.ruff]\nline-length = 200\n'}) == "ask")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ts-project", help="directory with eslint, prettier and typescript-eslint installed")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_python(tmp)
        test_typescript(tmp, args.ts_project)
        test_rust(tmp)
        test_git_guard(tmp)
        test_lint_config_guard(tmp)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
