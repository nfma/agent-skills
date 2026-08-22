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
- Never force-push.
- Never amend a commit unless explicitly asked.
- Write commit messages in imperative mood with a one-line summary first.
