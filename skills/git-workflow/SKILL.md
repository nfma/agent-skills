---
name: git-workflow
description: Apply Nuno's Git conventions and repository safeguards. Use for Git operations such as staging, committing, branching, merging, rebasing, resetting, pushing, or preparing pull requests.
metadata:
  skill-audit-context-reads: user_request, repository_state, current_branch, remote_state
  skill-audit-context-requires: explicit_git_goal, target_repository
  skill-audit-context-writes: files_changed, refs_changed, commands_run, verification_result
  skill-audit-confirmation: on-risk
compatibility: Requires Git and a target Git repository; remote operations additionally require network access and repository authentication.
---

# Git Workflow

- Never push to `main` or `master`.
- Never force-push directly; the only permitted lease-protected exception is
  defined below.
- Never amend a commit unless explicitly asked.
- Write commit messages in imperative mood with a one-line summary first.

## Stacked pull requests

Permit the official `github/gh-stack` extension to update a tracked stack with
its built-in per-branch `--force-with-lease` only when every guard below holds:

- Verify `gh extension list` identifies `gh stack` as `github/gh-stack`.
- Require a clean working tree before updating the stack.
- Run `gh stack view` and require every branch being updated to be listed in
  the current stack, use the expected same-repository remote, and exclude the
  repository's default branch.
- Allow only `gh stack push` or `gh stack submit` to issue the extension's
  built-in lease-protected update.
- Never run `git push --force`, `git push -f`, or direct
  `git push --force-with-lease`, including for a stack-managed branch.
- If a lease check rejects an update, stop and inspect the remote state. Never
  retry with a broader force option or bypass the lease.
