---
name: test-rust
description: Assess, design, implement, or audit a risk-adaptive Rust test portfolio using fast public-API tests, property-based testing, mutation testing, and risk-gated formal methods, with every edit made under this skill confined to concrete Cargo package `tests/` directories. Use for Rust integration, CLI, property, state-machine, mutation-audited, concurrency, Lean, TLA+, or Kani tests; testing strategy; example-test consolidation; or auditing human- or LLM-authored oracles already under `tests/`. In mixed requests, use this skill only for the `tests/` portion. Do not use for inline/source tests, doctests, manifests, lockfiles, CI, benches, fuzz harness authoring, or merely running an existing test command once.
---

# Test Rust

Build the smallest defensible test portfolio for the repository's material
risks. Preserve test-pyramid economics without calling crate-level integration
targets unit tests.

## 1. Enforce the tests-only boundary

Read [the tests-only boundary](references/tests-only-boundary.md) and
[the strategy-selection guide](references/strategy-selection.md) completely
before planning tests or executing repository tools.

- Inspect production code, manifests, config, CI, and existing tests read-only.
- Create, edit, or move files only below `tests/` of a concrete Cargo package.
  A virtual workspace root's `tests/` is not an integration target.
- Do not create nested Cargo packages below `tests/`.
- Do not modify `src/`, inline `#[cfg(test)]` modules, source doctests,
  `Cargo.toml`, `Cargo.lock`, `.cargo/`, CI, examples, benches, build scripts,
  generated code, or root `fuzz/` directories.
- These limits govern work performed under this skill. In a mixed request,
  isolate and complete only its `tests/**` portion, then hand production work
  back explicitly to the caller's normal implementation workflow; never drop
  or silently suppress that part of the request.
- Do not follow a `tests` symlink or let path normalization escape an allowed
  package test root.

Before deleting a redundant test, require all of the following:

1. The boundary snapshot identifies the exact file as clean and Git-tracked.
2. List the intended deletion and the property or faster evidence that subsumes
   it, then obtain explicit user approval.
3. Pass that exact repository-relative path to verification with
   `--allow-test-deletion`. Never delete a baseline-dirty or untracked test.

A non-empty same-content rename between allowed test paths with the same file
suffix is a move, not a deletion; the verifier pairs it by content digest and
prints the exact source and destination. Empty files, cross-kind copies, and a
rename that also changes content remain a deletion plus creation and need
deletion approval for the original.

Record Git status and snapshot repository-authored content before any edit or
test execution, then verify it after every write-producing phase. Resolve the
bundled script relative to this `SKILL.md`; it supports Python 3.9+:

```sh
test_rust_skill_dir=/resolved/native/skill/root/test-rust
python3 "$test_rust_skill_dir/scripts/check_tests_boundaries.py" snapshot \
  /path/to/workspace --output /external/tmp/test-rust-boundary.json \
  --package-root /path/to/workspace/crate-a \
  --package-root /path/to/workspace/crates/crate-b \
  --exclude-scratch .idea

python3 "$test_rust_skill_dir/scripts/check_tests_boundaries.py" verify \
  /path/to/workspace --baseline /external/tmp/test-rust-boundary.json
```

Pass every package root in scope explicitly. Stop if the snapshot cannot prove
the boundary or if verification reports a write outside allowed `tests/**`.
The verifier automatically excludes only Git-ignored `target`, `node_modules`,
`.venv`, and `.DS_Store` roots that already exist at snapshot time. A new one
appearing during the run is a violation. Add an exact, Git-ignored ambient root
such as `.idea` with repeatable `--exclude-scratch`; the baseline records every
such choice and refuses protected or test-overlapping paths. The verifier hashes
every other ignored path, recurses into initialized Git submodules, and
discloses unavailable gitlinks. Each exclusion is a reported blind spot, not
permission to leave current-run tool output in the repository.

## 2. Probe capabilities before selecting techniques

For each concrete package:

1. Identify library, binary, proc-macro, and existing integration-test targets.
2. Map public APIs and executable entry points to risk-bearing behavior. Mark
   private production behavior unreachable from the allowed boundary.
3. Read existing dependencies, features, test utilities, simulators, formal
   models, and pinned commands. Before any Cargo command, compare a declared
   `rust-toolchain*` channel with `rustup toolchain list`; do not trigger an
   automatic toolchain download. Detect installed tools without installing or
   setting them up.
4. Inventory material risks: business invariants, parsing or untrusted input,
   persistence, serialization, external contracts, unsafe/FFI, async ordering,
   clocks/retries, compatibility, irreversible effects, recovery, and SLOs.
5. Classify each candidate technique as:
   - **available** within the boundary;
   - **requires pre-existing wiring** the skill may use but not add; or
   - **structurally unavailable** to author or unable to reach the target.

Unknown availability is not availability. In an inert or design-only task where
the prompt does not positively establish an installed tool and no probe may run,
exclude that tool from the selected portfolio, commands, and cadence table.
Name it only as an unconfirmed prerequisite; do not schedule a conditional job
as though it were available.

Read [the tool-feasibility profile](references/tool-feasibility.md) before
recommending or running Proptest, Trybuild, Tokio test utilities, Loom, Shuttle,
Turmoil, cargo-mutants, Cargo Fuzz, Criterion, Miri, sanitizers, Kani, Nextest,
cargo-semver-checks, coverage, Lean, or TLA+.

Do not install a dependency, Cargo subcommand, Rust toolchain/component, Lean,
or TLA+ tool. Missing capabilities become explicit prerequisites.

## 3. Map risks to the cheapest adequate evidence

For each material risk:

1. State the failure, consequence, and independent oracle: invariant, contract,
   reference model, standard vector, or named business example.
2. Consider only techniques available from the capability probe.
3. Choose the fastest narrow evidence that directly exposes the failure.
4. Consolidate partitions or sequences into a property/model when it truly
   replaces examples. Keep examples that communicate canonical behavior,
   preserve a regression, or reproduce an authoritative vector.
5. Add boundary, system, mutation, fuzz, concurrency, or formal evidence only
   for a distinct risk the cheaper evidence cannot establish.
6. Assign local, fast-PR, separate-PR-assurance, scheduled, or release cadence.
7. Record residual gaps instead of claiming inaccessible coverage.

Use the balanced p95 design budgets: 2 minutes local, 15 minutes fast PR,
30 minutes separate mapped mutation, and 2 hours per scheduled portfolio. An
incomplete run never passes merely because its budget expired.

## 4. Keep the authored pyramid honest

- Treat existing compiler invariants, inline units, and doctests as a read/run
  production base; never edit or duplicate them.
- Put most authored evidence in fast, deterministic public-API or CLI tests
  below package `tests/`.
- Add fewer narrow real-boundary/component tests.
- Keep only irreplaceable executable/system journeys at the top.
- Treat mutation, fuzzing, dynamic safety, concurrency exploration,
  compatibility, performance, and formal methods as cross-cutting assurance,
  not pyramid layers.

This skill writes zero inline unit tests. If private behavior cannot be reached
through a public API or executable, report the structural gap; do not mislabel
that as a weak external oracle.

## 5. Prefer strong properties to test-count growth

Read [the property and mutation guide](references/property-mutation.md)
completely before adding PBT, state-machine tests, mutation checks, or auditing
LLM-authored tests.

- With no testing dependency, use `std` integration tests, tables, bounded
  exhaustive domains, and metamorphic/differential loops.
- Use Proptest only when it is already a dependency. Derive generators from
  semantic partitions and properties from an independent specification.
- Include at least one positive property that establishes valid input is
  accepted through a round trip, metamorphic relationship, or equivalent
  independent oracle. Pair rejection properties with acceptance-path evidence.
- For parsers and checksummed formats, cover applicable valid structure,
  invariant-preserving transformations, targeted corruption, and boundary
  partitions; arbitrary bytes or strings alone are not a semantic generator.
- In authored Proptest targets, configure a reviewed fixed `RngSeed` and one
  concrete `FileFailurePersistence::Direct` file below
  `tests/proptest-regressions/` per integration target. Create the parent and
  an empty source-controlled persistence file before the first run;
  `FileFailurePersistence::Direct` does not create a missing parent.
- Use fixed seeds and case counts for local, PR, and mutation runs. Use fresh
  recorded seeds for scheduled exploration.
- Use state-machine PBT for sequential workflows and transition sequences; do
  not claim it covers concurrent schedules unless a separate scheduler does.

## 6. Mutation-audit every new oracle consistently

Ground every human- or LLM-authored oracle outside the implementation. Treat
LLM output as provisional until grounded and mutation-audited where feasible.
When the task identifies LLM authorship, state explicitly that provenance
changes review priority, not the evidence standard: human and LLM oracles get
the same independent grounding, survivor interpretation, and no trust discount.

- Confirm cargo-mutants is installed before selecting a mutation job. Supplied
  mutation results may be audited without the tool; otherwise an unconfirmed
  tool is a prerequisite, not a portfolio row.
- Establish a baseline for the declared public-API-reachable risk set.
- Map each new test/property to reachable packages or production files; use
  package/file mutant filters as the primary scope.
- Use diff selection only when auditing an existing production diff. A
  tests-only diff contains no production mutants.
- Use cargo-mutants' scratch copy, an external `--output`, external Cargo target
  output, and deterministic test seeds. Never use `--in-place`.
- Classify outcomes as killed, viable survivor, timeout, unviable compile,
  equivalent/no-op, tooling limitation, or boundary-unreachable.
- Gate on no new unexplained high-signal survivor in the mapped reachable
  target. Do not target a global mutation percentage or add one brittle example
  per mutant.

Whenever mutation is in scope, report the mapped package/file reachability,
state why a tests-only diff cannot select production mutants, reject a global
mutation-score target, and apply the same oracle rule regardless of authorship.

Keep mapped mutation as a separate PR assurance job unless measured
p95 fits the fast PR path. Existing survivor debt does not automatically fail a
new tests-only change.

## 7. Escalate concurrency and formal methods by risk

Read [the concurrency and formal-method guide](references/concurrency-formal.md)
completely before testing async ordering, distributed protocols, Lean models,
TLA+ specifications, or Kani harnesses.

When Lean is selected, also read
[the Lean business-logic proof guide](references/lean-business-logic.md)
completely before authoring or running a proof.

- Never use wall-clock sleeps as synchronization.
- Use paused time, Loom, Shuttle, or Turmoil only when their required production
  and manifest wiring already exists.
- Use Kani integration harnesses under `tests/` only when Kani is installed and
  the property is bounded and reachable through the public API.
- Use Lean for stable pure business logic with inductive, recursive, algebraic,
  or unbounded invariants. Require a checked named theorem; never accept
  `sorry`, `admit`, new axioms, or unchecked native/code-generation shortcuts.
- Use TLA+ for severe concurrent/distributed safety or liveness properties.
- Require a named invariant, stable specification, owner, explicit assumptions,
  and feasible black-box Rust conformance bridge before formalizing.
- Keep TLC state, traces, and bulk output in external scratch, and say so in the
  portfolio whenever TLA+ is selected.
- Keep Lean `.lake`, `.olean`, and `.ilean` output external or run Lake in a
  disposable copy. State representation, overflow, saturation, rounding, and
  theorem-to-Rust mapping assumptions explicitly.

Retire or update a formal model when its assumptions, mapping, owner, or risk
case no longer holds. A passing stale model is not evidence about shipped Rust.

## 8. Implement and verify in guarded slices

1. Add the smallest failing public-behavior reproducer or reference model.
2. Add the strongest affordable property or boundary assertion.
3. Run the narrow target with Cargo output directed to an external temporary
   directory and locked dependency resolution.
4. If the source repository lacks a usable lockfile, run in a disposable copy;
   never create or update the source lockfile.
5. Run mapped mutation or other risk-triggered evidence only after the ordinary
   suite is reliable.
6. Inspect generated regressions/snapshots before retaining reviewed evidence
   below `tests/`; keep caches, coverage profiles, mutation output, formal-tool
   state, fuzz output, and benchmark output external.
7. Re-run the boundary verifier. Do not clean up an unexpected outside-boundary
   write destructively without user direction.

Propose local/PR/scheduled/release gate commands and ownership, but never edit CI
from this skill.

Report:

- package capability and public-reachability gaps;
- risk → oracle → technique → cadence → owner → residual-risk mapping;
- test files and authored formal assets changed below `tests/`;
- commands, seeds, scratch locations, durations, and outcomes;
- mutation classifications and unexplained survivors; and
- unapplied production, dependency, manifest, config, or CI prerequisites.

For an audit or design response, do not omit applicable capability exclusions,
source-controlled PBT seed/persistence details, mutation scope/score rules, or
formal-tool scratch routing merely because no repository write is performed.
