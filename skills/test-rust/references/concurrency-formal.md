# Concurrency and formal-method escalation

## Control the source of nondeterminism

| Risk | Lowest candidate | Boundary condition | Escalate when |
| --- | --- | --- | --- |
| Timer, timeout, retry, backoff | Existing Tokio paused time or public clock seam | Tokio `test-util` or seam already exists | Multiple actors/network faults change ordering |
| Sequential stateful workflow | Model-based PBT | Existing PBT dependency | Concurrent schedules change results |
| Small lock/atomic/channel primitive | Loom | Production synchronization/cfg and manifest already support Loom | State space or scenario outgrows exhaustive model |
| Larger task/thread scenario | Shuttle | Code already uses compatible primitives/features | Protocol safety/liveness itself is disputed |
| Delay, partition, disconnect, crash/restart | Turmoil | Production networking is already simulator-compatible | Abstract protocol state needs exhaustive analysis |
| Bounded public Rust property | Kani | Kani installed; harness reachable under `tests/` | Invariant is unbounded or protocol-level |
| Protocol safety/liveness | TLA+ with TLC | Tool installed; stable model and bridge | Proof/model assumptions need stronger reasoning |

Never use wall-clock sleeps for synchronization. A passing random schedule is a
sample. A passing bounded exploration establishes only its explicit bounds and
assumptions.

## Kani under the tests-only boundary

Kani sets `cfg(kani)` and injects the `kani` crate. When installed, place a
public-API proof harness under `tests/`, guard it with `#[cfg(kani)]`, and invoke
`cargo kani --tests`. Keep unwind bounds and assumptions explicit. Do not add
manifest lint/configuration solely to silence `cfg(kani)` warnings.

Kani is not a replacement for unbounded proof, production unsafe-internal
access, or protocol liveness reasoning.

## Formal entry gate

Enter Lean or TLA+ work only when the full conjunction holds:

```text
(high/severe consequence OR sampling is demonstrably inadequate OR repeated
 regression of the same invariant)
AND the rule/protocol is stable enough to model
AND a named theorem or safety/liveness property and assumptions exist
AND a maintainer owns the model
AND a feasible implementation-conformance bridge exists
```

Subjective complexity alone is not a trigger.

Choose:

- **Lean** for stable pure domain logic with inductive or unbounded mathematical
  invariants;
- **TLA+ with TLC** for concurrent/distributed actions, safety, and liveness;
- **Kani** for bounded executable Rust properties; or
- **ordinary/state-machine PBT** when sampling and shrinking adequately control
  the risk.

Author Lean/TLA+ source below `tests/formal/`. Keep generated `.lake`, TLC state,
and bulk reports external or use a disposable copy. Do not install the tools.

## Implementation bridge

A formal model verifies its specification, not the shipped Rust. Record:

- model state/action ↔ Rust public behavior mapping;
- theorem/invariant ↔ black-box Rust property or conformance vector mapping;
- explicit bounds, fairness, environment, failure, and trusted assumptions; and
- tool/version plus reproducible command.

Exercise the bridge with public Rust properties, standardized vectors,
differential behavior, or deterministic scenarios under `tests/`.

## Exit and retirement

Review the model whenever mapped public behavior changes. Update, explicitly
retire, or reject it when:

- an assumption becomes false;
- the Rust/model mapping breaks;
- the named owner disappears;
- the risk falls below the entry gate;
- the specification becomes too volatile; or
- maintenance cost exceeds remaining exposure.

Do not silently run a stale model. A stale pass provides no implementation
assurance.
