# Functional-first Rust

Use functional ideas to expose transformations, invariants, and failure. Do not
treat avoidance of every loop or mutable binding as a quality metric.

## Contents

1. Functional core and effect boundaries
2. Data and state
3. Iterator and stream pipelines
4. Fallible composition
5. Ownership and higher-order functions
6. When imperative Rust is better
7. Performance discipline
8. Review checklist
9. Sources

## 1. Functional core and effect boundaries

Split code by responsibility rather than by syntax:

- The core receives explicit inputs and returns values, decisions, commands, or
  errors. It does not read clocks, environment variables, global state, files,
  sockets, or random sources directly.
- The effect boundary gathers inputs, calls the core, and interprets the
  returned decision through I/O or runtime APIs.
- Pass capabilities or values inward only when the core genuinely needs them.
  Do not build a trait around every function.

This separation makes behavior easier to reason about and lets ownership reveal
where effects cross the system. It is a design preference, not a requirement to
turn the application into one expression.

## 2. Data and state

Prefer:

- enums for mutually exclusive states and outcomes;
- newtypes for units, identifiers, validated values, and capabilities;
- private fields plus constructors for invariants;
- consuming transitions when the old state must become unusable;
- exhaustive `match` for state-machine transitions; and
- returned values rather than hidden mutation of shared state.

Use typestate only when an invalid transition is important enough to justify
the additional types and generic surface. Runtime validation returning a
structured error is often the better boundary for dynamic input.

Default to immutable bindings. Use a narrow `mut` binding for builders, buffers,
in-place algorithms, parsers, accumulators, and state machines when it makes the
transition more direct. Interior mutability is not a substitute for choosing an
owner.

## 3. Iterator and stream pipelines

Use a pipeline when its stages have clear names and one data-flow direction:

| Intent | Prefer | Avoid |
| --- | --- | --- |
| Transform every item | `map` | Index-based output mutation |
| Keep some items | `filter` | Manual push loop with no other control |
| Transform and discard failures/absence deliberately | `filter_map` | `filter` followed by repeated transformation |
| Flatten optional or nested sequences | `flatten` / `flat_map` | Temporary nested collections |
| Find one item | `find` / `find_map` | Loop plus sentinel variable |
| Aggregate | `fold` / `reduce` | External accumulator with trivial loop |
| Fallible aggregation | `try_fold` | Manual error flag or delayed error |
| Fallible effects | `try_for_each` | `map(...).collect::<Result<(), _>>()` |
| Complex effects or control flow | `for` / `while` | Side effects hidden inside `map` |

Keep pipelines lazy until a collection is part of the required output. Avoid
`collect` followed immediately by another iteration when the adaptors can be
chained.

When parsing a fixed number of fields, consume the iterator directly with
`next`, `split_once`, or an equivalent total pattern. Do not collect borrowed
fields into an intermediate `Vec` solely to check arity or destructure them;
detect missing and extra fields without allocation, and return structured
errors instead of indexing.

Name a closure or extract a function when a stage contains branching, logging,
multiple effects, or enough logic that the pipeline no longer reads linearly.
Do not use `inspect` for business effects; reserve it for observation such as
debugging or tracing that does not change semantics.

## 4. Fallible composition

Use `Option<T>` for meaningful absence and `Result<T, E>` for recoverable
failure. Keep the distinction visible.

- Use `?` for straight-line propagation with automatic conversion through
  `From` where that conversion preserves meaning.
- Use `map` or `and_then` for a short, single-purpose transformation.
- Use `ok_or`/`ok_or_else` only when absence truly becomes an error at that
  boundary.
- Use `match` when branches recover differently, add different context, perform
  effects, or need distinct ownership.
- Use `transpose` when changing between `Option<Result<...>>` and
  `Result<Option<...>>` clarifies the calling contract.
- Collect an iterator of `Result` into `Result<Collection, E>` to preserve
  fail-fast behavior without manual flags.

Do not compress domain-specific recovery into nested combinators. A clear match
is functional in the important sense: it is an exhaustive expression that
returns a value.

## 5. Ownership and higher-order functions

Let ownership describe the data flow:

- Borrow read-only inputs when the operation is temporary.
- Accept ownership when values cross a task, thread, message, cache, or durable
  lifecycle boundary.
- Return owned outputs unless a borrowed view naturally belongs to an input.
- Prefer `into_iter` when consuming input is part of the contract; use `iter`
  or `iter_mut` when the caller retains ownership.
- Avoid cloning to get around a design question. Clone when shared ownership,
  snapshot semantics, message isolation, or cheap copy-on-write is intentional.

Choose `Fn`, `FnMut`, or `FnOnce` from the actual capture and call contract.
Prefer a named generic callable for static dispatch. Use a boxed callable only
when runtime heterogeneity, recursive representation, or API stability requires
type erasure.

## 6. When imperative Rust is better

Choose a loop or scoped mutation when one or more are true:

- the body performs effects and the sequence is the control structure;
- several accumulators evolve together;
- `break`, `continue`, labels, or early returns express the algorithm directly;
- borrow scopes are substantially clearer in a loop;
- in-place mutation avoids a material allocation or copy;
- the algorithm is a parser or state machine whose state transitions are the
  main subject; or
- a pipeline requires repeated `inspect`, side effects in `map`, deeply nested
  closures, or type annotations to remain understandable.

Keep mutation local, uphold invariants at every early exit, and return the final
value. The goal is explicit state, not zero mutation.

## 7. Performance discipline

Iterator and closure abstractions are designed to compile efficiently, but
functional-looking code can still allocate, clone, box, dispatch dynamically,
or increase compile time.

- Keep data lazy and borrowed where possible.
- Avoid accidental `collect`, `to_owned`, `clone`, `Box<dyn ...>`, and
  `Arc<Mutex<_>>` in hot paths.
- Consider iterator item ownership and adaptor state size.
- Prefer simple monomorphized pipelines until code-size or compile-time evidence
  justifies type erasure.
- Benchmark representative workloads before replacing clear code with a
  lower-level implementation.
- Inspect generated code only for a demonstrated hot path or correctness issue.

## 8. Review checklist

- Are invalid states excluded by types or validated once at a boundary?
- Is deterministic logic separated from I/O and runtime orchestration?
- Does each pipeline have a clear input, stage sequence, and terminal result?
- Are `Option`, `Result`, and `?` preserving the intended failure semantics?
- Is `map` free of business side effects?
- Are mutations narrow, named by purpose, and valid across early exits?
- Are clones, allocations, boxes, and shared ownership deliberate?
- Would a loop or match be easier to review than the current combinators?

## 9. Sources

- [The Rust Book: closures](https://doc.rust-lang.org/stable/book/ch13-01-closures.html)
- [The Rust Book: iterators](https://doc.rust-lang.org/stable/book/ch13-02-iterators.html)
- [The Rust Book: loops versus iterators](https://doc.rust-lang.org/stable/book/ch13-04-performance.html)
- [Standard iterator module](https://doc.rust-lang.org/stable/std/iter/)
- [Iterator trait](https://doc.rust-lang.org/stable/std/iter/trait.Iterator.html)
- [Result module](https://doc.rust-lang.org/stable/std/result/)
- [Enums and pattern matching](https://doc.rust-lang.org/stable/book/ch06-00-enums.html)
- [Clippy lint index](https://rust-lang.github.io/rust-clippy/master/index.html)

Verified 2026-08-16. Recheck when the repository MSRV, Rust edition, or standard
library changes materially.
