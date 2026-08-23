# Agent Skill production evaluations

This directory contains runner-visible task packs and non-secret manifests for every skill in the repository. It deliberately contains no plaintext expected answers, reference solutions, or semantic rubrics.

## Contract

Each skill has:

- ten realistic positive tasks and ten difficult near-misses;
- at least two regression tasks in each class;
- three fresh trials per harness;
- a no-skill baseline for outcome comparisons;
- deterministic native-load and prohibited-effect graders;
- a blinded semantic outcome grader backed by an external sealed key;
- complete traces, immutable fixtures, and pre/post state hashes;
- explicit trigger, abstention, paired-quality, and zero-critical-regression thresholds; and
- a human calibration manifest that expires after 183 days or a material change.

Automated trials must not receive credentials or live mutation tools. Gmail, Calendar, Drive, Notion, publishing, upload, deployment, Git remote, and similar tasks run against recorded fixtures or stateful mocks. The model workspace contains only the skill under test and that task's fixtures. Coordinator evidence, other trials, eval manifests, and grader keys remain outside it.

## Status

`draft` means the task bank is structurally valid but its external reference key and human calibration are not complete. `provisional` requires a sealed key but not a current passing calibration. `production` requires current calibration and passing evidence on every required available harness. `stale` and `unavailable` are fail-closed states, never passes.

Run the structural gate:

```sh
npm run test:skill-evals
python3 scripts/skill-evals.py status --json
```

Regenerate the committed runner packs from the reviewed catalog:

```sh
python3 scripts/skill-evals.py sync
```

Create a randomized external plan for one skill on the canonical harness:

```sh
python3 scripts/skill-evals.py plan \
  --skill coding-preferences \
  --harness claude-code \
  --output /private/eval-evidence/coding-preferences/plan.json
```

Generate the deliberately unsealed human-review packet for a suite:

```sh
python3 scripts/skill-evals.py init-key \
  --skill coding-preferences \
  --output /private/coordinator-keys/coding-preferences/key.json
```

The packet infers a first-pass rubric from each task's explicit outcome, constraints, regression class, and verification language. Reviewers must still write the reference outcome and confirm failure impact. Rubric weights use a fixed meaning: `4` is a critical core outcome, `3` is a critical hard constraint or major requirement, `2` is material, and `1` is supporting. The validator rejects missing must-pass criteria, critical criteria with low weights, duplicated criterion text, and suite-wide uniform weight/critical signatures unless the key contains a specific `rubric_uniformity_justification`.

The evidence pipeline validates frozen runs, external keys, and blind grades before aggregation:

```sh
python3 scripts/skill-evals.py validate-run \
  --plan /private/eval-evidence/coding-preferences/plan.json \
  --run-manifest /private/eval-evidence/coding-preferences/run-manifest.json

python3 scripts/skill-evals.py validate-grades \
  --skill coding-preferences \
  --key /private/coordinator-keys/coding-preferences/key.json \
  --plan /private/eval-evidence/coding-preferences/plan.json \
  --run-manifest /private/eval-evidence/coding-preferences/run-manifest.json \
  --grade-report /private/eval-evidence/coding-preferences/grade-report.json

python3 scripts/skill-evals.py aggregate \
  --skill coding-preferences \
  --key /private/coordinator-keys/coding-preferences/key.json \
  --plan /private/eval-evidence/coding-preferences/plan.json \
  --run-manifest /private/eval-evidence/coding-preferences/run-manifest.json \
  --grade-report /private/eval-evidence/coding-preferences/grade-report.json \
  --output /private/eval-evidence/coding-preferences/aggregate-report.json
```

The current harness profile is fail-closed. Claude Code is qualified for native load observation on the frozen profile; Cursor, Antigravity, and Codex remain explicitly unavailable based on the latest captured traces. A run may record those lanes as unavailable, but the aggregate becomes `not-proven` until all four required lanes pass.

## Research basis

The design follows Anthropic's agent-evaluation guidance: realistic tasks, multiple independent trials, reference solutions, mixed deterministic and model graders, isolated environments, transcript review, capability/regression separation, and long-term ownership.

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://github.com/anthropics/skills/tree/main/skills/skill-creator
