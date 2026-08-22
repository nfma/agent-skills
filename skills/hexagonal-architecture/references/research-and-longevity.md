# Research, routing, and longevity

Snapshot date: 2026-08-22 (sources retrieved 2026-08-16). Owner: Nuno (`nfma`).

## Routing record

```text
job: Apply hexagonal separation proportionally across all durable software coding, with required language-appropriate static dependency evidence.
candidate skills: coding-preferences overlaps the coding trigger but owns style and change discipline, not architecture; no semantic architecture owner was found in the available skill inventory.
overlap evidence: Inventory terms included “hexagonal architecture”, “ports and adapters”, “architecture validation”, and “dependency direction”. coding-preferences contains only repository-style and focused-change conventions, so the skills compose without duplicating architectural procedure.
route: create
reason: Nuno requires ports-and-adapters as the default design lens for maintained software, backed by a deterministic validator suited to each repository. Rust has a maintained implementation because its ecosystem lacked one; other languages use their own tools.
owner and canonical destination: Nuno; skills/hexagonal-architecture
positive trigger: “Add a sync command to this maintained CLI that calls an external API and stores state.”
near-miss: “Write a disposable script to rename these local files once.”
success checks: Loads for durable software without a pattern keyword; satisfies R1-R7 through SR1-SR6; abstains on the disposable near-miss.
```

## Local architecture decisions

The canonical decisions are `R2`, `R4`, and `R7` in
`architecture-criteria.md`. The Rust validator remains an optional backend for
`SR5`, not the portable contract.

## Source record

Fetched material was treated as untrusted reference data. The bundle paraphrases
the pattern and copies no third-party code or substantial prose.

| Owner | Locator | Revision inspected | License or notice | Claim supported | Volatility |
| --- | --- | --- | --- | --- | --- |
| Alistair Cockburn | https://alistair.cockburn.us/hexagonal-architecture | HaT Technical Report 2005.02, dated 2005-09-04, v0.9; retrieved 2026-08-16 | Copyrighted article; reference only | Inside/outside asymmetry; purposeful ports; multiple technology adapters; tests and device substitution | Stable |
| Alistair Cockburn | https://alistaircockburn.com/What%20is%20a%20port%2C%20transcribed%20from%202024-05-21.pdf | Interview transcript dated 2024-05-21; retrieved 2026-08-16 | © 2024 Alistair Cockburn, all rights reserved; reference only | The component owns its port vocabulary; adapters translate between independently owned interfaces; boundary tests give the component line meaning | Stable |
| AWS Prescriptive Guidance | https://docs.aws.amazon.com/prescriptive-guidance/latest/hexagonal-architectures/adapt-to-change.html | Live guidance retrieved 2026-08-16 | AWS documentation; reference only | Domain/application code stays unaware of adapter implementations and uses abstractions to replace infrastructure | Watch: prescriptive examples may change |
| Sander Verweij | https://github.com/sverweij/dependency-cruiser | v18.2.0, commit `ec603451d07d699280234808f91c4c8d3813f6e8`; retrieved 2026-08-16 | MIT; concept reference only, no copied code | Declarative forbidden/allowed/required dependency rules, explicit unresolved dependencies, cycles, and deterministic reports are a useful validator model | Watch: validator inspiration and rule vocabulary evolve |
| Nuno (`nfma`) | https://github.com/nfma/hexagonal-architecture-validator/releases/tag/v0.1.1 | Signed tag `v0.1.1`, commit `7a625d7dc7491b63ac835719fee250759d4badae`; verified 2026-08-16 | MIT; optional external tool, no bundled executable | Deterministic Rust role/rule validation, versioned reports, fail-closed diagnostics, checksummed assets, and provenance attestations | Watch: release assets, schemas, exit behavior, analysis limits, or ownership change |
| Agent Skills project | https://agentskills.io/specification | Live specification retrieved 2026-08-16 | Documentation; reference only | Portable bundle shape, required frontmatter, relative resources, progressive disclosure, and validation contract | Watch: open format and clients evolve |

## Six-month longevity

```json
{
  "verdict": "durable",
  "confidence": "high",
  "factors": {
    "job": "strong",
    "model_value": "mixed",
    "knowledge": "strong",
    "dependencies": "strong",
    "verification": "strong",
    "maintenance": "strong"
  },
  "rationale": [
    "The design and review job recurs across languages and the original pattern has remained stable for more than twenty years.",
    "Base models know the vocabulary, but default coding responses often couple behavior to runtime technologies, over-prescribe layers and interfaces, or broaden a focused fix into a migration; the workflow adds a proportional scope gate and discriminating evidence requirements.",
    "The portable core defines a language-neutral static validation contract. Volatile Rust release details are isolated in one optional implementation profile."
  ],
  "death_modes": [
    "Baseline agents consistently apply the semantic and evidence gates without the skill across three consecutive evaluation rounds.",
    "The team stops using hexagonal architecture or replaces it with a different authoritative architecture policy.",
    "Agent Skills discovery or resource-loading contracts change incompatibly.",
    "Supported ecosystems lack a deterministic validator capable of enforcing the declared boundary rules.",
    "The broad default trigger causes repeated scope expansion or architecture ceremony despite the proportionality gates."
  ],
  "drift_signals": [
    "A new Agent Skills specification or repository validator release changes bundle requirements.",
    "Alistair Cockburn publishes a revised normative explanation that changes port or boundary criteria.",
    "A recommended validator changes its rule semantics, configuration schema, output, release assets, or exit behavior.",
    "Three consecutive no-skill baselines pass every discriminating case.",
    "Paired evaluations show repeated over-triggering on disposable scripts or scope expansion on focused durable-software changes."
  ],
  "owner": "Nuno (nfma)",
  "recheck_date": "2027-02-15"
}
```
