# Agent Skills

Nuno's shared, harness-neutral skills for Codex, Claude, Cursor, and Antigravity.

## Layout

- `skills/` contains the discoverable skill directories.
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

## Maintenance

Treat this repository as the source of truth. Edit tracked skill directories here rather than installed copies. Update `skill-audit` in its own repository, update the pinned submodule commit here, then rerun `./scripts/prepare-vendored-skills.sh`.

## License

Original collection content is MIT licensed. Third-party skills retain their upstream terms; see [THIRD_PARTY.md](THIRD_PARTY.md).
