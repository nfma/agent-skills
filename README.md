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
- `.skill-audit-release.json` pins the attested immutable `nfma/skill-audit`
  release descriptor byte-for-byte.
- `skills/skill-audit` contains the descriptor-verified installable
  documentation; the ignored executable is installed under
  `vendor/skill-audit/dist/`.
- `LICENSES/` and `THIRD_PARTY.md` preserve upstream licensing and provenance.

Clone and prepare the pinned audit executable:

```sh
git clone https://github.com/nfma/agent-skills.git
cd agent-skills
./scripts/prepare-vendored-skills.sh
```

The preparation step derives the immutable GitHub Release URL from the tracked
descriptor, verifies the download before execution, and checks its version,
export contract, embedded rules, side-effect-free import, documentation, and a
fixed legacy-and-portable contract corpus. A correctly installed executable is
verified locally without requiring the network.

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

Codex keeps the native Chrome DevTools plugin installed for its bundled skills,
but overlays its MCP server with the tracked `chrome-devtools-vivaldi` launcher.
The launcher uses `/Applications/Vivaldi.app` and a persistent browser profile
under `~/.cache/chrome-devtools-mcp/vivaldi-profile`; the Codex fragment also
owns its 20-second startup timeout. Vivaldi renders its initial browser UI as an
extension target, so the launcher enables Chrome DevTools MCP's extension
category; extension targets and tools are therefore visible to the server. Set
`CHROME_DEVTOOLS_VIVALDI_BIN` or `CHROME_DEVTOOLS_VIVALDI_PROFILE_DIR` to
override those machine-local paths.

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

Notion uses its official hosted MCP endpoint and per-harness OAuth. After
installing the shared baseline, authenticate Codex with
`codex mcp login notion`; use `/mcp` in Claude Code; and complete the OAuth
prompt when Cursor or Antigravity first connects. See Notion's
[connection guide](https://developers.notion.com/guides/mcp/get-started-with-mcp).

Store the Discord bot token interactively under the `DISCORD_MCP_TOKEN` service
for the current macOS account, including its required `Bot ` prefix, then run
the guided profile setup:

```sh
security add-generic-password -U \
  -s DISCORD_MCP_TOKEN -a "$USER" -w
./scripts/install-mcps.sh --setup-discord
```

The setup verifies the caller-owned bot, selects the allowed guild, and creates
the non-secret `agent-coordination` profile. The tracked wrapper exposes the
token only to the pinned Discord MCP child and restricts its runtime to the
`messages`, `threads`, and `channels` categories with progressive discovery.
Discord is coordination transport only: Notion remains the task-status board,
and coordination messages should include the matching `TASK-*` reference.

### Optional Discord wake relay on macOS

After installing the MCP baseline and creating the Discord profile, optionally
install a user LaunchAgent that keeps the Discord wake relay running:

```sh
./scripts/install-mcps.sh --dry-run --setup-wake-relay
./scripts/install-mcps.sh --setup-wake-relay
```

This service setup is explicit: ordinary MCP installation never enables it.
Preflight requires macOS, the existing tracked Discord wrapper and profile,
Traycer at `~/.local/bin/traycer`, and Homebrew Python at
`/opt/homebrew/bin/python3`. The installed plist contains only fixed executable
paths and sends service stdout and stderr to `/dev/null`, so repeated startup or
idle output cannot grow a persistent service log. The Discord token remains in
Keychain and is passed only from the credential wrapper to its MCP child.

The coordination skill registers or refreshes role ownership separately. The
installer does not register roles, create agents, or change relay processing
state. Inspect or restart the current-user service with:

```sh
launchctl print "gui/$(id -u)/com.nfma.discord-wake-relay"
launchctl kickstart -k "gui/$(id -u)/com.nfma.discord-wake-relay"
```

Use `launchctl print` for service status and launchd diagnostics. Registration,
delivery cursor, cooldown, and the relay's bounded metadata-only audit state
remain under `~/.local/state/discord-agent-coordination/wake-relay/`. Older
`~/Library/Logs/discord-wake-relay/` files are preserved, but the service no
longer creates or writes them.

Preview removal, then stop and remove the optional service:

```sh
./scripts/install-mcps.sh --dry-run --uninstall-wake-relay
./scripts/install-mcps.sh --uninstall-wake-relay
```

Uninstall removes only the relay-owned LaunchAgent plist and launcher link. It
preserves relay state, any legacy logs, the Discord profile and token, inbox
content, and agent processing state. Without the service, agents can continue
using the `discord-agent-coordination` skill to read and process handoffs
manually.

The installer uses the pinned Bats-core test framework; the JSON-line filter
uses Node's built-in test runner:

```sh
npm ci --ignore-scripts
uv sync --frozen
npm test
```

## Automated skill auditing

Every pull request and every push to `main` installs the descriptor-pinned
`skill-audit` release, verifies the executable and descriptor attestations,
installs pinned Trivy, and audits every skill under `skills/`. Recursive
discovery includes skills added later without changing the workflow. The audit
blocks high or critical findings and risk scores of 3 or higher.

Reviewed false positives are recorded as content-addressed fingerprints in
`.skill-audit-baseline.json`. Identity is scoped by skill, identifier, severity,
file, and the SHA-256 digest of the reviewed evidence, so an unchanged finding
can move without editing policy state. Changed or ambiguously repeated evidence
requires fresh review, and stale entries fail the workflow. Migrate a legacy
line-addressed baseline explicitly with
`npm run migrate:skill-audit-baseline`. Every skill must also declare a non-empty top-level
`compatibility` value or have a justified entry in the baseline's
`compatibilityOmissions` policy; missing declarations, stale exceptions, and
exceptions for undiscovered skills fail the same audit. `skill-audit` itself is
spec-validated by the runner while the
consumer verifies the released executable's provenance and contract. Its
self-referential source tests and dependency audits run in the upstream release
workflow bound by the descriptor.

Run the same audit locally after preparing the vendored CLI:

```sh
./scripts/prepare-vendored-skills.sh
node scripts/audit-skills.mjs
```

The workflow consumes the immutable GitHub Release directly; it never installs
or publishes `skill-audit` through npm.

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
review. Dependabot checks the root npm and uv dependencies and GitHub Actions
every week. The immutable `skill-audit` release pin is upgraded explicitly with
provenance review.

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

## SonarQube Cloud

CI-based analysis uses `sonar-project.properties` for project
`nfma_agent-skills`. Automatic Analysis must be disabled under **Administration
→ Analysis Method** in SonarQube Cloud. Add a repository Actions secret named
`SONAR_TOKEN`. Dependabot and fork pull requests, which cannot read Actions
secrets, skip the scan with a warning; a missing token on any other run fails
the job. The workflow waits for the quality gate when the scan runs.

## Maintenance

Treat this repository as the source of truth. Edit tracked skill and MCP files
here rather than installed copies. Update `skill-audit` in its own repository,
publish an immutable attested release, port the reviewed documentation, replace
the byte-exact descriptor pin, then rerun `./scripts/prepare-vendored-skills.sh`.

## License

Original collection content is MIT licensed. Third-party skills retain their upstream terms; see [THIRD_PARTY.md](THIRD_PARTY.md).
