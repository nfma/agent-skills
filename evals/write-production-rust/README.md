# Write Production Rust proof

This suite proves two different properties and does not substitute one for the
other:

1. project-skill discovery, automatic loading on positive `src/` prompts, and
   non-loading on adjacent out-of-scope prompts; and
2. response-quality lift over a frozen no-skill baseline in fresh, zero-tool
   sessions.

Behavior runs receive only prompt text and emit final text. The coordinator
injects frozen skill guidance only into the treatment arm. Both responses are
frozen before the external semantic key is loaded. A fresh different-model
grader sees anonymized response pairs and response-only criteria.

Raw responses, traces, and the plaintext semantic key stay outside Git. The
committed report contains hashes, scores, and proof status only. The current
runner proves one explicit Claude Code profile; it does not claim cross-harness
portability.

Run from the repository root:

```sh
python skills/write-production-rust/scripts/run_evals.py run

python skills/write-production-rust/scripts/run_evals.py grade \
  --run-manifest /absolute/path/outside/the/repository/run-manifest.json \
  --key /absolute/path/outside/the/repository/semantic-key.json \
  > evals/write-production-rust/proof-report.json
```
