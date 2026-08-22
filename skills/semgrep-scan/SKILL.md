---
name: semgrep-scan
description: >-
  Run Semgrep SAST (CLI and/or Guardian MCP) on files, a directory, a diff, or
  the repo. Use when the user asks for Semgrep, Guardian findings, rule-based
  SAST, or a quick security scan of code changes.
metadata:
  skill-audit-context-reads: scan_scope, changed_files, rule_config, privacy_constraints
  skill-audit-context-requires: explicit_scan_goal, target_scope
  skill-audit-context-writes: commands_run, security_findings, verification_result
  skill-audit-confirmation: on-risk
compatibility: Requires the Semgrep CLI for local scans or a configured Guardian MCP integration for platform findings; registry rules and cloud findings require network access and may require authentication.
---

# Semgrep scan (shared)

Prefer local **Semgrep CLI** for on-demand scans. Use **Guardian MCP** when the user wants platform/history findings or login.

## CLI (works on all harnesses)

```bash
semgrep scan --config auto <path>
# or JSON for triage:
semgrep scan --config auto --json <path>
# git-aware CI-style:
semgrep ci
```

- Binary: `~/.local/bin/semgrep` or `semgrep` on PATH.
- Scope to changed files when the user asks about a PR/branch (`git diff --name-only`).
- Summarise findings: file, line, check id, severity/message; propose fixes; re-scan after edits.

## Guardian MCP (when available)

Server name is usually **`guardian`** (Cursor may label it `Guardian`).

Useful tools:

- `get_semgrep_sast_findings` — existing AppSec platform findings (does **not** run a new local scan)
- `get_semgrep_secrets_findings` / `get_semgrep_supply_chain_findings`
- `list_semgrep_projects`, `login`, `whoami`

If Guardian is missing, say so and fall back to the CLI.

## Codex note

Codex runs a PostToolUse Semgrep CLI hook on writes (`~/.codex/scripts/semgrep_sast_hook.py`) and exposes Guardian as `mcp_servers.guardian`. Still use this skill for intentional full/diff scans.
