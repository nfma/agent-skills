# Language-neutral examples

These examples show responsibilities and scope, not required folders, types, or
language constructs.

## Maintained CLI with external devices

Request: add a durable synchronization CLI that reads configuration, calls a
remote catalog, stores a cursor, and reports progress.

A proportional boundary might be:

| Role | Purpose |
| --- | --- |
| Application entry | Start or resume synchronization |
| Driving adapter | Parse CLI input, invoke synchronization, and render progress or errors |
| Application | Decide what to fetch, persist, and report, and which retries are business decisions |
| Driven ports | Load configuration, fetch catalog pages, store the cursor, obtain time |
| Driven adapters | Translate data and errors for the file, API, state store, and clock |
| Edge assembly | Select concrete adapters and start the command |

Test synchronization behavior directly with deterministic catalog, state, and
clock substitutes. Test CLI parsing and both directions of HTTP translation
separately. Do not require six interfaces or matching folders when functions or
modules express the same seams clearly.

## Focused change in an existing service

Request: fix a timezone bug in an invoicing worker.

Load the architecture lens, but keep the bug fix as hard scope. Identify the
boundary touched by time handling, preserve the worker's current architecture,
and prevent a new dependency on the system clock from entering business rules.
Add the smallest deterministic time seam and tests needed for the fix.

Do not extract every database or queue interaction, rename the repository, or
start a full ports-and-adapters migration unless the user separately authorizes
that work.

## Structural violation

Observed dependency: application pricing behavior imports a concrete SQL rate
repository.

The violation is the inward source dependency, not the fact that data flows to
a database. Define the pricing behavior's required rate conversation in
application language, make the SQL adapter satisfy it, and select that adapter
at an edge assembly point. Verify the behavior with an in-memory rate adapter
and the SQL translation with an integration test.

A clean result from the repository's language-appropriate static validator
supports the dependency claim only. Review driven-port meaning, adapter
responsibility, and device-free behavior independently.

## Disposable script near miss

Request: write a one-off local script that renames a known batch of files.

Do not apply this skill's structure. Use direct, safe file handling with the
tests or dry-run behavior the script's risk justifies. Do not invent ports,
adapters, or an application core for disposable work.
