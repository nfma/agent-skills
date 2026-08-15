# Repository instructions

## Source of truth

- Edit shared skills under `skills/`, not in harness-specific discovery directories.
- Preserve each third-party skill's upstream notices and record source or version changes in `THIRD_PARTY.md`.
- Treat `skills/skill-audit` as a generated link. Make code changes in the `vendor/skill-audit` repository, publish them there, then update the submodule commit here.

## Validation

- Validate every changed `SKILL.md` with the available Agent Skills validator.
- Run focused tests for any changed scripts or executable resources.
- After initializing or updating the `skill-audit` submodule, run `./scripts/prepare-vendored-skills.sh` and verify that the installed skill resolves to the reported CLI version.
- Check for secrets and unsafe code before publication.
