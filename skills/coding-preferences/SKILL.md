---
name: coding-preferences
description: Apply Nuno's personal coding conventions. Use when creating, modifying, refactoring, debugging, testing, or reviewing source code or project configuration.
metadata:
  skill-audit-context-reads: user_request, target_files, repository_conventions
  skill-audit-context-requires: explicit_change_or_review_goal, target_scope
  skill-audit-context-writes: conventions_applied, files_or_findings_changed
  skill-audit-confirmation: never
---

# Coding Preferences

- Match the existing style of the file and project. Do not reformat unrelated code.
- Prefer small, focused changes over broad refactors unless asked.
- Do not add comments that merely restate the code.
