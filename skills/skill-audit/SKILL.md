---
name: skill-audit
description: Security auditing tool for AI agent skills. Use when reviewing skill security, detecting vulnerabilities in skill code, checking for PII leakage, or validating skills against OWASP Agentic Top 10.
context:
  reads:
    - user_goal
    - target_skill_path
    - agent_environment_config
    - shell_command_under_review
  requires:
    - explicit_user_intent_for_audit_or_environment_check
  writes:
    - risk_summary
    - findings_count
    - environment_drift_summary
    - recommended_next_action
  confirmation: on-risk
---

# skill-audit

Security auditing CLI for AI agent skills.

## When to Use This Skill

- When installing new skills from external sources
- When auditing existing skills for security issues
- When validating skills before distribution
- When investigating security alerts in skill dependencies
- When checking whether the local agent shell/config environment is safe before invoking skills
- When comparing current agent environment state against a trusted baseline

## Quick Start

```bash
# Download and verify the immutable Node 24 release executable
version=v0.10.2
gh release download "$version" \
  --repo nfma/skill-audit \
  --pattern "skill-audit-$version.mjs" \
  --pattern "skill-audit-$version.mjs.sha256"
shasum -a 256 -c "skill-audit-$version.mjs.sha256"
install -m 0755 "skill-audit-$version.mjs" "$HOME/.local/bin/skill-audit"

# Audit skills
skill-audit -g              # Audit global skills
skill-audit -v              # Verbose output
skill-audit --json          # JSON for CI
skill-audit --threshold 5 --block  # Exit 1 if risk meets or exceeds 5
skill-audit --compliance    # Add opt-in regulatory heuristics

# Audit the agent execution environment
skill-audit doctor          # Read-only shell/config/PATH/hook scan
skill-audit trust env       # Save current environment baseline
skill-audit diff-env        # Detect drift from baseline

# Hook-friendly shell command assessment
skill-audit --check-command "npx skills add owner/repo"
```

## Release Integrity

GitHub Releases include the executable, its checksum, and a descriptor. The
descriptor's `embeddedRulesSha256` binds the decoded rule data rather than its
base64 transport. To recompute it, encode canonical UTF-8 JSON with array order
preserved and object keys sorted by UTF-8 bytes. Strings, booleans, and `null`
use JSON encoding; finite numbers use the RFC 8785 shortest
round-trippable IEEE 754 form, with negative zero encoded as `0`. Reject sparse
arrays, non-finite numbers, unsupported values, and non-plain objects.

The descriptor also binds normalized documentation digests. Verify both the
asset checksum and GitHub attestation before accepting an upgrade.

## Security Categories

| Category         | OWASP | What It Detects                                      |
| ---------------- | ----- | ---------------------------------------------------- |
| Prompt Injection | ASI01 | Ignore instructions, role bypass, context forgetting |
| Tool Misuse      | ASI02 | Data exfiltration, unauthorized API calls            |
| PII Exposure     | ASI03 | Hardcoded secrets, API keys, Vietnamese IDs          |
| Supply Chain     | ASI04 | Vulnerable dependencies, credential leaks            |
| Code Execution   | ASI05 | Shell injection, dangerous commands                  |
| Behavioral       | ASI09 | Manipulation attempts, blind trust requests          |

Compliance checks are opt-in heuristics for Vietnam AI Law 2026, the EU AI Act, and GDPR. They identify documentation gaps but are not a legal determination.

## Agent Environment Checks

`skill-audit doctor` checks risks outside a skill package, including:

- Agent hooks and config files
- Shell startup files
- PATH hijacking and workspace-local sensitive binaries
- MCP/tool command config risks
- Workspace instruction files such as `AGENTS.md`, `CLAUDE.md`, `QWEN.md`, and `GEMINI.md`
- Package lifecycle scripts that an agent might run through shell commands

Use `skill-audit trust env` and `skill-audit diff-env` to keep a compact trusted baseline across sessions. The baseline stores file hashes and redacted finding summaries, not full conversation history.

## Session Context Contracts

Skills should declare the narrow session facts they read, the preconditions required before invocation, what they write back after execution, and when user confirmation is needed. `skill-audit` reports `CTX-*` findings when these boundaries are absent.

## Risk Scoring

- **0**: Safe ✅
- **0.1-3.0**: Risky ⚠️
- **3.1-7.0**: Dangerous 🔴
- **7.1+**: Malicious ☠️

## Package Lifecycle Safety

The GitHub Release `.mjs` executable has no package-install lifecycle. When
auditing an npm package that declares lifecycle scripts, use
`references/postinstall-safety.md` to distinguish documented informational
behavior from unsafe automatic actions.
