---
name: hexagonal-architecture-rust
description: >-
  Bridge language-neutral hexagonal architecture into Rust module boundaries
  and deterministic hav validation. Use alongside the hexagonal-architecture
  skill when implementing or reviewing ports and adapters in a Cargo workspace,
  writing or auditing hav.toml, making a Rust dependency-direction claim, or
  investigating a Rust boundary violation. Do not use for non-Rust repositories
  or Rust work with no hexagonal boundary or structural-validation concern.
---

# Hexagonal Architecture for Rust

Keep the language-neutral `hexagonal-architecture` skill authoritative for
boundary semantics, scope, dependency direction, and evidence quality. This
bridge owns only the Rust mapping and the pinned validator integration.

## Compose the skills

Load and apply `hexagonal-architecture` first. If it is unavailable, report the
missing semantic owner rather than reconstructing its rules here. Use its
canonical `R<n>` and `SR<n>` handles in internal reasoning, but explain the
task-specific meaning before citing them in user-facing results.

Keep the requested change as hard scope. Do not introduce a Cargo layout,
module hierarchy, port, adapter, or validator configuration merely to satisfy a
template.

## Map responsibilities to Rust

Classify modules and crates by responsibility and actual dependencies, not
folder names:

- application behavior and its owned contracts belong on the inside;
- driving adapters invoke application entries;
- driven adapters implement application-owned needs; and
- a binary entry point, library constructor, test harness, or equivalent edge
  assembly point may wire concrete adapters.

Do not require one crate per role. Use the smallest role patterns that the
workspace can distinguish deterministically. If driven ports, application
behavior, or domain data share an inseparable module, disclose that limit
rather than claiming proof of a boundary the graph cannot express.

## Validate only when the claim requires it

Read [the pinned hav profile](references/validator.md) when the base skill
requires structural evidence or the task explicitly asks for hav. Review the
configuration before execution. Require approval before downloading a binary,
then verify both checksum and provenance.

Treat exit `0` as evidence only for the configured, analyzable edges. Treat
violations as findings and any configuration or analysis failure as an evidence
gap. Combine the report with semantic and behavioral evidence from the base
skill.

## Report the bridge evidence

Report:

- the Rust roles and patterns actually matched;
- the reviewed rules and narrow exceptions;
- validator version, command, exit state, and deterministic findings;
- analysis unknowns and documented blind spots; and
- the separate semantic and behavioral evidence supporting the conclusion.
