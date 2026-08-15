# Agent Skills and MCPs

Nuno's shared skills and credential-free MCP configuration for Codex, Claude,
Cursor, and Antigravity.

## Layout

- `skills/` contains the discoverable skill directories.
- `mcp/manifest.json` defines the shared MCP baseline and pinned direct-server
  versions.
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

- installs or verifies MCP-providing plugins in Codex and Claude;
- reconciles repo-owned user MCP entries through the Codex and Claude CLIs;
- links Cursor and Antigravity's MCP-only config files to this checkout;
- links the runtime launchers into `~/.local/bin`;
- preserves replaced files in timestamped `~/.agents/mcp-backups/` folders.

It intentionally does not symlink `~/.codex/config.toml` or `~/.claude.json`,
because those files also contain unrelated user and project state.

GitHub is a deliberate Codex exception to native plugin ownership: the native
plugin requires a globally exported token, so Codex uses the tracked `gh`
Keychain wrapper instead. Claude's native GitHub plugin already supports that
same local override.

No credentials belong in this repository. Before installation, authenticate
GitHub with `gh auth login` and store the Hugging Face token in the macOS
Keychain item named `HF_TOKEN` for the current account. OAuth and Keychain
contents remain machine-local.

## Maintenance

Treat this repository as the source of truth. Edit tracked skill and MCP files
here rather than installed copies. Update `skill-audit` in its own repository,
update the pinned submodule commit here, then rerun
`./scripts/prepare-vendored-skills.sh`.

## License

Original collection content is MIT licensed. Third-party skills retain their upstream terms; see [THIRD_PARTY.md](THIRD_PARTY.md).
