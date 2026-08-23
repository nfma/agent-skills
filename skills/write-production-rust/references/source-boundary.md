# Production source boundary

This repository policy is stricter than conventional Rust layouts: the
production skill owns only Rust files under `src/`, and production source must
not contain or depend on test code.

## Allowed direction

```text
tests/**/*.rs  --->  crate public API from src/**/*.rs
src/**/*.rs    -X->  tests/**/*.rs or test-only modules
```

Tests should import the crate by its library/package API. They must not be made
reachable by production modules, and production behavior must not change under
the `test` cfg.

## Prohibited under src

- `#[cfg(test)]`, `#[cfg(doctest)]`, or `cfg!(test)`
- `#[test]`, runtime-specific test attributes, or property-test attributes
- `mod test` or `mod tests`
- imports or paths resolving through a `test` or `tests` module
- `#[path = "..."]`, `include!`, `include_str!`, or `include_bytes!` references
  into a `test/` or `tests/` path
- test fixtures, mocks, assertions, or helper behavior compiled into production
  solely for tests
- conditional production behavior added to make tests pass

Public books and crate documentation often demonstrate inline Rust unit tests
inside `src/lib.rs`. Do not copy that layout here; the user policy overrides the
conventional example.

## Skill behavior

- Trigger only for `.rs` files below a normalized `src` path component.
- Do not apply to `tests/`, `benches/`, `examples/`, `build.rs`, manifests, or
  generated code.
- For a request spanning production and tests, change only production files and
  hand the test portion to `test-rust`.
- Read manifests or configuration only to understand production compilation;
  do not edit them under this skill.
- Validate the crate's library and binary targets without selecting test targets
  so dev-dependencies cannot mask a production dependency error.

## Boundary checker

Run from a crate or workspace root:

```sh
python3 /path/to/write-production-rust/scripts/check_src_boundaries.py .
```

The checker recursively examines Rust files below every `src` component and
ignores Rust files outside `src`. It reports the file, line, rule, and matching
source excerpt, and returns a nonzero status on violations.

You may also pass one or more explicit source files or crate directories. An
explicit file outside `src` is rejected instead of silently accepted.

The checker is intentionally conservative and textual. It catches direct
boundary violations without compiling or expanding macros, but it cannot prove
the complete Cargo dependency graph or understand generated tokens. After a
clean scan:

- run `cargo check --lib --bins` for applicable production targets;
- inspect macro expansion when a macro can generate modules or cfgs; and
- review build/generated source boundaries separately with the skill that owns
  those files.

Do not weaken the checker by adding an allowlist for a production-to-test edge.
Move the test behavior out of `src` and expose the smallest legitimate
production API needed by external tests.
