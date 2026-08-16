# Architecture criteria

Apply these criteria to one named application or component boundary. Terms such
as “core”, “port”, and “adapter” describe responsibilities, not required folders.

## Boundary map

Record a compact map before judging the implementation:

| Element | Question | Evidence |
| --- | --- | --- |
| Application | Which behavior must remain useful without production devices? | Use cases, domain behavior, acceptance tests |
| Driving port | What can an outside actor ask the application to do? | Application-owned callable or message contract |
| Driving adapter | Which technology translates an actor's input into that port? | HTTP, CLI, UI, scheduler, consumer, test driver |
| Driven port | Which outside capability does the application require? | Application-owned need expressed in its vocabulary |
| Driven adapter | Which technology satisfies that need? | Database, file, API, clock, queue, email, device |
| Composition-root role | Where are concrete adapters selected and assembled? | Process entrypoints, deployment wiring, driving adapters, or test drivers that assemble the applications they drive |

One port may have several adapters. One adapter may participate in several
conversations when its responsibility remains coherent. Split by purpose, not
by a desire to maximize the number of ports.

## Invariants and tests

### The application owns its vocabulary

- Port inputs, outputs, errors, and operations use application or domain terms.
- Vendor request types, ORM entities, transport status codes, widgets, and
  serialization objects do not become the core contract by convenience.
- A driven port states what the application needs; the adapter translates that
  need to the provider's API.

Evidence: change or replace one adapter without changing the relevant business
behavior or port contract.

### Dependencies point toward the application

- Adapters may depend on application-owned ports and core types.
- The core does not import concrete adapter, UI, persistence, network, broker,
  or framework implementation modules.
- Shared code is not used as a back door that exposes technology types inward.
- Composition-root roles are narrow, explicit edge assembly points that know
  both ports and concrete adapters.

Evidence: source/build dependency graph plus review of public boundary types.
Data or call flow can point outward while source dependencies still point
inward.

### Adapters translate; the application decides

- Driving adapters parse input, translate protocol errors, and invoke a
  driving port. They may verify transport-level credentials — TLS client
  certificates, request signatures, token signatures — when the boundary
  exposes untrusted callers, and need none when it does not. When identity or
  credential policy is itself application behavior, it stays inside and the
  adapter only carries the protocol.
- Application policy, authorization decisions, state transitions, and business
  validation stay inside.
- Driven adapters translate application requests and provider responses without
  choosing business outcomes.

Evidence: the same business tests pass through a non-production driving adapter
and deterministic driven substitutes.

### The boundary is executable without production devices

- Core behavior can run without UI, database, network, broker, clock, file
  system, or vendor service when those are outside the chosen boundary.
- Boundary tests call a driving port and substitute driven adapters.
- Adapter integration tests verify translation and provider contracts separately.

Evidence: run the boundary suite with in-memory or deterministic adapters and no
production services.

## Multiple components and events

Treat each independently deployable or replaceable component as a candidate
hexagon, then justify the actual boundary. Communication between two hexagons
crosses a port on each relevant side and needs translation when their vocabularies
differ.

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
