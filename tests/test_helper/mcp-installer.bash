setup_installer_test() {
  REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$BATS_TEST_FILENAME")/.." && pwd)
  INSTALLER="$REPO_ROOT/scripts/install-mcps.sh"
  WRAPPER="$REPO_ROOT/mcp/bin/github-mcp-keychain"
  VIVALDI_WRAPPER="$REPO_ROOT/mcp/bin/chrome-devtools-vivaldi"
  TEST_ROOT="$BATS_TEST_TMPDIR/sandbox"
  FAKE_BIN="$TEST_ROOT/bin"
  INSTALL_HOME="$TEST_ROOT/home"
  MCP_TEST_STATE="$TEST_ROOT/state"

  mkdir -p \
    "$FAKE_BIN" \
    "$INSTALL_HOME/.serena" \
    "$INSTALL_HOME/.local/share/uv/tools/serena-agent/bin" \
    "$MCP_TEST_STATE"
  : > "$INSTALL_HOME/.serena/serena_config.yml"

  write_success_command cursor
  write_success_command agy
  write_node_check
  write_security_mock
  write_github_server_mock
  write_codex_mock
  write_claude_mock
  write_serena_mock

  export PATH="$FAKE_BIN:$PATH"
  export MCP_INSTALL_HOME="$INSTALL_HOME"
  export MCP_SECURITY_BIN="$FAKE_BIN/security"
  export MCP_NODE_CHECK_BIN="$FAKE_BIN/node-check"
  export MCP_TEST_STATE

  unset MCP_CODEX_FRAGMENT
  unset MCP_CLAUDE_FRAGMENT
  unset MCP_TEST_CLAUDE_MARKETPLACE
  unset MCP_TEST_CLAUDE_PLUGIN_MODE
  unset MCP_TEST_CODEX_MARKETPLACE
  unset MCP_TEST_CODEX_PLUGIN_MODE
  unset MCP_TEST_NODE_FAILURE
  unset MCP_TEST_SECURITY_FAILURE
}

write_success_command() {
  command_name=$1
  printf '#!/bin/sh\nexit 0\n' > "$FAKE_BIN/$command_name"
  chmod +x "$FAKE_BIN/$command_name"
}

write_node_check() {
  cat > "$FAKE_BIN/node-check" <<'EOF'
#!/bin/sh
set -eu
[ "${MCP_TEST_NODE_FAILURE:-0}" != 1 ]
printf 'v24.0.0\n'
EOF
  chmod +x "$FAKE_BIN/node-check"
}

write_security_mock() {
  cat > "$FAKE_BIN/security" <<'EOF'
#!/bin/sh
set -eu
case " $* " in
  *' -s HF_TOKEN '*)
    [ "${MCP_TEST_SECURITY_FAILURE:-}" != hf ] || exit 1
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

write_github_server_mock() {
  cat > "$FAKE_BIN/github-mcp-server" <<'EOF'
#!/bin/sh
set -eu
[ -n "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]
printf '%s\n' "$*"
EOF
  chmod +x "$FAKE_BIN/github-mcp-server"
}

write_codex_mock() {
  cat > "$FAKE_BIN/codex" <<'EOF'
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
else
  printf '%s\n' "$*" >> "$MCP_TEST_STATE/codex-calls.log"
fi
EOF
  chmod +x "$FAKE_BIN/codex"
}

write_claude_mock() {
  cat > "$FAKE_BIN/claude" <<'EOF'
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
