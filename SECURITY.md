# Security Policy

## Supported Versions

Security fixes are made on `main`. The repository is a configuration and skill
collection rather than a versioned package; use the latest protected `main`
commit and the release descriptor it contains.

## Reporting a Vulnerability

Use GitHub's private security advisory flow for `nfma/agent-skills`. Do not open
a public issue containing exploit details, credentials, personal data, or other
secrets. Include the affected commit, reachable input, impact, and a minimal
reproduction when it is safe to do so.

## System and Scope

This repository supplies agent skills, MCP configuration fragments, local
installers and launchers, synchronization tools, and the descriptor for the
attested `skill-audit` release. It can update user-level harness configuration,
links under agent-specific discovery directories, and launchers under
`~/.local/bin` after explicit user invocation.

Credentials are outside the repository boundary. Where a launcher needs a
credential, it must resolve that credential from a machine-local protected store
at runtime without persisting or printing it.

## Threat Model and Trust Boundaries

Treat pull-request content, skill text and examples, MCP fragments, downloaded
release artifacts, filesystem paths, existing harness configuration, command
output, and line-oriented relay input as attacker-controlled. Local Keychain
entries and a user-confirmed invocation are trusted only for the specific
operation requested.

## Security Invariants

- No credential, OAuth token, private key, or secret may be committed, logged,
  embedded in generated configuration, or passed through an untrusted child
  process.
- Installers and synchronizers must be dry-run capable, confine writes to their
  documented targets, preserve unrelated configuration, create recoverable
  backups, and reject unsafe symlinks, traversal, ambiguous formats, and partial
  writes.
- Tracked skill or configuration content must not gain code execution merely by
  being discovered, parsed, synchronized, or audited.
- Downloaded executables and rule feeds must be version-pinned and verified by
  size, digest, schema, and expected contract before use.
- Parsers and relays must bound input, fail closed on unsupported structures,
  avoid terminal-control injection, and never reinterpret data as commands.
- Repository workflows must use immutable actions, least-privilege tokens, no
  persisted checkout credentials, and protected review paths for mutations.

## Reportable Findings and Severity Context

Report credential exposure, untrusted code execution, command or workflow
injection, writes outside documented targets, backup or atomicity failures that
can destroy user configuration, symlink or traversal escapes, verification
bypasses, and fail-open auditing or parsing behavior. Severity should reflect
real reachability, the sensitivity of affected local state, and whether ordinary
installation or discovery triggers the behavior.

## Known Limitations

Some launchers and smoke tests are macOS-specific because they integrate with
Keychain and local desktop harnesses. Third-party agent, MCP, GitHub, Hugging
Face, Notion, and browser behavior remains outside this repository's control,
but unsafe handling of their data by this repository remains in scope.
