#!/bin/bash

set -euo pipefail

# Assigning CDPATH only for cd prevents inherited values from changing output.
# shellcheck disable=SC1007
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1007
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
manifest="$repo_root/mcp/manifest.json"
codex_fragment=${MCP_CODEX_FRAGMENT:-"$repo_root/mcp/codex/mcp-fragment.json"}
claude_fragment=${MCP_CLAUDE_FRAGMENT:-"$repo_root/mcp/claude/mcp-fragment.json"}
wrapper_root="$repo_root/mcp/bin"
install_home=${MCP_INSTALL_HOME:-"${HOME:-}"}
security_bin=${MCP_SECURITY_BIN:-/usr/bin/security}
node_check_bin=${MCP_NODE_CHECK_BIN:-"$wrapper_root/nvm-default-exec"}
codex_config_updater="$repo_root/scripts/update-codex-mcp-config.cjs"
discord_profile_bin=${MCP_DISCORD_PROFILE_BIN:-"$install_home/.local/bin/npx"}
launchctl_bin=${MCP_LAUNCHCTL_BIN:-/bin/launchctl}
uname_bin=${MCP_UNAME_BIN:-/usr/bin/uname}
id_bin=${MCP_ID_BIN:-/usr/bin/id}
stat_bin=${MCP_STAT_BIN:-/usr/bin/stat}
plutil_bin=${MCP_PLUTIL_BIN:-/usr/bin/plutil}
wake_relay_label=com.nfma.discord-wake-relay
wake_relay_launcher_source="$wrapper_root/discord-wake-relay"
wake_relay_check_bin=${MCP_WAKE_RELAY_CHECK_BIN:-"$wake_relay_launcher_source"}
wake_relay_runtime="$repo_root/skills/discord-agent-coordination/scripts/discord_wake_relay.py"
wake_relay_plist_template="$repo_root/mcp/launchd/$wake_relay_label.plist.template"

dry_run=0
setup_discord=0
setup_wake_relay=0
uninstall_wake_relay=0
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
Usage: ./scripts/install-mcps.sh [--dry-run] [--setup-discord]
       [--setup-wake-relay | --uninstall-wake-relay]
       [--harness NAME]...

Install the repository's credential-free MCP baseline into every detected
harness. NAME may be codex, claude, cursor, antigravity, or all.

--setup-discord creates the non-secret agent-coordination profile from the
Discord bot token stored in macOS Keychain.

--setup-wake-relay installs and starts the optional user LaunchAgent. Run the
Discord profile setup first. --uninstall-wake-relay stops and removes only the
relay-owned LaunchAgent plist and launcher link; relay state is preserved.

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
    antigravity | agy) install_antigravity=1 ;;
    *) die "unknown harness: $1" ;;
  esac
}

harness_install_selected() {
  [ "$install_codex" -eq 1 ] \
    || [ "$install_claude" -eq 1 ] \
    || [ "$install_cursor" -eq 1 ] \
    || [ "$install_antigravity" -eq 1 ]
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --setup-discord)
      setup_discord=1
      shift
      ;;
    --setup-wake-relay)
      setup_wake_relay=1
      shift
      ;;
    --uninstall-wake-relay)
      uninstall_wake_relay=1
      shift
      ;;
    --harness)
      [ "$#" -ge 2 ] || die '--harness requires a name'
      selected_harnesses=1
      select_harness "$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown option: $1"
      ;;
  esac
done

if [ "$setup_wake_relay" -eq 1 ] && [ "$uninstall_wake_relay" -eq 1 ]; then
  die '--setup-wake-relay and --uninstall-wake-relay are mutually exclusive'
fi
if [ "$setup_wake_relay" -eq 1 ] && [ "$setup_discord" -eq 1 ]; then
  die '--setup-wake-relay requires an existing profile; run --setup-discord separately first'
fi
if [ "$uninstall_wake_relay" -eq 1 ] \
  && { [ "$setup_discord" -eq 1 ] || [ "$selected_harnesses" -eq 1 ]; }; then
  die '--uninstall-wake-relay cannot be combined with setup or harness installation'
fi

if [ "$selected_harnesses" -eq 0 ] \
  && [ "$setup_wake_relay" -eq 0 ] \
  && [ "$uninstall_wake_relay" -eq 0 ]; then
  select_harness all
fi

[ -n "$install_home" ] || die 'installation home is unavailable'
case "$install_home" in
  /*) [ "$install_home" != / ] || die 'installation home must be an absolute path other than /' ;;
  *) die 'installation home must be an absolute path other than /' ;;
esac
[ -x "$security_bin" ] || die "security command is unavailable at $security_bin"
[ -x "$node_check_bin" ] || die "Node runtime check is unavailable at $node_check_bin"
[ -r "$manifest" ] || die "manifest is unavailable at $manifest"
[ -r "$codex_config_updater" ] \
  || die "Codex config updater is unavailable at $codex_config_updater"
case "$install_home" in
  *[[:cntrl:]]*) die 'installation home must not contain control characters' ;;
esac

wake_relay_launcher="$install_home/.local/bin/discord-wake-relay"
wake_relay_launch_agents="$install_home/Library/LaunchAgents"
wake_relay_plist="$wake_relay_launch_agents/$wake_relay_label.plist"
wake_relay_domain=
wake_relay_service_target=

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"
}

keychain_item_available() {
  local keychain_value
  keychain_value=$("$security_bin" find-generic-password \
    "$@" -w 2>/dev/null) || return 1
  [ -n "$keychain_value" ]
}

validate_node_runtime() {
  local node_version
  local node_major
  local node_minor

  node_version=$("$node_check_bin" node --version 2>/dev/null) \
    || die 'the NVM default Node runtime is unavailable'
  if [[ ! "$node_version" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    die "cannot parse the NVM default Node version: $node_version"
  fi
  node_major=${BASH_REMATCH[1]}
  node_minor=${BASH_REMATCH[2]}
  if [ "$node_major" -lt 22 ] \
    || { [ "$node_major" -eq 22 ] && [ "$node_minor" -lt 12 ]; }; then
    die "@discord-mcp/cli@0.23.0 requires Node 22.12 or newer; the NVM default reports $node_version. Set a supported NVM default and rerun."
  fi
}

validate_harness_fragment() {
  harness_name=$1
  fragment_file=$2
  jq -e --arg harness "$harness_name" '
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
        all(.value.args[]; type == "string") and
        ((.value | has("overridesPlugin") | not) or
          ($harness == "codex" and
           (.value.overridesPlugin | type == "boolean"))) and
        ((.value | has("startupTimeoutSec") | not) or
          ($harness == "codex" and
           (.value.startupTimeoutSec | type == "number") and
           .value.startupTimeoutSec > 0))
      elif .value.type == "http" then
        (.value.url | type == "string") and
        (.value.url | test("^https://")) and
        (.value | has("overridesPlugin") | not) and
        (.value | has("startupTimeoutSec") | not)
      else
        false
      end)
  ' "$fragment_file" >/dev/null \
    || die "invalid harness MCP fragment: $fragment_file"
}

preflight() {
  [ -x "$security_bin" ] || die "security command is unavailable at $security_bin"
  [ -x "$node_check_bin" ] || die "Node runtime check is unavailable at $node_check_bin"
  [ -r "$manifest" ] || die "manifest is unavailable at $manifest"
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

  jq -e '.schemaVersion == 1 and (.baseline | length > 0)' "$manifest" >/dev/null \
    || die 'manifest is invalid'
  validate_harness_fragment codex "$codex_fragment"
  validate_harness_fragment claude "$claude_fragment"
  jq -e '.mcpServers | type == "object"' "$repo_root/mcp/cursor/mcp.json" >/dev/null \
    || die 'Cursor MCP config is invalid'
  jq -e '.mcpServers | type == "object"' "$repo_root/mcp/antigravity/mcp_config.json" >/dev/null \
    || die 'Antigravity MCP config is invalid'

  validate_node_runtime

  if [ "$install_codex" -eq 1 ] || [ "$install_claude" -eq 1 ] \
    || [ "$install_cursor" -eq 1 ] || [ "$install_antigravity" -eq 1 ]; then
    account_name=${USER:-$(/usr/bin/id -un)}
    keychain_item_available -s HF_TOKEN -a "$account_name" \
      || die "Keychain item HF_TOKEN is missing for $account_name"
    keychain_item_available -s DISCORD_MCP_TOKEN -a "$account_name" \
      || die "Keychain item DISCORD_MCP_TOKEN is missing for $account_name"
    keychain_item_available -l GITHUB_MCP_PAT \
      || die 'Keychain item labeled GITHUB_MCP_PAT is missing'
    command -v github-mcp-server >/dev/null 2>&1 \
      || [ -x /opt/homebrew/bin/github-mcp-server ] \
      || [ -x /usr/local/bin/github-mcp-server ] \
      || die 'github-mcp-server is unavailable'
    [ -r "$install_home/.serena/serena_config.yml" ] \
      || die 'Serena config is unavailable at ~/.serena/serena_config.yml'
    [ -x "$install_home/.local/share/uv/tools/serena-agent/bin/serena" ] \
      || die 'the Serena uv tool is unavailable'
  fi
}

preflight_wake_relay_platform() {
  [ -x "$launchctl_bin" ] || die "launchctl is unavailable at $launchctl_bin"
  [ -x "$uname_bin" ] || die "uname is unavailable at $uname_bin"
  [ -x "$id_bin" ] || die "id is unavailable at $id_bin"
  [ -x "$stat_bin" ] || die "stat is unavailable at $stat_bin"
  [ "$($uname_bin -s 2>/dev/null)" = Darwin ] \
    || die 'the Discord wake relay LaunchAgent is supported only on macOS'

  wake_relay_uid=$($id_bin -u 2>/dev/null) \
    || die 'cannot determine the current user ID'
  [[ "$wake_relay_uid" =~ ^[0-9]+$ ]] && [ "$wake_relay_uid" -gt 0 ] \
    || die 'the Discord wake relay must be installed by a non-root user'
  wake_relay_domain="gui/$wake_relay_uid"
  wake_relay_service_target="$wake_relay_domain/$wake_relay_label"
}

validate_wake_relay_profile() {
  local discord_token
  local discord_package

  discord_token=$("$security_bin" find-generic-password \
    -s DISCORD_MCP_TOKEN -a "${USER:-$($id_bin -un)}" -w 2>/dev/null) \
    || discord_token=
  [ -n "$discord_token" ] \
    || die 'Keychain item DISCORD_MCP_TOKEN is missing for the current user'
  discord_package=$(package_pin discord)
  if ! DISCORD_TOKEN="$discord_token" \
    MCP_CATEGORIES=messages,threads,channels \
    MCP_TOOL_SURFACE=progressive \
    "$discord_profile_bin" -y "$discord_package" \
    profile show agent-coordination --json >/dev/null 2>&1; then
    unset discord_token
    die 'Discord MCP profile agent-coordination is missing; run --setup-discord first'
  fi
  unset discord_token
}

preflight_wake_relay_setup() {
  preflight_wake_relay_platform
  if [ -e "$wake_relay_launcher" ] || [ -L "$wake_relay_launcher" ]; then
    wake_relay_launcher_owned \
      || die "refusing to replace an unrecognized wake relay launcher: $wake_relay_launcher"
  fi
  [ -x "$security_bin" ] || die "security command is unavailable at $security_bin"
  [ -x "$node_check_bin" ] || die "Node runtime check is unavailable at $node_check_bin"
  [ -x "$discord_profile_bin" ] \
    || die "Discord MCP profile command is unavailable at $discord_profile_bin"
  if [ -z "${MCP_DISCORD_PROFILE_BIN:-}" ]; then
    [ -L "$discord_profile_bin" ] \
      && [ "$(readlink "$discord_profile_bin")" = "$wrapper_root/npx" ] \
      || die 'the installed npx wrapper is missing or does not point to this repository'
  fi
  [ -x "$plutil_bin" ] || die "plutil is unavailable at $plutil_bin"
  [ -r "$manifest" ] || die "manifest is unavailable at $manifest"
  [ -x "$wake_relay_launcher_source" ] \
    || die "wake relay launcher is unavailable at $wake_relay_launcher_source"
  [ -x "$wake_relay_check_bin" ] \
    || die "wake relay launcher check is unavailable at $wake_relay_check_bin"
  [ -x "$wake_relay_runtime" ] \
    || die "wake relay runtime is unavailable at $wake_relay_runtime"
  [ -r "$wake_relay_plist_template" ] \
    || die "wake relay LaunchAgent template is unavailable at $wake_relay_plist_template"
  require_command jq
  require_command sed
  validate_node_runtime
  "$plutil_bin" -lint "$wake_relay_plist_template" >/dev/null \
    || die 'wake relay LaunchAgent template is invalid'
  if [ -e "$wake_relay_plist" ] || [ -L "$wake_relay_plist" ]; then
    wake_relay_plist_owned \
      || die "refusing to replace an unrecognized LaunchAgent plist: $wake_relay_plist"
  fi

  wake_relay_traycer="$install_home/.local/bin/traycer"
  wake_relay_mcp_wrapper="$install_home/.local/bin/discord-mcp-keychain"
  [ -x "$wake_relay_traycer" ] \
    || die "Traycer CLI is unavailable at $wake_relay_traycer"
  [ -L "$wake_relay_mcp_wrapper" ] \
    && [ "$(readlink "$wake_relay_mcp_wrapper")" = "$wrapper_root/discord-mcp-keychain" ] \
    || die 'the installed Discord MCP wrapper is missing or does not point to this repository'
  HOME="$install_home" "$wake_relay_check_bin" check >/dev/null \
    || die 'wake relay launcher validation failed'
  validate_wake_relay_profile
}

wake_relay_loaded() {
  "$launchctl_bin" print "$wake_relay_service_target" >/dev/null 2>&1
}

wake_relay_loaded_definition_current() {
  local definition

  definition=$("$launchctl_bin" print "$wake_relay_service_target" 2>/dev/null) \
    || return 1
  [[ "$definition" == *"path = $wake_relay_plist"* ]] || return 1
  [[ "$definition" == *"program = $wake_relay_launcher"* ]] || return 1
  [[ "$definition" == *'stdout path = /dev/null'* ]] || return 1
  [[ "$definition" == *'stderr path = /dev/null'* ]]
}

wake_relay_launcher_owned() {
  local owner

  [ -L "$wake_relay_launcher" ] \
    && [ "$(readlink "$wake_relay_launcher")" = "$wake_relay_launcher_source" ] \
    || return 1
  owner=$("$stat_bin" -f %u "$wake_relay_launcher") || return 1
  [ "$owner" = "$wake_relay_uid" ]
}

ensure_wake_relay_directory() {
  local directory=$1
  local label=$2
  local owner
  local mode

  if [ -e "$directory" ] || [ -L "$directory" ]; then
    [ ! -L "$directory" ] && [ -d "$directory" ] \
      || die "$label is not a real directory: $directory"
    owner=$("$stat_bin" -f %u "$directory") \
      || die "cannot inspect $label ownership: $directory"
    [ "$owner" = "$wake_relay_uid" ] \
      || die "$label is not owned by the current user: $directory"
    mode=$("$stat_bin" -f %Lp "$directory") \
      || die "cannot inspect $label permissions: $directory"
    (((8#$mode & 0022) == 0)) \
      || die "$label is group- or other-writable: $directory"
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    note "Would create $label: $directory"
    return 0
  fi
  /bin/mkdir -p "$directory"
  /bin/chmod 700 "$directory"
}

escape_plist_replacement() {
  printf '%s' "$1" \
    | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
    | sed -e 's/[\\&|]/\\&/g'
}

render_wake_relay_plist() {
  local launcher_value

  launcher_value=$(escape_plist_replacement "$wake_relay_launcher")
  sed \
    -e "s|__WAKE_RELAY_LAUNCHER__|$launcher_value|g" \
    "$wake_relay_plist_template"
}

wake_relay_plist_owned() {
  local owner
  local mode

  [ ! -L "$wake_relay_plist" ] \
    && [ -f "$wake_relay_plist" ] \
    && grep -F "<string>$wake_relay_label</string>" "$wake_relay_plist" >/dev/null \
    || return 1
  owner=$("$stat_bin" -f %u "$wake_relay_plist") || return 1
  [ "$owner" = "$wake_relay_uid" ] || return 1
  mode=$("$stat_bin" -f %Lp "$wake_relay_plist") || return 1
  (((8#$mode & 0022) == 0))
}

stage_wake_relay_plist() {
  local temporary_plist
  wake_relay_plist_changed=1
  wake_relay_temporary_plist=

  if [ -e "$wake_relay_plist" ] || [ -L "$wake_relay_plist" ]; then
    wake_relay_plist_owned \
      || die "refusing to replace an unrecognized LaunchAgent plist: $wake_relay_plist"
  fi
  if [ "$dry_run" -eq 1 ]; then
    if [ -f "$wake_relay_plist" ] \
      && render_wake_relay_plist | /usr/bin/cmp -s "$wake_relay_plist" -; then
      wake_relay_plist_changed=0
      note "Wake relay LaunchAgent plist is current: $wake_relay_plist"
    else
      note "Would reconcile wake relay LaunchAgent plist: $wake_relay_plist"
    fi
    return 0
  fi

  temporary_plist=$(mktemp "$wake_relay_plist.tmp.XXXXXX") \
    || die 'cannot create a temporary wake relay LaunchAgent plist'
  if ! render_wake_relay_plist >"$temporary_plist" \
    || ! "$plutil_bin" -lint "$temporary_plist" >/dev/null; then
    /bin/rm -f "$temporary_plist"
    die 'cannot render a valid wake relay LaunchAgent plist'
  fi
  /bin/chmod 600 "$temporary_plist"
  if [ -f "$wake_relay_plist" ] \
    && /usr/bin/cmp -s "$wake_relay_plist" "$temporary_plist"; then
    /bin/rm -f "$temporary_plist"
    wake_relay_plist_changed=0
    note "Wake relay LaunchAgent plist is current: $wake_relay_plist"
    return 0
  fi
  wake_relay_temporary_plist=$temporary_plist
}

install_staged_wake_relay_plist() {
  [ "$wake_relay_plist_changed" -eq 1 ] || return 0
  if ! /bin/mv "$wake_relay_temporary_plist" "$wake_relay_plist"; then
    /bin/rm -f "$wake_relay_temporary_plist"
    wake_relay_temporary_plist=
    die 'cannot install the wake relay LaunchAgent plist'
  fi
  wake_relay_temporary_plist=
  note "Installed wake relay LaunchAgent plist: $wake_relay_plist"
}

discard_staged_wake_relay_plist() {
  [ -n "$wake_relay_temporary_plist" ] || return 0
  /bin/rm -f "$wake_relay_temporary_plist"
  wake_relay_temporary_plist=
}

setup_wake_relay_service() {
  local was_loaded=0
  local loaded_definition_current=0

  preflight_wake_relay_setup
  wake_relay_loaded && was_loaded=1
  if [ "$was_loaded" -eq 1 ] && wake_relay_loaded_definition_current; then
    loaded_definition_current=1
  fi
  link_tracked_file \
    "$wake_relay_launcher_source" \
    "$wake_relay_launcher" \
    local-bin-discord-wake-relay
  ensure_wake_relay_directory "$wake_relay_launch_agents" 'LaunchAgents directory'
  stage_wake_relay_plist

  if [ "$dry_run" -eq 1 ]; then
    if [ "$was_loaded" -eq 1 ] \
      && { [ "$wake_relay_plist_changed" -eq 1 ] \
        || [ "$loaded_definition_current" -eq 0 ]; }; then
      note "Would reload wake relay service: $wake_relay_service_target"
      quote_command "$launchctl_bin" bootout "$wake_relay_service_target"
      quote_command "$launchctl_bin" bootstrap "$wake_relay_domain" "$wake_relay_plist"
    elif [ "$was_loaded" -eq 1 ]; then
      note "Would restart wake relay service: $wake_relay_service_target"
      quote_command "$launchctl_bin" kickstart -k "$wake_relay_service_target"
    else
      note "Would bootstrap wake relay service in $wake_relay_domain"
      quote_command "$launchctl_bin" bootstrap "$wake_relay_domain" "$wake_relay_plist"
    fi
    note 'Wake relay setup dry run complete; no files or services were changed.'
    return 0
  fi

  if [ "$was_loaded" -eq 1 ] \
    && { [ "$wake_relay_plist_changed" -eq 1 ] \
      || [ "$loaded_definition_current" -eq 0 ]; }; then
    if ! "$launchctl_bin" bootout "$wake_relay_service_target" >/dev/null 2>&1; then
      discard_staged_wake_relay_plist
      die "cannot boot out wake relay service: $wake_relay_service_target"
    fi
    install_staged_wake_relay_plist
    "$launchctl_bin" bootstrap "$wake_relay_domain" "$wake_relay_plist" >/dev/null 2>&1 \
      || die "cannot bootstrap wake relay service in $wake_relay_domain"
  elif [ "$was_loaded" -eq 1 ]; then
    "$launchctl_bin" kickstart -k "$wake_relay_service_target" >/dev/null 2>&1 \
      || die "cannot kickstart wake relay service: $wake_relay_service_target"
  else
    install_staged_wake_relay_plist
    "$launchctl_bin" bootstrap "$wake_relay_domain" "$wake_relay_plist" >/dev/null 2>&1 \
      || die "cannot bootstrap wake relay service in $wake_relay_domain"
  fi
  if ! wake_relay_loaded || ! wake_relay_loaded_definition_current; then
    die "wake relay service did not load the expected definition: $wake_relay_service_target"
  fi
  note "Wake relay service is ready: $wake_relay_service_target"
}

uninstall_wake_relay_service() {
  local was_loaded=0

  preflight_wake_relay_platform
  if [ -e "$wake_relay_plist" ] || [ -L "$wake_relay_plist" ]; then
    wake_relay_plist_owned \
      || die "refusing to remove an unrecognized LaunchAgent plist: $wake_relay_plist"
  fi
  wake_relay_loaded && was_loaded=1

  if [ "$was_loaded" -eq 1 ]; then
    if [ "$dry_run" -eq 1 ]; then
      note "Would stop wake relay service: $wake_relay_service_target"
      quote_command "$launchctl_bin" bootout "$wake_relay_service_target"
    else
      "$launchctl_bin" bootout "$wake_relay_service_target" >/dev/null 2>&1 \
        || die "cannot boot out wake relay service: $wake_relay_service_target"
    fi
  else
    note "Wake relay service is not loaded: $wake_relay_service_target"
  fi

  if [ -f "$wake_relay_plist" ]; then
    if [ "$dry_run" -eq 1 ]; then
      note "Would remove wake relay LaunchAgent plist: $wake_relay_plist"
    else
      /bin/rm -f "$wake_relay_plist"
      note "Removed wake relay LaunchAgent plist: $wake_relay_plist"
    fi
  fi
  if [ -L "$wake_relay_launcher" ] \
    && [ "$(readlink "$wake_relay_launcher")" = "$wake_relay_launcher_source" ]; then
    if [ "$dry_run" -eq 1 ]; then
      note "Would remove wake relay launcher link: $wake_relay_launcher"
    else
      /bin/rm -f "$wake_relay_launcher"
      note "Removed wake relay launcher link: $wake_relay_launcher"
    fi
  elif [ -e "$wake_relay_launcher" ] || [ -L "$wake_relay_launcher" ]; then
    note "Preserved unrecognized launcher path: $wake_relay_launcher"
  fi

  if [ "$dry_run" -eq 1 ]; then
    note 'Wake relay uninstall dry run complete; no files or services were changed.'
  else
    note 'Wake relay service uninstalled; relay state, legacy logs, and Discord data were preserved.'
  fi
}

ensure_backup_dir() {
  if [ -n "$backup_dir" ]; then
    return 0
  fi
  backup_dir="$install_home/.agents/mcp-backups/$(date +%Y%m%d-%H%M%S)"
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
    snapshot_file "$install_home/.codex/config.toml" codex-config.toml
    codex_config_snapshotted=1
  fi
}

snapshot_claude_config() {
  if [ "$claude_config_snapshotted" -eq 0 ]; then
    snapshot_file "$install_home/.claude.json" claude.json
    claude_config_snapshotted=1
  fi
}

install_wrappers() {
  for wrapper_name in \
    aikido-mcp-isolated-home.cjs \
    chrome-devtools-vivaldi \
    nvm-default-exec \
    npx \
    hf-mcp-filter.js \
    hf-mcp-remote \
    serena-mcp \
    github-mcp-keychain \
    discord-mcp-keychain \
    guardian-mcp; do
    link_tracked_file \
      "$wrapper_root/$wrapper_name" \
      "$install_home/.local/bin/$wrapper_name" \
      "local-bin-$wrapper_name"
  done
}

setup_discord_profile() {
  [ "$setup_discord" -eq 1 ] || return 0
  note 'Ensuring the non-secret Discord MCP profile is configured'
  run_command "$install_home/.local/bin/discord-mcp-keychain" setup
}

ensure_codex_marketplace() {
  if codex plugin marketplace list --json \
    | jq -e '.marketplaces[] | select(.name == "claude-plugins-official")' >/dev/null; then
    return 0
  fi
  note 'Installing the official plugin marketplace for Codex'
  snapshot_codex_config
  run_command codex plugin marketplace add anthropics/claude-plugins-official --json
}

ensure_claude_marketplace() {
  if claude plugin marketplace list --json \
    | jq -e '.[] | select(.name == "claude-plugins-official")' >/dev/null; then
    return 0
  fi
  note 'Installing the official plugin marketplace for Claude'
  snapshot_claude_config
  run_command claude plugin marketplace add anthropics/claude-plugins-official --scope user
}

ensure_codex_plugin() {
  plugin_id=$1
  plugin_state=$(codex plugin list --json \
    | jq -r --arg id "$plugin_id" \
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
  plugin_state=$(claude plugin list --json \
    | jq -r --arg id "$plugin_id" \
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
  if ! codex plugin list --json \
    | jq -e --arg id "$plugin_id" '.installed[] | select(.pluginId == $id)' >/dev/null; then
    return 0
  fi
  note "Removing Codex plugin in favor of a Keychain-backed direct MCP: $plugin_id"
  snapshot_codex_config
  run_command codex plugin remove "$plugin_id" --json
}

remove_claude_plugin_exception() {
  plugin_id=$1
  if ! claude plugin list --json \
    | jq -e --arg id "$plugin_id" '.[] | select((.id // .name) == $id)' >/dev/null; then
    return 0
  fi
  note "Removing Claude plugin in favor of a Keychain-backed direct MCP: $plugin_id"
  snapshot_claude_config
  run_command claude plugin uninstall --scope user --yes "$plugin_id"
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
    while IFS= read -r plugin_id; do
      remove_claude_plugin_exception "$plugin_id"
    done < <(jq -r '.plugins.remove[].id' "$claude_fragment")
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

codex_stdio_matches() {
  current_json=$1
  expected_command=$2
  expected_args=$3
  expected_startup_timeout=$4

  printf '%s' "$current_json" \
    | jq -e --arg command "$expected_command" \
      --argjson args "$expected_args" \
      --arg startup_timeout "$expected_startup_timeout" '
        .transport.type == "stdio" and
        .transport.command == $command and
        .transport.args == $args and
        ($startup_timeout == "" or
         .startup_timeout_sec == ($startup_timeout | tonumber))
      ' >/dev/null
}

codex_http_matches() {
  current_json=$1
  expected_url=$2

  printf '%s' "$current_json" \
    | jq -e --arg url "$expected_url" \
      '.transport.type == "streamable_http" and .transport.url == $url' \
      >/dev/null
}

reconcile_codex_stdio() {
  server_name=$1
  server_command=$2
  overrides_plugin=$3
  startup_timeout=$4
  shift 4
  expected_args=$(json_args "$@")
  current_json=$(codex mcp get "$server_name" --json 2>/dev/null || true)

  if [ -n "$current_json" ] \
    && codex_stdio_matches \
      "$current_json" "$server_command" "$expected_args" "$startup_timeout"; then
    note "Codex MCP ready: $server_name"
    return 0
  fi

  note "Reconciling Codex MCP: $server_name"
  snapshot_codex_config
  if [ -n "$current_json" ] && [ "$overrides_plugin" -eq 0 ]; then
    run_command codex mcp remove "$server_name"
  fi
  run_command codex mcp add "$server_name" -- "$server_command" "$@"
  if [ -n "$startup_timeout" ]; then
    run_command "$node_check_bin" node "$codex_config_updater" \
      "$install_home/.codex/config.toml" "$server_name" "$startup_timeout"
  fi
}

reconcile_codex_http() {
  server_name=$1
  server_url=$2
  current_json=$(codex mcp get "$server_name" --json 2>/dev/null || true)

  if [ -n "$current_json" ] \
    && codex_http_matches "$current_json" "$server_url"; then
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
  if [ ! -r "$install_home/.claude.json" ]; then
    return 1
  fi
  jq -c --arg name "$server_name" '.mcpServers[$name] // empty' "$install_home/.claude.json"
}

reconcile_claude_stdio() {
  server_name=$1
  server_command=$2
  shift 2
  expected_args=$(json_args "$@")
  current_json=$(claude_user_entry "$server_name" || true)

  if [ -n "$current_json" ] \
    && printf '%s' "$current_json" \
    | jq -e --arg command "$server_command" --argjson args "$expected_args" \
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

  if [ -n "$current_json" ] \
    && printf '%s' "$current_json" \
    | jq -e --arg url "$server_url" \
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
        overrides_plugin=$(printf '%s' "$server_entry" | jq -r '.value.overridesPlugin // false')
        startup_timeout=$(printf '%s' "$server_entry" \
          | jq -r 'if .value | has("startupTimeoutSec") then .value.startupTimeoutSec else empty end')
        if [ "$overrides_plugin" = true ]; then
          overrides_plugin=1
        else
          overrides_plugin=0
        fi
        server_arg_count=$(printf '%s' "$server_entry" | jq '.value.args | length')
        if [ "$server_arg_count" -eq 0 ]; then
          if [ "$harness_name" = codex ]; then
            reconcile_codex_stdio \
              "$server_name" "$install_home/.local/bin/$local_command" \
              "$overrides_plugin" "$startup_timeout"
          else
            reconcile_claude_stdio "$server_name" "$install_home/.local/bin/$local_command"
          fi
        else
          server_args=()
          while IFS= read -r server_arg; do
            server_args+=("$server_arg")
          done < <(printf '%s' "$server_entry" | jq -r '.value.args[]')
          if [ "$harness_name" = codex ]; then
            reconcile_codex_stdio \
              "$server_name" "$install_home/.local/bin/$local_command" \
              "$overrides_plugin" "$startup_timeout" "${server_args[@]}"
          else
            reconcile_claude_stdio \
              "$server_name" "$install_home/.local/bin/$local_command" "${server_args[@]}"
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
      "$install_home/.cursor/mcp.json" \
      cursor-mcp.json
  fi
  if [ "$install_antigravity" -eq 1 ]; then
    link_tracked_file \
      "$repo_root/mcp/antigravity/mcp_config.json" \
      "$install_home/.gemini/config/mcp_config.json" \
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
    grep -F -- "$pinned_package" "$repo_root/mcp/cursor/mcp.json" >/dev/null \
      || die "Cursor config is missing package pin: $pinned_package"
    grep -F -- "$pinned_package" "$repo_root/mcp/antigravity/mcp_config.json" >/dev/null \
      || die "Antigravity config is missing package pin: $pinned_package"
  done
  for fragment_file in "$codex_fragment" "$claude_fragment"; do
    for package_name in browser-tools excalidraw; do
      pinned_package=$(package_pin "$package_name")
      grep -F -- "$pinned_package" "$fragment_file" >/dev/null \
        || die "harness fragment is missing package pin: $pinned_package"
    done
  done
  grep -F -- "$(package_pin aikido)" "$wrapper_root/npx" >/dev/null \
    || die 'Aikido isolation wrapper is out of sync with the manifest'
  grep -F -- "$(package_pin chrome-devtools)" \
    "$wrapper_root/chrome-devtools-vivaldi" >/dev/null \
    || die 'Chrome DevTools Vivaldi wrapper is out of sync with the manifest'
  grep -F -- "$(package_pin hf-bridge)" "$wrapper_root/hf-mcp-filter.js" >/dev/null \
    || die 'Hugging Face bridge filter is out of sync with the manifest'
  grep -F -- "$(package_pin discord)" "$wrapper_root/discord-mcp-keychain" >/dev/null \
    || die 'Discord MCP wrapper is out of sync with the manifest'
}

verify_links() {
  for wrapper_name in \
    aikido-mcp-isolated-home.cjs chrome-devtools-vivaldi \
    nvm-default-exec npx hf-mcp-filter.js \
    hf-mcp-remote serena-mcp github-mcp-keychain discord-mcp-keychain \
    guardian-mcp; do
    target_file="$install_home/.local/bin/$wrapper_name"
    [ -L "$target_file" ] || die "wrapper is not linked: $target_file"
    [ "$(readlink "$target_file")" = "$wrapper_root/$wrapper_name" ] \
      || die "wrapper points to an unexpected target: $target_file"
  done
  if [ "$install_cursor" -eq 1 ]; then
    [ "$(readlink "$install_home/.cursor/mcp.json")" = "$repo_root/mcp/cursor/mcp.json" ] \
      || die 'Cursor MCP config link is incorrect'
  fi
  if [ "$install_antigravity" -eq 1 ]; then
    [ "$(readlink "$install_home/.gemini/config/mcp_config.json")" = "$repo_root/mcp/antigravity/mcp_config.json" ] \
      || die 'Antigravity MCP config link is incorrect'
  fi
}

verify_codex_entry() {
  server_entry=$1
  server_name=$(printf '%s' "$server_entry" | jq -r '.key')
  server_type=$(printf '%s' "$server_entry" | jq -r '.value.type')
  current_json=$(codex mcp get "$server_name" --json 2>/dev/null || true)
  [ -n "$current_json" ] || die "Codex MCP verification failed: $server_name"

  case "$server_type" in
    stdio)
      local_command=$(printf '%s' "$server_entry" | jq -r '.value.localCommand')
      expected_command="$install_home/.local/bin/$local_command"
      expected_args=$(printf '%s' "$server_entry" | jq -c '.value.args')
      expected_startup_timeout=$(printf '%s' "$server_entry" \
        | jq -r 'if .value | has("startupTimeoutSec") then .value.startupTimeoutSec else empty end')
      codex_stdio_matches "$current_json" "$expected_command" \
        "$expected_args" "$expected_startup_timeout" \
        || die "Codex MCP verification failed: $server_name"
      ;;
    http)
      expected_url=$(printf '%s' "$server_entry" | jq -r '.value.url')
      codex_http_matches "$current_json" "$expected_url" \
        || die "Codex MCP verification failed: $server_name"
      ;;
  esac
}

verify_installed_state() {
  verify_no_literal_credentials
  verify_baseline_configs
  verify_version_pins
  if [ "$dry_run" -eq 0 ]; then
    verify_links
    if [ "$install_codex" -eq 1 ]; then
      while IFS= read -r server_entry; do
        verify_codex_entry "$server_entry"
      done < <(jq -c '.mcpServers | to_entries[]' "$codex_fragment")
    fi
    if [ "$install_claude" -eq 1 ]; then
      while IFS= read -r server_name; do
        claude_user_entry "$server_name" >/dev/null \
          || die "Claude MCP verification failed: $server_name"
      done < <(jq -r '.mcpServers | keys[]' "$claude_fragment")
    fi
  fi
}

if harness_install_selected; then
  preflight
  install_wrappers
  setup_discord_profile
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
fi

if [ "$setup_wake_relay" -eq 1 ]; then
  setup_wake_relay_service
elif [ "$uninstall_wake_relay" -eq 1 ]; then
  uninstall_wake_relay_service
fi
