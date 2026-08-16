---
name: orchestrate-risk-scaled-review
description: Lead a risk-scaled, multi-agent review of code changes or implementation artifacts. Use when Claude is the review lead and must choose an economical adversarial reviewer panel, arbitrate evidence with the artifact author, run iterative review-and-fix rounds, and prepare the result for human review.
context:
  reads:
    - review_scope
    - changed_files
    - reviewer_assignments
  requires:
    - explicit_review_lead_role
  writes:
    - risk_assessment
    - reviewer_assignments
    - approved_write_scope
    - relay_conversation_metadata
    - review_artifacts
    - finding_ledger
    - commands_run
    - verification_result
  confirmation: on-risk
---

# Orchestrate Risk-Scaled Review

Act as the review lead. Own reviewer selection, evidence standards, round control, and final approval. Do not replace the artifact author or the Codex implementer.

The frontmatter's `confirmation: on-risk` governs confirmation for risky shell or tool execution, including paid agent spawns. It does not authorize artifact write-back; the separate approval barrier in §5 is stricter.

## 1. Assess risk before selecting reviewers

Inspect the complete change, its surrounding code, relevant Traycer artifacts, and available tests. Estimate:

- impact: user harm, data loss, security, money, availability, or irreversible side effects;
- blast radius: callers, shared contracts, persisted data, deployments, and external systems;
- uncertainty: unfamiliar code, ambiguous requirements, weak tests, concurrency, or hidden state;
- size: changed behavior, files, interfaces, and paths—not line count alone.

Use this starting point, then adjust for the actual failure modes:

| Risk | Typical change | Adversarial reviewers |
| --- | --- | ---: |
| Trivial | Mechanical, local, proven by focused checks | 0 |
| Low | Small behavioral change with narrow impact | 0–1 |
| Medium | Multiple paths or a meaningful contract change | 1 |
| High | Broad, stateful, security-sensitive, or weakly tested | 2 |
| Critical | Irreversible, externally consequential, or unusually uncertain | 3+ with distinct lenses |

There is no reviewer cap. Add a reviewer only when another independent pass or lens has credible marginal value. Reduce the panel when evidence makes the remaining risk cheap to check directly. Record the risk assessment, selected count, and rationale.

## 2. Assign adversarial reviewers randomly

Adversarial reviewers come from two arms:

- Cursor: a direct Traycer child using harness `cursor`, model `grok-4.5`, and reasoning `high`;
- Antigravity: model `gemini-3.7-flash-high` reached through the transport-only Codex relay below.

Let N be the adversarial reviewer count chosen in §1. Build the panel so both arms appear as evenly as N allows:

1. define every lens first, before any arm is assigned;
2. take a balanced base of `floor(N/2)` Cursor and `floor(N/2)` Antigravity reviewers;
3. when N is odd, allocate the single remaining slot by one random draw between the arms;
4. permute the resulting arm multiset across the lens slots.

The two arm counts therefore differ by at most one. N = 0 needs no base, no draw, and no permutation. N = 1 is one randomized slot; N = 2 is one of each; N = 3 is one of each plus a random third; N = 4 is two of each.

Obtain the odd-slot draw and the permutation from a randomness source outside the model's own token generation, and make the permutation uniformly random. Fixing lenses before arms and permuting afterward stops the lead pairing a chosen lens with a chosen model. Record the odd-slot draw when present, the permutation, and the final lens-to-arm assignment in the review ledger.

Rebuild the panel for every round: recompute the base, redraw the odd slot when N is odd, and generate a fresh permutation. Do not carry a previous round's assignment forward, and do not select based on preference or an expected conclusion.

The relay transports an Antigravity review and does not count as a reviewer. If a selected arm is unavailable under the failure policy in §3—or a direct Cursor child cannot start or authenticate—use the other arm and record the fallback; do not silently substitute a third model. A fallback may leave the realized panel imbalanced, and an arm unavailable for a whole round collapses it to a single model: record that the round lost model diversity and keep N unchanged, since distinct lenses retain value on one model.

Run independent reviewers concurrently when practical. Platform concurrency limits may require waves but must not change the chosen panel.

## 3. Relay Antigravity through Codex

Create a relay child using harness `codex`, model `gpt-5.6-terra`, and reasoning `low`. This is an explicit exception to the normal Codex implementation rule: do not invoke `$traycer-implement`. The relay must not review, summarize, arbitrate, approve, or interpret content.

The relay may only:

- exchange messages with the Claude lead;
- invoke `scripts/agy_review_relay.py`;
- read exactly the durable per-slot prompt file whose absolute path Claude names in the handoff;
- create exactly one new review artifact at the path named by Claude. After the required artifact frontmatter, its body must be only the UTF-8 `result.response` value from agy's `result` event, including its verified sentinel line and excluding all NDJSON wrappers and the `relay_metadata` line.

Permit no other project or workspace read or write beyond the explicit review scope. Antigravity may maintain only its CLI-managed conversation, cache, or log state outside the workspace. Neither the relay nor Antigravity may modify an existing artifact or workspace file.

Claude creates one dedicated directory per reviewer slot and round at an absolute path outside the read-only set. The directory must contain exactly one entry: that slot's durable, regular prompt file. Do not put an `index.md`, `.DS_Store`, another slot's prompt, or any other sibling in it. This is a supporting review file, not a Traycer `index.md`: its first line must be a sentinel matching `TRAYCER_PROMPT_SENTINEL_[A-Za-z0-9_-]{32}`, generated with a randomness source outside the model's token generation. The remaining content must be a compact, task-specific brief made mostly of handles: explicit paths, an intended-behavior artifact, the review lens, risk probes, validation commands, and an absolute handle to `references/antigravity-review-contract.md`.

Before dispatch, verify the shared contract is a readable regular file, then remove write bits from the completed prompt file as operational hygiene. Claude owns the prompt path and dedicated directory exclusively until agy exits: do not rewrite, replace, rename, delete, or add a sibling during the call. Non-writable mode reduces accidental edits but is not a security boundary. Record both the prompt file and dedicated directory in the review ledger.

Invoke the helper with `--prompt-file <absolute-path>`; do not send prompt content through stdin or argv. The helper requires the dedicated directory to contain exactly the named prompt, rejects symlinks and non-regular entries with `lstat`, and rejects a resolved prompt that escapes the directory. A directory diagnostic names every offending entry, including dotfiles. The helper checks the file's size with `os.stat`, verifies readability, and reads and validates only the first line. It never reads the prompt body. The 16 KiB ceiling enforces the handles-only design rather than merely preventing resource exhaustion: code, diffs, logs, and other substantial content belong behind a path. A size error must report both the actual size and the 16 KiB ceiling.

The helper records the prompt's device, inode, size, modification time, and change time at validation. It compares that identity immediately before the paid call and fails with exit `64` if it changed. It compares again after agy exits and forces exit `65` if it changed during the call, because the result is then untrusted. The helper still never reads the body. It grants only the dedicated directory with `--add-dir` and passes only the path plus a fixed read instruction through `--prompt`.

Use `--print-timeout 30m` for a full review and `10m` for reconciliation or evidence follow-ups. Claude may choose another explicit timeout only when its handoff records why; never rely on agy's five-minute default.

The helper always invokes agy with:

- `--model gemini-3.7-flash-high`;
- `--mode plan` and `--sandbox`;
- `--output-format stream-json`;
- an explicit `--print-timeout`;
- `--add-dir <prompt-file-directory>`;
- `--prompt <fixed-handle-instruction>`;
- `--conversation <id>` only when resuming the current slot.

Do not pass `--dangerously-skip-permissions`, `--continue`, `--effort`, or `--disable-slash-commands`. The reasoning tier is already part of `gemini-3.7-flash-high`; do not layer `--effort` onto it. Antigravity 1.1.13 warns that `--disable-slash-commands` makes `--mode plan` ineffective. The full reviewer prompt is read from the file after command expansion, so restoring plan mode does not expose its `$` or `/` content to slash-command parsing.

Require Antigravity to copy the sentinel from the prompt file's first line exactly as the first line of its response. If it cannot read the file, it must stop and report the fixed unreadable-file message verbatim. A `result.response` whose first line is not exactly the expected sentinel is relay-protocol exit `65`: retry once on the same arm with a fresh conversation, then treat the arm as unavailable and fall back after a second recorded failure. Preserve the verified sentinel line in the review artifact body because `result.response` is copied verbatim.

The handle design keeps the task-specific review body and code under review out of argv and relay-created temporary files. The durable prompt persists as review evidence and names the scoped paths, lens, and commands. Its permissions and retention follow the artifact location chosen by Claude; other processes with access to that location may read it. Do not put secrets or inlined source content in the prompt file.

The helper always reproduces agy's original stdout byte-for-byte and in order. When stdout is empty or newline-terminated, it appends exactly one `relay_metadata` NDJSON line and preserves agy's stderr byte-for-byte. The metadata mechanically exposes the explicit conversation ID, result status, usage, and agy exit code; parsing those fields and `result.response` for verbatim passthrough is not interpretation.

If stdout is not newline-terminated, the helper leaves it untouched, appends a relay diagnostic naming agy's exit code to agy's original stderr, and returns relay-protocol exit `65` without appending metadata. If conversation metadata is missing, conflicting, or malformed, treat exit `65` as authoritative even when agy also exits nonzero; when safe, the helper emits best-effort metadata carrying agy's exit code. Never recover with `--continue` or an implicit new conversation.

Classify relay outcomes from both the exit code and its diagnostic or metadata:

| Signal | Class | Required action |
| --- | --- | --- |
| argparse exit `2` | orchestration defect | Stop and fix the invocation; do not fall back. |
| exit `64` | orchestration defect | Fix the dedicated directory, prompt path, file type, readability, size, malformed first-line sentinel, pre-invocation identity change, or blank conversation ID; do not fall back. |
| exit `65` | failed attempt | Fix missing or conflicting stream metadata, a malformed stream, a prompt identity change during execution, or a `result.response` whose first line is not exactly the expected sentinel. Retry once on the same arm with a fresh conversation. After a second failure, treat the arm as unavailable, fall back, and record both failures. |
| exit `69` | arm unavailable | The agy executable is absent, invalid, non-executable, or could not be spawned; fall back and record it. |
| any other nonzero exit with a valid stream | reviewer failure | Record the failure; do not treat it as an availability signal. |

Treat an unmatched failure signal as an orchestration defect until it is classified; never infer arm unavailability from an unknown nonzero exit.

Scope one Antigravity conversation to one reviewer slot in one round. Reuse its explicit ID for that slot's review and reconciliation exchanges within the round. Start a fresh conversation for every new slot and every new round. Record each prompt directory, prompt path, ID, status, raw response, and usage in the review ledger.

## 4. Generate a focused prompt for each reviewer

Give every reviewer enough primary context to inspect the work without leaking another reviewer's conclusions. Include:

- handles to the exact artifact, diff, commit, or file scope; never inline a diff, source file, or log in an Antigravity prompt file;
- handles to the intended behavior and relevant planning artifacts;
- the risk hypotheses and a distinct lens when specialization adds value;
- commands or evidence sources that can validate claims;
- the read-only set as explicit paths, covering every Traycer artifact and file in `review_scope` or `changed_files`.

Frame risk hypotheses as probe directions, not conclusions: any or all may prove false, and an empty findings list is an acceptable result.

Tell Cursor reviewers to use `$traycer-review` and create their own review artifact. Antigravity does not receive Traycer-managed skills, so point every Antigravity prompt file to the absolute path of `references/antigravity-review-contract.md`; keep only task-specific scope, intent, lens, probes, and commands in the per-slot file.

Ensure every reviewer receives this posture; for Antigravity, the shared contract reference supplies it:

> Be adversarial, not contrarian. Try to falsify the change with realistic inputs, states, timing, and integration behavior. Report only issues that survive inspection and would change an engineering decision. Do not invent objections or reward disagreement.

Require each finding to contain severity, the falsifiable claim, exact evidence, a realistic failure scenario, impact, and the smallest required change. Unsupported suspicion may be reported as a question, never as a required change. The shared Antigravity contract supplies this output schema by handle.

Tell Antigravity that command execution may be unavailable under plan/sandbox mode. It must classify a claim that depends on an unrun command as a question. Claude must run the named read-only validation command and send its exact output through the same round-and-slot conversation before deciding whether to promote the claim to a finding.

## 5. Enforce Claude's write barrier

Treat every explicit path in the handoff's read-only set as immutable throughout review and discussion. This includes each Traycer artifact under review and every scoped code or configuration file. The artifact author, reviewers, and implementers may create separate review artifacts, ledgers, change descriptions, or proposed patches delivered as text outside that set. A proposed patch is content only, never an edit applied in place before the Claude lead approves it.

Require Claude's explicit written approval to identify the accepted finding IDs and the exact write-back scope. Do not infer approval from consensus, silence, a finding's status, or an earlier round. Nothing may be written merely because the author and reviewers agree.

After approval, allow only the Codex implementer to modify the original artifact, and only within the approved set. If implementation reveals a necessary deviation or additional change, stop the write-back and return it to Claude for approval before proceeding.

## 6. Reconcile findings with all participants

Collect the independent review artifacts. Have the artifact author discuss every actionable finding with the review lead and all reviewers. Mediate the discussion when a shared thread is unavailable; do not treat silence as agreement.

Maintain a finding ledger with one of these states:

- `accepted`: factually supported and worth changing;
- `rejected`: contradicted, irrelevant, or not worth its cost;
- `needs evidence`: plausible but not yet demonstrated;
- `HITL decision`: a product or risk choice that agents must not assume.

The finding's proposer bears the burden of evidence. Let participants challenge claims and counter-evidence. Resolve disputes against the code, tests, runtime behavior, and settled artifacts. Require agreement on the disposition where facts allow it; escalate genuine value judgments to HITL.

Before approving fixes, independently verify every accepted finding. Reject or downgrade claims that lack factual support, even when several reviewers repeat them.

## 7. Apply fixes and run a full new round

After reconciling a round, issue explicit written approval for the exact accepted fix set. Only then hand it to a Codex agent using `$traycer-implement`. Require Codex to confirm that its write-back stays within the approval, perform focused verification, and produce an implementation report.

Review the resulting artifact or codebase in full, not only the patched lines. Reassess risk, choose a new panel, make fresh random reviewer assignments, and generate new prompts. A round may uncover regressions, incomplete fixes, or previously missed issues.

Stop early when a full round produces no evidence-backed required changes. Run at most three complete review/fix rounds.

After round three, do not start another agent review. Ground and consolidate all remaining findings, approve the final required changes, and have Codex apply and verify them. Send unresolved factual uncertainty or value judgments to HITL instead of pretending consensus.

## 8. Finish for HITL

Produce one concise review ledger or artifact containing:

- scope and final risk assessment;
- reviewer selections, relay metadata, and any fallbacks per round, plus the odd-slot draw, permutation, and final lens-to-arm assignment per round;
- accepted, rejected, and HITL findings with evidence;
- Claude's explicit approval and its exact write-back scope for each applied set;
- changes applied and verification performed;
- remaining risks or decisions for the human reviewer.

Finish only when the final Codex pass is verified and the HITL handoff can be reviewed without reconstructing the agent discussions.
