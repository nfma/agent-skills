# Variables initialized here are consumed by the Bats files that load this helper.
# shellcheck disable=SC2034
setup_installer_test() {
  # Assigning CDPATH only for cd prevents inherited values from changing output.
  # shellcheck disable=SC1007
  REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$BATS_TEST_FILENAME")/.." && pwd)
  INSTALLER="$REPO_ROOT/scripts/install-mcps.sh"
  WRAPPER="$REPO_ROOT/mcp/bin/github-mcp-keychain"
  VIVALDI_WRAPPER="$REPO_ROOT/mcp/bin/chrome-devtools-vivaldi"
  DISCORD_WRAPPER="$REPO_ROOT/mcp/bin/discord-mcp-keychain"
  WAKE_RELAY_LAUNCHER="$REPO_ROOT/mcp/bin/discord-wake-relay"
  TEST_ROOT="$BATS_TEST_TMPDIR/sandbox"
  FAKE_BIN="$TEST_ROOT/bin"
  INSTALL_HOME="$TEST_ROOT/home"
  MCP_TEST_STATE="$TEST_ROOT/state"

  mkdir -p \
    "$FAKE_BIN" \
    "$INSTALL_HOME/.serena" \
    "$INSTALL_HOME/.local/share/uv/tools/serena-agent/bin" \
    "$MCP_TEST_STATE"
  : >"$INSTALL_HOME/.serena/serena_config.yml"

  write_success_command cursor
  write_success_command agy
  write_node_check
  write_security_mock
  write_github_server_mock
  write_discord_launcher_mock
  write_launchctl_mock
  write_uname_mock
  write_plutil_mock
  write_codex_mock
  write_claude_mock
  write_serena_mock

  export PATH="$FAKE_BIN:$PATH"
  export MCP_INSTALL_HOME="$INSTALL_HOME"
  export MCP_SECURITY_BIN="$FAKE_BIN/security"
  export MCP_NODE_CHECK_BIN="$FAKE_BIN/node-check"
  export DISCORD_MCP_SECURITY_BIN="$FAKE_BIN/security"
  export DISCORD_MCP_NPX_BIN="$FAKE_BIN/discord-npx"
  export MCP_DISCORD_PROFILE_BIN="$FAKE_BIN/discord-npx"
  export MCP_LAUNCHCTL_BIN="$FAKE_BIN/launchctl"
  export MCP_UNAME_BIN="$FAKE_BIN/uname"
  export MCP_PLUTIL_BIN="$FAKE_BIN/plutil"
  export MCP_TEST_STATE

  WAKE_RELAY_PLIST="$INSTALL_HOME/Library/LaunchAgents/com.nfma.discord-wake-relay.plist"
  WAKE_RELAY_LOG_DIR="$INSTALL_HOME/Library/Logs/discord-wake-relay"
  WAKE_RELAY_STATE_DIR="$INSTALL_HOME/.local/state/discord-agent-coordination/wake-relay"
  WAKE_RELAY_LOADED_PLIST="$MCP_TEST_STATE/wake-relay-definition.plist"

  unset MCP_CODEX_FRAGMENT
  unset MCP_CLAUDE_FRAGMENT
  unset MCP_TEST_CLAUDE_MARKETPLACE
  unset MCP_TEST_CLAUDE_PLUGIN_MODE
  unset MCP_TEST_CODEX_MARKETPLACE
  unset MCP_TEST_CODEX_PLUGIN_MODE
  unset MCP_TEST_CODEX_ADD_NOOP
  unset MCP_TEST_DISCORD_PROFILE_MISSING
  unset MCP_TEST_DISCORD_SETUP_FAILURE
  unset MCP_TEST_LAUNCHCTL_FAILURE
  unset MCP_TEST_NODE_FAILURE
  unset MCP_TEST_NODE_VERSION
  unset MCP_TEST_SECURITY_FAILURE
  unset MCP_TEST_TRAYCER_FAILURE
  unset MCP_TEST_UNAME
}

write_success_command() {
  command_name=$1
  printf '#!/bin/sh\nexit 0\n' >"$FAKE_BIN/$command_name"
  chmod +x "$FAKE_BIN/$command_name"
}

write_node_check() {
  cat >"$FAKE_BIN/node-check" <<'EOF'
#!/bin/sh
set -eu
[ "${MCP_TEST_NODE_FAILURE:-0}" != 1 ]
if [ "$1 $2" = 'node --version' ]; then
  printf '%s\n' "${MCP_TEST_NODE_VERSION:-v24.0.0}"
  exit 0
fi

"$@"
if [ "$1" = node ] && [ "${2##*/}" = update-codex-mcp-config.cjs ]; then
  state_file="$MCP_TEST_STATE/codex-mcp-$4.json"
  if [ -r "$state_file" ]; then
    jq --argjson timeout "$5" \
      '. + {startup_timeout_sec:$timeout}' \
      "$state_file" > "$state_file.tmp"
    mv "$state_file.tmp" "$state_file"
  fi
fi
EOF
  chmod +x "$FAKE_BIN/node-check"
}

write_security_mock() {
  cat >"$FAKE_BIN/security" <<'EOF'
#!/bin/sh
set -eu
case " $* " in
  *' -s HF_TOKEN '*)
    [ "${MCP_TEST_SECURITY_FAILURE:-}" != hf ] || exit 1
    ;;
  *' -s DISCORD_MCP_TOKEN '*)
    [ "${MCP_TEST_SECURITY_FAILURE:-}" != discord ] || exit 1
    ;;
  *' -l GITHUB_MCP_PAT '*)
    [ "${MCP_TEST_SECURITY_FAILURE:-}" != github ] || exit 1
    ;;
  *)
    exit 64
    ;;
esac
printf 'test-credential\n'
EOF
  chmod +x "$FAKE_BIN/security"
}

write_discord_launcher_mock() {
  cat >"$FAKE_BIN/discord-npx" <<'EOF'
#!/bin/sh
set -eu

[ "${DISCORD_TOKEN:-}" = test-credential ]
[ "${MCP_CATEGORIES:-}" = messages,threads,channels ]
[ "${MCP_TOOL_SURFACE:-}" = progressive ]

printf '%s\n' "$*" >>"$MCP_TEST_STATE/discord-npx-calls.log"

case " $* " in
  *' profile show agent-coordination --json '*)
    if [ -e "$MCP_TEST_STATE/discord-profile-ready" ]; then
      exit 0
    fi
    [ "${MCP_TEST_DISCORD_PROFILE_MISSING:-0}" != 1 ]
    ;;
  *' setup --profile agent-coordination --client generic --tool-surface progressive '*)
    [ "${MCP_TEST_DISCORD_SETUP_FAILURE:-0}" != 1 ]
    : >"$MCP_TEST_STATE/discord-profile-ready"
    printf '%s\n' "$*"
    ;;
  *' serve --profile agent-coordination '*)
    printf '%s\n' "$*"
    ;;
  *)
    exit 64
    ;;
esac
EOF
  chmod +x "$FAKE_BIN/discord-npx"
}

write_launchctl_mock() {
  cat >"$FAKE_BIN/launchctl" <<'EOF'
#!/bin/sh
set -eu

case " $* " in
  *test-credential*) exit 65 ;;
esac
printf '%s\n' "$*" >>"$MCP_TEST_STATE/launchctl-calls.log"

state_file_for_label() {
  case "$1" in
    com.nfma.discord-wake-relay)
      printf '%s\n' "$MCP_TEST_STATE/wake-relay-loaded"
      ;;
    *) exit 64 ;;
  esac
}

definition_file_for_label() {
  case "$1" in
    com.nfma.discord-wake-relay)
      printf '%s\n' "$MCP_TEST_STATE/wake-relay-definition.plist"
      ;;
    *) exit 64 ;;
  esac
}

definition_path_file_for_label() {
  case "$1" in
    com.nfma.discord-wake-relay)
      printf '%s\n' "$MCP_TEST_STATE/wake-relay-definition-path"
      ;;
    *) exit 64 ;;
  esac
}

case "${1:-}" in
  print)
    label=${2##*/}
    state_file=$(state_file_for_label "$label")
    [ -e "$state_file" ]
    definition_file=$(definition_file_for_label "$label")
    definition_path_file=$(definition_path_file_for_label "$label")
    program=$(/usr/bin/plutil -extract ProgramArguments.0 raw -o - "$definition_file")
    stdout_path=$(/usr/bin/plutil -extract StandardOutPath raw -o - "$definition_file")
    stderr_path=$(/usr/bin/plutil -extract StandardErrorPath raw -o - "$definition_file")
    printf 'path = %s\n' "$(cat "$definition_path_file")"
    printf 'program = %s\n' "$program"
    printf 'stdout path = %s\n' "$stdout_path"
    printf 'stderr path = %s\n' "$stderr_path"
    ;;
  bootstrap)
    [ "${MCP_TEST_LAUNCHCTL_FAILURE:-}" != bootstrap ]
    plist=${3:-}
    label=$(basename "$plist" .plist)
    state_file=$(state_file_for_label "$label")
    definition_file=$(definition_file_for_label "$label")
    definition_path_file=$(definition_path_file_for_label "$label")
    cp "$plist" "$definition_file"
    printf '%s\n' "$plist" >"$definition_path_file"
    : >"$state_file"
    ;;
  bootout)
    [ "${MCP_TEST_LAUNCHCTL_FAILURE:-}" != bootout ]
    label=${2##*/}
    state_file=$(state_file_for_label "$label")
    definition_file=$(definition_file_for_label "$label")
    definition_path_file=$(definition_path_file_for_label "$label")
    rm -f "$state_file" "$definition_file" "$definition_path_file"
    ;;
  kickstart)
    [ "${MCP_TEST_LAUNCHCTL_FAILURE:-}" != kickstart ]
    label=${3##*/}
    state_file=$(state_file_for_label "$label")
    [ -e "$state_file" ]
    ;;
  *) exit 64 ;;
esac
EOF
  chmod +x "$FAKE_BIN/launchctl"
}

write_uname_mock() {
  cat >"$FAKE_BIN/uname" <<'EOF'
#!/bin/sh
set -eu
[ "${1:-}" = -s ]
printf '%s\n' "${MCP_TEST_UNAME:-Darwin}"
EOF
  chmod +x "$FAKE_BIN/uname"
}

write_plutil_mock() {
  cat >"$FAKE_BIN/plutil" <<'EOF'
#!/bin/sh
set -eu
[ "${1:-}" = -lint ]
[ -r "${2:-}" ]
grep -F '<plist version="1.0">' "$2" >/dev/null
grep -F '</plist>' "$2" >/dev/null
EOF
  chmod +x "$FAKE_BIN/plutil"
}

prepare_wake_relay_preflight() {
  mkdir -p "$INSTALL_HOME/.local/bin"
  ln -s "$REPO_ROOT/mcp/bin/discord-mcp-keychain" \
    "$INSTALL_HOME/.local/bin/discord-mcp-keychain"
  cat >"$INSTALL_HOME/.local/bin/traycer" <<'EOF'
#!/bin/sh
set -eu
[ "${1:-}" = --version ]
[ "${MCP_TEST_TRAYCER_FAILURE:-0}" != 1 ]
printf '%s\n' 'traycer test'
EOF
  chmod +x "$INSTALL_HOME/.local/bin/traycer"
}

write_github_server_mock() {
  cat >"$FAKE_BIN/github-mcp-server" <<'EOF'
#!/bin/sh
set -eu
[ -n "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]
printf '%s\n' "$*"
EOF
  chmod +x "$FAKE_BIN/github-mcp-server"
}

write_codex_mock() {
  cat >"$FAKE_BIN/codex" <<'EOF'
#!/bin/bash
set -eu

if [ "$1 $2 $3" = 'plugin marketplace list' ]; then
  if [ "${MCP_TEST_CODEX_MARKETPLACE:-present}" = missing ]; then
    printf '%s\n' '{"marketplaces":[]}'
  else
    printf '%s\n' '{"marketplaces":[{"name":"claude-plugins-official"}]}'
  fi
elif [ "$1 $2" = 'plugin list' ]; then
  aikido_enabled=true
  if [ "${MCP_TEST_CODEX_PLUGIN_MODE:-}" = disabled-aikido ]; then
    aikido_enabled=false
  fi
  printf '{"installed":['
  printf '{"pluginId":"aikido@claude-plugins-official","enabled":%s},' "$aikido_enabled"
  printf '%s' '{"pluginId":"chrome-devtools-mcp@claude-plugins-official","enabled":true},{"pluginId":"context7@claude-plugins-official","enabled":true},{"pluginId":"playwright@claude-plugins-official","enabled":true},{"pluginId":"github@claude-plugins-official","enabled":true}]}'
  printf '\n'
elif [ "$1 $2" = 'mcp get' ]; then
  state_file="$MCP_TEST_STATE/codex-mcp-$3.json"
  [ -r "$state_file" ] || exit 1
  cat "$state_file"
elif [ "$1 $2" = 'mcp remove' ]; then
  printf '%s\n' "$*" >> "$MCP_TEST_STATE/codex-calls.log"
  rm -f "$MCP_TEST_STATE/codex-mcp-$3.json"
elif [ "$1 $2" = 'mcp add' ]; then
  printf '%s\n' "$*" >> "$MCP_TEST_STATE/codex-calls.log"
  server_name=$3
  if [ "${MCP_TEST_CODEX_ADD_NOOP:-}" != "$server_name" ]; then
    shift 4
    server_command=$1
    shift
    if [ "$#" -eq 0 ]; then
      server_args='[]'
    else
      server_args=$(printf '%s\n' "$@" | jq -R . | jq -s -c .)
    fi
    jq -n --arg command "$server_command" --argjson args "$server_args" \
      '{transport:{type:"stdio",command:$command,args:$args}}' \
      > "$MCP_TEST_STATE/codex-mcp-$server_name.json"
  fi
else
  printf '%s\n' "$*" >> "$MCP_TEST_STATE/codex-calls.log"
fi
EOF
  chmod +x "$FAKE_BIN/codex"
}

write_claude_mock() {
  cat >"$FAKE_BIN/claude" <<'EOF'
#!/bin/bash
set -eu

if [ "$1 $2 $3" = 'plugin marketplace list' ]; then
  if [ "${MCP_TEST_CLAUDE_MARKETPLACE:-present}" = missing ]; then
    printf '%s\n' '[]'
  else
    printf '%s\n' '[{"name":"claude-plugins-official"}]'
  fi
elif [ "$1 $2" = 'plugin list' ]; then
  context7_enabled=true
  semgrep_entry=',{"id":"semgrep@claude-plugins-official","enabled":true}'
  case "${MCP_TEST_CLAUDE_PLUGIN_MODE:-}" in
    disabled-context7) context7_enabled=false ;;
    missing-semgrep) semgrep_entry= ;;
  esac
  printf '['
  printf '%s' '{"id":"aikido@claude-plugins-official","enabled":true},{"id":"chrome-devtools-mcp@claude-plugins-official","enabled":true},'
  printf '{"id":"context7@claude-plugins-official","enabled":%s},' "$context7_enabled"
  printf '%s' '{"id":"playwright@claude-plugins-official","enabled":true}'
  printf '%s' "$semgrep_entry"
  printf '%s\n' ',{"id":"github@claude-plugins-official","enabled":true}]'
else
  printf '%s\n' "$*" >> "$MCP_TEST_STATE/claude-calls.log"
fi
EOF
  chmod +x "$FAKE_BIN/claude"
}

write_serena_mock() {
  printf '#!/bin/sh\nexit 0\n' > \
    "$INSTALL_HOME/.local/share/uv/tools/serena-agent/bin/serena"
  chmod +x "$INSTALL_HOME/.local/share/uv/tools/serena-agent/bin/serena"
}
