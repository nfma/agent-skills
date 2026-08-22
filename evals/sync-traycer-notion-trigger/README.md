# Claude trigger and deterministic behavior evaluation

This runner executes the Claude Code lane in the repository production-eval
framework. It reads the 20-task production suite from
`evals/sync-traycer-notion/suite.json`, runs three fresh trials per task, and
compares each positive treatment response with a no-skill baseline. Near-miss
tasks run only with the project skill installed.

Evaluation prompts never name or invoke the skill. Claude Code must discover the
project skill and automatically invoke it for positive tasks while abstaining on
near-misses. MCP servers and live mutation tools are unavailable. The runner
uses the frozen qualified Claude profile: Claude Opus 5 `[1m]`, xhigh effort,
plan permission mode, project settings only, and `Skill`, `Read`, `Glob`, and
`Grep` tools.

The answer-bearing deterministic grading key, its sealing manifest, all raw
traces, and the generated report remain outside the repository. Supply the key
manifest explicitly when grading. The generated report contains aggregate
results, check identifiers, and hashes that bind the external evidence.

Run from the repository root:

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/run-trigger-evals.py run \
  --output-dir /absolute/path/outside/the/repository \
  --max-budget-usd 1.00 \
  --timeout-seconds 420 \
  --jobs 4

uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/run-trigger-evals.py grade \
  --run-manifest /absolute/path/outside/the/repository/run-manifest.json \
  --key /absolute/path/outside/the/repository/key.json \
  --key-manifest /absolute/path/outside/the/repository/key-manifest.json \
  --output /absolute/path/outside/the/repository/proof-report.json
```

The evaluation does not establish a production or four-harness portability
claim. The suite remains `draft`, human calibration is pending, and its overall
production-evidence status is `not-proven`.

## Other harness preflights

Before a Codex model evaluation, copy the candidate bundle into an isolated
workspace at `.agents/skills/sync-traycer-notion/`. Capture the target and full
model-visible inventory with the no-model preflight, keeping the record outside
the workspace and outside Git:

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion/codex-single-candidate-preflight.py \
  --workspace /absolute/path/to/isolated/workspace \
  --candidate /absolute/path/to/isolated/workspace/.agents/skills/sync-traycer-notion/SKILL.md \
  > /absolute/external/evidence/codex-inventory-capture.json
```

Immediately before model execution, rerun the diagnostic against that capture:

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion/codex-single-candidate-preflight.py \
  --workspace /absolute/path/to/isolated/workspace \
  --candidate /absolute/path/to/isolated/workspace/.agents/skills/sync-traycer-notion/SKILL.md \
  --expected-evidence /absolute/external/evidence/codex-inventory-capture.json \
  > /absolute/external/evidence/codex-inventory-verified.json
```

Only a schema-v2 record with `verification.verified: true` satisfies the full
inventory gate. It binds the candidate bundle, Codex version, complete before
and after skill inventories, skills-instruction blocks, and exact
`skills.config` override. `prompt_input_sha256` is a per-invocation audit digest
because it includes the user prompt; `inventory.sha256` and
`skills_instructions_sha256` are the prompt-independent anchors. Carry the
verified override unchanged into the later Codex evaluation. The preflight
calls only `codex debug prompt-input`; it never invokes a model.
Codex's discovery roots and same-name behavior are documented in the
[OpenAI Skills documentation](https://learn.chatgpt.com/docs/build-skills).

Cursor has no equivalent no-model inventory command. Its clean-room contract is
documented in
[`CURSOR_ENVIRONMENT.md`](../sync-traycer-notion/CURSOR_ENVIRONMENT.md). Paid
Codex and Cursor canaries remain blocked until their respective preflight
contracts pass and the coordinator explicitly authorizes the calls.
