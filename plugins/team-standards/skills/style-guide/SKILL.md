---
name: style-guide
description: Our team's engineering style guide for Python, TypeScript and Rust. Use when writing, reviewing or refactoring code in any of these languages, when the user asks how we do something (naming, errors, logging, tests), or when asked to check code against our standards.
---

# Team style guide (PoC placeholder)

> This is sample content for the proof of concept. Replace it with the real
> style guide. Keep this file short and move long material into
> `references/` files that Claude reads only when needed.

Linters and formatters enforce the mechanical rules automatically (the
`*-lint` plugins run them on every edit). This guide covers the judgment calls
the tools can't check.

## All languages
- Fix lint findings; never silence them with suppression comments or config
  changes unless the user agrees. If a suppression is truly needed, add a
  comment explaining why on the same line.
- Prefer small, pure functions. Keep side effects at the edges.
- Errors must carry context: what failed and with which input.
- No secrets, tokens or customer data in code, logs, tests or fixtures.
- Every bug fix comes with a test that fails without the fix.

## Python
- Target the Python version in `pyproject.toml`. Use type hints on public functions.
- Use `pathlib`, f-strings, and `logging` (never `print`) in library code.
- Raise specific exceptions; never `except:` or `except Exception: pass`.
- Tests use `pytest`, live in `tests/`, and mirror the source layout.

## TypeScript
- `strict` mode is on. Avoid `any`; use `unknown` and narrow.
- Prefer `type` aliases for data, `interface` for extendable contracts.
- Use `async`/`await`, never raw `.then()` chains. Always handle rejections.
- Named exports only; no default exports.

## Rust
- Libraries return `Result` with a typed error (`thiserror`); binaries may use `anyhow`.
- No `unwrap()`/`expect()` outside tests and `main`, unless an invariant is
  documented in a comment.
- Clippy runs with `-D warnings`. Don't add `#[allow(...)]` to get past it.
- Public items get doc comments with at least one example where practical.

## Reviewing code against this guide
When asked to check code, report findings grouped by severity (must fix,
should fix, nit), cite the rule above, and suggest the concrete change.
