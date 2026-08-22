# Architecture criteria

Apply these criteria to one named application or component boundary. Terms such
as “core”, “port”, and “adapter” describe responsibilities, not required folders.

## Boundary map

Record a compact map before judging the implementation:

| Element | Question | Evidence |
| --- | --- | --- |
| Application | Which behavior must remain useful without production devices? | Use cases, domain behavior, acceptance tests |
| Application entry | Which reusable behavior does an outside actor invoke? | Use case, command handler, callable, or message handler |
| Driving adapter | Which technology translates actor input and invokes the application? | HTTP, CLI, UI, scheduler, consumer, test driver |
| Driven port | Which outside capability does the application require? | Application-owned need expressed in its vocabulary |
| Driven adapter | Which technology satisfies that need? | Database, file, API, clock, queue, email, device |
| Composition-root role | Where are concrete adapters selected and assembled? | Process entrypoints, deployment wiring, driving adapters, or test drivers that assemble the applications they drive |

One driven port may have several adapters. One adapter may participate in
several conversations when its responsibility remains coherent. Split by
purpose, not by a desire to maximize the number of ports. Do not invent a
driving port unless a real substitutability test, such as static A/B testing,
requires parallel driving implementations.

## Invariants and tests

### The application owns its vocabulary

- Application entry and driven-port inputs, outputs, errors, and operations use
  application or domain terms.
- Vendor request types, ORM entities, transport status codes, widgets, and
  serialization objects do not become the core contract by convenience.
- A driven port states what the application needs; the adapter translates that
  need to the provider's API.

Evidence: change or replace one adapter without changing the relevant business
behavior or port contract.

### Dependencies respect adapter direction

- Driving adapters may depend on application entry behavior and invoke it.
- Driven adapters may depend on driven-port and domain-data contracts needed for
  translation, but never on application orchestration or use-case
  implementations.
- The core does not import concrete adapter, UI, persistence, network, broker,
  or framework implementation modules.
- Shared code is not used as a back door that exposes technology types inward.
- Composition-root roles are narrow, explicit edge assembly points that know
  both ports and concrete adapters.

Evidence: source/build dependency graph plus review of public boundary types.
Data or call flow can point outward while source dependencies still point
inward.

### Adapters translate; the application decides

- Driving adapters parse input, translate protocol errors, and invoke reusable
  application behavior. They may verify transport-level credentials — TLS client
  certificates, request signatures, token signatures — when the boundary
  exposes untrusted callers, and need none when it does not. When identity or
  credential policy is itself application behavior, it stays inside and the
  adapter only carries the protocol.
- Application policy, authorization decisions, state transitions, and business
  validation stay inside.
- Driven adapters translate domain or application data into provider DTOs, make
  the external request, parse and validate the response, translate it back into
  domain results or unrecoverable errors, and apply technology-level recovery
  or retry handling. Business retry policy and outcome decisions remain inside.

Evidence: the same application behavior passes with deterministic driven
substitutes, while adapter tests cover both input and output translation.

### The boundary is executable without production devices

- Core behavior can run without UI, database, network, broker, clock, file
  system, or vendor service when those are outside the chosen boundary.
- Boundary tests invoke application behavior and substitute driven adapters.
- Adapter integration tests verify translation and provider contracts separately.

Evidence: run the boundary suite with in-memory or deterministic adapters and no
production services.

## Multiple components and events

Treat each independently deployable or replaceable component as a candidate
hexagon, then justify the actual boundary. Communication between two hexagons
crosses an adapter boundary and needs translation when their vocabularies
differ. The producing side normally exposes a driven port, while the consuming
side's driving adapter invokes its application behavior.

For event-driven systems, a message consumer is usually a driving adapter and a
publisher is usually a driven adapter. The event schema may be a port contract
only when the application owns it; an external broker or partner schema normally
belongs in the adapter and is translated.

## Common false assurances

Flag these as missing evidence, not automatic failures:

- folders named `ports` and `adapters` with business logic in controllers or
  repositories;
- an interface for every class, even when it expresses no boundary conversation;
- a domain package that imports framework annotations, persistence models, or
  transport errors;
- a driving adapter that calls a persistence adapter directly and bypasses
  application behavior;
- concrete adapter selection scattered through business modules;
- tests that require every production device, or tests that mock only internal
  collaborators while never exercising the boundary;
- a static rule set generated to accept every dependency already present; or
  a clean graph whose port semantics and adapter responsibilities were never
  reviewed.

## Static dependency validation

- Use the repository's language-appropriate deterministic validator.
- Declare architectural roles and rules from intended design, not from the
  current graph.
- Distinguish driving adapters from driven adapters when enforcing that driven
  adapters cannot depend on application use-case implementations.
- Treat unresolved analysis, unsupported relationships, and unmatched roles as
  failures or explicit evidence gaps, never as passes.

Evidence: pinned tool identity, reviewed configuration, deterministic report,
and the limitations relevant to the analyzed source.

## Proportionality and scope

Load this architecture lens for every durable software change, then add only the
structure the requested scope and actual external devices justify.

- For new maintained software, establish the necessary seams before runtime
  technologies spread into application behavior.
- For a focused change in an existing system, preserve its architecture, avoid
  new inward leaks, and improve only the boundary the request touches.
- Start a wider ports-and-adapters migration only when the user authorizes that
  scope.
- For a small pure program or cohesive module with no meaningful external
  devices, functions plus tests may provide sufficient separation.

Do not add interfaces, layers, folders, or assembly types solely to match a
diagram.
