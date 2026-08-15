#!/bin/bash

set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
test_root=$(mktemp -d "${TMPDIR:-/tmp}/agent-skills-mcp-test.XXXXXX")

cleanup() {
  case "$test_root" in
    "${TMPDIR:-/tmp}"/agent-skills-mcp-test.*)
      /bin/rm -rf -- "$test_root"
      ;;
    *)
      printf 'Refusing to clean unexpected test path: %s\n' "$test_root" >&2
      ;;
  esac
}
trap cleanup EXIT

fail() {
  printf 'not ok - %s\n' "$*" >&2
  exit 1
}

assert_contains() {
  haystack=$1
  needle=$2
  case "$haystack" in
    *"$needle"*) ;;
    *) fail "expected output to contain: $needle" ;;
  esac
}

fake_bin="$test_root/bin"
install_home="$test_root/home"
/bin/mkdir -p \
  "$fake_bin" \
  "$install_home/.serena" \
  "$install_home/.local/share/uv/tools/serena-agent/bin"
: > "$install_home/.serena/serena_config.yml"

make_success_command() {
  command_name=$1
  command_path="$fake_bin/$command_name"
  printf '#!/bin/sh\nexit 0\n' > "$command_path"
  /bin/chmod +x "$command_path"
}

for command_name in cursor node-check; do
  make_success_command "$command_name"
done
printf '#!/bin/sh\nprintf "test-credential\\n"\n' > "$fake_bin/security"
/bin/chmod +x "$fake_bin/security"
cat > "$fake_bin/github-mcp-server" <<'EOF'
#!/bin/sh
set -eu
[ -n "${GITHUB_PERSONAL_ACCESS_TOKEN:-}" ]
printf '%s\n' "$*"
EOF
/bin/chmod +x "$fake_bin/github-mcp-server"
printf '#!/bin/sh\nexit 0\n' > \
  "$install_home/.local/share/uv/tools/serena-agent/bin/serena"
/bin/chmod +x "$install_home/.local/share/uv/tools/serena-agent/bin/serena"

cat > "$fake_bin/codex" <<'EOF'
#!/bin/bash
set -eu
if [ "$1 $2 $3" = 'plugin marketplace list' ]; then
  printf '%s\n' '{"marketplaces":[{"name":"claude-plugins-official"}]}'
elif [ "$1 $2" = 'plugin list' ]; then
  printf '%s\n' '{"installed":[{"pluginId":"aikido@claude-plugins-official","enabled":true},{"pluginId":"chrome-devtools-mcp@claude-plugins-official","enabled":true},{"pluginId":"context7@claude-plugins-official","enabled":true},{"pluginId":"playwright@claude-plugins-official","enabled":true},{"pluginId":"github@claude-plugins-official","enabled":true}]}'
elif [ "$1 $2" = 'mcp get' ]; then
  exit 1
else
  exit 0
fi
EOF
/bin/chmod +x "$fake_bin/codex"

cat > "$fake_bin/claude" <<'EOF'
#!/bin/bash
set -eu
if [ "$1 $2 $3" = 'plugin marketplace list' ]; then
  printf '%s\n' '[{"name":"claude-plugins-official"}]'
elif [ "$1 $2" = 'plugin list' ]; then
  printf '%s\n' '[{"id":"aikido@claude-plugins-official","enabled":true},{"id":"chrome-devtools-mcp@claude-plugins-official","enabled":true},{"id":"context7@claude-plugins-official","enabled":true},{"id":"playwright@claude-plugins-official","enabled":true},{"id":"semgrep@claude-plugins-official","enabled":true},{"id":"github@claude-plugins-official","enabled":true}]'
else
  exit 0
fi
EOF
/bin/chmod +x "$fake_bin/claude"

export PATH="$fake_bin:$PATH"
export MCP_INSTALL_HOME="$install_home"
export MCP_SECURITY_BIN="$fake_bin/security"
export MCP_NODE_CHECK_BIN="$fake_bin/node-check"

wrapper_output=$(GITHUB_MCP_SECURITY_BIN="$fake_bin/security" \
  MCP_BIN="$fake_bin/github-mcp-server" \
  "$repo_root/mcp/bin/github-mcp-keychain")
[ "$wrapper_output" = stdio ] || fail 'GitHub wrapper did not isolate and launch with its Keychain credential'

dry_run_output=$("$repo_root/scripts/install-mcps.sh" \
  --dry-run --harness codex --harness claude)
assert_contains "$dry_run_output" 'codex plugin remove github@claude-plugins-official'
assert_contains "$dry_run_output" 'claude plugin uninstall --scope user --yes github@claude-plugins-official'
assert_contains "$dry_run_output" "codex mcp add github -- $install_home/.local/bin/github-mcp-keychain stdio"
assert_contains "$dry_run_output" "claude mcp add --scope user github -- $install_home/.local/bin/github-mcp-keychain stdio"

invalid_fragment="$test_root/invalid-fragment.json"
cat > "$invalid_fragment" <<'EOF'
{
  "plugins": {"install": [], "remove": []},
  "mcpServers": {
    "unsafe": {"type": "stdio", "localCommand": "../unsafe", "args": []}
  }
}
EOF
if MCP_CODEX_FRAGMENT="$invalid_fragment" \
   "$repo_root/scripts/install-mcps.sh" --dry-run --harness cursor \
   >"$test_root/invalid.out" 2>&1; then
  fail 'malformed fragment was accepted'
fi
assert_contains "$(<"$test_root/invalid.out")" 'invalid harness MCP fragment'

first_install_output=$("$repo_root/scripts/install-mcps.sh" --harness cursor)
assert_contains "$first_install_output" "Linked $install_home/.cursor/mcp.json"
[ -L "$install_home/.cursor/mcp.json" ] || fail 'Cursor config was not linked'
[ "$(readlink "$install_home/.cursor/mcp.json")" = "$repo_root/mcp/cursor/mcp.json" ] ||
  fail 'Cursor config link target is incorrect'

second_install_output=$("$repo_root/scripts/install-mcps.sh" --harness cursor)
assert_contains "$second_install_output" 'MCP installation complete; no changes were necessary.'

printf 'ok - installer validates fragments, renders Codex/Claude changes, and installs idempotently\n'
