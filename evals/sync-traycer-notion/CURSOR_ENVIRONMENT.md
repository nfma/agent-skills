# Clean Cursor evaluation environment

Cursor has no documented no-model equivalent of `codex debug prompt-input` and
searches project, Cursor, Claude, and Codex-compatible skill roots. A trustworthy
native-loading evaluation therefore needs a disposable macOS account or VM whose
entire discovery surface is controlled. A temporary directory inside Nuno's live
login is not sufficient. The discovery roots and automatic-loading behavior are
documented in [Cursor's Agent Skills documentation](https://cursor.com/docs/skills).

## Isolation contract

Provision a fresh macOS user or revert a disposable VM snapshot for each
evaluation batch. The lane must meet every condition below before any paid call:

1. Record the OS image and `cursor-agent --version`. Do not copy Nuno's home
   directory, Keychain, Cursor credential store, cookies, editor state, MCP
   configuration, or harness settings into the lane.
2. Authenticate interactively in that account, or inject an approved ephemeral
   `CURSOR_API_KEY` from the coordinator's secret broker. Never write the key to
   the repository, shell history, evidence, or process arguments.
3. Set a task-specific `EVAL_USER_HOME` variable to the disposable account's
   real home path. Do not override `HOME`. Before installing the candidate,
   require all of these native roots to be absent or empty:
   `$EVAL_USER_HOME/.agents/skills`, `$EVAL_USER_HOME/.cursor/skills`,
   `$EVAL_USER_HOME/.claude/skills`, and `$EVAL_USER_HOME/.codex/skills`.
4. Create a fresh project root with no parent repository and no user or project
   MCP configuration. Copy, rather than symlink, the candidate bundle to exactly
   `.agents/skills/sync-traycer-notion/`.
5. Search the project, every parent directory, and all four native user roots
   for `SKILL.md`. Canonicalize every discovered entry as its name,
   description, resolved path, and bundle SHA-256. Require exactly one
   declaration named `sync-traycer-notion`, located at the project candidate
   path, and seal the full filesystem inventory with the OS image, Cursor CLI
   version, candidate hash, and task-surface hash.
6. Obtain the full model-visible inventory from a non-model Cursor surface and
   canonicalize every entry, including unavoidable built-in skills. Seal that
   inventory separately from any prompt or trace. If the installed Cursor
   version cannot expose the complete inventory without a model call, mark the
   Cursor lane unavailable; filesystem isolation alone does not authorize a
   paid canary.
7. Immediately before any later model execution, re-collect both inventories
   and require exact equality with the sealed records. A changed built-in,
   plugin, description, path, bundle, CLI version, or discovery root invalidates
   the lane.
8. Keep raw stream-JSON traces, inventory records, hashes, and the
   answer-bearing grading key in a
   coordinator-owned directory outside the project and outside Git.

The inventory gate proves a stable discovery surface, not native loading.
Cursor's model must still provide the exact load/non-load evidence after the
coordinator separately authorizes paid canaries.

## Future canary gate

Do not run this gate until the coordinator explicitly authorizes paid canaries.
Then run only one positive and one frozen near-miss in fresh Ask-mode sessions,
using sandboxing and the isolated project as the workspace. The frozen execution
profile is:

- `cursor-agent --mode ask --sandbox enabled --print`
- stream-JSON output written only to the external evidence directory
- no MCP servers, repository credentials, mutation tools, or live Notion access
- the exact evaluated model and CLI version recorded with the trace

Accept the positive only when the trace contains an exact read of the physical
project candidate before the answer. Accept the near-miss only when the candidate
is discoverable but is not read. Model self-report, a glob of `**/SKILL.md`, or a
read of a same-name compatibility copy is not loading proof. Both sessions must
reach a terminal result, preserve the before/after task-surface hash, and show no
forbidden effect succeeded.

Destroy the disposable account or revert the VM after the coordinator has
verified and archived the evidence. Never reuse the lane once another skill or
MCP server has been installed.
