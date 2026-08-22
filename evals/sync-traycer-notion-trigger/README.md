# Claude trigger and deterministic behavior evaluation

This runner executes the Claude Code lane in the repository production-eval
framework. It reads the 20-task production suite from
`evals/sync-traycer-notion-trigger/suite.json`, runs three fresh trials per task, and
compares each of 12 positive treatment responses with a no-skill baseline.
Eight near-miss tasks run only with the project skill installed, for 96 total
sessions.

Evaluation prompts never name or invoke the skill. Claude Code must discover the
project skill and automatically invoke it for positive tasks while abstaining on
near-misses. MCP servers and live mutation tools are unavailable. The runner
uses the frozen qualified Claude profile: Claude Opus 5 `[1m]`, xhigh effort,
plan permission mode, project settings only, and `Skill`, `Read`, `Glob`, and
`Grep` tools.

The answer-bearing deterministic grading key and all raw traces remain outside
the repository. The answer-free sealing manifest is committed and pushed before
execution. The generated report contains aggregate results, check identifiers,
and hashes that bind the external evidence. See [the custody runbook](CUSTODY.md)
for encrypted-key and raw-archive recovery.

Run from the repository root:

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/run-trigger-evals.py run \
  --output-dir /absolute/path/outside/the/repository \
  --workspace-root /different/absolute/path/outside/the/repository \
  --key-manifest evals/sync-traycer-notion-trigger/key-manifest.json \
  --freeze-commit 0123456789abcdef0123456789abcdef01234567 \
  --max-budget-usd 1.00 \
  --timeout-seconds 420 \
  --jobs 4

uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/run-trigger-evals.py grade \
  --run-manifest /absolute/path/outside/the/repository/run-manifest.json \
  --key /absolute/path/outside/the/repository/key.json \
  --key-manifest /absolute/path/outside/the/repository/key-manifest.json \
  --raw-evidence-sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --raw-evidence-size 123456 \
  --private-release-tag sync-traycer-notion-trigger-v1-run-UUID \
  --private-asset-name raw-evidence.zip \
  --output /absolute/path/outside/the/repository/proof-report.json
```

The workspace and evidence roots must be disjoint. Run records store only
manifest-relative evidence paths, so the downloaded archive can be re-graded
after the original run root is removed. The suite enforces integer-basis-point
trigger thresholds, a case-cluster paired bootstrap over all 12 positive cases,
and the exact critical-regression rule recorded in the answer-free manifest.

The evaluation does not establish a production or four-harness portability
claim. The suite remains `draft`, human calibration is pending, and its overall
production-evidence status is `not-proven`.

## Other harness preflights

Before a Codex model evaluation, copy the candidate bundle into an isolated
workspace at `.agents/skills/sync-traycer-notion/` and run the no-model
single-candidate preflight:

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion/codex-single-candidate-preflight.py \
  --workspace /absolute/path/to/isolated/workspace \
  --candidate /absolute/path/to/isolated/workspace/.agents/skills/sync-traycer-notion/SKILL.md
```

The emitted `skills.config` override is evidence-bound to the discovered
competing paths and must be carried unchanged into the later Codex evaluation.
The preflight calls only `codex debug prompt-input`; it never invokes a model.
Codex's discovery roots and same-name behavior are documented in the
[OpenAI Skills documentation](https://learn.chatgpt.com/docs/build-skills).

Cursor has no equivalent no-model inventory command. Its clean-room contract is
documented in
[`CURSOR_ENVIRONMENT.md`](../sync-traycer-notion/CURSOR_ENVIRONMENT.md). Paid
Codex and Cursor canaries remain blocked until their respective preflight
contracts pass and the coordinator explicitly authorizes the calls.
