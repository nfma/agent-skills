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

@test "rejects overridesPlugin outside a Codex stdio entry" {
  fragment="$TEST_ROOT/invalid-claude-override.json"
  jq '.mcpServers["browser-tools"].overridesPlugin = true' \
    "$REPO_ROOT/mcp/claude/mcp-fragment.json" >"$fragment"

  run env MCP_CLAUDE_FRAGMENT="$fragment" \
    "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'invalid harness MCP fragment'* ]]
}

@test "rejects a non-positive Codex startup timeout" {
  fragment="$TEST_ROOT/invalid-codex-timeout.json"
  jq '.mcpServers["chrome-devtools"].startupTimeoutSec = 0' \
    "$REPO_ROOT/mcp/codex/mcp-fragment.json" >"$fragment"

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

@test "accepts the minimum Discord MCP Node runtime" {
  export MCP_TEST_NODE_VERSION=v22.12.0

  run "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 0 ]
}

@test "rejects a Node runtime below the Discord MCP minimum" {
  export MCP_TEST_NODE_VERSION=v22.11.0

  run "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'@discord-mcp/cli@0.23.0 requires Node 22.12 or newer'* ]]
  [[ "$output" == *'the NVM default reports v22.11.0'* ]]
  [[ "$output" == *'Set a supported NVM default and rerun.'* ]]
}

@test "fails when the Hugging Face Keychain item is unavailable" {
  export MCP_TEST_SECURITY_FAILURE=hf

  run "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'Keychain item HF_TOKEN is missing'* ]]
}

@test "fails when the Discord Keychain item is unavailable" {
  export MCP_TEST_SECURITY_FAILURE=discord

  run "$INSTALLER" --dry-run --harness cursor

  [ "$status" -eq 1 ]
  [[ "$output" == *'Keychain item DISCORD_MCP_TOKEN is missing'* ]]
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
  [[ "$output" == *"codex mcp add chrome-devtools -- $INSTALL_HOME/.local/bin/chrome-devtools-vivaldi"* ]]
  [[ "$output" == *"update-codex-mcp-config.cjs $INSTALL_HOME/.codex/config.toml chrome-devtools 20"* ]]
  [[ "$output" == *"claude mcp add --scope user github -- $INSTALL_HOME/.local/bin/github-mcp-keychain stdio"* ]]
  [[ "$output" == *"codex mcp add discord -- $INSTALL_HOME/.local/bin/discord-mcp-keychain"* ]]
  [[ "$output" == *"claude mcp add --scope user discord -- $INSTALL_HOME/.local/bin/discord-mcp-keychain"* ]]
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

@test "overlays the Chrome DevTools plugin without removing its server" {
  printf '%s\n' \
    '{"transport":{"type":"stdio","command":"npx","args":["chrome-devtools-mcp@1.7.0"]}}' \
    >"$MCP_TEST_STATE/codex-mcp-chrome-devtools.json"

  run "$INSTALLER" --dry-run --harness codex

  [ "$status" -eq 0 ]
  [[ "$output" == *'Codex plugin ready: chrome-devtools-mcp@claude-plugins-official'* ]]
  [[ "$output" != *'codex mcp remove chrome-devtools'* ]]
  [[ "$output" == *"codex mcp add chrome-devtools -- $INSTALL_HOME/.local/bin/chrome-devtools-vivaldi"* ]]
}

@test "keeps a matching Vivaldi override with its startup timeout" {
  jq -n \
    --arg command "$INSTALL_HOME/.local/bin/chrome-devtools-vivaldi" \
    '{transport:{type:"stdio",command:$command,args:[]},startup_timeout_sec:20}' \
    >"$MCP_TEST_STATE/codex-mcp-chrome-devtools.json"

  run "$INSTALLER" --dry-run --harness codex

  [ "$status" -eq 0 ]
  [[ "$output" == *'Codex MCP ready: chrome-devtools'* ]]
  [[ "$output" != *'codex mcp add chrome-devtools'* ]]
}

@test "fails verification when a plugin server masks a missing override" {
  mkdir -p "$INSTALL_HOME/.codex"
  cat >"$INSTALL_HOME/.codex/config.toml" <<'EOF'
[mcp_servers.chrome-devtools]
command = "npx"
args = ["chrome-devtools-mcp@1.7.0"]
EOF
  printf '%s\n' \
    '{"transport":{"type":"stdio","command":"npx","args":["chrome-devtools-mcp@1.7.0"]}}' \
    >"$MCP_TEST_STATE/codex-mcp-chrome-devtools.json"
  export MCP_TEST_CODEX_ADD_NOOP=chrome-devtools

  run "$INSTALLER" --harness codex

  [ "$status" -eq 1 ]
  [[ "$output" == *'Codex MCP verification failed: chrome-devtools'* ]]
}

@test "Vivaldi wrapper launches the pinned MCP with a persistent profile" {
  wrapper_dir="$TEST_ROOT/vivaldi-wrapper"
  mkdir -p "$wrapper_dir"
  cp "$VIVALDI_WRAPPER" "$wrapper_dir/chrome-devtools-vivaldi"
  cat >"$wrapper_dir/npx" <<'EOF'
#!/bin/sh
printf '%s\n' "$*"
EOF
  chmod +x "$wrapper_dir/chrome-devtools-vivaldi" "$wrapper_dir/npx"
  write_success_command vivaldi

  run env HOME="$INSTALL_HOME" \
    CHROME_DEVTOOLS_VIVALDI_BIN="$FAKE_BIN/vivaldi" \
    "$wrapper_dir/chrome-devtools-vivaldi"

  [ "$status" -eq 0 ]
  [ "$output" = "-y chrome-devtools-mcp@1.7.0 --categoryExtensions --executablePath $FAKE_BIN/vivaldi --userDataDir $INSTALL_HOME/.cache/chrome-devtools-mcp/vivaldi-profile" ]
  [ -d "$INSTALL_HOME/.cache/chrome-devtools-mcp/vivaldi-profile" ]
}

@test "Vivaldi wrapper fails closed when the browser is unavailable" {
  wrapper_dir="$TEST_ROOT/vivaldi-wrapper"
  mkdir -p "$wrapper_dir"
  cp "$VIVALDI_WRAPPER" "$wrapper_dir/chrome-devtools-vivaldi"
  write_success_command npx
  cp "$FAKE_BIN/npx" "$wrapper_dir/npx"
  chmod +x "$wrapper_dir/chrome-devtools-vivaldi" "$wrapper_dir/npx"

  run env HOME="$INSTALL_HOME" \
    CHROME_DEVTOOLS_VIVALDI_BIN="$FAKE_BIN/missing-vivaldi" \
    "$wrapper_dir/chrome-devtools-vivaldi"

  [ "$status" -eq 69 ]
  [[ "$output" == *'Vivaldi is unavailable'* ]]
}

@test "Vivaldi wrapper fails closed when HOME is unavailable" {
  run env -u HOME "$VIVALDI_WRAPPER"

  [ "$status" -eq 64 ]
  [[ "$output" == *'HOME is unavailable'* ]]
}

@test "Vivaldi wrapper fails closed when the npx launcher is unavailable" {
  wrapper_dir="$TEST_ROOT/vivaldi-wrapper"
  mkdir -p "$wrapper_dir"
  cp "$VIVALDI_WRAPPER" "$wrapper_dir/chrome-devtools-vivaldi"
  chmod +x "$wrapper_dir/chrome-devtools-vivaldi"
  write_success_command vivaldi

  run env HOME="$INSTALL_HOME" \
    CHROME_DEVTOOLS_VIVALDI_BIN="$FAKE_BIN/vivaldi" \
    "$wrapper_dir/chrome-devtools-vivaldi"

  [ "$status" -eq 69 ]
  [[ "$output" == *'tracked npx launcher is unavailable'* ]]
}

@test "Vivaldi wrapper rejects a relative profile path" {
  wrapper_dir="$TEST_ROOT/vivaldi-wrapper"
  mkdir -p "$wrapper_dir"
  cp "$VIVALDI_WRAPPER" "$wrapper_dir/chrome-devtools-vivaldi"
  write_success_command npx
  cp "$FAKE_BIN/npx" "$wrapper_dir/npx"
  chmod +x "$wrapper_dir/chrome-devtools-vivaldi" "$wrapper_dir/npx"
  write_success_command vivaldi

  run env HOME="$INSTALL_HOME" \
    CHROME_DEVTOOLS_VIVALDI_BIN="$FAKE_BIN/vivaldi" \
    CHROME_DEVTOOLS_VIVALDI_PROFILE_DIR=relative/profile \
    "$wrapper_dir/chrome-devtools-vivaldi"

  [ "$status" -eq 64 ]
  [[ "$output" == *'profile path must be absolute'* ]]
}

@test "Vivaldi wrapper refuses the root directory as a profile" {
  wrapper_dir="$TEST_ROOT/vivaldi-wrapper"
  mkdir -p "$wrapper_dir"
  cp "$VIVALDI_WRAPPER" "$wrapper_dir/chrome-devtools-vivaldi"
  write_success_command npx
  cp "$FAKE_BIN/npx" "$wrapper_dir/npx"
  chmod +x "$wrapper_dir/chrome-devtools-vivaldi" "$wrapper_dir/npx"
  write_success_command vivaldi

  run env HOME="$INSTALL_HOME" \
    CHROME_DEVTOOLS_VIVALDI_BIN="$FAKE_BIN/vivaldi" \
    CHROME_DEVTOOLS_VIVALDI_PROFILE_DIR=/ \
    "$wrapper_dir/chrome-devtools-vivaldi"

  [ "$status" -eq 64 ]
  [[ "$output" == *'refusing to use / as the profile path'* ]]
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

@test "Discord wrapper exposes the Keychain value only to its pinned child" {
  run "$DISCORD_WRAPPER"

  [ "$status" -eq 0 ]
  [ "$output" = '-y @discord-mcp/cli@0.23.0 serve --profile agent-coordination' ]
  [[ "$output" != *'test-credential'* ]]
}

@test "Discord wrapper fails closed when the Keychain item is missing" {
  export MCP_TEST_SECURITY_FAILURE=discord

  run "$DISCORD_WRAPPER"

  [ "$status" -eq 1 ]
  [[ "$output" == *'Keychain item DISCORD_MCP_TOKEN is missing'* ]]
}

@test "Discord wrapper fails closed with a profile setup instruction" {
  export MCP_TEST_DISCORD_PROFILE_MISSING=1

  run "$DISCORD_WRAPPER"

  [ "$status" -eq 1 ]
  [[ "$output" == *'profile agent-coordination is missing'* ]]
  [[ "$output" == *'--setup-discord'* ]]
  [[ "$output" != *'test-credential'* ]]
}

@test "Discord setup creates a non-secret pinned profile" {
  export MCP_TEST_DISCORD_PROFILE_MISSING=1

  run "$DISCORD_WRAPPER" setup

  [ "$status" -eq 0 ]
  [[ "$output" == *'-y @discord-mcp/cli@0.23.0 setup --profile agent-coordination --client generic --tool-surface progressive'* ]]
  [[ "$output" != *'test-credential'* ]]
  [ -e "$MCP_TEST_STATE/discord-profile-ready" ]
}

@test "installer runs the guided Discord profile setup after linking wrappers" {
  export MCP_TEST_DISCORD_PROFILE_MISSING=1

  run "$INSTALLER" --setup-discord --harness cursor

  [ "$status" -eq 0 ]
  [[ "$output" == *'Ensuring the non-secret Discord MCP profile is configured'* ]]
  [[ "$output" == *'-y @discord-mcp/cli@0.23.0 setup --profile agent-coordination --client generic --tool-surface progressive'* ]]
  [ -e "$MCP_TEST_STATE/discord-profile-ready" ]
}

@test "Discord extends every baseline" {
  jq -e '.npmPackages.discord == "@discord-mcp/cli@0.23.0"' \
    "$REPO_ROOT/mcp/manifest.json" >/dev/null
  jq -e '.credentials.discord == {source:"macos-keychain",service:"DISCORD_MCP_TOKEN",account:"current-user",environmentVariable:"DISCORD_TOKEN"}' \
    "$REPO_ROOT/mcp/manifest.json" >/dev/null
  jq -e '.mcpServers.discord == {type:"stdio",localCommand:"discord-mcp-keychain",args:[]}' \
    "$REPO_ROOT/mcp/codex/mcp-fragment.json" >/dev/null
  jq -e '.mcpServers.discord == {type:"stdio",localCommand:"discord-mcp-keychain",args:[]}' \
    "$REPO_ROOT/mcp/claude/mcp-fragment.json" >/dev/null
  jq -e '.mcpServers.discord == {command:"/bin/sh",args:["-c","exec \"$HOME/.local/bin/discord-mcp-keychain\""]}' \
    "$REPO_ROOT/mcp/cursor/mcp.json" >/dev/null
  jq -e '.mcpServers.discord == {command:"/bin/sh",args:["-c","exec \"$HOME/.local/bin/discord-mcp-keychain\""]}' \
    "$REPO_ROOT/mcp/antigravity/mcp_config.json" >/dev/null
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

@test "wake relay setup is explicit and does not run during baseline installation" {
  run "$INSTALLER" --harness cursor

  [ "$status" -eq 0 ]
  [ ! -e "$WAKE_RELAY_PLIST" ]
  [ ! -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  [ ! -e "$MCP_TEST_STATE/launchctl-calls.log" ]
}

@test "wake relay setup rejects non-macOS hosts without mutation" {
  prepare_wake_relay_preflight
  export MCP_TEST_UNAME=Linux

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'supported only on macOS'* ]]
  [ ! -e "$WAKE_RELAY_PLIST" ]
  [ ! -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
}

@test "wake relay setup fails when launchctl is unavailable" {
  prepare_wake_relay_preflight

  run env MCP_LAUNCHCTL_BIN="$FAKE_BIN/missing-launchctl" \
    "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'launchctl is unavailable'* ]]
  [ ! -e "$WAKE_RELAY_PLIST" ]
}

@test "wake relay setup requires the tracked installed Discord wrapper" {
  prepare_wake_relay_preflight
  rm "$INSTALL_HOME/.local/bin/discord-mcp-keychain"

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'installed Discord MCP wrapper is missing'* ]]
  [ ! -e "$WAKE_RELAY_PLIST" ]
}

@test "wake relay setup requires a working Traycer CLI" {
  prepare_wake_relay_preflight
  export MCP_TEST_TRAYCER_FAILURE=1

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'wake relay launcher validation failed'* ]]
  [ ! -e "$WAKE_RELAY_PLIST" ]
}

@test "wake relay setup requires the Discord MCP Node runtime" {
  prepare_wake_relay_preflight
  export MCP_TEST_NODE_FAILURE=1

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'NVM default Node runtime is unavailable'* ]]
  [ ! -e "$WAKE_RELAY_PLIST" ]
}

@test "wake relay setup fails closed when the Discord profile is missing" {
  prepare_wake_relay_preflight
  export MCP_TEST_DISCORD_PROFILE_MISSING=1

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'profile agent-coordination is missing'* ]]
  [[ "$output" == *'--setup-discord first'* ]]
  [[ "$output" != *'test-credential'* ]]
  [ ! -e "$WAKE_RELAY_PLIST" ]
}

@test "wake relay setup fails closed when its Keychain item is missing" {
  prepare_wake_relay_preflight
  export MCP_TEST_SECURITY_FAILURE=discord

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'Keychain item DISCORD_MCP_TOKEN is missing'* ]]
  [[ "$output" != *'test-credential'* ]]
  [ ! -e "$WAKE_RELAY_PLIST" ]
}

@test "wake relay setup refuses an unrecognized plist before mutation" {
  prepare_wake_relay_preflight
  mkdir -p "$(dirname "$WAKE_RELAY_PLIST")"
  printf '%s\n' '<plist><dict><string>unrelated.service</string></dict></plist>' \
    >"$WAKE_RELAY_PLIST"

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing to replace an unrecognized LaunchAgent plist'* ]]
  [ ! -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  [ ! -e "$WAKE_RELAY_LOG_DIR" ]
}

@test "wake relay setup dry run refuses an unrecognized launcher before mutation" {
  prepare_wake_relay_preflight
  printf '%s\n' 'unrelated launcher' \
    >"$INSTALL_HOME/.local/bin/discord-wake-relay"

  run "$INSTALLER" --dry-run --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing to replace an unrecognized wake relay launcher'* ]]
  [ "$(<"$INSTALL_HOME/.local/bin/discord-wake-relay")" = 'unrelated launcher' ]
  [ ! -e "$INSTALL_HOME/.agents/mcp-backups" ]
  [ ! -e "$WAKE_RELAY_PLIST" ]
  [ ! -e "$WAKE_RELAY_LOG_DIR" ]
}

@test "wake relay setup refuses an unrecognized launcher before mutation" {
  prepare_wake_relay_preflight
  printf '%s\n' 'unrelated launcher' \
    >"$INSTALL_HOME/.local/bin/discord-wake-relay"

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'refusing to replace an unrecognized wake relay launcher'* ]]
  [ "$(<"$INSTALL_HOME/.local/bin/discord-wake-relay")" = 'unrelated launcher' ]
  [ ! -e "$INSTALL_HOME/.agents/mcp-backups" ]
  [ ! -e "$WAKE_RELAY_PLIST" ]
  [ ! -e "$WAKE_RELAY_LOG_DIR" ]
}

@test "wake relay setup dry run explains every action without mutation" {
  prepare_wake_relay_preflight

  run "$INSTALLER" --dry-run --setup-wake-relay

  [ "$status" -eq 0 ]
  [[ "$output" == *'Would link'*'discord-wake-relay'* ]]
  [[ "$output" == *'Would create LaunchAgents directory'* ]]
  [[ "$output" != *'wake relay log'* ]]
  [[ "$output" == *'Would reconcile wake relay LaunchAgent plist'* ]]
  [[ "$output" == *'Would bootstrap wake relay service in gui/'* ]]
  [[ "$output" == *'no files or services were changed'* ]]
  [[ "$output" != *'test-credential'* ]]
  [ ! -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  [ ! -e "$WAKE_RELAY_PLIST" ]
  [ ! -e "$WAKE_RELAY_LOG_DIR" ]
  [ ! -e "$MCP_TEST_STATE/wake-relay-loaded" ]
}

@test "wake relay setup installs a fixed no-secret user LaunchAgent" {
  prepare_wake_relay_preflight
  relay_uid=$(/usr/bin/id -u)

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 0 ]
  [ -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  [ "$(readlink "$INSTALL_HOME/.local/bin/discord-wake-relay")" = \
    "$WAKE_RELAY_LAUNCHER" ]
  [ -f "$WAKE_RELAY_PLIST" ]
  [ "$(/usr/bin/plutil -extract Label raw -o - "$WAKE_RELAY_PLIST")" = \
    com.nfma.discord-wake-relay ]
  [ "$(/usr/bin/plutil -extract RunAtLoad raw -o - "$WAKE_RELAY_PLIST")" = true ]
  [ "$(/usr/bin/plutil -extract KeepAlive raw -o - "$WAKE_RELAY_PLIST")" = true ]
  [ "$(/usr/bin/plutil -extract ThrottleInterval raw -o - "$WAKE_RELAY_PLIST")" = 30 ]
  /usr/bin/plutil -extract ProgramArguments json -o - "$WAKE_RELAY_PLIST" \
    | jq -e --arg launcher "$INSTALL_HOME/.local/bin/discord-wake-relay" \
      '. == [$launcher, "run"]' >/dev/null
  [ "$(/usr/bin/plutil -extract StandardOutPath raw -o - "$WAKE_RELAY_PLIST")" = \
    /dev/null ]
  [ "$(/usr/bin/plutil -extract StandardErrorPath raw -o - "$WAKE_RELAY_PLIST")" = \
    /dev/null ]
  [ ! -e "$WAKE_RELAY_LOG_DIR" ]
  [ ! -e "$WAKE_RELAY_STATE_DIR" ]
  grep -F "bootstrap gui/$relay_uid $WAKE_RELAY_PLIST" \
    "$MCP_TEST_STATE/launchctl-calls.log" >/dev/null
  grep -F 'profile show agent-coordination --json' \
    "$MCP_TEST_STATE/discord-npx-calls.log" >/dev/null
  ! grep -E ' setup |register|agent create' \
    "$MCP_TEST_STATE/discord-npx-calls.log" "$MCP_TEST_STATE/launchctl-calls.log"
  ! grep -E 'test-credential|DISCORD_TOKEN|TOKEN|handoff|message content' \
    "$WAKE_RELAY_PLIST" "$MCP_TEST_STATE/launchctl-calls.log"
  ! grep -E 'TOKEN|token|handoff|message content' "$WAKE_RELAY_LAUNCHER"
  [[ "$output" != *'test-credential'* ]]

  if [ "$(/usr/bin/uname -s)" = Darwin ]; then
    run env HOME="$INSTALL_HOME" "$INSTALL_HOME/.local/bin/discord-wake-relay" check
    [ "$status" -eq 0 ]
    [ "$output" = 'Discord wake relay launcher is ready.' ]
  fi
}

@test "wake relay setup preserves pre-existing legacy logs" {
  prepare_wake_relay_preflight
  mkdir -p "$WAKE_RELAY_LOG_DIR"
  printf '%s\n' 'legacy stdout' >"$WAKE_RELAY_LOG_DIR/stdout.log"
  printf '%s\n' 'legacy stderr' >"$WAKE_RELAY_LOG_DIR/stderr.log"

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 0 ]
  [ "$(<"$WAKE_RELAY_LOG_DIR/stdout.log")" = 'legacy stdout' ]
  [ "$(<"$WAKE_RELAY_LOG_DIR/stderr.log")" = 'legacy stderr' ]
  [ "$(/usr/bin/plutil -extract StandardOutPath raw -o - "$WAKE_RELAY_PLIST")" = \
    /dev/null ]
  [ "$(/usr/bin/plutil -extract StandardErrorPath raw -o - "$WAKE_RELAY_PLIST")" = \
    /dev/null ]
}

@test "a second wake relay setup is idempotent and kickstarts the loaded service" {
  prepare_wake_relay_preflight

  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  first_checksum=$(cksum "$WAKE_RELAY_PLIST")

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 0 ]
  [ "$(cksum "$WAKE_RELAY_PLIST")" = "$first_checksum" ]
  [[ "$output" == *'LaunchAgent plist is current'* ]]
  [ "$(grep -c '^bootstrap ' "$MCP_TEST_STATE/launchctl-calls.log")" -eq 1 ]
  [ "$(grep -c '^kickstart -k ' "$MCP_TEST_STATE/launchctl-calls.log")" -eq 1 ]
  ! grep -q '^bootout ' "$MCP_TEST_STATE/launchctl-calls.log"
}

@test "wake relay setup reloads a loaded service after the rendered plist changes" {
  prepare_wake_relay_preflight
  relay_uid=$(/usr/bin/id -u)
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  /usr/bin/plutil -replace ThrottleInterval -integer 31 "$WAKE_RELAY_PLIST"

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 0 ]
  [ "$(/usr/bin/plutil -extract ThrottleInterval raw -o - "$WAKE_RELAY_PLIST")" = 30 ]
  [ "$(/usr/bin/plutil -extract ThrottleInterval raw -o - "$WAKE_RELAY_LOADED_PLIST")" = 30 ]
  expected_mutations=$(printf '%s\n' \
    "bootstrap gui/$relay_uid $WAKE_RELAY_PLIST" \
    "bootout gui/$relay_uid/com.nfma.discord-wake-relay" \
    "bootstrap gui/$relay_uid $WAKE_RELAY_PLIST")
  [ "$(grep -E '^(bootstrap|bootout|kickstart) ' "$MCP_TEST_STATE/launchctl-calls.log")" = \
    "$expected_mutations" ]
}

@test "wake relay setup reloads an unchanged plist when launchd has a stale definition" {
  prepare_wake_relay_preflight
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  /usr/bin/plutil -replace StandardOutPath -string \
    "$WAKE_RELAY_LOG_DIR/stdout.log" "$WAKE_RELAY_LOADED_PLIST"
  /usr/bin/plutil -replace StandardErrorPath -string \
    "$WAKE_RELAY_LOG_DIR/stderr.log" "$WAKE_RELAY_LOADED_PLIST"
  first_checksum=$(cksum "$WAKE_RELAY_PLIST")

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 0 ]
  [ "$(cksum "$WAKE_RELAY_PLIST")" = "$first_checksum" ]
  [[ "$output" == *'LaunchAgent plist is current'* ]]
  [ "$(/usr/bin/plutil -extract StandardOutPath raw -o - "$WAKE_RELAY_LOADED_PLIST")" = \
    /dev/null ]
  [ "$(/usr/bin/plutil -extract StandardErrorPath raw -o - "$WAKE_RELAY_LOADED_PLIST")" = \
    /dev/null ]
  [ "$(grep -c '^bootout ' "$MCP_TEST_STATE/launchctl-calls.log")" -eq 1 ]
  [ "$(grep -c '^bootstrap ' "$MCP_TEST_STATE/launchctl-calls.log")" -eq 2 ]
  ! grep -q '^kickstart ' "$MCP_TEST_STATE/launchctl-calls.log"
}

@test "wake relay setup surfaces bootout failure and safely reloads on rerun" {
  prepare_wake_relay_preflight
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  /usr/bin/plutil -replace ThrottleInterval -integer 31 "$WAKE_RELAY_PLIST"
  changed_checksum=$(cksum "$WAKE_RELAY_PLIST")
  export MCP_TEST_LAUNCHCTL_FAILURE=bootout

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'cannot boot out wake relay service: gui/'* ]]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ -f "$WAKE_RELAY_PLIST" ]
  [ "$(cksum "$WAKE_RELAY_PLIST")" = "$changed_checksum" ]
  [ "$(/usr/bin/plutil -extract ThrottleInterval raw -o - "$WAKE_RELAY_LOADED_PLIST")" = 30 ]
  [ -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  ! compgen -G "$WAKE_RELAY_PLIST.tmp.*" >/dev/null

  unset MCP_TEST_LAUNCHCTL_FAILURE
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  [ "$(/usr/bin/plutil -extract ThrottleInterval raw -o - "$WAKE_RELAY_PLIST")" = 30 ]
  [ "$(/usr/bin/plutil -extract ThrottleInterval raw -o - "$WAKE_RELAY_LOADED_PLIST")" = 30 ]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
}

@test "wake relay setup surfaces bootstrap failure and recovers on rerun" {
  prepare_wake_relay_preflight
  export MCP_TEST_LAUNCHCTL_FAILURE=bootstrap

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'cannot bootstrap wake relay service in gui/'* ]]
  [ ! -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ ! -e "$WAKE_RELAY_LOADED_PLIST" ]
  [ -f "$WAKE_RELAY_PLIST" ]
  [ -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]

  unset MCP_TEST_LAUNCHCTL_FAILURE
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ -f "$WAKE_RELAY_LOADED_PLIST" ]
}

@test "wake relay setup surfaces kickstart failure and recovers on rerun" {
  prepare_wake_relay_preflight
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  export MCP_TEST_LAUNCHCTL_FAILURE=kickstart

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'cannot kickstart wake relay service: gui/'* ]]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ -f "$WAKE_RELAY_PLIST" ]
  [ -f "$WAKE_RELAY_LOADED_PLIST" ]
  [ -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]

  unset MCP_TEST_LAUNCHCTL_FAILURE
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  [ "$(grep -c '^kickstart -k ' "$MCP_TEST_STATE/launchctl-calls.log")" -eq 2 ]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
}

@test "wake relay uninstall dry run preserves the loaded installation" {
  prepare_wake_relay_preflight
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]

  run "$INSTALLER" --dry-run --uninstall-wake-relay

  [ "$status" -eq 0 ]
  [[ "$output" == *'Would stop wake relay service'* ]]
  [[ "$output" == *'Would remove wake relay LaunchAgent plist'* ]]
  [[ "$output" == *'Would remove wake relay launcher link'* ]]
  [[ "$output" == *'no files or services were changed'* ]]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ -f "$WAKE_RELAY_PLIST" ]
  [ -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
}

@test "wake relay uninstall is idempotent and preserves relay and Discord state" {
  prepare_wake_relay_preflight
  : >"$MCP_TEST_STATE/discord-profile-ready"
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  mkdir -p "$WAKE_RELAY_STATE_DIR"
  mkdir -p "$WAKE_RELAY_LOG_DIR"
  printf '%s\n' '{"registrations":{"role":"preserved"},"processing_cursor":"preserved"}' \
    >"$WAKE_RELAY_STATE_DIR/state.json"
  printf '%s\n' 'metadata-only log' >"$WAKE_RELAY_LOG_DIR/stdout.log"

  run "$INSTALLER" --uninstall-wake-relay

  [ "$status" -eq 0 ]
  [ ! -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ ! -e "$WAKE_RELAY_PLIST" ]
  [ ! -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  [ -f "$WAKE_RELAY_STATE_DIR/state.json" ]
  grep -F 'processing_cursor' "$WAKE_RELAY_STATE_DIR/state.json" >/dev/null
  [ "$(<"$WAKE_RELAY_LOG_DIR/stdout.log")" = 'metadata-only log' ]
  [ -e "$MCP_TEST_STATE/discord-profile-ready" ]
  [ -L "$INSTALL_HOME/.local/bin/discord-mcp-keychain" ]
  [[ "$output" == *'state, legacy logs, and Discord data were preserved'* ]]

  run "$INSTALLER" --uninstall-wake-relay
  [ "$status" -eq 0 ]
  [[ "$output" == *'service is not loaded'* ]]
}

@test "wake relay uninstall surfaces bootout failure without removing targets and recovers" {
  prepare_wake_relay_preflight
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  mkdir -p "$WAKE_RELAY_STATE_DIR" "$WAKE_RELAY_LOG_DIR"
  printf '%s\n' 'preserved state' >"$WAKE_RELAY_STATE_DIR/state.json"
  printf '%s\n' 'legacy log' >"$WAKE_RELAY_LOG_DIR/stdout.log"
  export MCP_TEST_LAUNCHCTL_FAILURE=bootout

  run "$INSTALLER" --uninstall-wake-relay

  [ "$status" -eq 1 ]
  [[ "$output" == *'cannot boot out wake relay service: gui/'* ]]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ -f "$WAKE_RELAY_LOADED_PLIST" ]
  [ -f "$WAKE_RELAY_PLIST" ]
  [ -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  [ "$(<"$WAKE_RELAY_STATE_DIR/state.json")" = 'preserved state' ]
  [ "$(<"$WAKE_RELAY_LOG_DIR/stdout.log")" = 'legacy log' ]

  unset MCP_TEST_LAUNCHCTL_FAILURE
  run "$INSTALLER" --uninstall-wake-relay
  [ "$status" -eq 0 ]
  [ ! -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ ! -e "$WAKE_RELAY_LOADED_PLIST" ]
  [ ! -e "$WAKE_RELAY_PLIST" ]
  [ ! -L "$INSTALL_HOME/.local/bin/discord-wake-relay" ]
  [ "$(<"$WAKE_RELAY_STATE_DIR/state.json")" = 'preserved state' ]
  [ "$(<"$WAKE_RELAY_LOG_DIR/stdout.log")" = 'legacy log' ]
}

@test "wake relay can be reinstalled without changing preserved registrations" {
  prepare_wake_relay_preflight
  run "$INSTALLER" --setup-wake-relay
  [ "$status" -eq 0 ]
  mkdir -p "$WAKE_RELAY_STATE_DIR"
  printf '%s\n' 'registered-role-state' >"$WAKE_RELAY_STATE_DIR/state.json"
  run "$INSTALLER" --uninstall-wake-relay
  [ "$status" -eq 0 ]

  run "$INSTALLER" --setup-wake-relay

  [ "$status" -eq 0 ]
  [ "$(<"$WAKE_RELAY_STATE_DIR/state.json")" = registered-role-state ]
  [ -e "$MCP_TEST_STATE/wake-relay-loaded" ]
  [ -f "$WAKE_RELAY_PLIST" ]
}
