# Tests-only boundary

## Allowed repository surface

Resolve every path lexically and physically before editing. The only allowed
persistent repository writes are descendants of `tests/` directly beneath a
Cargo manifest that contains a `[package]` table.

```text
workspace/
├── Cargo.toml                 read-only, often a virtual workspace
├── crates/
│   └── ledger/
│       ├── Cargo.toml         read-only concrete package
│       ├── src/               read-only
│       └── tests/             writable
└── tests/                     read-only when workspace root is virtual
```

Allowed authored assets include Rust integration targets, fixtures, reviewed
snapshots/regressions, reference models, Lean sources, and TLA+ specifications.
Do not create a `Cargo.toml` anywhere below `tests/`.

Read access to production is necessary to find public behavior and material
risk. It does not authorize production edits, test seams, `cfg` flags, imports,
annotations, dependencies, features, lint configuration, or bug fixes.

## Workspace and target cases

- A package with a library target exposes integration-test imports through its
  public API.
- A bin-only package is black-box tested through
  `env!("CARGO_BIN_EXE_<name>")`; Cargo builds the binary for the integration
  target without a manifest edit.
- A virtual workspace root does not own an integration-test target. Write under
  each selected member package's `tests/`.
- A proc-macro/type-level API can receive ordinary success tests. Compile-UI
  tests require a pre-existing dependency and harness such as Trybuild.
- Never turn `tests/` into a nested Cargo package to gain dependencies or a
  different toolchain.

## Write-surface controls

Capture Git status and a content snapshot with `check_tests_boundaries.py`
before edits. The verifier hashes tracked and non-ignored authored content,
complete allowed `tests/` trees, initialized submodule content, and every ignored
path outside its recorded scratch exclusions. It automatically excludes only
Git-ignored `target`, `node_modules`, `.venv`, and `.DS_Store` roots present at
snapshot time; a new one is a violation. Use repeatable `--exclude-scratch` for
an exact Git-ignored ambient root such as `.idea`, after confirming it is not
authored or production content. Protected repository paths and overlaps with
allowed tests are rejected. It reports every exclusion and unavailable gitlink
as a proof limitation. The snapshot still records pre-existing dirty content,
so a later mutation of an already-dirty or ignored production file fails
verification. Keep the timestamped snapshot outside the repository and pass
every concrete package root explicitly.

Deletion is a separate capability. Never delete a baseline-dirty or untracked
test. For a clean tracked test, first show the exact deletion set, explain which
reviewed property or faster evidence subsumes it, and obtain user approval. Pass
each approved repository-relative path to verification with
`--allow-test-deletion`; the verifier rejects blanket or mismatched approvals.
Non-empty same-content renames within allowed test roots and the same file suffix
are paired by content digest and reported as exact source-to-destination moves.
Zero-byte files, cross-kind copies, and renames combined with edits are not
provably lossless and still require approval for deletion of the original.

For repository tools:

- set Cargo build output to a fresh external `CARGO_TARGET_DIR`;
- use locked dependency resolution for in-repository Cargo commands;
- when the lockfile is absent/stale, use a disposable repository copy and copy
  back only reviewed allowed test files;
- set cargo-mutants `--output` to an external directory and never use
  `--in-place`;
- set `CARGO_TARGET_DIR` externally before relying on its derived
  `CARGO_TARGET_TMPDIR`, or use another proven external path for runtime scratch;
- configure Proptest persistence to a concrete source-controlled file below
  package `tests/`, creating its parent before the first run;
- retain reviewed snapshots for integration targets below allowed `tests/`;
  redirect pending snapshots originating outside the allowed roots, coverage
  profiles, TLC state, Lean build state, `mutants.out`, fuzz artifacts, and
  benchmark reports externally, or run in a disposable copy when redirection is
  not proven; and
- do not install tools, components, dependencies, or language runtimes.

Before invoking Cargo, read `rust-toolchain.toml` or `rust-toolchain` without
executing it and compare the requested channel with `rustup toolchain list`. If
the declared or `+toolchain` channel is absent, report a prerequisite instead of
letting rustup auto-install it. Treat first-run setup such as Miri sysroot
preparation as unavailable unless it is already complete and proven
non-mutating outside disposable scratch.

Post-run boundary verification is necessary but not sufficient: also inspect
the authored `tests/` diff so generated bulk output does not masquerade as
maintained evidence.

## Boundary-caused evidence gaps

Classify code as boundary-unreachable when no allowed integration/CLI path can
observe its behavior. This is distinct from:

- a weak oracle for reachable behavior;
- missing generator partitions;
- a tool limitation; or
- an equivalent mutant.

Report the affected risk and the smallest external prerequisite. Do not add a
test-only production export or broaden visibility.

## Failure handling

Stop the current write-producing phase when:

- package roots cannot be resolved safely;
- an allowed test root or parent is a symlink;
- the workspace is not the resolved Git worktree root;
- a command can write in-repository output that cannot be redirected;
- the boundary verifier detects a production/config/generated-output write; or
- cleanup would require deleting or reverting user-owned material.

Preserve evidence, identify exact paths, and ask for direction before cleanup of
anything not created unambiguously by the current run.
