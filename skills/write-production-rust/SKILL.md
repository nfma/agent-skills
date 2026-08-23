---
name: write-production-rust
description: Write, refactor, debug, or review idiomatic production Rust in `.rs` files under a `src/` directory, favoring functional composition, explicit effects, strong type modeling, and robust async design. Use for Rust library and binary modules below `src/`, including mixed source-and-test requests when any requested Rust production file is under `src/`; handle only the `src/` portion. Especially relevant to ownership, errors, iterators, traits, public APIs, unsafe boundaries, futures, streams, cancellation, backpressure, or task orchestration. Do not use for requests limited to `tests/`, inline test modules, benches, examples, build scripts, manifests, generated code, or Rust files outside `src/`; use a testing skill for test code.
---

# Write Production Rust

Produce readable, reliable Rust whose types expose invariants and whose effects
remain at explicit boundaries. Prefer functional constructs when they clarify
data flow; retain loops and scoped mutation when they are the more idiomatic or
efficient expression.

## 1. Gate the scope

Resolve every requested or changed path before editing.

- Operate only on Rust files whose normalized path contains a `src` component.
- Reject files below a `tests` component, including a `src/tests` subtree.
- For a mixed request, handle only the `src/**/*.rs` portion. Leave test work to
  the Rust testing skill.
- Do not create, inspect for implementation guidance, or modify test code while
  applying this skill. Tests consume the crate's production API; production
  code never consumes tests.
- Read manifests and project configuration when needed to learn the edition,
  MSRV, feature policy, runtime, lint policy, or crate topology, but do not edit
  files outside `src/` under this skill.

Run the boundary check before and after a change:

```sh
python3 /path/to/write-production-rust/scripts/check_src_boundaries.py <workspace-or-crate-root>
```

Read [the source-boundary contract](references/source-boundary.md) if the layout
is nonstandard or the checker reports a violation.

## 2. Establish the production contract

Before changing code, identify:

- the observable behavior and invariants;
- the ownership and lifetime of each input, output, and shared resource;
- the recoverable errors, programmer errors, and external effects;
- the public API and compatibility constraints;
- the crate edition, MSRV, enabled feature combinations, and `no_std` policy;
- the async runtime and whether returned futures must be `Send`; and
- performance constraints supported by evidence rather than intuition.

Preserve repository conventions that satisfy these constraints. Do not add a
new abstraction merely to make the code look more functional.

## 3. Design a functional core

Read [the functional Rust guide](references/functional-rust.md) before an
implementation or refactor.

- Represent alternatives and state with enums, newtypes, validated
  constructors, and exhaustive matching. Prefer making invalid states
  unrepresentable to scattering boolean checks.
- Keep deterministic decisions in small functions that take values and return
  values. Interpret I/O, clocks, randomness, process state, and runtime actions
  at explicit outer boundaries.
- Prefer immutable bindings and value transformations. Keep necessary `mut`
  bindings in the narrowest scope.
- Use iterator or stream adaptors for recognizable transform, filter,
  short-circuit, and fold pipelines. Avoid intermediate collections.
- Use `Option`, `Result`, `?`, and fallible iterator operations for expected
  absence and failure. Use `match` when branches recover differently or carry
  distinct effects.
- Use a `for` or `while` loop when the work is primarily side effects, needs
  complex early exits, mutates several coupled values, or is clearer that way.
- Never use `map` only for side effects or make a long combinator chain the goal.

Treat higher-order code as successful only when the resulting ownership,
failure, and control flow remain easy to read.

## 4. Apply idiomatic Rust features

Read [the idiomatic Rust feature guide](references/idiomatic-rust.md) when the
change affects public APIs, traits, generics, lifetimes, smart pointers,
interior mutability, macros, unsafe code, FFI, feature gates, or `no_std`.

- Borrow inputs when the callee does not need ownership; return owned values
  unless a borrowed result is naturally tied to an input.
- Do not clone, box, allocate, or add `Arc<Mutex<_>>` merely to silence the
  borrow checker. Make the ownership boundary explicit and justify its cost.
- Prefer standard traits and conventions: `From`/`TryFrom`, `AsRef`,
  `IntoIterator`, `Display`, `Error`, and the common comparison or collection
  traits when their semantics are honest.
- Prefer static dispatch. Use trait objects for deliberate runtime
  heterogeneity or interface stability, not by reflex.
- Return structured errors for recoverable failures. Reserve panics for broken
  internal invariants and document reachable panic conditions.
- Keep modules private by default and expose the smallest stable API that
  expresses the domain.
- Keep unsafe operations minimal and private. State each safety invariant next
  to the unsafe boundary and expose a sound safe abstraction.
- Document public behavior, errors, panics, cancellation behavior, and safety
  contracts without restating type signatures.

## 5. Design async code explicitly

Read [the async Rust guide](references/async-rust.md) completely before changing
an async function, future, stream, channel, task, lock, or runtime boundary.

- Treat every `.await` as a possible cancellation point. Never leave shared or
  durable state half-committed across an await.
- Prefer composing and awaiting futures in the current task. Spawn only when
  independent task ownership, parallelism, isolation, or a longer lifetime is
  intentional.
- Retain and await join handles, propagate child errors, and define how parent
  cancellation reaches children. Detached tasks require an explicit top-level
  owner and shutdown path.
- Bound queues, streams of in-flight work, retries, sockets, and blocking jobs.
  Define overload behavior and use backpressure rather than unbounded growth.
- Do not block an executor thread. Isolate blocking or CPU-heavy work, bound its
  parallelism, and account for work that cannot be aborted after starting.
- Do not hold a synchronous lock guard across `.await`. Use short sync locks for
  synchronous data; use message passing or an async lock only when an operation
  genuinely spans awaits.
- Audit `select` or race branches for cancellation safety and fairness.
- Implement shutdown as signal, stop intake, drain or cancel, clean up, and
  join. Do not depend on asynchronous work in `Drop`.
- Keep library cores runtime-neutral when practical. Put runtime-specific
  spawning, timers, signals, and adapters behind explicit boundaries.

## 6. Implement in reviewable slices

1. Model the domain and errors.
2. Implement the pure transformation or decision layer.
3. Attach effects at the narrowest boundary.
4. Add bounded async orchestration where concurrency is required.
5. Recheck ownership, cancellation, shutdown, and public API compatibility.
6. Remove redundant clones, collections, indirection, and abstraction layers.

Do not add inline tests, `#[cfg(test)]`, test-only behavior, or references to
test modules and paths under `src/`, even when public Rust books show inline
unit-test examples.

## 7. Verify production targets

Use the repository's pinned commands when present. Otherwise run the applicable
production-only checks from the crate or workspace root:

```sh
rustfmt --edition <crate-edition> --check <touched-src-files>
cargo check --lib --bins
cargo clippy --lib --bins -- -D warnings
python3 /path/to/write-production-rust/scripts/check_src_boundaries.py .
```

Also check declared feature combinations and MSRV when the change can affect
them. Do not run, create, or modify tests under this skill; hand that work to the
testing skill.

Report:

- the production behavior and invariants changed;
- where functional composition was used and where imperative code was retained;
- async ownership, bounds, cancellation, and shutdown decisions;
- production checks run and their outcomes; and
- any unresolved performance, compatibility, or safety trade-off.
