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
```

To install a skill, symlink its directory under `skills/` into `~/.agents/skills/`. Harness-specific discovery links can continue pointing at `~/.agents/skills`.

## Maintenance

Treat this repository as the source of truth. Edit tracked skill directories here rather than installed copies. Update `skill-audit` in its own repository, then update the pinned submodule commit here.

## License

Original collection content is MIT licensed. Third-party skills retain their upstream terms; see [THIRD_PARTY.md](THIRD_PARTY.md).
