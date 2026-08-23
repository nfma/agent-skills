# Static boundary validation

Use this profile when `SR5` requires structural evidence. It operationalizes
`R4` and `R7`; it does not redefine or replace them.

## Validator contract

A suitable validator must:

1. understand the repository's language, module system, and build graph;
2. classify the vocabulary roles needed by the reviewed `R4` rules;
3. express narrow edge-assembly exceptions without weakening other rules;
4. report deterministic source, target, rule, and dependency-path evidence; and
5. fail or expose an evidence gap for every `R7` unknown.

Apply `R7`'s separability gate before claiming the driven-adapter part of `R4`.
A compiler pass, folder or package names, a diagram, or manually inspected
imports do not replace this contract.

## Tool routing

Use an existing architecture-test or dependency-graph tool that understands the
repository and satisfies this contract. Route language- and tool-specific
configuration through a separate bridge skill when one exists. If no suitable
validator exists, report missing structural evidence; add a tool only when
authorized.

## Evidence record

Retain the tool identity and provenance, reviewed configuration and narrow
exceptions, exact command and exit state, deterministic findings, and known
false-positive or false-negative risks. Report semantic and behavioral evidence
separately under `SR5`.
