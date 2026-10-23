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


Requirements: Claude Code, `git`, and `python3` on PATH.

When updates are shipped to the plugins, people will need to have the auto-update feature enabled in claude code.
