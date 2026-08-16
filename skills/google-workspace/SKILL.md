---
name: google-workspace
description: Work with Nuno's Google Workspace services. Use when a task involves Gmail, Google Calendar, Google Drive, or another Google Workspace API, including searching, reading, creating, updating, or organizing Workspace data.
metadata:
  skill-audit-context-reads: user_goal, workspace_identity, target_resource, auth_status
  skill-audit-context-requires: explicit_workspace_goal, target_scope
  skill-audit-context-writes: query_results, remote_resources_changed, verification_result
  skill-audit-confirmation: on-risk
---

# Google Workspace

- Prefer a dedicated integration when one is available.
- Otherwise use `gws <service> <resource> <method>`.
- Discover an unfamiliar method before using it with `gws schema <service.resource.method>`.
