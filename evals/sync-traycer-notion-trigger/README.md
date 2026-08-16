# Trigger and behavior proof

This suite compares fresh Claude Code sessions with no project skill installed
against fresh sessions where `sync-traycer-notion` is installed as a project
skill. Evaluation prompts never name or invoke the skill.

The runner permits only Claude's `Skill` and `Read` tools, disables MCP servers,
instructs the model to plan without executing, and records the session
initialization plus every tool event. This proves discovery and automatic
triggering on the tested Claude Code profile. It is not a claim of zero-tool
portability across other harnesses.

The answer-bearing grading key and raw traces stay outside the repository. The
committed key manifest seals their digests and check counts before either arm is
run. The proof report contains only hashes, check identifiers, and aggregate
scores.

Run from the repository root:

```sh
uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/run-trigger-evals.py run \
  --output-dir /absolute/path/outside/the/repository

uv run --frozen --no-build python \
  evals/sync-traycer-notion-trigger/run-trigger-evals.py grade \
  --run-manifest /absolute/path/outside/the/repository/run-manifest.json \
  --key /absolute/path/outside/the/repository/key.json \
  --output evals/sync-traycer-notion-trigger/proof-report.json
```

## Recorded result

The committed proof report records a 2026-08-16 run with Claude Code 2.1.233,
Claude Opus 5, and xhigh effort:

- 16 fresh sessions completed with zero trace-contract failures.
- All 4 positive treatment prompts automatically invoked the skill.
- All 4 treatment near misses discovered but did not invoke the skill.
- The no-skill baseline passed 16/24 behavioral checks (66.7%).
- The with-skill arm passed 23/24 checks (95.8%), an improvement of 7 checks
  and 29.2 percentage points.

The one missed treatment check was the frozen `identity-before-create` pattern.
The answer queried the story identity at step 10 and created it at step 13, but
the check's bounded regular expression did not span that JSON structure. The
frozen key was not changed after seeing this answer.
