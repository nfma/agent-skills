#!/bin/bash

set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
manifest="$repo_root/mcp/manifest.json"
codex_fragment="$repo_root/mcp/codex/mcp-fragment.json"
claude_fragment="$repo_root/mcp/claude/mcp-fragment.json"
wrapper_root="$repo_root/mcp/bin"

dry_run=0
selected_harnesses=0
install_codex=0
install_claude=0
install_cursor=0
install_antigravity=0
backup_dir=
codex_config_snapshotted=0
claude_config_snapshotted=0

usage() {
  cat <<'EOF'
Usage: ./scripts/install-mcps.sh [--dry-run] [--harness NAME]...

Install the repository's credential-free MCP baseline into every detected
harness. NAME may be codex, claude, cursor, antigravity, or all.
EOF
}

die() {
  printf 'install-mcps: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*"
}

quote_command() {
  printf '  '
  printf '%q ' "$@"
  printf '\n'
}

run_command() {
  if [ "$dry_run" -eq 1 ]; then
    quote_command "$@"
    return 0
  fi
  "$@"
}

select_harness() {
  case "$1" in
    all)
      install_codex=1
      install_claude=1
      install_cursor=1
      install_antigravity=1
      ;;
    codex) install_codex=1 ;;
    claude) install_claude=1 ;;
    cursor) install_cursor=1 ;;
    antigravity|agy) install_antigravity=1 ;;
    *) die "unknown harness: $1" ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --harness)
      [ "$#" -ge 2 ] || die '--harness requires a name'
      selected_harnesses=1
      select_harness "$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown option: $1"
      ;;
  esac
done

if [ "$selected_harnesses" -eq 0 ]; then
  select_harness all
fi

[ -n "${HOME:-}" ] || die 'HOME is unavailable'
[ -r "$manifest" ] || die "manifest is unavailable at $manifest"

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

validate_harness_fragment() {
  fragment_file=$1
  jq -e '
    (.plugins.install | type == "array") and
    (.plugins.remove | type == "array") and
    all(.plugins.install[];
      (.id | type == "string") and
      (.servers | type == "array") and
      all(.servers[]; type == "string")) and
    all(.plugins.remove[];
      (.id | type == "string") and
      (.reason | type == "string")) and
    (.mcpServers | type == "object") and
    all(.mcpServers | to_entries[];
      if .value.type == "stdio" then
        (.value.localCommand | type == "string") and
        (.value.localCommand | test("^[A-Za-z0-9._-]+$")) and
        (.value.args | type == "array") and
        all(.value.args[]; type == "string")
      elif .value.type == "http" then
        (.value.url | type == "string") and
        (.value.url | test("^https://"))
      else
        false
      end)
  ' "$fragment_file" >/dev/null ||
    die "invalid harness MCP fragment: $fragment_file"
}

preflight() {
  require_command jq
  require_command git

  if [ "$install_codex" -eq 1 ]; then
    require_command codex
  fi
  if [ "$install_claude" -eq 1 ]; then
    require_command claude
  fi
  if [ "$install_cursor" -eq 1 ]; then
    require_command cursor
  fi
  if [ "$install_antigravity" -eq 1 ]; then
    require_command agy
  fi

  jq -e '.schemaVersion == 1 and (.baseline | length > 0)' "$manifest" >/dev/null ||
    die 'manifest is invalid'
  validate_harness_fragment "$codex_fragment"
  validate_harness_fragment "$claude_fragment"
  jq -e '.mcpServers | type == "object"' "$repo_root/mcp/cursor/mcp.json" >/dev/null ||
    die 'Cursor MCP config is invalid'
  jq -e '.mcpServers | type == "object"' "$repo_root/mcp/antigravity/mcp_config.json" >/dev/null ||
    die 'Antigravity MCP config is invalid'

  "$wrapper_root/nvm-default-exec" node --version >/dev/null 2>&1 ||
    die 'the NVM default Node runtime is unavailable'

  if [ "$install_codex" -eq 1 ] || [ "$install_claude" -eq 1 ] ||
     [ "$install_cursor" -eq 1 ] || [ "$install_antigravity" -eq 1 ]; then
    account_name=${USER:-$(/usr/bin/id -un)}
    /usr/bin/security find-generic-password -s HF_TOKEN -a "$account_name" -w \
      >/dev/null 2>&1 ||
      die "Keychain item HF_TOKEN is missing for $account_name"
    command -v gh >/dev/null 2>&1 || [ -x /opt/homebrew/bin/gh ] ||
      die 'gh is unavailable'
    if command -v gh >/dev/null 2>&1; then
      gh auth token >/dev/null 2>&1 || die 'GitHub authentication is unavailable; run `gh auth login`'
    else
      /opt/homebrew/bin/gh auth token >/dev/null 2>&1 ||
        die 'GitHub authentication is unavailable; run `gh auth login`'
    fi
    command -v github-mcp-server >/dev/null 2>&1 ||
      [ -x /opt/homebrew/bin/github-mcp-server ] ||
      [ -x /usr/local/bin/github-mcp-server ] ||
      die 'github-mcp-server is unavailable'
    [ -r "$HOME/.serena/serena_config.yml" ] ||
      die 'Serena config is unavailable at ~/.serena/serena_config.yml'
    [ -x "$HOME/.local/share/uv/tools/serena-agent/bin/serena" ] ||
      die 'the Serena uv tool is unavailable'
  fi
}

ensure_backup_dir() {
  if [ -n "$backup_dir" ]; then
    return 0
  fi
  backup_dir="$HOME/.agents/mcp-backups/$(date +%Y%m%d-%H%M%S)"
  /bin/mkdir -p "$backup_dir"
  /bin/chmod 700 "$backup_dir"
}

snapshot_file() {
  source_file=$1
  backup_name=$2
  if [ ! -e "$source_file" ] && [ ! -L "$source_file" ]; then
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    note "Would back up $source_file"
    return 0
  fi
  ensure_backup_dir
  if [ ! -e "$backup_dir/$backup_name" ] && [ ! -L "$backup_dir/$backup_name" ]; then
    /bin/cp -Pp "$source_file" "$backup_dir/$backup_name"
  fi
}

link_tracked_file() {
  source_file=$1
  target_file=$2
  backup_name=$3

  if [ -L "$target_file" ] && [ "$(readlink "$target_file")" = "$source_file" ]; then
    note "Already linked: $target_file"
    return 0
  fi

  if [ "$dry_run" -eq 1 ]; then
    note "Would link $target_file -> $source_file"
    return 0
  fi

  ensure_backup_dir
  /bin/mkdir -p "$(dirname -- "$target_file")"
  if [ -e "$target_file" ] || [ -L "$target_file" ]; then
    if [ -e "$backup_dir/$backup_name" ] || [ -L "$backup_dir/$backup_name" ]; then
      die "backup collision at $backup_dir/$backup_name"
    fi
    /bin/mv "$target_file" "$backup_dir/$backup_name"
  fi
  /bin/ln -s "$source_file" "$target_file"
  note "Linked $target_file -> $source_file"
}

snapshot_codex_config() {
  if [ "$codex_config_snapshotted" -eq 0 ]; then
    snapshot_file "$HOME/.codex/config.toml" codex-config.toml
    codex_config_snapshotted=1
  fi
}

snapshot_claude_config() {
  if [ "$claude_config_snapshotted" -eq 0 ]; then
    snapshot_file "$HOME/.claude.json" claude.json
    claude_config_snapshotted=1
  fi
}

install_wrappers() {
  for wrapper_name in \
    aikido-mcp-isolated-home.cjs \
    nvm-default-exec \
    npx \
    hf-mcp-filter.js \
    hf-mcp-remote \
    serena-mcp \
    github-mcp-keychain \
    guardian-mcp; do
    link_tracked_file \
      "$wrapper_root/$wrapper_name" \
      "$HOME/.local/bin/$wrapper_name" \
      "local-bin-$wrapper_name"
  done
}

ensure_codex_marketplace() {
  if codex plugin marketplace list --json |
     jq -e '.marketplaces[] | select(.name == "claude-plugins-official")' >/dev/null; then
    return 0
  fi
  note 'Installing the official plugin marketplace for Codex'
  snapshot_codex_config
  run_command codex plugin marketplace add anthropics/claude-plugins-official --json
}

ensure_claude_marketplace() {
  if claude plugin marketplace list --json |
     jq -e '.[] | select(.name == "claude-plugins-official")' >/dev/null; then
    return 0
  fi
  note 'Installing the official plugin marketplace for Claude'
  snapshot_claude_config
  run_command claude plugin marketplace add anthropics/claude-plugins-official --scope user
}

ensure_codex_plugin() {
  plugin_id=$1
  plugin_state=$(codex plugin list --json |
    jq -r --arg id "$plugin_id" \
      '[.installed[] | select(.pluginId == $id)][0] | if . == null then "missing" elif .enabled then "enabled" else "disabled" end')
  case "$plugin_state" in
    enabled)
      note "Codex plugin ready: $plugin_id"
      ;;
    missing)
      note "Installing Codex plugin: $plugin_id"
      snapshot_codex_config
      run_command codex plugin add "$plugin_id" --json
      ;;
    disabled)
      die "Codex plugin is installed but disabled: $plugin_id"
      ;;
  esac
}

ensure_claude_plugin() {
  plugin_id=$1
  plugin_state=$(claude plugin list --json |
    jq -r --arg id "$plugin_id" \
      '[.[] | select((.id // .name) == $id)][0] | if . == null then "missing" elif .enabled == true then "enabled" else "disabled" end')
  case "$plugin_state" in
    enabled)
      note "Claude plugin ready: $plugin_id"
      ;;
    missing)
      note "Installing Claude plugin: $plugin_id"
      snapshot_claude_config
      run_command claude plugin install --scope user --yes "$plugin_id"
      ;;
    disabled)
      note "Enabling Claude plugin: $plugin_id"
      snapshot_claude_config
      run_command claude plugin enable --scope user "$plugin_id"
      ;;
  esac
}

remove_codex_plugin_exception() {
  plugin_id=$1
  if ! codex plugin list --json |
       jq -e --arg id "$plugin_id" '.installed[] | select(.pluginId == $id)' >/dev/null; then
    return 0
  fi
  note "Removing Codex plugin in favor of a Keychain-backed direct MCP: $plugin_id"
  snapshot_codex_config
  run_command codex plugin remove "$plugin_id" --json
}

install_native_plugins() {
  if [ "$install_codex" -eq 1 ]; then
    ensure_codex_marketplace
    while IFS= read -r plugin_id; do
      ensure_codex_plugin "$plugin_id"
    done < <(jq -r '.plugins.install[].id' "$codex_fragment")
    while IFS= read -r plugin_id; do
      remove_codex_plugin_exception "$plugin_id"
    done < <(jq -r '.plugins.remove[].id' "$codex_fragment")
  fi

  if [ "$install_claude" -eq 1 ]; then
    ensure_claude_marketplace
    while IFS= read -r plugin_id; do
      ensure_claude_plugin "$plugin_id"
    done < <(jq -r '.plugins.install[].id' "$claude_fragment")
  fi
}

json_args() {
  if [ "$#" -eq 0 ]; then
    printf '[]\n'
    return 0
  fi
  printf '%s\n' "$@" | jq -R . | jq -s -c .
}

package_pin() {
  jq -er --arg package_name "$1" '.npmPackages[$package_name]' "$manifest"
}

reconcile_codex_stdio() {
  server_name=$1
  server_command=$2
  shift 2
  expected_args=$(json_args "$@")
  current_json=$(codex mcp get "$server_name" --json 2>/dev/null || true)

  if [ -n "$current_json" ] &&
     printf '%s' "$current_json" |
       jq -e --arg command "$server_command" --argjson args "$expected_args" \
         '.transport.type == "stdio" and .transport.command == $command and .transport.args == $args' \
         >/dev/null; then
    note "Codex MCP ready: $server_name"
    return 0
  fi

  note "Reconciling Codex MCP: $server_name"
  snapshot_codex_config
  if [ -n "$current_json" ]; then
    run_command codex mcp remove "$server_name"
  fi
  run_command codex mcp add "$server_name" -- "$server_command" "$@"
}

reconcile_codex_http() {
  server_name=$1
  server_url=$2
  current_json=$(codex mcp get "$server_name" --json 2>/dev/null || true)

  if [ -n "$current_json" ] &&
     printf '%s' "$current_json" |
       jq -e --arg url "$server_url" \
         '.transport.type == "streamable_http" and .transport.url == $url' \
         >/dev/null; then
    note "Codex MCP ready: $server_name"
    return 0
  fi

  note "Reconciling Codex MCP: $server_name"
  snapshot_codex_config
  if [ -n "$current_json" ]; then
    run_command codex mcp remove "$server_name"
  fi
  run_command codex mcp add "$server_name" --url "$server_url"
}

claude_user_entry() {
  server_name=$1
  if [ ! -r "$HOME/.claude.json" ]; then
    return 1
  fi
  jq -c --arg name "$server_name" '.mcpServers[$name] // empty' "$HOME/.claude.json"
}

reconcile_claude_stdio() {
  server_name=$1
  server_command=$2
  shift 2
  expected_args=$(json_args "$@")
  current_json=$(claude_user_entry "$server_name" || true)

  if [ -n "$current_json" ] &&
     printf '%s' "$current_json" |
       jq -e --arg command "$server_command" --argjson args "$expected_args" \
         '(.type // "stdio") == "stdio" and .command == $command and (.args // []) == $args' \
         >/dev/null; then
    note "Claude MCP ready: $server_name"
    return 0
  fi

  note "Reconciling Claude MCP: $server_name"
  snapshot_claude_config
  if [ -n "$current_json" ]; then
    run_command claude mcp remove --scope user "$server_name"
  fi
  run_command claude mcp add --scope user "$server_name" -- "$server_command" "$@"
}

reconcile_claude_http() {
  server_name=$1
  server_url=$2
  current_json=$(claude_user_entry "$server_name" || true)

  if [ -n "$current_json" ] &&
     printf '%s' "$current_json" |
       jq -e --arg url "$server_url" \
         '.type == "http" and .url == $url' >/dev/null; then
    note "Claude MCP ready: $server_name"
    return 0
  fi

  note "Reconciling Claude MCP: $server_name"
  snapshot_claude_config
  if [ -n "$current_json" ]; then
    run_command claude mcp remove --scope user "$server_name"
  fi
  run_command claude mcp add --scope user --transport http "$server_name" "$server_url"
}

install_fragment_mcps() {
  harness_name=$1
  fragment_file=$2

  while IFS= read -r server_entry; do
    server_name=$(printf '%s' "$server_entry" | jq -r '.key')
    server_type=$(printf '%s' "$server_entry" | jq -r '.value.type')

    case "$server_type" in
      stdio)
        local_command=$(printf '%s' "$server_entry" | jq -r '.value.localCommand')
        server_arg_count=$(printf '%s' "$server_entry" | jq '.value.args | length')
        if [ "$server_arg_count" -eq 0 ]; then
          if [ "$harness_name" = codex ]; then
            reconcile_codex_stdio "$server_name" "$HOME/.local/bin/$local_command"
          else
            reconcile_claude_stdio "$server_name" "$HOME/.local/bin/$local_command"
          fi
        else
          server_args=()
          while IFS= read -r server_arg; do
            server_args+=("$server_arg")
          done < <(printf '%s' "$server_entry" | jq -r '.value.args[]')
          if [ "$harness_name" = codex ]; then
            reconcile_codex_stdio \
              "$server_name" "$HOME/.local/bin/$local_command" "${server_args[@]}"
          else
            reconcile_claude_stdio \
              "$server_name" "$HOME/.local/bin/$local_command" "${server_args[@]}"
          fi
        fi
        ;;
      http)
        server_url=$(printf '%s' "$server_entry" | jq -r '.value.url')
        if [ "$harness_name" = codex ]; then
          reconcile_codex_http "$server_name" "$server_url"
        else
          reconcile_claude_http "$server_name" "$server_url"
        fi
        ;;
    esac
  done < <(jq -c '.mcpServers | to_entries[]' "$fragment_file")
}

install_dedicated_configs() {
  if [ "$install_cursor" -eq 1 ]; then
    link_tracked_file \
      "$repo_root/mcp/cursor/mcp.json" \
      "$HOME/.cursor/mcp.json" \
      cursor-mcp.json
  fi
  if [ "$install_antigravity" -eq 1 ]; then
    link_tracked_file \
      "$repo_root/mcp/antigravity/mcp_config.json" \
      "$HOME/.gemini/config/mcp_config.json" \
      antigravity-mcp_config.json
  fi
}

verify_no_literal_credentials() {
  if grep -R -E \
    '(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|hf_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{20,}|sk-[A-Za-z0-9]{20,})' \
    "$repo_root/mcp" >/dev/null; then
    die 'credential-shaped literal found under mcp/'
  fi
}

verify_baseline_configs() {
  expected_names=$(jq -c '.baseline | sort' "$manifest")
  codex_names=$(jq -c \
    '[.mcpServers | keys[]] + [.plugins.install[].servers[]] | unique | sort' \
    "$codex_fragment")
  claude_names=$(jq -c \
    '[.mcpServers | keys[]] + [.plugins.install[].servers[]] | unique | sort' \
    "$claude_fragment")
  cursor_names=$(jq -c '.mcpServers | keys | sort' "$repo_root/mcp/cursor/mcp.json")
  antigravity_names=$(jq -c '.mcpServers | keys | sort' "$repo_root/mcp/antigravity/mcp_config.json")
  [ "$codex_names" = "$expected_names" ] || die 'Codex fragment does not match the baseline manifest'
  [ "$claude_names" = "$expected_names" ] || die 'Claude fragment does not match the baseline manifest'
  [ "$cursor_names" = "$expected_names" ] || die 'Cursor config does not match the baseline manifest'
  [ "$antigravity_names" = "$expected_names" ] || die 'Antigravity config does not match the baseline manifest'
}

verify_version_pins() {
  for package_name in aikido browser-tools chrome-devtools context7 excalidraw playwright; do
    pinned_package=$(package_pin "$package_name")
    grep -F -- "$pinned_package" "$repo_root/mcp/cursor/mcp.json" >/dev/null ||
      die "Cursor config is missing package pin: $pinned_package"
    grep -F -- "$pinned_package" "$repo_root/mcp/antigravity/mcp_config.json" >/dev/null ||
      die "Antigravity config is missing package pin: $pinned_package"
  done
  for fragment_file in "$codex_fragment" "$claude_fragment"; do
    for package_name in browser-tools excalidraw; do
      pinned_package=$(package_pin "$package_name")
      grep -F -- "$pinned_package" "$fragment_file" >/dev/null ||
        die "harness fragment is missing package pin: $pinned_package"
    done
  done
  grep -F -- "$(package_pin aikido)" "$wrapper_root/npx" >/dev/null ||
    die 'Aikido isolation wrapper is out of sync with the manifest'
  grep -F -- "$(package_pin hf-bridge)" "$wrapper_root/hf-mcp-remote" >/dev/null ||
    die 'Hugging Face bridge wrapper is out of sync with the manifest'
}

verify_links() {
  for wrapper_name in \
    aikido-mcp-isolated-home.cjs nvm-default-exec npx hf-mcp-filter.js \
    hf-mcp-remote serena-mcp github-mcp-keychain guardian-mcp; do
    target_file="$HOME/.local/bin/$wrapper_name"
    [ -L "$target_file" ] || die "wrapper is not linked: $target_file"
    [ "$(readlink "$target_file")" = "$wrapper_root/$wrapper_name" ] ||
      die "wrapper points to an unexpected target: $target_file"
  done
  if [ "$install_cursor" -eq 1 ]; then
    [ "$(readlink "$HOME/.cursor/mcp.json")" = "$repo_root/mcp/cursor/mcp.json" ] ||
      die 'Cursor MCP config link is incorrect'
  fi
  if [ "$install_antigravity" -eq 1 ]; then
    [ "$(readlink "$HOME/.gemini/config/mcp_config.json")" = "$repo_root/mcp/antigravity/mcp_config.json" ] ||
      die 'Antigravity MCP config link is incorrect'
  fi
}

verify_installed_state() {
  verify_no_literal_credentials
  verify_baseline_configs
  verify_version_pins
  if [ "$dry_run" -eq 0 ]; then
    verify_links
    if [ "$install_codex" -eq 1 ]; then
      while IFS= read -r server_name; do
        codex mcp get "$server_name" --json >/dev/null ||
          die "Codex MCP verification failed: $server_name"
      done < <(jq -r '.mcpServers | keys[]' "$codex_fragment")
    fi
    if [ "$install_claude" -eq 1 ]; then
      while IFS= read -r server_name; do
        claude_user_entry "$server_name" >/dev/null ||
          die "Claude MCP verification failed: $server_name"
      done < <(jq -r '.mcpServers | keys[]' "$claude_fragment")
    fi
  fi
}

preflight
install_wrappers
install_native_plugins

if [ "$install_codex" -eq 1 ]; then
  install_fragment_mcps codex "$codex_fragment"
fi
if [ "$install_claude" -eq 1 ]; then
  install_fragment_mcps claude "$claude_fragment"
fi

install_dedicated_configs
verify_installed_state

if [ "$dry_run" -eq 1 ]; then
  note 'Dry run complete; no files or harness settings were changed.'
elif [ -n "$backup_dir" ]; then
  note "MCP installation complete. Backups: $backup_dir"
else
  note 'MCP installation complete; no changes were necessary.'
fi
