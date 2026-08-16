# Agent Skills and MCPs

Nuno's shared skills and credential-free MCP configuration for Codex, Claude,
Cursor, and Antigravity.

## Layout

- `skills/` contains the discoverable skill directories.
- `mcp/manifest.json` defines the shared MCP baseline and pinned direct-server
  versions.
- `mcp/codex/` and `mcp/claude/` contain declarative MCP fragments that define
  native plugin ownership and direct user-level servers. The installer merges
  these fragments into each harness's monolithic user config.
- `mcp/cursor/` and `mcp/antigravity/` contain MCP-only configuration files
  that can safely be linked into those harnesses.
- `mcp/bin/` contains portable launchers that resolve credentials from local
  Keychain-backed stores at runtime.
- `vendor/skill-audit` pins the separately maintained `nfma/skill-audit` fork.
- `skills/skill-audit` links to the installable directory inside that submodule.
- `LICENSES/` and `THIRD_PARTY.md` preserve upstream licensing and provenance.

Clone with submodules:

```sh
git clone --recurse-submodules https://github.com/nfma/agent-skills.git
cd agent-skills
./scripts/prepare-vendored-skills.sh
```

The preparation step installs `skill-audit` dependencies without running lifecycle scripts, builds its ignored `dist/` runtime, and verifies the local CLI version.

To install a skill, symlink its directory under `skills/` into `~/.agents/skills/`. Harness-specific discovery links can continue pointing at `~/.agents/skills`.

## Skill synchronization

After linking repository skills into `~/.agents/skills/`, preview and apply the
harness discovery links:

```sh
./scripts/sync-agent-skills.sh --dry-run
./scripts/sync-agent-skills.sh
```

The synchronizer links the canonical skills directory into Claude and Gemini,
writes Gemini's skills discovery file, and creates per-skill links for Codex
without touching its `.system` skills. Cursor is left unchanged because it
discovers `~/.agents/skills/` directly.

Use `AGENT_SKILLS_HOME=/absolute/path` to override the canonical skills
directory, or `--verbose` to print every Codex skill link.

## MCP installation

Preview the all-harness installation, then apply it:

```sh
./scripts/install-mcps.sh --dry-run
./scripts/install-mcps.sh
```

Use repeated `--harness` flags to limit a run, for example:

```sh
./scripts/install-mcps.sh --harness codex --harness cursor
```

The installer:

- installs or verifies the plugins declared in the Codex and Claude fragments;
- reconciles the fragments' direct user MCP entries through the Codex and
  Claude CLIs;
- links Cursor and Antigravity's MCP-only config files to this checkout;
- links the runtime launchers into `~/.local/bin`;
- preserves replaced files in timestamped `~/.agents/mcp-backups/` folders.

It intentionally does not symlink `~/.codex/config.toml` or `~/.claude.json`,
because those files also contain unrelated user and project state.

GitHub is a deliberate Codex exception to native plugin ownership: the native
plugin requires a globally exported token, so Codex uses a tracked wrapper that
loads a dedicated fine-grained PAT from macOS Keychain. Claude's native GitHub
plugin has the same limitation, so Claude uses that wrapper directly as well.

No credentials belong in this repository. Create a fine-grained GitHub PAT at
<https://github.com/settings/personal-access-tokens/new>, limit it to the
required repositories and permissions, then store it interactively without
putting it in shell history:

```sh
security add-generic-password -U \
  -l GITHUB_MCP_PAT -s GITHUB_MCP_PAT -a "$USER" -w
```

Store the Hugging Face token the same way under the `HF_TOKEN` service. OAuth
and Keychain contents remain machine-local.

The installer uses the pinned Bats-core test framework; the JSON-line filter
uses Node's built-in test runner:

```sh
npm ci --ignore-scripts
uv sync --frozen
npm test
```

## Automated skill auditing

Every pull request and every push to `main` builds and tests the pinned
`skill-audit` submodule, installs pinned Trivy, and audits every skill under
`skills/`. Recursive discovery includes skills added later without changing the
workflow. The audit blocks high or critical findings and risk scores of 3 or
higher.

Reviewed false positives are recorded as exact fingerprints in
`.skill-audit-baseline.json`. A change to the finding's skill, identifier,
severity, file, line, or evidence requires fresh review, and stale entries fail
the workflow. `skill-audit` itself is spec-validated by the runner, then built,
dependency-audited, and tested separately because its source contains the
detection signatures it is designed to find.

Run the same audit locally after preparing the vendored CLI:

```sh
./scripts/prepare-vendored-skills.sh
node scripts/audit-skills.mjs
```

The workflow consumes the GitHub-pinned source directly; it never publishes
`skill-audit` to npm.

## Quality and security gates

Every pull request and push to `main` runs language-specific and repository
security gates:

- JavaScript and TypeScript: Prettier, ESLint security and SonarJS rules,
  strict TypeScript checking, Node tests, and `npm audit`.
- Python: Ruff linting and formatting, Pyright, mypy, Bandit, compile tests,
  and `pip-audit` from the pinned `uv.lock` environment.
- Shell: `bash -n` or `sh -n`, ShellCheck, shfmt, and Bats tests.
- Repository security: Skill Audit, Semgrep community security rules, Trivy
  vulnerability/misconfiguration/secret scanning, Gitleaks, CodeQL for
  Actions/JavaScript/TypeScript/Python, dependency review, and OpenSSF
  Scorecard.
- Workflow/configuration quality: Actionlint, Zizmor, yamllint, and JSON
  parsing.

First-party code must be clean. Existing findings in imported skill examples
are recorded as exact fingerprints in `.python-quality-baseline.json` and
`.shell-quality-baseline.json`; new or stale fingerprints fail CI and require
review. Dependabot checks the root npm and uv dependencies, GitHub Actions, and
the `skill-audit` submodule pin every week.

SonarQube Cloud is configured for organization `nfma` and project
`nfma_agent-skills`. Disable Automatic Analysis under **Administration →
Analysis Method** so SonarQube Cloud uses the repository's
`sonar-project.properties`. Create the `sonar` GitHub environment and add a
`SONAR_TOKEN` environment secret, then add the same name and value as a
Dependabot secret. The workflow fails explicitly when the token is absent and
waits for the quality gate.

Run the local gates with:

```sh
npm ci --ignore-scripts
uv sync --frozen
npm run format:check
npm run lint:js
npm run typecheck
npm run lint:python
npm run lint:shell
npm test
```

## Maintenance

Treat this repository as the source of truth. Edit tracked skill and MCP files
here rather than installed copies. Update `skill-audit` in its own repository,
update the pinned submodule commit here, then rerun
`./scripts/prepare-vendored-skills.sh`.

## License

Original collection content is MIT licensed. Third-party skills retain their upstream terms; see [THIRD_PARTY.md](THIRD_PARTY.md).
