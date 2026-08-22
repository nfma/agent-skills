# Risk-to-evidence selection

## Decision rule

Choose evidence by expected material risk reduced per authoring effort,
maintenance burden, feedback latency, compute cost, and flake risk. Avoid fake
numeric precision: document the trade-off when probabilities or losses are not
grounded.

For every material risk, record:

| Field | Question |
| --- | --- |
| Failure | What observable behavior can go wrong? |
| Consequence | Which asset/user/property is harmed, and how severely? |
| Oracle | Which independent invariant, contract, model, vector, or example decides correctness? |
| Reachability | Can `tests/` observe it through a public API or executable? |
| Lowest adequate evidence | Which available technique directly exposes it fastest? |
| Cadence and owner | When does it run, within what p95 budget, and who triages it? |
| Residual gap | What remains untested, unsampled, assumed, or blocked? |

## Portfolio matrix

| Technique | Primary risk | Effort | Feedback/cost | Portfolio rule |
| --- | --- | ---: | --- | --- |
| Existing types/lints/inline units/doctests | Invalid states, API misuse | Low | Fast/low | Read/run base; never duplicate or edit |
| Named examples/regressions | Canonical rules and known defects | Low | Fast/low | Keep scarce and specification-bearing |
| Tables/bounded exhaustive domains | Small partitions and boundaries | Low | Fast/low | Bare-manifest default |
| Property-based testing | Large input spaces, laws, round trips | Medium | Fast–medium/low | Prefer when dependency exists and property replaces examples |
| Stateful/model PBT | Sequential workflows and APIs | Medium–high | Medium/medium | Use a smaller abstract model; shrink transition sequences |
| Compile/pass-fail UI | Public type/API constraints and diagnostics | Medium | Fast/low | Requires existing harness dependency |
| Snapshots/golden files | Stable structured output and compatibility | Low initially | Fast/maintenance-sensitive | Review semantics; never mirror implementation noise |
| Narrow real-boundary tests | Serialization, DB, filesystem, HTTP/message behavior | Medium | Medium/medium | One focused test per material boundary risk |
| Consumer/provider contracts | Independently deployed compatibility | Medium | Medium/medium | Prefer over duplicated broad E2E |
| Executable/system journeys | Startup, migrations, critical wiring | High | Slow/high | Minimal top only |
| Mutation testing | Weak/missing oracles | Medium triage | Slow/high | Cross-cutting; mapped PR plus scheduled baseline |
| Coverage-guided fuzzing | Parsers, hostile inputs, panics, memory faults | Medium | Time-budgeted/high | Run existing harnesses only; authoring is outside boundary |
| Miri/sanitizers | Undefined behavior and FFI | Medium | Slow/medium | Run installed tools through reachable safe behavior |
| Paused time/schedule exploration | Timers and async interleavings | Medium–high | Medium/high | Only with pre-existing wiring |
| Kani | Bounded properties of executable Rust | Medium–high | Slow/high | Public API harness under `tests/` when installed |
| Lean | Unbounded mathematical/domain invariants | Very high | Proof loop/expert cost | Severe stable pure logic only |
| TLA+ with TLC | Concurrent/distributed safety and liveness | High | Model-check loop/high | Severe stable protocols plus Rust bridge |
| Compatibility/feature/target checks | MSRV, semver, feature and platform promises | Medium | Medium/high | Risk-selected matrix, not powerset by default |
| Performance/load/soak | Latency, throughput, resource/SLO regression | High | Slow/noisy/high | Separate gates; existing harnesses only when outside `tests/` |
| Fault injection/recovery | Retry, failover, durability, operational recovery | High | Slow/high | Scheduled/release only when public seams permit |

## Bare-manifest minimum

When no testing dev-dependencies or special features exist:

- use standard-library integration/CLI tests;
- encode canonical examples and minimal regressions;
- cover semantic partitions with tables;
- exhaust small domains;
- write metamorphic relations and differential checks against a smaller
  independent implementation expressible with existing dependencies; and
- report unavailable PBT, compile-UI, simulation, or contract dependencies.

PBT is a preferred consolidation tool, not a universal capability.

## Pyramid economics

Treat the pyramid as a distribution of runtime, scope, and diagnostic cost:

1. Existing production/compiler evidence is the base the skill may run but not
   author.
2. Fast external public-API/CLI tests are the majority of skill-authored work.
3. Narrow real boundaries and components are fewer.
4. Critical system journeys are fewest.
5. Mutation, fuzz, formal, concurrency, compatibility, and performance evidence
   cut across layers and run at risk-appropriate cadence.

Never use test count or global line coverage as a target. Remove a slower test
when faster evidence controls the same risk and preserves diagnostic value.

## Default cadence

| Cadence | Default p95 | Evidence |
| --- | ---: | --- |
| Local | 2 minutes | Focused fast external tests and persisted regressions |
| Fast PR | 15 minutes | Full fast base and narrow deterministic integrations |
| Separate PR assurance | 30 minutes | Mapped mutation and justified bounded verification |
| Scheduled | 2 hours | Random high-case PBT, broader mutation, installed safety/concurrency tools |
| Release/risk trigger | Explicit risk budget | Critical system, recovery, conformance, performance, maintained formal obligations |

If assurance exceeds a budget, split or optimize it visibly. Do not silently
truncate, retry to green, or count incomplete evidence as passing.
