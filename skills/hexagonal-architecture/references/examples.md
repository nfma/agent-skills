# Language-neutral examples

These examples apply the canonical `R<n>` and `SR<n>` handles; they do not
define new rules or prescribe layouts.

## Maintained CLI with external devices

Request: add a durable synchronization CLI that reads configuration, calls a
remote catalog, stores a cursor, and reports progress.

`SR1` identifies one synchronization boundary. The CLI is a driving adapter;
synchronization is the application entry; configuration, catalog, state, and
time are driven needs; their technologies are driven adapters. Apply `R2`–`R5`
without requiring matching folders or six interfaces. Prove `R6` with
deterministic catalog, state, and clock substitutes and separate CLI/HTTP tests.

## Focused change in an existing service

Request: fix a timezone bug in an invoicing worker.

Use `SR1` and `R1` to keep the fix as hard scope. Prevent a system-clock
dependency from entering business rules, add the smallest deterministic time
seam and tests, and stop. Do not extract unrelated database or queue
interactions or begin a migration.

## Structural violation

Observed dependency: application pricing behavior imports a concrete SQL rate
repository.

This violates `R4`; outbound data flow is irrelevant. Define the required rate
conversation with `R3`, satisfy it in the SQL adapter, and wire it at edge
assembly. Verify `R6`, then use `SR5` to report the static result and its
limited claim.

## Disposable script near miss

Request: write a one-off local script that renames a known batch of files.

This is outside the skill trigger. Use direct safe file handling and only the
tests or dry-run behavior its risk justifies; do not invent a hexagonal
boundary.
