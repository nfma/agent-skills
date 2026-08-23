# Idiomatic production Rust by feature family

Use this guide as a routing and review checklist. Consult the linked Rust Book,
Reference, standard-library, Cargo, or Rustonomicon page for exact semantics;
do not make this skill a substitute for the language documentation.

## Contents

1. Project and language constraints
2. Ownership, borrowing, and lifetimes
3. Structs, enums, patterns, and invariants
4. Traits, generics, and dispatch
5. Errors, panics, and cleanup
6. Modules, privacy, and public APIs
7. Collections, iterators, and conversions
8. Smart pointers, interior mutability, and concurrency
9. Macros and conditional compilation
10. Unsafe Rust and FFI
11. Documentation, formatting, and performance
12. Production review checklist
13. Primary sources

## 1. Project and language constraints

Read the nearest `Cargo.toml`, toolchain file, lint configuration, and crate
attributes before choosing a language feature. Respect:

- the Rust edition and minimum supported Rust version (MSRV);
- `std`, `alloc`, or `no_std` availability;
- target platforms and pointer widths;
- default, optional, and mutually compatible Cargo features;
- unsafe-code policy; and
- public compatibility and SemVer constraints.

Do not use a newly stabilized feature because the current toolchain supports it
when the declared MSRV does not. Keep Cargo features additive; avoid using one
feature to disable behavior enabled by another.

## 2. Ownership, borrowing, and lifetimes

- Borrow when access is temporary; own when a value crosses a lifecycle,
  storage, task, thread, or message boundary.
- Prefer slices and `str` for borrowed sequence inputs. Accept owned containers
  when consumption or retention is part of the contract.
- Let lifetime elision handle ordinary relationships. Add named lifetimes only
  to express a real relationship that inference cannot state.
- Avoid returning references tied to internal locks, temporary guards, or data
  whose owner is not obvious to the caller.
- Use reborrowing and smaller lexical scopes before cloning or adding shared
  ownership.
- Treat `Clone` as a semantic operation with a cost, not a borrow-checker escape.
- Use `Cow` only when the borrowed-fast/owned-slow contract is valuable and
  visible to callers.

## 3. Structs, enums, patterns, and invariants

- Use structs for product data and enums for alternatives or states.
- Prefer newtypes over primitive aliases when units, validation, capabilities,
  or trait behavior differ.
- Keep fields private when callers could otherwise violate invariants.
- Use constructors that validate external input and return `Result` when
  failure is expected.
- Use exhaustive `match` for domain decisions. Use `if let` or `let ... else`
  for one interesting pattern with a simple remainder.
- Derive traits only when their semantics are correct. A convenient but false
  `Ord`, `Hash`, `Default`, `Copy`, or serialization contract becomes public
  behavior.
- Use destructuring to name relevant data and ignore the rest explicitly.

## 4. Traits, generics, and dispatch

- Define a trait around shared behavior or an owned abstraction boundary, not
  merely to wrap one concrete call.
- Keep traits cohesive and object-safe only when dynamic dispatch is required.
- Prefer generic/static dispatch for closed composition and hot paths.
- Prefer `impl Trait` in argument or return position when hiding a concrete type
  improves the API without removing bounds callers need.
- Use `dyn Trait` for runtime heterogeneity, plugins, recursive containers, or a
  deliberate compile-time/code-size trade-off.
- Put associated types on traits when one implementation has one natural type;
  use generic parameters when callers choose among multiple types.
- Keep bounds at the narrowest public contract that needs them. Avoid repeating
  large bounds; use helper traits only when they carry genuine semantics.
- Consider `Send`, `Sync`, and `'static` as ownership/concurrency contracts, not
  boilerplate to add until code compiles.

For async methods in public traits, decide up front whether returned futures
must be `Send`, whether dynamic dispatch is required, and what the MSRV allows.
Do not apply `async-trait` or a boxed future automatically.

## 5. Errors, panics, and cleanup

- Return `Result` for recoverable failures and `Option` for expected absence.
- Give library errors stable, meaningful variants and source chains. Keep
  application-only context flexible at the application boundary.
- Add context where it identifies the failed operation or resource; do not
  replace a useful source error with an opaque string.
- Use `?` for propagation. Match when handling, retrying, translating, or
  compensating differs by variant.
- Avoid `unwrap` and `expect` on external data, I/O, concurrency, or normal
  runtime conditions. An `expect` is acceptable for a locally proved invariant
  when its message states that invariant.
- Panic for programmer errors or violated internal invariants, not ordinary
  input or service failure. Document reachable panic conditions.
- Keep values valid during unwinding. Use RAII guards for synchronous cleanup.
- Provide explicit fallible or async `close`/`shutdown` operations when cleanup
  can fail, block, or await; keep `Drop` infallible and nonblocking.

## 6. Modules, privacy, and public APIs

- Keep modules and items private by default. Re-export a deliberate public
  vocabulary instead of exposing the internal tree.
- Name methods and conversions using Rust conventions: `as_` borrows, `to_`
  allocates or copies, and `into_` consumes.
- Implement standard conversion traits when they express the relationship.
- For collection-like types, provide the natural `iter`, `iter_mut`, and
  `into_iter` forms and consider `FromIterator`/`Extend`.
- Accept the weakest useful input abstraction without making signatures
  needlessly generic.
- Return concrete domain types rather than tuples or booleans whose meaning the
  caller must remember.
- Avoid exposing dependencies' concrete types unless they are intentionally
  part of the public contract.
- Make feature-gated APIs discoverable and preserve compatible feature
  combinations.

## 7. Collections, iterators, and conversions

- Choose collections from lookup, ordering, duplication, mutation, and memory
  requirements, not habit.
- Prefer iterator consumption to manual indexing. Use indexing only when its
  panic behavior and complexity are part of the intended algorithm.
- Keep adaptors lazy and avoid intermediate collections.
- Use checked and fallible numeric conversions at trust boundaries. Reserve
  `as` for conversions whose truncation, wrapping, pointer, or representation
  semantics are deliberate and reviewable.
- Preserve ordering requirements explicitly; do not depend on unspecified map
  order.
- Preallocate only when a reliable size estimate exists and allocation cost
  matters.

## 8. Smart pointers, interior mutability, and concurrency

- Start with owned values and ordinary references.
- Use `Box<T>` for stable indirection, recursive size, or ownership of a trait
  object—not as a generic way to satisfy the compiler.
- Use `Rc<T>` for single-threaded shared ownership and `Arc<T>` for cross-thread
  shared ownership. Cloning either clones a handle, not the underlying value.
- Add `Cell`, `RefCell`, `Mutex`, `RwLock`, or atomics only with a named owner,
  invariant, contention model, and failure strategy.
- Prefer message passing when one task naturally owns a resource and operations
  around it are asynchronous.
- Use atomics only with a documented memory-ordering argument. Prefer higher-
  level synchronization when that argument is not obvious.
- Never write unsafe implementations of `Send` or `Sync` without a complete
  soundness argument covering all contained and aliased state.

## 9. Macros and conditional compilation

- Prefer functions and generics when they can express the behavior.
- Use declarative macros for repeated syntax patterns with a small, documented
  grammar. Keep expansions hygienic and diagnostics actionable.
- Use procedural macros only when syntax generation materially improves the
  caller experience and the compile-time/debugging cost is justified.
- Qualify paths in exported macros through `$crate` where appropriate.
- Keep conditional compilation at module or adapter boundaries. Avoid weaving
  many feature conditions through core business logic.
- Never use the `test` or `doctest` cfgs under `src/` for this repository policy.

## 10. Unsafe Rust and FFI

- Prefer safe Rust. Introduce unsafe only for a capability unavailable through
  a suitable safe abstraction or for measured low-level requirements.
- Keep unsafe blocks as small as possible and enable explicit unsafe operations
  inside unsafe functions.
- Write a `SAFETY` justification for every unsafe operation that identifies the
  exact preconditions and why they hold.
- Document a public unsafe function with a `# Safety` contract for callers.
- Protect unsafe invariants with privacy and expose a safe API that remains
  sound for all safe inputs and trait implementations.
- Account for aliasing, initialization, validity, provenance, layout, unwind,
  drop, concurrency, and panic paths relevant to the operation.
- At FFI boundaries, define ownership, allocation/deallocation side, ABI,
  nullability, lengths, threading, callbacks, and unwind behavior explicitly.
- Run dedicated tools such as Miri or sanitizers when they apply, but do not
  treat a clean run as a proof of soundness.

## 11. Documentation, formatting, and performance

- Use default `rustfmt` style unless the repository pins an alternative.
- Document every public item that is not self-evident, emphasizing purpose and
  contracts rather than repeating the signature.
- On every public function returning `Result`, add a `# Errors` section that
  connects inputs or runtime states to the error families callers can receive.
  Documentation on the error type supplements this function-level contract; it
  does not replace it.
- Include `# Panics` and `# Safety` sections when applicable.
- Explain cancellation, blocking, ordering, and resource-lifetime behavior for
  async APIs.
- Keep examples copyable and use `?` for fallible flows. Under this project's
  source boundary, place executable test code outside `src/`.
- Use Clippy as review evidence, not an unquestioned source of truth. Resolve or
  narrowly justify lint allowances.
- Optimize from profiles and benchmarks. Preserve readable zero-cost
  abstractions until evidence identifies allocation, dispatch, copying,
  contention, cache behavior, or code size as a problem.

## 12. Production review checklist

- Does the code respect edition, MSRV, targets, features, and `no_std` policy?
- Are ownership and lifetime boundaries intentional?
- Do types exclude invalid states or validate them once?
- Are trait and dispatch choices proportional to the abstraction?
- Are errors recoverable, contextual, and non-panicking where expected?
- Is the public surface minimal, conventional, and documented?
- Are allocations, clones, boxes, locks, atomics, and unsafe blocks justified?
- Are cfgs additive and kept away from core decisions?
- Does production compile independently from test-only code and dependencies?

## 13. Primary sources

- [The Rust Programming Language](https://doc.rust-lang.org/stable/book/)
- [The Rust Reference](https://doc.rust-lang.org/stable/reference/)
- [Standard library documentation](https://doc.rust-lang.org/stable/std/)
- [Rust API Guidelines](https://rust-lang.github.io/api-guidelines/)
- [Rust Style Guide](https://doc.rust-lang.org/stable/style-guide/)
- [The rustdoc book](https://doc.rust-lang.org/stable/rustdoc/)
- [The Cargo Book: features](https://doc.rust-lang.org/cargo/reference/features.html)
- [The Rustonomicon](https://doc.rust-lang.org/stable/nomicon/)
- [Clippy lint index](https://rust-lang.github.io/rust-clippy/master/index.html)
- [Rust Edition Guide](https://doc.rust-lang.org/stable/edition-guide/)

Verified 2026-08-16. Prefer the Reference over the Rustonomicon if they disagree,
and always reconcile recommendations with the repository's declared MSRV.
