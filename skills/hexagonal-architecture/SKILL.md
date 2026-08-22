---
name: hexagonal-architecture
description: >-
  Apply hexagonal architecture (ports and adapters) as the default design and
  separation discipline for durable software. Use when creating, modifying,
  refactoring, debugging, testing, or reviewing any maintained CLI,
  application, service, library, daemon, worker, or component, even if the
  request does not name the pattern; also use for explicit hexagonal or
  ports-and-adapters work. Scale guidance to the requested scope and do not turn
  a focused change into an architecture migration. Do not use for disposable
  one-off scripts, non-coding tasks, or geometric hexagons.
---

# Hexagonal Architecture

Apply ports and adapters proportionally to every durable software task. Protect
application behavior from runtime technologies by making each necessary
boundary explicit, owned by the application, replaceable, and tested. Treat
static dependency checks as evidence of declared boundaries, not proof that the
architecture is semantically correct.

## 1. Establish the target boundary

Treat the user's requested change boundary as hard scope. For a focused change
in an existing system, map only the boundary it touches, preserve the current
architecture, and prevent new inward dependency leaks. Do not initiate a
repository-wide migration unless the user explicitly asks for one.

Within that scope, identify the application or component that forms the
hexagon, and name only what the task requires:

- the behavior that belongs inside;
- the external actors and runtime technologies outside;
- the purposeful conversations crossing the boundary; and
- the existing constraints, tests, and dependency structure.

Do not assume that a repository, service, package, bounded context, or folder is
one hexagon. Ask only when choosing the wrong boundary would materially change
the design. For an explanation or review request, remain read-only unless the
user also asks for changes.

Read [the architecture criteria](references/architecture-criteria.md) before
changing or assessing durable software. Read [the language-neutral
examples](references/examples.md) when deciding how much structure to add or
how to keep a change scoped. Apply the criteria to the chosen boundary; do not
impose an example layout or terminology on the repository.

## 2. Model reusable behavior and driven ports

Let driving adapters translate external input and invoke reusable application
behavior directly. The application's own entry API is its inbound boundary; do
not put a separate driving-port abstraction in front of it by default.
Introduce a distinct inbound abstraction only when a real substitutability test
needs parallel driving implementations, such as static A/B testing.

Describe each driven port in application or domain language. A driven port is a
purposeful need the application has from an outside capability, not every
function, type, or language interface near a boundary. A driven adapter
translates between that vocabulary and a specific technology or external
component.

Represent a driven port with the language's simplest suitable mechanism: a
function, protocol, trait, module contract, message schema, command handler, or
interface.
Do not introduce an interface only to make the diagram look hexagonal. The
application owns the driven boundary vocabulary even when the language has no
explicit interface construct.

## 3. Set dependency direction and wiring

Keep dependency permissions asymmetric:

- keep business decisions and application orchestration independent of UI,
  transport, persistence, vendor SDK, and framework implementations;
- place concrete translation, serialization, persistence, and protocol behavior
  in adapters;
- let driving adapters depend on and invoke application entry behavior;
- let driven adapters depend only on the driven-port and domain-data contracts
  needed for translation, never on application orchestration or use-case
  implementations;
- make driven adapters satisfy application-owned needs rather than exposing
  vendor APIs to the core; and
- assemble concrete adapters at explicit edge assembly points outside the core,
  such as process entrypoints, deployment wiring modules, driving adapters, or
  test drivers.

Treat each edge assembly point as a composition-root role. It may know concrete
implementations only for assembly and startup; do not let it accumulate business
decisions. Do not infer dependency direction from request or data flow: a
database adapter can receive calls originating in the application while its
source dependencies remain limited to driven-port and domain-data contracts.

Preserve existing conventions when they satisfy these invariants. Hexagonal
architecture does not require `domain`, `application`, `ports`, and `adapters`
folders, dependency-injection containers, object orientation, domain-driven
design, CQRS, or microservices.

## 4. Change the system through executable seams

For new durable software, implement one reusable application behavior and any
needed driven ports before attaching driving and production driven adapters.

For existing software, work in slices scaled to the authorized scope:

1. preserve current behavior with characterization or acceptance tests;
2. choose one external interaction the change touches and define the
   application-owned conversation;
3. move that technology translation behind an adapter;
4. wire the adapter at the relevant edge assembly point; and
5. repeat only while the authorized scope still calls for it.

For a focused change, apply only the steps its boundary actually requires and
stop when the requested outcome is complete. An authorized architectural
refactor runs the full sequence; a bug fix or small feature usually needs step 1
and nothing beyond the seam it touches.

Test application behavior directly using deterministic substitutes for driven
ports. Test each adapter against the relevant application or driven-port
contract and real technology at the narrowest useful integration boundary. A
mock-heavy internal object graph is not a substitute for tests around the
application boundary.

## 5. Validate with independent evidence

Collect evidence proportional to the change. A focused change needs evidence
only for the boundary it touched; new durable software or an authorized
refactor needs all three kinds:

1. **Semantic:** application entry behavior and driven ports use meaningful
   application vocabulary; adapters translate rather than decide business
   policy.
2. **Structural:** source and build dependencies do not point from the core to
   concrete adapters or runtime mechanisms; any exception is explicit and
   justified.
3. **Behavioral:** the application runs relevant business tests without its UI,
   database, network, broker, or other production devices.

Read [the static validation contract](references/static-validation.md). Run a
deterministic dependency validator when the work is new durable software or an
authorized refactor, when the change moves dependency direction, or when the
result claims a structural boundary invariant. For a focused change that does
neither, record structural evidence as out of scope rather than running a
repository-wide validator. A structural compliance claim always requires a
reviewed rule configuration and a completed validator result. If no suitable
validator exists and adding one is outside scope, report the missing evidence
instead of claiming compliance.

For Rust only, also read [the Rust validator
profile](references/rust-validator.md). Never apply that binary to another
language. Require approval, checksum verification, and provenance verification
before downloading or installing any external validator. Never generate a
passing configuration from the current graph and present it as proof.

Do not claim compliance from folder names, interface counts, a diagram, or a
clean dependency graph alone. Report analyzer blind spots and unresolved edges
as limitations, not passes.

## 6. Return a reviewable result

Summarize what the change actually involved. For a focused change, report the
touched boundary, the change, its tests, and any structural evidence marked out
of scope; omit the rest. For new durable software or an authorized refactor,
cover:

- the chosen boundary and its inside/outside map;
- application entry behavior, each driven port, and their adapters;
- dependency rules and justified edge-assembly exceptions;
- the static validator, reviewed configuration, result, and limitations;
- changes made or findings, with concrete evidence;
- tests and deterministic validation run; and
- unresolved semantic questions, tool limitations, and justified deviations.

For a review, rank findings by architectural impact and cite the dependency or
behavioral path that demonstrates each violation. For an implementation, keep
the repository working after every slice and match its established style.

## 7. Maintain the bundle

Bundle provenance, volatile sources, drift signals, and the next recheck are
recorded in [research and
longevity](references/research-and-longevity.md).
