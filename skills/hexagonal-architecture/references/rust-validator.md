# Rust validator profile

Status: pending first release.

Do not treat this placeholder as an available validator. The first release must
be independently verified before adding executable commands to the skill.

## Required release contract

- Canonical repository:
  https://github.com/nfma/hexagonal-architecture-validator
- Required targets: `aarch64-apple-darwin` and
  `x86_64-unknown-linux-gnu`.
- The release must publish checksummed binaries, a versioned configuration
  schema, versioned JSON output, deterministic finding order, and distinct exit
  states for pass, violations, and analysis/configuration failure.
- Installation instructions must pin an immutable version and verify SHA-256
  before execution.
- The project configuration must declare intended architectural roles and
  dependency rules. An inferred configuration is a draft for human review, not
  evidence of compliance.

## Integration gate

Before replacing this placeholder:

1. verify the release and asset checksums against the canonical repository;
2. inspect the binary's provenance and release workflow;
3. run it on one compliant fixture, one inverted core-to-adapter dependency, one
   malformed configuration, and one documented unresolved-analysis case;
4. confirm deterministic text and JSON output across repeated runs; and
5. record the version, asset names, checksums, exit behavior, known blind spots,
   retrieval date, owner, and recheck trigger here.

Never report an unresolved or unsupported source relationship as a pass. Combine
the validator result with the semantic and behavioral criteria in
`architecture-criteria.md`.
