# Traycer quota supervisor

The Traycer quota supervisor is an optional, current-user macOS LaunchAgent.
It watches active Traycer agents, groups them by provider profile, and wakes
waiting agents when capacity becomes available. It is not installed by the MCP
installer.

## Requirements

- macOS running the service as a non-root login user.
- Homebrew Python at `/opt/homebrew/bin/python3`, version 3.11 or newer.
- A dedicated normal clone under `~/Projects` that remains at its current
  absolute path while the service is installed.

The launcher validates the Python executable and tracked runtime before every
start. Both must be owned by the current user or root and must not be writable
by the group or other users.

## Recovery model

The supervisor scans user processes only to refresh a pool of local Traycer
A2A transports. A transport remains cached in memory after its source process
exits and is discarded only when the local A2A service rejects it. Tokens and
endpoints are never written to state or logs and are removed from Cursor and
Antigravity probe environments.

Through every usable cached transport, the supervisor reconciles Traycer's
agent registry. Successful views are combined, and the registry—not provider
process lifetime—determines which agents remain open and messageable. A failed
registry refresh preserves the last non-secret session and candidate state.
Archived agents are removed immediately; an agent absent from an authoritative
parent/children view is kept non-messageable for a short grace period before
removal.

Registry-open agents are grouped by harness and provider profile. Cursor and
Antigravity always use their ambient profile. Other profile lookups, quota
checks, target wakes, and parent notifications can use any cached transport;
they are not bound to a target agent's provider process.

## Setup

Run setup from the dedicated long-lived clone. Setup refuses Traycer worktrees
under `~/.traycer/worktrees/` and linked Git worktrees because pruning or moving
one would break the installed launcher link and leave `launchd` retrying a
missing executable. `--allow-ephemeral-checkout` is an expert escape hatch for
controlled testing only; it does not make the source path durable.

Preview the complete file and `launchctl` plan, then apply it:

```sh
./scripts/manage-quota-supervisor.sh --dry-run setup
./scripts/manage-quota-supervisor.sh setup
```

Setup creates a tracked launcher link at
`~/.local/bin/traycer-quota-supervisor` and a private LaunchAgent plist at
`~/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist`. Runtime state
and logs live under `~/.local/state/traycer-quota-supervisor/` with user-only
permissions.

Repeated setup is safe and leaves an already-current service running without
file or `launchctl` changes. It reloads the LaunchAgent only when managed files
change. Manager-owned launchers are recognized by their reviewed launcher and
runtime digest pair rather than by the current checkout path, so status, setup,
and uninstall can validate the same reviewed release from another checkout.

### Legacy cutover

Setup recognizes the earlier self-installed supervisor only when its executable
SHA-256 and complete plist structure match the reviewed legacy release. Each
legacy file is copied to a new private, timestamped directory under
`~/.agents/service-backups/traycer-quota-supervisor/` and compared byte-for-byte
before that file is replaced.

If an interruption leaves the reviewed new launcher with the reviewed legacy
plist, setup safely resumes after backing up the remaining plist. Other
incomplete, modified, or mixed states are refused. A changed loaded service is
stopped before file replacement, so an interrupted rerun bootstraps it instead
of retaining stale loaded settings. Dry-run prints the ordered stop, backup,
replacement, and bootstrap plan without mutation. State and logs are never
moved or deleted during cutover.

## Status

```sh
./scripts/manage-quota-supervisor.sh status
~/.local/bin/traycer-quota-supervisor status --json
```

Status is read-only and reports agent IDs, surface/harness/profile names,
registry-open, registry-fresh, and messageable state, quota state, and aggregate
cached transport/source-process counts. The registry timestamp shows when each
session was last confirmed. Stale sessions are preserved for a later
authoritative reconciliation but are not polled or awakened. Status does not
return A2A tokens, endpoints, process command lines, or environment values.

The service log is
`~/.local/state/traycer-quota-supervisor/supervisor.log`.

## Uninstall

Preview and remove only manager-owned service files:

```sh
./scripts/manage-quota-supervisor.sh --dry-run uninstall
./scripts/manage-quota-supervisor.sh uninstall
```

Uninstall stops the LaunchAgent and removes its recognized plist and launcher
link. It deliberately preserves supervisor state, logs, and legacy backups.
Running it again is safe. Unrecognized launcher or plist targets are refused
instead of overwritten or removed.

The manager never reads or writes A2A credentials and does not modify Traycer,
Codex, or Claude hook configuration.
