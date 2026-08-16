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
owns its 20-second startup timeout. Set
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

The installer uses the pinned Bats-core test framework; the JSON-line filter
uses Node's built-in test runner:

```sh
npm ci --ignore-scripts
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
update the pinned submodule commit here, then rerun
`./scripts/prepare-vendored-skills.sh`.

## License

Original collection content is MIT licensed. Third-party skills retain their upstream terms; see [THIRD_PARTY.md](THIRD_PARTY.md).
