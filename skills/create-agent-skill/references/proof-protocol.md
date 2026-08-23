# Four-lane proof protocol

## Keep the claims separate

Prove format, discovery, triggering, behavior, longevity, and portability independently. Schema validation proves only format, and model self-report is not load-state or isolation evidence.

There are three non-convertible portability claims. Loaded-content-safe is primary; the other two are optional stronger isolation labels:

| Tier                  | Allowed complete claim         | Behavior surface                                                          |
| --------------------- | ------------------------------ | ------------------------------------------------------------------------- |
| `loaded-content-safe` | `portable-loaded-content-safe` | Native skill discovery/loading plus approved reads; observed effects fail |
| `zero-tools`          | `portable-zero-tools`          | Supplied text and final response only; no tool events                     |
| `read-only-tools`     | `portable-read-only-tools`     | Typed list/stat/read/search calls inside one immutable fixture root       |

Never emit generic `portable`. Passing one tier does not imply, upgrade, or weaken the other. Tier comes from the frozen artifacts; the CLI intentionally has no `--tier` switch.

## Separate every proof artifact

Each tier owns a distinct runner pack, target profile, manifest, external key, report, raw traces, and evidence namespace:

| Tier                | Runner pack                                                | Target and grader profiles                                                                       | Manifest                                                    |
| ------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------------- |
| Loaded-content-safe | `evals/create-agent-skill-loaded-content/runner-pack.json` | `references/loaded-content-target-profile.json`; `references/loaded-content-grader-profile.json` | `evals/create-agent-skill-loaded-content/key-manifest.json` |
| Zero-tools          | `evals/create-agent-skill-zero-tools/runner-pack.json`     | `references/zero-tools-target-profile.json`; requested-lane cross-grading                        | `evals/create-agent-skill-zero-tools/key-manifest.json`     |
| Read-only-tools     | `evals/create-agent-skill-read-only/runner-pack.json`      | `references/read-only-target-profile.json`; `references/read-only-grader-profile.json`           | `evals/create-agent-skill-read-only/key-manifest.json`      |

Every pack, profile, manifest, external key, and report uses schema v2 and carries the same `tier` and tier-specific `suite`. Case IDs use `lds-*`, `zro-*`, or `rdo-*`. Do not symlink or dynamically include one tier's artifacts from another.

Runner packs contain prompts, setup, baseline type, and prohibited effects, but no expected answers or checks. The committed manifests contain only schema identity, pack/key digests, case IDs, and check counts. Plaintext keys remain outside Git, all skill bundles, and every runner workspace in coordinator custody. Disclose a tier's key only after both of that tier's behavior arms freeze.

Validate either frozen pack without disclosing its key:

```sh
python3 /path/to/create-agent-skill/scripts/proof_protocol.py validate-cases \
  --manifest /path/to/agent-skills/evals/create-agent-skill-loaded-content/key-manifest.json \
  /path/to/agent-skills/evals/create-agent-skill-loaded-content/runner-pack.json

python3 /path/to/create-agent-skill/scripts/proof_protocol.py validate-cases \
  --manifest /path/to/agent-skills/evals/create-agent-skill-zero-tools/key-manifest.json \
  /path/to/agent-skills/evals/create-agent-skill-zero-tools/runner-pack.json

python3 /path/to/create-agent-skill/scripts/proof_protocol.py validate-cases \
  --manifest /path/to/agent-skills/evals/create-agent-skill-read-only/key-manifest.json \
  /path/to/agent-skills/evals/create-agent-skill-read-only/runner-pack.json
```

If a case changes, replace only that tier's runner digest, re-cut its external key, reseal its manifest, and start a new proof round.

## Loaded-content-safe tier

This is the primary four-harness proof. The with-skill arm installs the real bundle through the harness's native skill root; it never injects skill text into the prompt. A positive case must contain a host-generated read/load event for the resolved `create-agent-skill` bundle. The paired baseline runs without that skill installed, and an installed-skill near-miss must complete without loading it.

Use the strongest current non-effectful host mode and a fresh coordinator-owned harness/task root. Permit only native skill discovery/loading, reads inside the resolved skill bundle, typed fixture-root reads, and final text. Record broad unused tool exposure as a caveat. Reject observed writes, mutations, outside-root success, network/browser/external/MCP/credential use, permission expansion, VCS mutation, subagents, background work, incomplete traces, or changed task hashes.

Each case binds:

- complete baseline and with-skill trace locators and SHA-256 values;
- the exact with-skill native-load event and absence of a baseline load;
- normalized events with arm, capability, resolved path, scope, and status;
- identical pre/post task-surface hashes; and
- frozen final-response artifacts.

Trace admissibility never earns semantic credit. If a harness cannot expose native load/non-load evidence, complete events, and unchanged task state, mark only that lane unavailable. A complete exposed-tool inventory is not required for this tier.

## Zero-tools tier

For each behavior arm and grader:

- pass fixtures as inert prompt content;
- expose an explicit empty tool set and empty MCP configuration;
- disable model-accessible network, writable workspaces, credentials, VCS, browsers, subagents, and background tasks;
- capture the complete host stream and final response in coordinator custody; and
- reject any tool event, incomplete trace, or forbidden-effect event.

The model returns one final text response. Coordinator trace capture is bookkeeping outside the task surface and never counts as case success.

## Read-only-tools tier

This tier assesses final response content while permitting only these canonical capabilities:

- `fixture-list`
- `fixture-stat`
- `fixture-read`
- `fixture-search`

The target profile maps each capability to exact host tool names and declares the exact top-level host record and field that carry the complete inventory. Ignore nested or alternative tool-name lists. A missing, empty, or conflicting declared inventory makes the lane unavailable. Unknown or unmapped exposed tools make the lane unavailable. Shell, generic processes, VCS, network, MCP, browsers, credentials, writes, permission changes, subagents, and background work remain forbidden even if a host describes them as read-only.

For every behavior run:

1. create a fresh coordinator-owned harness root and a dedicated fixture root;
2. use a symlink-free fixture tree with files `0444` and directories `0555`;
3. expose no writable model workspace and only the exact typed read-tool allowlist;
4. hold prompts, fixture-root identity and digest, permissions, and tool mapping constant across lane isolation and both arms;
5. capture the complete tool inventory, tool stream, and final response outside the task surface; and
6. hash the fixture tree before and after the run.

Normalize raw host traces without overwriting prior evidence:

```sh
python3 /path/to/create-agent-skill/scripts/isolation_trace.py \
  --host claude \
  --trace /coordinator/read-only-tools/raw.jsonl \
  --profile /path/to/create-agent-skill/references/read-only-target-profile.json \
  --lane-id claude-opus-1m-xhigh \
  --fixture-root /coordinator/harness/fixtures \
  --fixture-root-id fixtures-v1 \
  --pre-sha256 <fixture-tree-sha256> \
  --post-sha256 <fixture-tree-sha256> \
  --output /coordinator/read-only-tools/normalized.json
```

The normalizer supports Cursor, Antigravity, Codex, and Claude adapters. It fails closed to `unavailable` for unknown tools or tool-shaped events, incomplete streams, successful outside-root reads, symlinked behavior fixtures, and changed hashes. It refuses to overwrite output.

Qualification separately proves an in-root read succeeds, `..` traversal is denied, an in-root symlink to a coordinator-created safe outside sentinel is rejected or denied, and an absolute outside path is denied. Use `--purpose containment` only for that disposable symlink fixture; routine behavior fixtures remain symlink-free. If the host does not expose enough evidence to decide, mark it unavailable.

Tool traces decide arm admissibility only. Never give traces to semantic graders or reward extra reads, discovered files, or tool-call patterns.
Every passing read-only case must nevertheless contain at least one allowed typed read event inside the frozen fixture root; an empty event list is zero-tools-grade evidence and cannot support the read-only claim.

## Observe load state and tier-local availability

Immediately before proof, verify every lane's host, model, reasoning setting, load-state method, tool catalog, permissions, and trace format. A reliable method must prove both loading and non-loading. Do not substitute behavior differences for negative load-state evidence.

Availability is tier-local. A host may be verified for one tier and unavailable for another. An unavailable lane blocks only its tier's claim.
Every verified target lane records a coordinator-evidence locator and SHA-256. Complete validation resolves those locators under `--evidence-root` and hashes the referenced bytes; structural validation does not require external evidence custody.

## Initialize and structurally validate reports

Initialize the primary loaded-content-safe report with its independent zero-tools grader profile:

```sh
python3 /path/to/create-agent-skill/scripts/proof_protocol.py init-report \
  --cases /path/to/agent-skills/evals/create-agent-skill-loaded-content/runner-pack.json \
  --profile /path/to/create-agent-skill/references/loaded-content-target-profile.json \
  --manifest /path/to/agent-skills/evals/create-agent-skill-loaded-content/key-manifest.json \
  --grader-profile /path/to/create-agent-skill/references/loaded-content-grader-profile.json \
  --output /coordinator/loaded-content-safe/proof-report.json
```

Initialize zero-tools without a grader profile:

```sh
python3 /path/to/create-agent-skill/scripts/proof_protocol.py init-report \
  --cases /path/to/agent-skills/evals/create-agent-skill-zero-tools/runner-pack.json \
  --profile /path/to/create-agent-skill/references/zero-tools-target-profile.json \
  --manifest /path/to/agent-skills/evals/create-agent-skill-zero-tools/key-manifest.json \
  --output /coordinator/zero-tools/proof-report.json
```

Initialize read-only-tools with its independently qualified zero-tools grader profile:

```sh
python3 /path/to/create-agent-skill/scripts/proof_protocol.py init-report \
  --cases /path/to/agent-skills/evals/create-agent-skill-read-only/runner-pack.json \
  --profile /path/to/create-agent-skill/references/read-only-target-profile.json \
  --manifest /path/to/agent-skills/evals/create-agent-skill-read-only/key-manifest.json \
  --grader-profile /path/to/create-agent-skill/references/read-only-grader-profile.json \
  --output /coordinator/read-only-tools/proof-report.json
```

Structural validation omits `--key` and `--complete`. Use the same tier-specific arguments passed to `init-report`; include `--grader-profile` for loaded-content-safe and read-only-tools.

## Freeze, then grade response content only

For loaded-content-safe, use native installation and automatic routing. Frozen-text injection is allowed only in the optional stronger-tier behavior comparison, where discovery is proven separately. After both responses and admissibility traces freeze:

1. disclose only the matching tier's external semantic key;
2. record `observed_route` and `observed_longevity`;
3. anonymize the two final responses;
4. give the fresh grader only response text and semantic checks; and
5. expose no grader tools, filesystem, network, writable workspace, traces, fixtures, lane labels, prior grades, or another grader's output.

Zero-tools uses a different requested-lane model for cross-grading. Loaded-content-safe and read-only-tools use their independent grader profiles; each chosen grader must be verified under zero-tools and have a canonical model identity different from its behavior lane. Record `primary_outcome` as `determinate`, `indeterminate`, or `conflict`. A determinate primary grade records secondary grading as `not-required`; an indeterminate or conflicting primary grade requires a second blind grade from another independently qualified model. If no different-model verified grader is qualified, record secondary grading as `unavailable` with evidence and keep the claim `not-proven`. Grader qualification cannot improve target-lane availability or any portability claim.

Complete loaded-content-safe validation:

```sh
python3 /path/to/create-agent-skill/scripts/proof_protocol.py validate-report \
  --cases /path/to/agent-skills/evals/create-agent-skill-loaded-content/runner-pack.json \
  --profile /path/to/create-agent-skill/references/loaded-content-target-profile.json \
  --manifest /path/to/agent-skills/evals/create-agent-skill-loaded-content/key-manifest.json \
  --grader-profile /path/to/create-agent-skill/references/loaded-content-grader-profile.json \
  --evidence-root /coordinator/evidence-root \
  --key /path/outside-git/loaded-content-key.json \
  /coordinator/loaded-content-safe/proof-report.json \
  --complete
```

Complete zero-tools validation:

```sh
python3 /path/to/create-agent-skill/scripts/proof_protocol.py validate-report \
  --cases /path/to/agent-skills/evals/create-agent-skill-zero-tools/runner-pack.json \
  --profile /path/to/create-agent-skill/references/zero-tools-target-profile.json \
  --manifest /path/to/agent-skills/evals/create-agent-skill-zero-tools/key-manifest.json \
  --evidence-root /coordinator/evidence-root \
  --key /path/outside-git/zero-tools-key.json \
  /coordinator/zero-tools/proof-report.json \
  --complete
```

Complete read-only-tools validation:

```sh
python3 /path/to/create-agent-skill/scripts/proof_protocol.py validate-report \
  --cases /path/to/agent-skills/evals/create-agent-skill-read-only/runner-pack.json \
  --profile /path/to/create-agent-skill/references/read-only-target-profile.json \
  --manifest /path/to/agent-skills/evals/create-agent-skill-read-only/key-manifest.json \
  --grader-profile /path/to/create-agent-skill/references/read-only-grader-profile.json \
  --evidence-root /coordinator/evidence-root \
  --key /path/outside-git/read-only-tools-key.json \
  /coordinator/read-only-tools/proof-report.json \
  --complete
```

The validator checks tier/suite equality, live qualification-evidence hashes, external-key boundaries, frozen criteria, canonical cross-model grading, conditional second grading, response-artifact presence, reconciled fixture provenance, normalized isolation evidence, structured longevity, and completeness. `arm_labels_anonymized`, `graded_after_both_arms`, and `key_custody` remain attestations whose substance requires review.

Claim a tier's portability only after every requested lane and frozen case passes its complete gates. Otherwise keep that report at `not-proven` and name unavailable lanes and unresolved evidence.
