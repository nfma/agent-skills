# Static boundary validation

Use deterministic static dependency evidence for every claimed hexagonal
architecture. Select a validator that understands the repository's language,
module system, and build graph. Never substitute a validator written for a
different language.

## Language-neutral contract

A valid structural check must:

1. declare roles and dependency rules from the intended architecture rather
   than generate a configuration that accepts the current graph;
2. distinguish application behavior, driving adapters, driven ports, driven
   adapters, domain data, and edge assembly wherever those distinctions affect
   a rule;
3. forbid core or application behavior from depending on runtime adapters;
4. forbid driven adapters from depending on application orchestration or
   use-case implementations while allowing the driven-port and domain-data
   contracts required for translation;
5. report concrete source and target evidence in deterministic order; and
6. fail or report an explicit evidence gap for unresolved imports, unsupported
   source relationships, unmatched roles, or incomplete analysis.

If driven-port or domain-data contracts are not statically separable from
use-case or orchestration modules — same file, same barrel export, or a
role-matched parent module — do not claim structural proof of the
driven-adapter rule. Record an explicit evidence gap for that invariant and
rely on semantic review of the public imports until the contracts are separable
in the module graph. Separating them is a static-evidence requirement, not a
folder convention.

Driving adapters may depend on application entry behavior. Driven adapters may
depend only on application-owned driven-port and domain-data contracts. Edge
assembly may know both sides only for selecting and wiring concrete adapters.

## Tool routing

- For JavaScript or TypeScript, prefer the repository's existing
  dependency-cruiser configuration or another module-graph validator with the
  same fail-closed evidence contract.
- For Rust, read `rust-validator.md` and use the pinned `hav` release only after
  approval and integrity verification.
- For other languages, use an existing architecture-test or dependency-graph
  tool that can express the required roles and rules. If none exists, report
  structural validation as missing. Propose or add a tool only when the user
  authorizes that scope.

Do not claim structural compliance from a compiler pass, folder names, package
names, a diagram, or manually inspected imports alone.

## Evidence to retain

Record:

- tool name, version, and integrity or provenance information;
- the reviewed rule configuration and any narrow exception;
- the exact command and exit state;
- deterministic findings with dependency paths; and
- unresolved analysis and known false-positive or false-negative risks.

Static evidence proves only the declared dependency model. Review application
behavior, driven-port meaning, adapter translation, and device-free tests
separately.
