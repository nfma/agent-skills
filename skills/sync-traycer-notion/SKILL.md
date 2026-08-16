---
name: sync-traycer-notion
description: "Synchronize Traycer Tasks and only `kind: story` or `kind: ticket` artifacts with Nuno's Notion Task List. Never load for `kind: spec`, `kind: review`, ordinary Notion todo management, or non-Traycer work, even when a request mentions both Traycer and Notion. Use on the first agent turn in a Traycer Task, when a story or ticket is created, renamed, moved, reparented, or changes status, when their work is managed from the Notion board, or when the user asks to reconcile in-scope Traycer work with Notion."
---

# Sync Traycer to Notion

Mirror Traycer's three-level work hierarchy into the Notion Task List. Treat a
Traycer Task as the epic. Keep Traycer authoritative for structure and artifact
content; use Notion as the status board and durable linked index.

Before synchronizing, read [the Notion Task List adapter](references/notion-task-list.md).
It contains the current target, schema, tool routing, and maintenance record.
Re-fetch the live schema each turn instead of trusting the reference blindly.

## Scope and authority

- Synchronize the current Traycer Task and only artifacts with `kind: story` or
  `kind: ticket`. Ignore `spec`, `review`, and unrelated Notion tasks.
- Perform the epic sync at the first safe opportunity in the first agent turn;
  Traycer does not expose a pre-agent task-created hook.
- Treat Traycer artifact nesting as authoritative for hierarchy and Notion as
  authoritative for board-driven status, subject to the precedence rules below.
- Never change the Notion data-source schema, views, permissions, or OAuth
  connection unless the user explicitly asks.

## Mapping

| Traycer item | Native Notion parent | Traycer key |
| --- | --- | --- |
| Epic | none | `traycer:epic:<epic-uuid>` |
| Story artifact | epic page through `Parent task` | `traycer:epic:<epic-uuid>:story:<artifact-relative-directory>` |
| Ticket artifact | story page through `Parent task` | `traycer:epic:<epic-uuid>:ticket:<artifact-relative-directory>` |

Map artifact status `0`, `1`, and `2` to `To do`, `Doing`, and `Done`.
Normalize Notion's generated ID to a positive integer even when a query returns
it as text and display it as `TASK-<number>`. Make Notion's native `Parent task`
relation authoritative for hierarchy so `Sub-task` is populated automatically.
While the legacy `parent task id` property remains in the data source, mirror
the parent's numeric ID there for compatibility; never use it instead of the
native relation.

The artifact-relative directory is stable across title edits, but not moves.
Before moving or reparenting an artifact, resolve its existing Notion row by the
old key. After the filesystem move, update that same row's key and parent. If an
out-of-band move has already lost the old identity and no single candidate can
be proven, stop instead of creating a replacement.

## Synchronization workflow

1. Resolve the current epic UUID, title, and artifact root from Traycer's runtime
   context. Do not infer an epic UUID from a branch, folder, or title. Ask only
   when the title is genuinely ambiguous; otherwise use the exposed title.
2. Verify the connected Notion workspace and tool access, then fetch the data
   source schema before the first write of a turn. Stop writes on workspace or
   schema mismatch. Use the adapter's view-mode fallback when SQL is unavailable
   or its plan allowance is exhausted.
3. Query by `Traycer key` before creating anything. In SQL mode, use the
   parameterized identity query from the adapter; never interpolate a key.
4. If no key matches, look for exactly one unkeyed row with the same title and
   expected parent. Adopt it by setting `Traycer key`; never take over a row
   owned by another key. If multiple candidates exist, stop and report the
   conflicting task references.
5. Ensure the parent chain before creating a child. Create pages under the Task
   List data source with `Task`, `Status`, `Traycer key`, and, for children,
   the native `Parent task` relation targeting the parent page. Also mirror the
   parent's numeric ID into `parent task id` while that compatibility property
   exists.
6. Re-query after creation to obtain the generated ID and page URL and to detect
   a concurrent duplicate. If more than one row now has the key, make no further
   hierarchy writes and report the conflict. In user-facing messages, link the
   returned page URL with label `TASK-<number>`.
7. Update an existing row when its Traycer title, expected parent, or status
   changes. Update only the required properties; never replace page content
   merely to synchronize properties.
8. After any hierarchy write, fetch the child and parent pages. Require the
   child's `Parent task` to contain exactly the parent page and the parent's
   reciprocal `Sub-task` relation to contain the child.

## Hierarchy invariants

- An epic has no parent.
- A story must have one parent, and that parent must be an epic.
- A ticket must have one parent, that parent must be a story, and the story's
  parent must be an epic.
- Require every generated and mirrored parent ID to be a finite positive
  integer. Require every native parent relation to resolve to exactly one page.
  Reject missing, zero, negative, or fractional compatibility values.
- Reject orphan stories, orphan tickets, self-parenting, cycles, and a fourth
  hierarchy level. Do not create a child until its valid parent is synchronized.
- Require every parent row's key to belong to the same epic and expected level.
  If Notion's parent differs, repair it only after validating both rows.
- Leave manual Notion rows with an empty `Traycer key` untouched unless the
  exact-title-and-parent adoption rule selects one unambiguously.

## Board-driven status

Apply status changes in this order:

1. An explicit status instruction in the current user request wins; update the
   artifact when applicable and push the mapped status to Notion.
2. Otherwise, at the start of work on an existing story or ticket, pull a
   differing Notion status into artifact frontmatter.
3. After a later Traycer status change in the same turn, push the mapped value
   back to Notion.

Create a new epic as `To do`. Move it to `Doing` when active work begins, unless
the board already says `Done`; reopen a done epic only when the user explicitly
resumes it. Move an epic to `Done` only when the user or Traycer workflow
explicitly completes it.

Do not infer structural changes from board movement, native relation edits, or
legacy `parent task id` edits.
Do not mark an epic or story done solely because one child completed.

## Failure handling

- Make synchronization idempotent and use parameterized Notion SQL queries.
- Do not delete or trash Notion items automatically.
- If an artifact disappears, leave its Notion row intact and report it for
  manual disposition.
- If Notion is unavailable, preserve the Traycer artifact change, report that
  synchronization is pending, and retry on the next relevant turn.
- If authentication, write access, schema, duplicates, identity, or hierarchy is
  invalid, make no additional writes in the affected epic until resolved.
