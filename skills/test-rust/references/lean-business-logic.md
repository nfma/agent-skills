# Lean 4 for business-logic proofs

Use Lean only for a stable, severe pure rule whose material invariant is
inductive, recursive, algebraic, or unbounded. Ordinary examples, bounded
enumeration, PBT, or Kani remain cheaper when their sampling or bounds control
the risk.

- [Capability and boundary gate](#capability-and-boundary-gate)
- [Model the rule](#model-the-rule-not-the-rust-implementation)
- [Build the Rust bridge](#build-the-rust-conformance-bridge)
- [Challenge the proof](#challenge-the-proof-and-bridge)
- [Cadence and retirement](#cadence-and-retirement)

## Capability and boundary gate

Require the shared formal entry gate from `concurrency-formal.md`, then verify:

- the required Lean version is already installed;
- the proof needs no new repository-root project, manifest, or toolchain file;
- every authored source stays below the concrete package's
  `tests/formal/lean/`; and
- a public Rust behavior can exercise the theorem's implementation mapping.

An Elan shim is not proof of an installed toolchain. Read any existing
`lean-toolchain` file and compare it with `elan toolchain list` before invoking
`lean` or `lake`; do not trigger an automatic download. When a standalone file
uses only bundled libraries, prefer direct typechecking with output redirected
outside the repository:

```sh
lean -o /external/scratch/Domain.olean tests/formal/lean/Domain.lean
```

Run an existing Lake project only in a disposable repository copy unless its
build/cache paths are already proven external. Never retain `.lake`, `.olean`,
or `.ilean` output in the source repository.

## Model the rule, not the Rust implementation

1. Define domain values with the smallest total Lean types that express the
   rule. Avoid encoding implementation branches or private helper structure.
2. Define a pure reference transition or calculation.
3. Name each business obligation as a theorem with quantified inputs.
4. State representation and environment assumptions next to the theorem.
5. Prove reusable lemmas first, then use induction or case analysis over the
   unbounded structure.
6. Reject `sorry`, `admit`, new axioms, or unchecked native/code-generation
   shortcuts. If an existing axiom is unavoidable, list it in the trusted base
   and do not describe the result as fully proved.

For example, a credit sequence must never increase the amount charged:

```lean
def applyCredits : Nat → List Nat → Nat
  | subtotal, [] => subtotal
  | subtotal, credit :: rest => applyCredits (subtotal - credit) rest

theorem applyCredits_le_subtotal (subtotal : Nat) (credits : List Nat) :
    applyCredits subtotal credits ≤ subtotal := by
  induction credits generalizing subtotal with
  | nil => simp [applyCredits]
  | cons credit rest ih =>
      simp only [applyCredits]
      exact Nat.le_trans (ih (subtotal - credit)) (Nat.sub_le subtotal credit)
```

The theorem ranges over lists of any length. It does not establish that a Rust
implementation uses the same model.

## Build the Rust conformance bridge

For every theorem, add or retain black-box Rust evidence below `tests/` and
record:

Include this exact marker line in the Lean source so automated evidence keeps
the model/implementation boundary explicit:

```lean
-- proof-scope: model-only; does-not-prove-rust
```

| Mapping                           | Required detail                                                        |
| --------------------------------- | ---------------------------------------------------------------------- |
| Lean domain ↔ Rust domain         | Width, signedness, invalid states, ordering, and serialization         |
| Lean arithmetic ↔ Rust arithmetic | Overflow, saturation, rounding, currency scale, and division rules     |
| Lean function ↔ public behavior   | Inputs, outputs, errors, and observable effects                        |
| Theorem ↔ executable oracle       | Property, standardized vectors, or differential reference behavior     |
| Quantification ↔ runtime sampling | Which Rust cases are sampled and what remains proved only of the model |

The Rust property should use the public API and an oracle independent of its
implementation. Preserve authoritative examples for rounding, caps, and legal
or financial edge rules even when the Lean theorem subsumes their abstract
shape.

## Challenge the proof and bridge

In a disposable copy:

- seed a model/definition defect and require Lean typechecking to fail;
- seed the mapped Rust defect and require the black-box conformance test to
  fail; and
- inspect the proof for broadened assumptions, unreachable cases, vacuous
  premises, and hidden trusted axioms.

This is mutation evidence for the named theorem and bridge, not a global formal
mutation score.

## Cadence and retirement

Run the smallest direct Lean target in separate PR assurance when it fits the
30-minute budget; otherwise schedule it within the 2-hour assurance budget.
Recheck the Rust bridge in the fast base when cheap. Update or retire the proof
when the rule, representation assumptions, public mapping, owner, Lean version,
or risk case changes.
