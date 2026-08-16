# Property and mutation testing

## Build independent properties

Prefer properties that compress many examples without hiding intent:

- invariants and conservation laws;
- round trips and inverse relationships;
- metamorphic relationships between transformed inputs;
- equivalence to a deliberately smaller reference model;
- agreement with standardized vectors or an independent implementation; and
- state-machine postconditions and transition invariants.

Do not translate implementation branches into a property. Partition generators
by domain meaning, emphasize boundaries, and avoid filter-heavy strategies that
discard most cases.

Keep examples for named business rules, meaningful boundary semantics, smallest
regressions, and authoritative vectors. Remove examples that add neither
specification nor diagnostic value beyond a reviewed property.

## Proptest configuration under `tests/`

Use Proptest only when the package already declares it. Configure a
source-controlled deterministic default while making scheduled exploration an
explicit, opt-in mode:

```rust
use proptest::test_runner::{Config as ProptestConfig, FileFailurePersistence, RngSeed};

fn test_config() -> ProptestConfig {
    let mut config = ProptestConfig::default();
    config.failure_persistence = Some(Box::new(FileFailurePersistence::Direct(
        "tests/proptest-regressions/public_rules.txt",
    )));

    if std::env::var_os("TEST_RUST_SCHEDULED").is_none() {
        config.cases = 256;
        config.rng_seed = RngSeed::Fixed(0x5EED_5EED);
    } else {
        assert!(std::env::var_os("PROPTEST_CASES").is_some());
        assert!(std::env::var_os("PROPTEST_RNG_SEED").is_some());
    }

    config
}

proptest! {
    #![proptest_config(test_config())]
    // Properties use the deterministic mode unless explicitly scheduled.
}
```

Use one concrete persistence file per integration target. Create its parent
directory and an empty source-controlled file before the first run;
`FileFailurePersistence::Direct` cannot retain a failure when the parent is
missing. Run from the package root. Do not use the default `SourceParallel`
from a `tests/` source: without a `lib.rs`/`main.rs` ancestor it falls back
with a warning. Do not rely on `TestRng::deterministic_rng` as a durable
cross-release seed.

Use two modes:

- local/PR/mutation: reviewed fixed seed, fixed case count, and persisted
  minimal regressions;
- scheduled exploration: set `TEST_RUST_SCHEDULED=1`, `PROPTEST_CASES` to the
  larger reviewed budget, and `PROPTEST_RNG_SEED` to a newly generated seed that
  is recorded before the run, together with tool version and minimal failure.
  The authored configuration must fail closed if either Proptest variable is
  absent; do not silently fall back to its default case count or random seed.

`ProptestConfig::default()` reads the Proptest environment before the local/PR
branch overwrites its seed and case count. The explicit scheduled switch leaves
those two environment-derived values intact while preserving the concrete
failure-persistence file. The fixed PR run is reproducible evidence; the seeded
scheduled run is broader replayable exploration. Revisit the fixed seed when
generator semantics change so a fixed stream does not become a blind ritual.

## Stateful models

Use state-machine PBT when behavior depends on operation sequences. Keep the
model smaller than the implementation, compare observable state/results after
transitions, and shrink failing sequences. Current Proptest state-machine
support is sequential; use a separate scheduler for concurrent interleavings.

## Ground all oracles

Require the same independent grounding for human- and LLM-authored assertions.
An ASE 2025 study found average mutation scores of 43% for LLM-generated oracles
and 45% for human-designed oracles; neither authorship class warrants trust by
provenance alone.

Source: Molinelli et al.,
[Do LLMs Generate Useful Test Oracles?](https://homes.cs.washington.edu/~mernst/pubs/neurosymbolic-oracles-ase2025-abstract.html),
ASE 2025.

## Mutation workflow

1. Require a reliable unmodified suite and deterministic PBT configuration.
2. Declare the public-API-reachable, risk-bearing production set.
3. Bootstrap an explicit baseline. Existing survivors are owned debt.
4. Map new tests/properties to packages or files they can reach. Prefer
   package/file filters; use diff selection only for an existing production
   diff, never the tests-only diff itself.
5. Run cargo-mutants in its scratch copy, with external Cargo target and
   `--output` directories. Never pass `--in-place`.
6. Triage every relevant result before changing tests.

Use these outcome classes:

| Outcome | Interpretation |
| --- | --- |
| Killed | Current suite detected the mutation |
| Viable survivor | Mutation compiled and tests passed; inspect oracle/coverage |
| Timeout | Inconclusive performance/nontermination result, not killed |
| Unviable | Mutation did not compile, not killed |
| Equivalent/no-op | No meaningful observable behavior change |
| Tooling limitation | Mutator/runner could not provide valid evidence |
| Boundary-unreachable | Allowed public tests cannot observe the mutated private behavior |

A high-signal survivor is viable, non-timeout, public-API-reachable, in the
declared risk-bearing set, and neither equivalent nor unsupported. Gate on no
new unexplained high-signal survivor for the mapped target.

Strengthen a property, model, or public assertion when the survivor represents
material behavior. Do not add an implementation-coupled example per mutant.
Avoid global mutation-score thresholds: they combine equivalent, unreachable,
low-value, and material mutants into a misleading number.

cargo-mutants does not mutate unsafe functions. Audit reachable safe behavior
plus installed Miri/sanitizers, existing fuzz harnesses, and bounded
verification for unsafe/FFI exposure.
