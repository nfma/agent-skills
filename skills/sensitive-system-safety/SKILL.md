---
name: sensitive-system-safety
description: Handle sensitive or high-impact local system work safely. Use when a task may access or change dotfiles, credentials, tokens, SSH or GnuPG data, keychains, privileged or system-wide configuration, or when it may delete, overwrite, or irreversibly alter data.
---

# Sensitive System Safety

## Scope

- Treat an explicit request as authority for the named target and the minimum adjacent state needed to complete it.
- Proceed without extra confirmation for directly requested, reversible changes.
- Do not access sensitive locations merely because they may be useful. If access is necessary but not clearly in scope, ask first.

## Secrets

- Prefer existence, permission, and metadata checks over reading secret values.
- Do not access private keys, credential stores, keychains, or raw tokens unless the request clearly requires it.
- Never expose secret values in commands, logs, patches, or responses. Redact incidental disclosures.

## Changes

- Resolve and inspect exact targets before changing them. Avoid broad paths, unresolved variables, and ambiguous globs.
- Prefer focused, recoverable operations that preserve unrelated state.
- Ask immediately before destructive, difficult-to-recover, or materially broader actions.
- After deleting material data, state what was removed and whether it can be recovered.
