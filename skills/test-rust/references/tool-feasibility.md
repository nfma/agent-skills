# Rust testing tool feasibility profile

Verified against primary documentation on 2026-08-16. Owner: Nuno (`nfma`).
Recheck by 2026-11-15 or on any drift signal below.

## Capability table

| Technique/tool | Tests-only status | Required existing capability |
| --- | --- | --- |
| `std` integration and CLI tests | Available | Public library API or binary target |
| Tables, bounded exhaustive, metamorphic/differential loops | Available | Oracle expressible with current dependencies |
| Proptest/QuickCheck | Requires wiring | Existing dev-dependency |
| Stateful/model PBT | Requires wiring | Existing dependency/API; current Proptest model is sequential |
| Trybuild/compile-UI | Requires wiring | Existing dev-dependency/harness |
| Snapshot/approval libraries | Requires wiring | Existing dependency; redirect pending output |
| Tokio paused time | Requires wiring | Existing Tokio `test-util` feature |
| Loom | Requires wiring; otherwise unavailable | Production synchronization/cfg and manifest already compatible |
| Shuttle | Requires wiring; otherwise unavailable | Production code already uses compatible primitives/features |
| Turmoil | Requires wiring; otherwise unavailable | Production networking already simulator-compatible |
| Kani integration harness | Available when installed | `cargo kani --tests`, `#[cfg(kani)]`, public API; no manifest dependency needed |
| Miri/sanitizers | Run-only when installed and setup-complete | Supported toolchain/component, no first-run sysroot/setup mutation, reachable path |
| Cargo Fuzz | Structurally unavailable to author | Conventional root `fuzz/` package; existing harness run-only in disposable copy |
| Criterion | Structurally unavailable to author | Conventional `benches/` plus manifest entry; existing benches run-only |
| Nextest | Available when installed | Use for tests; run existing doctests separately with Cargo |
| cargo-semver-checks | Available when installed | Explicit authorized registry, revision, root, or rustdoc baseline; required toolchain already installed |
| cargo-llvm-cov | Available when installed | Redirect target, profiles, and reports; coverage remains diagnostic |
| Lean | Available when installed | Sources under `tests/formal/lean`; generated state external |
| TLA+/TLC | Available when installed | Models under `tests/formal/tla`; TLC state external |

"Available" means the skill can author or run evidence without changing a
forbidden repository path. It does not mean the technique is strategically
justified.

Unknown is not installed. When an inert prompt neither establishes a tool as
installed nor permits a probe, exclude it from the selected portfolio,
commands, and cadence table; list only the unconfirmed prerequisite. Supplied
tool results may still be audited without invoking that tool.

## Primary sources

- Rust: [testing](https://doc.rust-lang.org/stable/book/ch11-00-testing.html),
  [Cargo integration-test environment](https://doc.rust-lang.org/cargo/reference/environment-variables.html#environment-variables-cargo-sets-for-crates),
  [rustdoc tests](https://doc.rust-lang.org/rustdoc/write-documentation/documentation-tests.html)
- Proptest: [runner configuration](https://docs.rs/proptest/latest/proptest/test_runner/struct.Config.html),
  [RngSeed](https://docs.rs/proptest/latest/proptest/test_runner/enum.RngSeed.html),
  [failure persistence](https://docs.rs/proptest/latest/proptest/test_runner/enum.FileFailurePersistence.html),
  [state machines](https://proptest-rs.github.io/proptest/proptest/state-machine.html)
- Mutation: [getting started](https://mutants.rs/getting-started.html),
  [diff selection](https://mutants.rs/in-diff.html),
  [output](https://mutants.rs/output.html)
- Async/concurrency: [Tokio testing](https://tokio.rs/tokio/topics/testing),
  [Loom](https://docs.rs/loom/latest/loom/),
  [Shuttle](https://docs.rs/shuttle/latest/shuttle/),
  [Turmoil](https://docs.rs/turmoil/latest/turmoil/)
- Verification/safety: [Kani usage](https://model-checking.github.io/kani/usage.html),
  [Miri](https://github.com/rust-lang/miri/),
  [Rust Fuzz Book](https://rust-fuzz.github.io/book/)
- Formal: [Lean theorem proving](https://docs.lean-lang.org/theorem_proving_in_lean4/),
  [Aeneas](https://github.com/AeneasVerif/aeneas),
  [TLA+ high-level view](https://lamport.azurewebsites.net/tla/high-level-view.html)
- Other: [cargo-nextest](https://nexte.st/docs/running/),
  [cargo-semver-checks](https://github.com/obi1kenobi/cargo-semver-checks),
  [Criterion.rs](https://bheisler.github.io/criterion.rs/book/)

## Drift signals

Re-verify affected rows when:

- cargo-mutants changes scratch, output, selection, unsafe, or result classes;
- Proptest changes configuration, regression persistence, RNG semantics, or
  concurrent state-machine support;
- Cargo changes integration-target, lockfile, `CARGO_BIN_EXE_*`, target, or
  temporary-directory behavior;
- Loom, Shuttle, Turmoil, Kani, Miri, fuzz, coverage, Nextest, or semver-checks
  changes source/manifest requirements or output locations;
- Lean, TLA+, Aeneas, or Kani changes its trusted base or Rust bridge; or
- an eval observes any persistent repository write outside allowed package
  `tests/` paths.

Do not encode a tool version floor unless the repository already pins it or a
required capability is absent from the detected version. Prefer capability
probing and record the observed version in the task report.
