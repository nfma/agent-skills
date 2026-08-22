# Architecture criteria

Apply this registry to one named application or component. The handles are the
canonical rule definitions for the bundle.

## Vocabulary

| Term | Responsibility |
| --- | --- |
| Application | Behavior that remains useful without production devices |
| Application entry | Reusable behavior invoked by an outside actor |
| Driving adapter | Translates actor input and invokes the application |
| Driven port | Application-owned need for an outside capability |
| Driven adapter | Technology satisfying a driven port |
| Edge assembly | Selects concrete adapters and starts or drives the application |

These are responsibilities, not required folders or types. One port may have
several adapters; one coherent adapter may serve several conversations.

## Rule registry

### R1 — Scope one justified boundary

Treat the requested change as hard scope. Choose the smallest application or
component boundary that explains the touched behavior and devices. Add only the
seams those devices justify; begin a wider migration only when authorized. A
small cohesive program with no meaningful external device may need only
functions and tests.

Evidence: a compact inside/outside map tied to the requested behavior.

### R2 — Use the application entry as the inbound boundary

Let driving adapters invoke reusable application behavior directly. Add a
separate driving-port abstraction only when a real substitutability test needs
parallel driving implementations, such as static A/B testing.

Evidence: each driving adapter reaches the same reusable behavior without
bypassing it or adding a ceremonial inbound interface.

### R3 — Let the application own driven vocabulary

A driven port names a purposeful application need, not every nearby function or
interface. Its operations, data, and errors use application or domain terms.
Vendor DTOs, ORM entities, transport statuses, widgets, and serialization types
remain in adapters. Express the port with the language's simplest suitable
mechanism.

Evidence: replace one driven adapter without changing the behavior or contract.

### R4 — Keep dependencies asymmetric

- Core behavior never imports concrete UI, persistence, network, broker,
  framework, or adapter implementations.
- Driving adapters may depend on application entry behavior.
- Driven adapters may depend only on driven-port and domain-data contracts
  needed for translation, never application orchestration or use cases.
- Edge assembly may know both ports and concrete adapters only for selection,
  startup, and test wiring; it contains no business decisions.
- Shared modules never expose technology types inward.

Data or call flow may point outward while source dependencies still point
inward.

Evidence: source/build graph plus review of public boundary types.

### R5 — Let adapters translate and the application decide

Driving adapters parse input, translate protocol errors, and may verify
transport credentials. Application behavior owns business validation,
authorization, state transitions, and policy. Driven adapters translate
application data to provider DTOs, perform the external interaction, validate
the response, and return domain results or unrecoverable errors.

A driven adapter may retry only provider-defined transient failures within the
port's attempt envelope. Put the decision inside the application when retry can
change a business outcome, cost, ordering, or non-idempotent effect.

Evidence: application tests use deterministic driven substitutes; adapter tests
cover both translation directions and provider contracts.

### R6 — Keep the boundary executable without production devices

Run application behavior without its UI, database, network, broker, clock,
filesystem, or vendor service when those devices are outside the boundary.
Test the application through its entry with deterministic driven substitutes;
test adapters separately at the narrowest useful integration boundary.

Evidence: the boundary suite runs without production services.

### R7 — Fail closed on structural proof

Derive role and dependency rules from intended design, not the current graph.
Use a deterministic language-appropriate validator and retain concrete
source/target evidence. Treat unresolved imports, unsupported relationships,
unmatched roles, and analyzer blind spots as failures or explicit evidence gaps.

When driven-port or domain-data contracts are not statically separable from
use-case modules, do not claim proof of `R4` for driven adapters. A clean graph
proves only the declared dependency model; it does not prove `R2`, `R3`, `R5`,
or `R6`.

Evidence: pinned tool identity, reviewed configuration, deterministic report,
and relevant limitations.

## Multiple components and events

Treat each independently deployable or replaceable component as a candidate
boundary, then justify the actual split with `R1`. Communication between two
boundaries crosses adapters when vocabularies differ. A message consumer is
usually a driving adapter and a publisher a driven adapter; an external schema
stays in the adapter unless the application owns it.

## Common false assurances

- Named `ports` and `adapters` folders do not satisfy `R3`–`R5`.
- An interface per class does not satisfy `R2` or `R3`.
- Controllers or driving adapters that bypass application behavior violate
  `R2` even when dependencies otherwise look clean.
- Framework types in core contracts or concrete adapter imports violate `R3`
  or `R4`.
- Production-only or internal-mock-only tests do not satisfy `R6`.
- Rules generated to accept the current graph, or graphs with unresolved edges,
  do not satisfy `R7`.
