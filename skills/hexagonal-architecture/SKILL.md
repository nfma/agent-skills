---
name: hexagonal-architecture
description: >-
  Apply hexagonal architecture (ports and adapters) as the default design and
  separation discipline for durable software. Use when creating, modifying,
  refactoring, debugging, testing, or reviewing any maintained CLI,
  application, service, library, daemon, worker, or component, even if the
  request does not name the pattern; also use for explicit hexagonal or
  ports-and-adapters coding work. Scale guidance to the requested scope and do
  not turn a focused change into an architecture migration. Do not use for
  explanation-only questions, disposable one-off scripts, non-coding tasks, or
  geometric hexagons.
---

# Hexagonal Architecture

Apply ports and adapters proportionally. Treat static dependency checks as
evidence of declared boundaries, not proof of semantic correctness.

## Canonical rules and optional detail

Read the `R1`–`R7` registry in [architecture
criteria](references/architecture-criteria.md) before changing or assessing
durable software. Those handles are normative inside this bundle: references
cite them instead of restating their rules. In user-facing results, cite a
handle only after explaining its concrete task-specific meaning.

Load other references only when needed:

- [examples](references/examples.md) for proportional, language-neutral cases;
- [static validation](references/static-validation.md) for a structural claim;
- [the Rust profile](references/rust-validator.md) only for Rust; and
- [research and longevity](references/research-and-longevity.md) for provenance
  or maintenance.

## SR1 — Scope the boundary

Apply `R1` before editing. Record only what the task needs:

- the application or component forming the hexagon;
- behavior inside it;
- actors and runtime technologies outside it;
- conversations crossing it; and
- relevant constraints, tests, and dependencies.

Ask only when the boundary choice would materially change the result. Keep
architecture reviews read-only unless changes are also requested.

## SR2 — Model the conversations

Identify the application entry, driving adapters, driven needs, and driven
adapters by applying `R2` and `R3`.

## SR3 — Direct dependencies and decisions

Apply `R4` to source and build dependencies and `R5` to decision ownership.
Preserve repository conventions when they satisfy both rules.

## SR4 — Change through executable seams

For new durable software, implement one reusable application behavior and its
necessary driven ports before production adapters.

For existing software, work in authorized slices:

1. preserve relevant behavior with characterization or acceptance tests;
2. define one touched external conversation in application vocabulary;
3. move its technology translation behind an adapter; and
4. wire that adapter at the relevant edge assembly point.

Repeat only while the requested scope requires it. A bug fix or small feature
usually needs the first step and no seam beyond the one it touches. Test using
`R6`; a mock-heavy internal object graph is not boundary evidence.

## SR5 — Validate proportionally

Collect evidence for the rules the change exercises:

- semantic evidence for `R2`, `R3`, and `R5`;
- structural evidence for `R4` and `R7`; and
- behavioral evidence for `R6`.

Run a deterministic dependency validator for new durable software, an
authorized refactor, a dependency-direction change, or any structural
compliance claim. A focused change that makes no structural claim may mark that
evidence out of scope. When no suitable validator exists and adding one is not
authorized, report the evidence gap rather than a pass.

Follow [static validation](references/static-validation.md) for every structural
claim. For Rust, additionally follow [the Rust
profile](references/rust-validator.md); never use that binary for another
language. Require approval and verify checksums and provenance before obtaining
an external validator.

## SR6 — Report what was proved

For a focused change, report the touched boundary, change, tests, and structural
evidence marked out of scope. For new durable software or an authorized
refactor, report:

- the boundary and its inside/outside map;
- application entry, driven ports, adapters, and edge assembly;
- applicable `R<n>` rules and concrete evidence;
- validator identity, reviewed configuration, result, and limitations;
- tests and deterministic checks; and
- unresolved questions, evidence gaps, and justified deviations.

For reviews, rank findings by impact and cite the dependency or behavioral path.
For implementations, keep every slice working and match the repository style.
