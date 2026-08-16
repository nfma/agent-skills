#!/usr/bin/env bats

load 'test_helper/mcp-installer.bash'

setup() {
  setup_installer_test
}

@test "prints help without running preflight" {
  run "$INSTALLER" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *'Usage: ./scripts/install-mcps.sh'* ]]
}

@test "rejects an unknown harness" {
  run "$INSTALLER" --harness unknown

  [ "$status" -eq 1 ]
  [[ "$output" == *'unknown harness: unknown'* ]]
}

@test "rejects an unsafe installation home" {
  run env MCP_INSTALL_HOME=relative "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'installation home must be an absolute path other than /'* ]]
}

@test "rejects an unsafe local command in a fragment" {
  fragment="$TEST_ROOT/unsafe-fragment.json"
  cat >"$fragment" <<'EOF'
{
  "plugins": {"install": [], "remove": []},
  "mcpServers": {
    "unsafe": {"type": "stdio", "localCommand": "../unsafe", "args": []}
  }
}
EOF

  run env MCP_CODEX_FRAGMENT="$fragment" \
    "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'invalid harness MCP fragment'* ]]
}

@test "rejects a fragment that no longer matches the baseline" {
  fragment="$TEST_ROOT/incomplete-fragment.json"
  jq 'del(.mcpServers.github)' \
    "$REPO_ROOT/mcp/codex/mcp-fragment.json" >"$fragment"

  run env MCP_CODEX_FRAGMENT="$fragment" \
    "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'Codex fragment does not match the baseline manifest'* ]]
}

@test "rejects an unpinned package in a fragment" {
  fragment="$TEST_ROOT/unpinned-fragment.json"
  jq '(.mcpServers["browser-tools"].args[1]) = "@agentdeskai/browser-tools-mcp@latest"' \
    "$REPO_ROOT/mcp/codex/mcp-fragment.json" >"$fragment"

  run env MCP_CODEX_FRAGMENT="$fragment" \
    "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'harness fragment is missing package pin'* ]]
}

@test "fails when the Node runtime check fails" {
  export MCP_TEST_NODE_FAILURE=1

  run "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'the NVM default Node runtime is unavailable'* ]]
}

@test "fails when the Hugging Face Keychain item is unavailable" {
  export MCP_TEST_SECURITY_FAILURE=hf

  run "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'Keychain item HF_TOKEN is missing'* ]]
}

@test "fails when the GitHub Keychain label is unavailable" {
  export MCP_TEST_SECURITY_FAILURE=github

  run "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'Keychain item labeled GITHUB_MCP_PAT is missing'* ]]
}

@test "dry run renders Codex and Claude reconciliation without mutation" {
  run "$INSTALLER" --dry-run --harness codex --harness claude

  [ "$status" -eq 0 ]
  [[ "$output" == *'codex plugin remove github@claude-plugins-official'* ]]
  [[ "$output" == *'claude plugin uninstall --scope user --yes github@claude-plugins-official'* ]]
  [[ "$output" == *"codex mcp add github -- $INSTALL_HOME/.local/bin/github-mcp-keychain stdio"* ]]
  [[ "$output" == *"claude mcp add --scope user github -- $INSTALL_HOME/.local/bin/github-mcp-keychain stdio"* ]]
  [ ! -e "$INSTALL_HOME/.local/bin/github-mcp-keychain" ]
  [ ! -e "$INSTALL_HOME/.agents/mcp-backups" ]
}

@test "installs Cursor links and backs up an existing config" {
  mkdir -p "$INSTALL_HOME/.cursor"
  printf 'existing-config\n' >"$INSTALL_HOME/.cursor/mcp.json"

  run "$INSTALLER" --harness cursor

  [ "$status" -eq 0 ]
  [ -L "$INSTALL_HOME/.cursor/mcp.json" ]
  [ "$(readlink "$INSTALL_HOME/.cursor/mcp.json")" = \
    "$REPO_ROOT/mcp/cursor/mcp.json" ]
  backup_files=("$INSTALL_HOME"/.agents/mcp-backups/*/cursor-mcp.json)
  [ "${#backup_files[@]}" -eq 1 ]
  [ "$(<"${backup_files[0]}")" = existing-config ]
}

@test "a second installation is idempotent" {
  run "$INSTALLER" --harness cursor
  [ "$status" -eq 0 ]

  run "$INSTALLER" --harness cursor

  [ "$status" -eq 0 ]
  [[ "$output" == *'MCP installation complete; no changes were necessary.'* ]]
}

@test "installs a missing Codex marketplace" {
  export MCP_TEST_CODEX_MARKETPLACE=missing

  run "$INSTALLER" --dry-run --harness codex

  [ "$status" -eq 0 ]
  [[ "$output" == *'codex plugin marketplace add anthropics/claude-plugins-official --json'* ]]
}

@test "fails when a required Codex plugin is disabled" {
  export MCP_TEST_CODEX_PLUGIN_MODE=disabled-aikido

  run "$INSTALLER" --dry-run --harness codex

  [ "$status" -eq 1 ]
  [[ "$output" == *'Codex plugin is installed but disabled: aikido@claude-plugins-official'* ]]
}

@test "installs a missing Claude plugin" {
  export MCP_TEST_CLAUDE_PLUGIN_MODE=missing-semgrep

  run "$INSTALLER" --dry-run --harness claude

  [ "$status" -eq 0 ]
  [[ "$output" == *'claude plugin install --scope user --yes semgrep@claude-plugins-official'* ]]
}

@test "keeps a matching Codex stdio MCP" {
  jq -n \
    --arg command "$INSTALL_HOME/.local/bin/npx" \
    '{transport:{type:"stdio",command:$command,args:["-y","@agentdeskai/browser-tools-mcp@1.2.0"]}}' \
    >"$MCP_TEST_STATE/codex-mcp-browser-tools.json"

  run "$INSTALLER" --dry-run --harness codex

  [ "$status" -eq 0 ]
  [[ "$output" == *'Codex MCP ready: browser-tools'* ]]
  [[ "$output" != *'codex mcp add browser-tools'* ]]
}

@test "replaces a mismatched Codex stdio MCP" {
  printf '%s\n' \
    '{"transport":{"type":"stdio","command":"/unexpected","args":[]}}' \
    >"$MCP_TEST_STATE/codex-mcp-browser-tools.json"

  run "$INSTALLER" --dry-run --harness codex

  [ "$status" -eq 0 ]
  [[ "$output" == *'codex mcp remove browser-tools'* ]]
  [[ "$output" == *'codex mcp add browser-tools'* ]]
}

@test "keeps a matching Claude stdio MCP" {
  jq -n \
    --arg command "$INSTALL_HOME/.local/bin/github-mcp-keychain" \
    '{mcpServers:{github:{type:"stdio",command:$command,args:["stdio"]}}}' \
    >"$INSTALL_HOME/.claude.json"

  run "$INSTALLER" --dry-run --harness claude

  [ "$status" -eq 0 ]
  [[ "$output" == *'Claude MCP ready: github'* ]]
  [[ "$output" != *'claude mcp add --scope user github'* ]]
}

@test "GitHub wrapper exposes the Keychain value only to its child" {
  run env \
    GITHUB_MCP_SECURITY_BIN="$FAKE_BIN/security" \
    MCP_BIN="$FAKE_BIN/github-mcp-server" \
    "$WRAPPER"

  [ "$status" -eq 0 ]
  [ "$output" = stdio ]
  [[ "$output" != *'test-credential'* ]]
}

@test "GitHub wrapper fails closed when the Keychain item is missing" {
  export MCP_TEST_SECURITY_FAILURE=github

  run env \
    GITHUB_MCP_SECURITY_BIN="$FAKE_BIN/security" \
    MCP_BIN="$FAKE_BIN/github-mcp-server" \
    "$WRAPPER"

  [ "$status" -eq 1 ]
  [[ "$output" == *'Keychain item labeled GITHUB_MCP_PAT is missing'* ]]
}

@test "installs a missing Claude marketplace" {
  export MCP_TEST_CLAUDE_MARKETPLACE=missing

  run "$INSTALLER" --dry-run --harness claude

  [ "$status" -eq 0 ]
  [[ "$output" == *'claude plugin marketplace add anthropics/claude-plugins-official --scope user'* ]]
}

@test "enables a disabled Claude plugin" {
  export MCP_TEST_CLAUDE_PLUGIN_MODE=disabled-context7

  run "$INSTALLER" --dry-run --harness claude

  [ "$status" -eq 0 ]
  [[ "$output" == *'claude plugin enable --scope user context7@claude-plugins-official'* ]]
}

@test "keeps a matching Claude HTTP MCP" {
  jq -n \
    '{mcpServers:{hf:{type:"http",url:"https://huggingface.co/mcp"}}}' \
    >"$INSTALL_HOME/.claude.json"

  run "$INSTALLER" --dry-run --harness claude

  [ "$status" -eq 0 ]
  [[ "$output" == *'Claude MCP ready: hf'* ]]
  [[ "$output" != *'claude mcp add --scope user --transport http hf'* ]]
}

@test "replaces a mismatched Claude HTTP MCP" {
  jq -n \
    '{mcpServers:{hf:{type:"http",url:"https://example.invalid/mcp"}}}' \
    >"$INSTALL_HOME/.claude.json"

  run "$INSTALLER" --dry-run --harness claude

  [ "$status" -eq 0 ]
  [[ "$output" == *'claude mcp remove --scope user hf'* ]]
  [[ "$output" == *'claude mcp add --scope user --transport http hf https://huggingface.co/mcp'* ]]
}

@test "installs the Antigravity configuration link" {
  run "$INSTALLER" --harness antigravity

  [ "$status" -eq 0 ]
  [ -L "$INSTALL_HOME/.gemini/config/mcp_config.json" ]
  [ "$(readlink "$INSTALL_HOME/.gemini/config/mcp_config.json")" = \
    "$REPO_ROOT/mcp/antigravity/mcp_config.json" ]
}

@test "refuses to overwrite an existing backup" {
  cat >"$FAKE_BIN/date" <<'EOF'
#!/bin/sh
printf '20000101-000000\n'
EOF
  chmod +x "$FAKE_BIN/date"
  mkdir -p \
    "$INSTALL_HOME/.local/bin" \
    "$INSTALL_HOME/.agents/mcp-backups/20000101-000000"
  printf 'existing-wrapper\n' >"$INSTALL_HOME/.local/bin/aikido-mcp-isolated-home.cjs"
  printf 'existing-backup\n' > \
    "$INSTALL_HOME/.agents/mcp-backups/20000101-000000/local-bin-aikido-mcp-isolated-home.cjs"

  run "$INSTALLER" --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'backup collision at'* ]]
  [ ! -L "$INSTALL_HOME/.local/bin/aikido-mcp-isolated-home.cjs" ]
}
