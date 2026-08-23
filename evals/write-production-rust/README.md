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
Host traces carry no user-prompt event, so prompt and injection digests are
runner-asserted rather than host-observed.

Raw responses, traces, and the plaintext semantic key stay outside Git. The
committed report contains hashes, scores, and proof status only. The current
runner proves one explicit Claude Code profile; it does not claim cross-harness
portability.

The ordered guidance inventory in the case pack is the complete treatment
bundle: `SKILL.md` plus every Markdown or text file below `references/`. A run
records every path and digest in order. Grading rediscovers the committed
inventory, rejects missing, extra, reordered, symlinked, or byte-modified
guidance, and revalidates the exact per-case injection digest. The report's
`proof_id` also binds the run manifest, semantic key and manifest, complete
guidance bundle, and every grader trace digest.

## Evidence status

`suite.json`, `key-manifest.json`, `calibration-manifest.json`, and
`evidence-manifest.json` remain unexecuted draft coordinator inputs. Their
`PENDING-COORDINATOR-SEAL` placeholders are not evidence. Passing evidence
consists only of the standalone three-case contract: `semantic-key-manifest.json`
and `proof-report.json`, plus the external run material identified by the
report's content hashes.

Three qualification attempts are retained outside Git as immutable evidence.
The first two failed the pre-registered two-win gate. Run 3 reused the
byte-identical recalibrated instrument after a diagnostic-led guidance revision
and passed all three cases. Its reported +14 margin is not an effect-size
estimate: on the identical instrument, treatment rose from 33 to 39 (+6), while
a weaker baseline draw fell from 28 to 25 (-3). Each arm is a single trial.

Run from the repository root:

```sh
python skills/write-production-rust/scripts/run_evals.py run

python skills/write-production-rust/scripts/run_evals.py grade \
  --run-manifest /absolute/path/outside/the/repository/run-manifest.json \
  --key /absolute/path/outside/the/repository/semantic-key.json \
  > evals/write-production-rust/proof-report.json
```
