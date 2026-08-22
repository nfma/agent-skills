# Repository instructions

## Source of truth

- Edit shared skills under `skills/`, not in harness-specific discovery directories.
- Preserve each third-party skill's upstream notices and record source or version changes in `THIRD_PARTY.md`.
- Treat `skills/skill-audit` as a reviewed consumer-owned port of the immutable release documentation. Make executable changes upstream, publish an attested release, then update the byte-exact `.skill-audit-release.json` pin and port documentation deliberately.

## Validation

- Validate every changed `SKILL.md` with the available Agent Skills validator.
- Run focused tests for any changed scripts or executable resources.
- After updating the `skill-audit` release pin or documentation, run `./scripts/prepare-vendored-skills.sh` and `npm run test:skill-audit-release`.
- Check for secrets and unsafe code before publication.
