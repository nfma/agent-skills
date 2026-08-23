#!/usr/bin/env bats

load 'test_helper/quota-supervisor-manager.bash'

setup() {
  setup_quota_supervisor_test
}

teardown() {
  cleanup_quota_status_server
}

@test "prints help without running platform preflight" {
  export QUOTA_TEST_OS=Linux

  run "$MANAGER" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *'Usage: ./scripts/manage-quota-supervisor.sh'* ]]
}

@test "rejects an unsafe home and a non-macOS platform" {
  run env QUOTA_SUPERVISOR_INSTALL_HOME=relative "$MANAGER" --dry-run setup
  [ "$status" -eq 1 ]
  [[ "$output" == *'installation home must be an absolute path'* ]]

  export QUOTA_TEST_OS=Linux
  run "$MANAGER" --dry-run setup
  [ "$status" -eq 1 ]
  [[ "$output" == *'supported only on macOS'* ]]
}

@test "setup refuses an ephemeral worktree unless explicitly overridden" {
  run "$MANAGER_SOURCE" --dry-run setup

  [ "$status" -eq 1 ]
  [[ "$output" == *'setup source must be a dedicated normal clone'* ]]
  [[ "$output" == *'--allow-ephemeral-checkout'* ]]
  [ ! -e "$INSTALL_HOME/.local" ]
  [ ! -e "$INSTALL_HOME/Library" ]
  [ ! -e "$QUOTA_TEST_STATE/launchctl.log" ]

  run "$MANAGER_SOURCE" --allow-ephemeral-checkout --dry-run setup

  [ "$status" -eq 0 ]
  [[ "$output" == *'Setup dry run complete'* ]]
}

@test "requires a non-root user and Homebrew Python 3.11 or newer" {
  export QUOTA_TEST_UID=0
  run "$MANAGER" --dry-run setup
  [ "$status" -eq 1 ]
  [[ "$output" == *'must be managed by a non-root user'* ]]

  export QUOTA_TEST_UID=501
  export QUOTA_TEST_PYTHON_VERSION=3.10.14
  run "$MANAGER" --dry-run setup
  [ "$status" -eq 1 ]
  [[ "$output" == *'Python 3.11 or newer is required'* ]]
}

@test "fresh setup dry run reports the plan without mutation" {
  run "$MANAGER" --dry-run setup

  [ "$status" -eq 0 ]
  [[ "$output" == *'Would install supervisor launcher'* ]]
  [[ "$output" == *'Would install LaunchAgent plist'* ]]
  [[ "$output" == *'Would bootstrap supervisor service'* ]]
  [[ "$output" == *'no files or services were changed'* ]]
  [ ! -e "$INSTALL_HOME/.local" ]
  [ ! -e "$INSTALL_HOME/Library" ]
  [ ! -e "$QUOTA_TEST_STATE/loaded" ]
  [ ! -e "$QUOTA_TEST_STATE/launchctl.log" ]
}

@test "fresh setup installs private managed files without exposing credentials" {
  export TRAYCER_A2A_TOKEN=never-print-this-token

  run "$MANAGER" setup

  [ "$status" -eq 0 ]
  [[ "$output" != *never-print-this-token* ]]
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  state_dir="$INSTALL_HOME/.local/state/traycer-quota-supervisor"
  log_file="$state_dir/supervisor.log"
  [ -L "$launcher" ]
  [ "$(readlink "$launcher")" = "$LAUNCHER_SOURCE" ]
  [ "$(file_mode "$plist")" = 600 ]
  [ "$(file_mode "$state_dir")" = 700 ]
  [ "$(file_mode "$log_file")" = 600 ]
  assert_plist_is_current
  [ -f "$QUOTA_TEST_STATE/loaded" ]
  grep -F 'bootstrap gui/501' "$QUOTA_TEST_STATE/launchctl.log"
}

@test "repeated setup is idempotent and preserves state and logs" {
  run "$MANAGER" setup
  [ "$status" -eq 0 ]
  printf '%s\n' 'preserved state' >"$INSTALL_HOME/.local/state/traycer-quota-supervisor/state.json"
  printf '%s\n' 'preserved log' >"$INSTALL_HOME/.local/state/traycer-quota-supervisor/supervisor.log"
  : >"$QUOTA_TEST_STATE/launchctl.log"

  run "$MANAGER" setup

  [ "$status" -eq 0 ]
  [[ "$output" == *'Supervisor launcher is current'* ]]
  [[ "$output" == *'LaunchAgent plist is current'* ]]
  [[ "$output" == *'Supervisor service is already loaded and current'* ]]
  [ "$(<"$INSTALL_HOME/.local/state/traycer-quota-supervisor/state.json")" = 'preserved state' ]
  [ "$(<"$INSTALL_HOME/.local/state/traycer-quota-supervisor/supervisor.log")" = 'preserved log' ]
  [ ! -s "$QUOTA_TEST_STATE/launchctl.log" ]
}

@test "a current but unloaded interrupted setup is bootstrapped on rerun" {
  run "$MANAGER" setup
  [ "$status" -eq 0 ]
  rm -f "$QUOTA_TEST_STATE/loaded"
  : >"$QUOTA_TEST_STATE/launchctl.log"

  run "$MANAGER" setup

  [ "$status" -eq 0 ]
  grep -F 'bootstrap gui/501' "$QUOTA_TEST_STATE/launchctl.log"
  [ -f "$QUOTA_TEST_STATE/loaded" ]
}

@test "recognized managed payload can be managed from another durable checkout" {
  checkout_a="$INSTALL_HOME/Projects/checkout-a"
  checkout_b="$INSTALL_HOME/Projects/checkout-b"
  create_quota_manager_checkout "$checkout_a"
  create_quota_manager_checkout "$checkout_b"
  manager_a="$checkout_a/scripts/manage-quota-supervisor.sh"
  manager_b="$checkout_b/scripts/manage-quota-supervisor.sh"
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"

  run "$manager_a" setup
  [ "$status" -eq 0 ]
  [ "$(readlink "$launcher")" = "$checkout_a/mcp/bin/traycer-quota-supervisor" ]

  : >"$QUOTA_TEST_STATE/launchctl.log"
  run "$manager_b" setup
  [ "$status" -eq 0 ]
  [[ "$output" == *'Supervisor launcher is current'* ]]
  [ "$(readlink "$launcher")" = "$checkout_a/mcp/bin/traycer-quota-supervisor" ]
  [ ! -s "$QUOTA_TEST_STATE/launchctl.log" ]

  run "$manager_b" uninstall
  [ "$status" -eq 0 ]
  [ ! -e "$launcher" ]
}

@test "status uses the tracked launcher and remains credential-free" {
  run "$MANAGER" setup
  [ "$status" -eq 0 ]
  start_quota_status_server
  export TRAYCER_A2A_MCP_TOKEN=never-print-this-status-token

  run "$MANAGER" status
  wait "$QUOTA_STATUS_SERVER_PID"

  [ "$status" -eq 0 ]
  [[ "$output" == *'Supervisor service is loaded: gui/501/com.nfma.traycer-quota-supervisor'* ]]
  [[ "$output" == *'com.nfma.traycer-quota-supervisor: running; 1 session(s), 1 quota group(s)'* ]]
  [[ "$output" == *'11111111-1111-4111-8111-111111111111 gui/codex ambient open registry-open registry-fresh messageable'* ]]
  [[ "$output" != *never-print-this-status-token* ]]
}

@test "setup refuses an unrecognized launcher before mutation" {
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  mkdir -p "$(dirname -- "$launcher")"
  printf '%s\n' '#!/bin/sh' 'exit 0' >"$launcher"
  chmod 700 "$launcher"

  run "$MANAGER" setup

  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing an unrecognized supervisor launcher'* ]]
  [ ! -e "$INSTALL_HOME/Library" ]
  [ ! -e "$INSTALL_HOME/.local/state" ]
  [ ! -e "$QUOTA_TEST_STATE/loaded" ]
}

@test "setup refuses an unrecognized plist before mutation" {
  plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  mkdir -p "$(dirname -- "$plist")"
  printf '%s\n' 'not a plist' >"$plist"
  chmod 600 "$plist"

  run "$MANAGER" setup

  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing an unrecognized LaunchAgent plist'* ]]
  [ ! -e "$INSTALL_HOME/.local" ]
  [ ! -e "$QUOTA_TEST_STATE/loaded" ]
}

@test "manager refuses foreign-owned targets and unsafe private state" {
  run "$MANAGER" setup
  [ "$status" -eq 0 ]
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  state_dir="$INSTALL_HOME/.local/state/traycer-quota-supervisor"

  export QUOTA_TEST_FOREIGN_PATH="$launcher"
  run "$MANAGER" uninstall
  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing to remove an unrecognized supervisor launcher'* ]]
  [ -L "$launcher" ]

  unset QUOTA_TEST_FOREIGN_PATH
  export QUOTA_TEST_WORLD_WRITABLE_PATH="$state_dir"
  run "$MANAGER" setup
  [ "$status" -eq 1 ]
  [[ "$output" == *'supervisor state directory is group- or other-writable'* ]]
}

@test "legacy cutover dry run reports backup and replacement without mutation" {
  create_legacy_quota_installation
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  launcher_before=$("$QUOTA_TEST_REAL_PYTHON" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$launcher")
  plist_before=$("$QUOTA_TEST_REAL_PYTHON" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$plist")

  run "$MANAGER" --dry-run setup

  [ "$status" -eq 0 ]
  [[ "$output" == *'Would back up the verified legacy launcher and plist'* ]]
  [[ "$output" == *'Would install supervisor launcher'* ]]
  [ ! -e "$INSTALL_HOME/.agents" ]
  [ ! -L "$launcher" ]
  [ "$launcher_before" = "$("$QUOTA_TEST_REAL_PYTHON" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$launcher")" ]
  [ "$plist_before" = "$("$QUOTA_TEST_REAL_PYTHON" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$plist")" ]
  [ ! -e "$QUOTA_TEST_STATE/loaded" ]
}

@test "verified legacy cutover backs up exact files and preserves state" {
  create_legacy_quota_installation
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  cp "$launcher" "$TEST_ROOT/legacy-launcher"
  cp "$plist" "$TEST_ROOT/legacy-plist"
  printf '%s\n' 'preserved state' >"$INSTALL_HOME/.local/state/traycer-quota-supervisor/state.json"
  : >"$QUOTA_TEST_STATE/loaded"

  run "$MANAGER" setup

  [ "$status" -eq 0 ]
  backup_dirs=("$INSTALL_HOME"/.agents/service-backups/traycer-quota-supervisor/cutover-*)
  [ "${#backup_dirs[@]}" -eq 1 ]
  cmp "$TEST_ROOT/legacy-launcher" "${backup_dirs[0]}/traycer-quota-supervisor"
  cmp "$TEST_ROOT/legacy-plist" "${backup_dirs[0]}/com.nfma.traycer-quota-supervisor.plist"
  [ "$(file_mode "${backup_dirs[0]}")" = 700 ]
  [ -L "$launcher" ]
  [ "$(readlink "$launcher")" = "$LAUNCHER_SOURCE" ]
  assert_plist_is_current
  [ "$(<"$INSTALL_HOME/.local/state/traycer-quota-supervisor/state.json")" = 'preserved state' ]
  [ "$(<"$INSTALL_HOME/.local/state/traycer-quota-supervisor/supervisor.log")" = 'preserved legacy log' ]
  grep -F 'bootout gui/501/com.nfma.traycer-quota-supervisor' "$QUOTA_TEST_STATE/launchctl.log"
  grep -F 'bootstrap gui/501' "$QUOTA_TEST_STATE/launchctl.log"
}

@test "verified new launcher and legacy plist resume an interrupted cutover" {
  create_legacy_quota_installation
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  backup_root="$INSTALL_HOME/.agents/service-backups/traycer-quota-supervisor"
  reviewed_backup="$backup_root/cutover-reviewed"
  legacy_launcher_copy="$TEST_ROOT/legacy-launcher-before-resume"
  legacy_plist_copy="$TEST_ROOT/legacy-plist-before-resume"
  cp "$launcher" "$legacy_launcher_copy"
  cp "$plist" "$legacy_plist_copy"
  mkdir -p "$reviewed_backup"
  chmod 700 "$backup_root" "$reviewed_backup"
  cp "$launcher" "$reviewed_backup/traycer-quota-supervisor"
  cp "$plist" "$reviewed_backup/com.nfma.traycer-quota-supervisor.plist"
  rm -f "$launcher"
  ln -s "$LAUNCHER_SOURCE" "$launcher"
  : >"$QUOTA_TEST_STATE/loaded"

  run "$MANAGER" setup

  [ "$status" -eq 0 ]
  [[ "$output" == *'Backed up the verified legacy installation'* ]]
  [ -L "$launcher" ]
  assert_plist_is_current
  cmp "$legacy_launcher_copy" "$reviewed_backup/traycer-quota-supervisor"
  cmp "$legacy_plist_copy" "$reviewed_backup/com.nfma.traycer-quota-supervisor.plist"
  backup_dirs=("$backup_root"/cutover-*)
  [ "${#backup_dirs[@]}" -eq 2 ]
  plist_only_backup=
  for backup_dir in "${backup_dirs[@]}"; do
    if [ ! -e "$backup_dir/traycer-quota-supervisor" ]; then
      plist_only_backup=$backup_dir
    fi
  done
  [ -n "$plist_only_backup" ]
  [ -f "$plist_only_backup/com.nfma.traycer-quota-supervisor.plist" ]
  launch_events=$(<"$QUOTA_TEST_STATE/launchctl.log")
  expected_events=$'bootout gui/501/com.nfma.traycer-quota-supervisor\nbootstrap gui/501 '"$plist"
  [ "$launch_events" = "$expected_events" ]
}

@test "legacy launcher with a new plist remains an ambiguous refused state" {
  create_legacy_quota_installation
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  legacy_copy="$TEST_ROOT/legacy-launcher"
  cp "$launcher" "$legacy_copy"
  run "$MANAGER" setup
  [ "$status" -eq 0 ]
  rm -f "$launcher"
  cp "$legacy_copy" "$launcher"
  chmod 700 "$launcher"
  : >"$QUOTA_TEST_STATE/launchctl.log"

  run "$MANAGER" setup

  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing an incomplete or mixed legacy supervisor installation'* ]]
  [ ! -s "$QUOTA_TEST_STATE/launchctl.log" ]
}

@test "uninstall gives an actionable recovery for a resumable cutover" {
  create_legacy_quota_installation
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  rm -f "$launcher"
  ln -s "$LAUNCHER_SOURCE" "$launcher"

  run "$MANAGER" uninstall

  [ "$status" -eq 1 ]
  [[ "$output" == *'verified resumable cutover detected; run setup to complete it, then rerun uninstall'* ]]
  [ -L "$launcher" ]
  [ -f "$plist" ]
}

@test "modified or incomplete legacy targets are refused without backup" {
  create_legacy_quota_installation
  unset QUOTA_TEST_LEGACY_HASH_RESULT

  run "$MANAGER" setup
  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing an unrecognized supervisor launcher'* ]]
  [ ! -e "$INSTALL_HOME/.agents" ]

  rm -f "$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  rm -f "$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  create_legacy_quota_installation
  rm -f "$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"

  run "$MANAGER" setup
  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing an incomplete or mixed legacy supervisor installation'* ]]
  [ ! -e "$INSTALL_HOME/.agents" ]
}

@test "uninstall dry run and uninstall preserve state logs and backups" {
  run "$MANAGER" setup
  [ "$status" -eq 0 ]
  state_dir="$INSTALL_HOME/.local/state/traycer-quota-supervisor"
  backup_dir="$INSTALL_HOME/.agents/service-backups/traycer-quota-supervisor/manual-review"
  printf '%s\n' 'preserved state' >"$state_dir/state.json"
  printf '%s\n' 'preserved log' >"$state_dir/supervisor.log"
  mkdir -p "$backup_dir"
  printf '%s\n' 'preserved backup' >"$backup_dir/manifest"

  run "$MANAGER" --dry-run uninstall
  [ "$status" -eq 0 ]
  [[ "$output" == *'Uninstall dry run complete'* ]]
  [ -L "$INSTALL_HOME/.local/bin/traycer-quota-supervisor" ]
  [ -f "$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist" ]
  [ -f "$QUOTA_TEST_STATE/loaded" ]

  run "$MANAGER" uninstall
  [ "$status" -eq 0 ]
  [ ! -e "$INSTALL_HOME/.local/bin/traycer-quota-supervisor" ]
  [ ! -e "$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist" ]
  [ ! -e "$QUOTA_TEST_STATE/loaded" ]
  [ "$(<"$state_dir/state.json")" = 'preserved state' ]
  [ "$(<"$state_dir/supervisor.log")" = 'preserved log' ]
  [ "$(<"$backup_dir/manifest")" = 'preserved backup' ]

  run "$MANAGER" uninstall
  [ "$status" -eq 0 ]
  [[ "$output" == *'Supervisor service is not loaded'* ]]
  [ "$(<"$state_dir/state.json")" = 'preserved state' ]
}

@test "uninstall refuses unrecognized files" {
  launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  mkdir -p "$(dirname -- "$launcher")"
  printf '%s\n' 'do not remove' >"$launcher"
  chmod 600 "$launcher"

  run "$MANAGER" uninstall

  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing to remove an unrecognized supervisor launcher'* ]]
  [ "$(<"$launcher")" = 'do not remove' ]

  rm -f "$launcher"
  ln -s "$TEST_ROOT/missing-checkout/mcp/bin/traycer-quota-supervisor" "$launcher"
  run "$MANAGER" uninstall
  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing to remove an unrecognized supervisor launcher'* ]]
  [ -L "$launcher" ]
}
