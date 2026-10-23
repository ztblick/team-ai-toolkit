# rust-lint

Runs rustfmt on every .rs file Claude edits, and `cargo clippy -D warnings` once when Claude tries to finish. Clippy findings send Claude back to work (once per stop). Requires `rustup component add rustfmt clippy`.

Part of the team-ai-toolkit marketplace. See the root README for setup.
