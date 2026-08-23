# Variables initialized here are consumed by the Bats file that loads this helper.
# shellcheck disable=SC2034
setup_quota_supervisor_test() {
  # Assigning CDPATH only for cd prevents inherited values from changing output.
  # shellcheck disable=SC1007
  REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$BATS_TEST_FILENAME")/.." && pwd)
  MANAGER_SOURCE="$REPO_ROOT/scripts/manage-quota-supervisor.sh"
  LAUNCHER_SOURCE="$REPO_ROOT/mcp/bin/traycer-quota-supervisor"
  TEST_ROOT="$BATS_TEST_TMPDIR/sandbox"
  FAKE_BIN="$TEST_ROOT/bin"
  MANAGER="$FAKE_BIN/manage-quota-supervisor"
  INSTALL_HOME="$TEST_ROOT/home"
  QUOTA_TEST_STATE="$TEST_ROOT/mock-state"
  QUOTA_TEST_REAL_PYTHON=$(command -v python3)

  mkdir -p "$FAKE_BIN" "$INSTALL_HOME" "$QUOTA_TEST_STATE"
  write_quota_python_mock
  write_quota_id_mock
  write_quota_stat_mock
  write_quota_uname_mock
  write_quota_plutil_mock
  write_quota_launchctl_mock
  write_quota_manager_wrapper

  export QUOTA_TEST_REAL_PYTHON
  export QUOTA_TEST_MANAGER_SOURCE="$MANAGER_SOURCE"
  export QUOTA_TEST_STATE
  export QUOTA_SUPERVISOR_INSTALL_HOME="$INSTALL_HOME"
  export QUOTA_SUPERVISOR_PYTHON_BIN="$FAKE_BIN/python3"
  export QUOTA_SUPERVISOR_ID_BIN="$FAKE_BIN/id"
  export QUOTA_SUPERVISOR_STAT_BIN="$FAKE_BIN/stat"
  export QUOTA_SUPERVISOR_UNAME_BIN="$FAKE_BIN/uname"
  export QUOTA_SUPERVISOR_PLUTIL_BIN="$FAKE_BIN/plutil"
  export QUOTA_SUPERVISOR_LAUNCHCTL_BIN="$FAKE_BIN/launchctl"

  unset QUOTA_TEST_FOREIGN_PATH
  unset QUOTA_TEST_LEGACY_HASH_RESULT
  unset QUOTA_TEST_OS
  unset QUOTA_TEST_PYTHON_VERSION
  unset QUOTA_TEST_UID
  unset QUOTA_TEST_WORLD_WRITABLE_PATH
  unset TRAYCER_A2A_TOKEN
}

write_quota_manager_wrapper() {
  cat >"$MANAGER" <<'EOF'
#!/bin/sh
set -eu
exec "$QUOTA_TEST_MANAGER_SOURCE" --allow-ephemeral-checkout "$@"
EOF
  chmod +x "$MANAGER"
}

write_quota_python_mock() {
  cat >"$FAKE_BIN/python3" <<'EOF'
#!/bin/sh
set -eu

if [ "${1:-}" = -c ]; then
  case "${2:-}" in
    *sys.version_info.major*)
      printf '%s\n' "${QUOTA_TEST_PYTHON_VERSION:-3.11.9}"
      exit 0
      ;;
    *hashlib.sha256*)
      if [ "${QUOTA_TEST_LEGACY_HASH_RESULT:-}" = match ]; then
        printf '%s\n' 'ff90223a24e99517c4f1c811a619d4a589eaac98b27beb965fb817e7409b57d5'
        exit 0
      fi
      ;;
  esac
fi

exec "$QUOTA_TEST_REAL_PYTHON" "$@"
EOF
  chmod +x "$FAKE_BIN/python3"
}

write_quota_id_mock() {
  cat >"$FAKE_BIN/id" <<'EOF'
#!/bin/sh
set -eu
[ "$#" -eq 1 ] && [ "$1" = -u ] || exit 64
printf '%s\n' "${QUOTA_TEST_UID:-501}"
EOF
  chmod +x "$FAKE_BIN/id"
}

write_quota_stat_mock() {
  cat >"$FAKE_BIN/stat" <<'EOF'
#!/bin/sh
set -eu

if [ "${1:-}" = -L ]; then
  shift
fi
[ "$#" -eq 3 ] && [ "$1" = -f ] || exit 64
format=$2
path=$3
case "$format" in
  %u)
    if [ "${QUOTA_TEST_FOREIGN_PATH:-}" = "$path" ]; then
      printf '%s\n' 502
    else
      printf '%s\n' "${QUOTA_TEST_UID:-501}"
    fi
    ;;
  %Lp)
    if [ "${QUOTA_TEST_WORLD_WRITABLE_PATH:-}" = "$path" ]; then
      printf '%s\n' 777
    else
      "$QUOTA_TEST_REAL_PYTHON" - "$path" <<'PY'
import os
import stat
import sys

print(f"{stat.S_IMODE(os.stat(sys.argv[1]).st_mode):o}")
PY
    fi
    ;;
  *) exit 64 ;;
esac
EOF
  chmod +x "$FAKE_BIN/stat"
}

write_quota_uname_mock() {
  cat >"$FAKE_BIN/uname" <<'EOF'
#!/bin/sh
set -eu
[ "$#" -eq 1 ] && [ "$1" = -s ] || exit 64
printf '%s\n' "${QUOTA_TEST_OS:-Darwin}"
EOF
  chmod +x "$FAKE_BIN/uname"
}

write_quota_plutil_mock() {
  cat >"$FAKE_BIN/plutil" <<'EOF'
#!/bin/sh
set -eu
[ "$#" -eq 2 ] && [ "$1" = -lint ] || exit 64
"$QUOTA_TEST_REAL_PYTHON" - "$2" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as source:
    plistlib.load(source)
PY
EOF
  chmod +x "$FAKE_BIN/plutil"
}

write_quota_launchctl_mock() {
  cat >"$FAKE_BIN/launchctl" <<'EOF'
#!/bin/sh
set -eu

command=$1
shift
case "$command" in
  print)
    [ -f "$QUOTA_TEST_STATE/loaded" ]
    ;;
  bootstrap)
    printf 'bootstrap %s\n' "$*" >>"$QUOTA_TEST_STATE/launchctl.log"
    : >"$QUOTA_TEST_STATE/loaded"
    ;;
  bootout)
    printf 'bootout %s\n' "$*" >>"$QUOTA_TEST_STATE/launchctl.log"
    rm -f "$QUOTA_TEST_STATE/loaded"
    ;;
  kickstart)
    printf 'kickstart %s\n' "$*" >>"$QUOTA_TEST_STATE/launchctl.log"
    ;;
  *) exit 64 ;;
esac
EOF
  chmod +x "$FAKE_BIN/launchctl"
}

create_legacy_quota_installation() {
  local launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  local plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  local state_dir="$INSTALL_HOME/.local/state/traycer-quota-supervisor"
  local log_file="$state_dir/supervisor.log"

  mkdir -p "$(dirname -- "$launcher")" "$(dirname -- "$plist")" "$state_dir"
  chmod 700 "$state_dir"
  printf '%s\n' '#!/usr/bin/env python3' 'print("reviewed legacy fixture")' >"$launcher"
  chmod 700 "$launcher"
  printf '%s\n' 'preserved legacy log' >"$log_file"
  chmod 600 "$log_file"
  "$QUOTA_TEST_REAL_PYTHON" - "$plist" "$launcher" "$log_file" <<'PY'
import plistlib
import sys

payload = {
    "KeepAlive": True,
    "Label": "com.nfma.traycer-quota-supervisor",
    "ProcessType": "Background",
    "ProgramArguments": [sys.argv[2], "run"],
    "RunAtLoad": True,
    "StandardErrorPath": sys.argv[3],
    "StandardOutPath": sys.argv[3],
    "ThrottleInterval": 10,
}
with open(sys.argv[1], "wb") as destination:
    plistlib.dump(payload, destination)
PY
  chmod 600 "$plist"
  export QUOTA_TEST_LEGACY_HASH_RESULT=match
}

create_quota_manager_checkout() {
  local checkout=$1

  mkdir -p \
    "$checkout/.git" \
    "$checkout/scripts" \
    "$checkout/mcp/bin" \
    "$checkout/mcp/launchd" \
    "$checkout/services/traycer-quota-supervisor"
  cp "$MANAGER_SOURCE" "$checkout/scripts/manage-quota-supervisor.sh"
  cp "$LAUNCHER_SOURCE" "$checkout/mcp/bin/traycer-quota-supervisor"
  cp \
    "$REPO_ROOT/mcp/launchd/com.nfma.traycer-quota-supervisor.plist.template" \
    "$checkout/mcp/launchd/com.nfma.traycer-quota-supervisor.plist.template"
  cp \
    "$REPO_ROOT/services/traycer-quota-supervisor/traycer_quota_supervisor.py" \
    "$checkout/services/traycer-quota-supervisor/traycer_quota_supervisor.py"
  chmod +x \
    "$checkout/scripts/manage-quota-supervisor.sh" \
    "$checkout/mcp/bin/traycer-quota-supervisor"
}

assert_plist_is_current() {
  local plist="$INSTALL_HOME/Library/LaunchAgents/com.nfma.traycer-quota-supervisor.plist"
  local launcher="$INSTALL_HOME/.local/bin/traycer-quota-supervisor"
  local log_file="$INSTALL_HOME/.local/state/traycer-quota-supervisor/supervisor.log"

  "$QUOTA_TEST_REAL_PYTHON" - "$plist" "$launcher" "$log_file" <<'PY'
import plistlib
import sys

with open(sys.argv[1], "rb") as source:
    payload = plistlib.load(source)
assert payload == {
    "KeepAlive": True,
    "Label": "com.nfma.traycer-quota-supervisor",
    "ProcessType": "Background",
    "ProgramArguments": [sys.argv[2], "run"],
    "RunAtLoad": True,
    "StandardErrorPath": sys.argv[3],
    "StandardOutPath": sys.argv[3],
    "ThrottleInterval": 30,
}
PY
}

start_quota_status_server() {
  QUOTA_STATUS_STATE_DIR=$(mktemp -d /tmp/quota-supervisor-test.XXXXXX)
  chmod 700 "$QUOTA_STATUS_STATE_DIR"
  export TRAYCER_QUOTA_SUPERVISOR_STATE_DIR="$QUOTA_STATUS_STATE_DIR"
  local socket_path="$QUOTA_STATUS_STATE_DIR/supervisor.sock"

  "$QUOTA_TEST_REAL_PYTHON" - "$socket_path" <<'PY' &
import json
import socket
import sys

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(sys.argv[1])
server.listen(1)
connection, _ = server.accept()
with connection:
    request = json.loads(connection.makefile("rb").readline())
    assert request == {"action": "status"}
    payload = {
        "groups": [{"harness": "codex", "profile": "ambient", "state": "available"}],
        "ok": True,
        "service": "com.nfma.traycer-quota-supervisor",
        "sessions": [
            {
                "agent_id": "11111111-1111-4111-8111-111111111111",
                "harness": "codex",
                "messageable": True,
                "parent_id": None,
                "parent_messageable": False,
                "profile": "ambient",
                "registry_fresh": True,
                "registry_last_seen": 1000.0,
                "registry_open": True,
                "status": "open",
                "surface": "gui",
            }
        ],
        "transport": {"cached": 1, "source_processes": 0},
    }
    connection.sendall((json.dumps(payload) + "\n").encode())
server.close()
PY
  QUOTA_STATUS_SERVER_PID=$!

  local attempts=0
  while [ ! -S "$socket_path" ]; do
    attempts=$((attempts + 1))
    [ "$attempts" -lt 100 ] || return 1
    sleep 0.05
  done
}

cleanup_quota_status_server() {
  case "${QUOTA_STATUS_STATE_DIR:-}" in
    /tmp/quota-supervisor-test.*)
      rm -rf -- "$QUOTA_STATUS_STATE_DIR"
      ;;
  esac
  unset QUOTA_STATUS_STATE_DIR
  unset TRAYCER_QUOTA_SUPERVISOR_STATE_DIR
}

file_mode() {
  "$QUOTA_TEST_REAL_PYTHON" - "$1" <<'PY'
import os
import stat
import sys

print(f"{stat.S_IMODE(os.stat(sys.argv[1]).st_mode):o}")
PY
}
