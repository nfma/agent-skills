#!/bin/bash

set -euo pipefail

# Assigning CDPATH only for cd prevents inherited values from changing output.
# shellcheck disable=SC1007
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck disable=SC1007
repo_root=$(CDPATH= cd -- "$script_dir/.." && pwd)

service_label=com.nfma.traycer-quota-supervisor
launcher_source="$repo_root/mcp/bin/traycer-quota-supervisor"
runtime="$repo_root/services/traycer-quota-supervisor/traycer_quota_supervisor.py"
plist_template="$repo_root/mcp/launchd/$service_label.plist.template"
# SHA-256 of the reviewed pre-service executable whose self-install lifecycle
# this manager replaces. No other regular file is eligible for migration.
legacy_executable_sha256=ff90223a24e99517c4f1c811a619d4a589eaac98b27beb965fb817e7409b57d5
# Reviewed digest pair for the first manager-owned launcher and runtime release.
# Future releases must retain explicit prior pairs when they need to update or
# uninstall an older recognized manager installation.
managed_launcher_sha256=c46405b32b5788263310c2018475510dd90fcffdccd75a3254afc6276d6c96d9
managed_runtime_sha256=e6682f247ca1ce06591c3fa03d87944343f4ec5af080f723799e2f886af9e3e6

install_home=${QUOTA_SUPERVISOR_INSTALL_HOME:-"${HOME:-}"}
python_bin=${QUOTA_SUPERVISOR_PYTHON_BIN:-/opt/homebrew/bin/python3}
launchctl_bin=${QUOTA_SUPERVISOR_LAUNCHCTL_BIN:-/bin/launchctl}
uname_bin=${QUOTA_SUPERVISOR_UNAME_BIN:-/usr/bin/uname}
id_bin=${QUOTA_SUPERVISOR_ID_BIN:-/usr/bin/id}
stat_bin=${QUOTA_SUPERVISOR_STAT_BIN:-/usr/bin/stat}
plutil_bin=${QUOTA_SUPERVISOR_PLUTIL_BIN:-/usr/bin/plutil}
sed_bin=${QUOTA_SUPERVISOR_SED_BIN:-/usr/bin/sed}
cmp_bin=${QUOTA_SUPERVISOR_CMP_BIN:-/usr/bin/cmp}
mktemp_bin=${QUOTA_SUPERVISOR_MKTEMP_BIN:-/usr/bin/mktemp}
date_bin=${QUOTA_SUPERVISOR_DATE_BIN:-/bin/date}
shasum_bin=${QUOTA_SUPERVISOR_SHASUM_BIN:-/usr/bin/shasum}

dry_run=0
allow_ephemeral_checkout=0
action=

usage() {
  cat <<'EOF'
Usage: ./scripts/manage-quota-supervisor.sh [--dry-run] [--allow-ephemeral-checkout] ACTION

Manage the optional current-user Traycer quota supervisor LaunchAgent.

Actions:
  setup      Install or update the launcher and LaunchAgent.
  uninstall  Stop and remove only manager-owned service files.
  status     Show loaded state and the runtime's sanitized status.

--dry-run applies globally. Setup and uninstall report their plan without
changing files or services; status is already read-only.

--allow-ephemeral-checkout permits setup outside a dedicated normal clone under
~/Projects. It is an expert escape hatch and does not make the source durable.
EOF
}

die() {
  printf 'manage-quota-supervisor: %s\n' "$*" >&2
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

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --allow-ephemeral-checkout)
      allow_ephemeral_checkout=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    setup | uninstall | status)
      [ -z "$action" ] || die 'expected exactly one action'
      action=$1
      shift
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
done

[ -n "$action" ] || {
  usage >&2
  die 'an action is required'
}

case "$install_home" in
  /*) [ "$install_home" != / ] || die 'installation home must not be /' ;;
  *) die 'installation home must be an absolute path' ;;
esac
case "$install_home" in
  *$'\n'* | *$'\r'*) die 'installation home must not contain newlines' ;;
esac

launcher="$install_home/.local/bin/traycer-quota-supervisor"
launch_agents_dir="$install_home/Library/LaunchAgents"
plist="$launch_agents_dir/$service_label.plist"
state_dir="$install_home/.local/state/traycer-quota-supervisor"
log_file="$state_dir/supervisor.log"
backup_root="$install_home/.agents/service-backups/traycer-quota-supervisor"
service_domain=
service_target=
current_uid=
launcher_state=
plist_state=
legacy_cutover=0

require_executable() {
  [ -x "$1" ] || die "$2 is unavailable at $1"
}

validate_platform() {
  require_executable "$launchctl_bin" launchctl
  require_executable "$uname_bin" uname
  require_executable "$id_bin" id
  require_executable "$stat_bin" stat
  [ "$($uname_bin -s 2>/dev/null)" = Darwin ] \
    || die 'the Traycer quota supervisor is supported only on macOS'

  current_uid=$($id_bin -u 2>/dev/null) \
    || die 'cannot determine the current user ID'
  [[ "$current_uid" =~ ^[0-9]+$ ]] && [ "$current_uid" -gt 0 ] \
    || die 'the Traycer quota supervisor must be managed by a non-root user'
  [ ! -L "$install_home" ] && [ -d "$install_home" ] \
    || die "installation home is not a real directory: $install_home"
  validate_owned_path "$install_home" 'installation home'
  service_domain="gui/$current_uid"
  service_target="$service_domain/$service_label"
}

validate_setup_source() {
  local ephemeral=0

  case "$repo_root/" in
    "$install_home/Projects/"*) ;;
    *) ephemeral=1 ;;
  esac
  [ -d "$repo_root/.git" ] || ephemeral=1
  if [ "$ephemeral" -eq 1 ] && [ "$allow_ephemeral_checkout" -ne 1 ]; then
    die "setup source must be a dedicated normal clone under $install_home/Projects; use that durable clone or explicitly pass --allow-ephemeral-checkout"
  fi
}

path_owner() {
  "$stat_bin" -f %u "$1"
}

path_mode() {
  "$stat_bin" -f %Lp "$1"
}

validate_owned_path() {
  local path=$1
  local label=$2
  local mode
  local owner

  owner=$(path_owner "$path") || die "cannot inspect $label ownership: $path"
  [ "$owner" = "$current_uid" ] || die "$label is not owned by the current user: $path"
  mode=$(path_mode "$path") || die "cannot inspect $label permissions: $path"
  (((8#$mode & 0022) == 0)) || die "$label is group- or other-writable: $path"
}

ensure_directory() {
  local path=$1
  local label=$2
  local private=$3
  local mode

  if [ -e "$path" ] || [ -L "$path" ]; then
    [ ! -L "$path" ] && [ -d "$path" ] || die "$label is not a real directory: $path"
    validate_owned_path "$path" "$label"
    if [ "$private" -eq 1 ]; then
      mode=$(path_mode "$path") || die "cannot inspect $label permissions: $path"
      (((8#$mode & 0077) == 0)) \
        || die "$label must be accessible only to the current user: $path"
    fi
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    note "Would create $label: $path"
    return 0
  fi
  /bin/mkdir -p "$path"
  /bin/chmod 700 "$path"
}

ensure_log() {
  local mode

  if [ -e "$log_file" ] || [ -L "$log_file" ]; then
    [ ! -L "$log_file" ] && [ -f "$log_file" ] \
      || die "supervisor log is not a real file: $log_file"
    validate_owned_path "$log_file" 'supervisor log'
    mode=$(path_mode "$log_file") || die "cannot inspect supervisor log permissions: $log_file"
    (((8#$mode & 0077) == 0)) \
      || die "supervisor log must be accessible only to the current user: $log_file"
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    note "Would create supervisor log: $log_file"
    return 0
  fi
  : >"$log_file"
  /bin/chmod 600 "$log_file"
}

ensure_setup_directories() {
  ensure_directory "$install_home/.local" 'local data directory' 0
  ensure_directory "$(dirname -- "$launcher")" 'local bin directory' 0
  ensure_directory "$install_home/Library" 'Library directory' 0
  ensure_directory "$launch_agents_dir" 'LaunchAgents directory' 0
  ensure_directory "$install_home/.local/state" 'local state directory' 0
  ensure_directory "$state_dir" 'supervisor state directory' 1
}

validate_existing_directory() {
  local path=$1
  local label=$2

  if [ -e "$path" ] || [ -L "$path" ]; then
    ensure_directory "$path" "$label" 0
  fi
}

validate_local_target_directories() {
  validate_existing_directory "$install_home/.local" 'local data directory'
  validate_existing_directory "$(dirname -- "$launcher")" 'local bin directory'
}

validate_plist_target_directories() {
  validate_existing_directory "$install_home/Library" 'Library directory'
  validate_existing_directory "$launch_agents_dir" 'LaunchAgents directory'
}

validate_target_directories() {
  validate_local_target_directories
  validate_plist_target_directories
}

escape_plist_replacement() {
  printf '%s' "$1" \
    | "$sed_bin" -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' \
    | "$sed_bin" -e 's/[\\&|]/\\&/g'
}

render_plist() {
  local launcher_value
  local log_value

  launcher_value=$(escape_plist_replacement "$launcher")
  log_value=$(escape_plist_replacement "$log_file")
  "$sed_bin" \
    -e "s|__QUOTA_SUPERVISOR_LAUNCHER__|$launcher_value|g" \
    -e "s|__QUOTA_SUPERVISOR_LOG__|$log_value|g" \
    "$plist_template"
}

file_sha256() {
  local output

  output=$("$shasum_bin" -a 256 "$1") || return 1
  printf '%s\n' "${output%% *}"
}

new_launcher_owned() {
  local installed_launcher
  local installed_repo_root
  local installed_runtime
  local launcher_digest
  local runtime_digest

  [ -L "$launcher" ] || return 1
  [ "$(path_owner "$launcher")" = "$current_uid" ] || return 1
  installed_launcher=$(readlink "$launcher") || return 1
  case "$installed_launcher" in
    /*/mcp/bin/traycer-quota-supervisor) ;;
    *) return 1 ;;
  esac
  [ -f "$installed_launcher" ] && [ -x "$installed_launcher" ] || return 1
  validate_owned_path "$installed_launcher" 'installed supervisor launcher'
  installed_repo_root=${installed_launcher%/mcp/bin/traycer-quota-supervisor}
  installed_runtime="$installed_repo_root/services/traycer-quota-supervisor/traycer_quota_supervisor.py"
  [ ! -L "$installed_runtime" ] && [ -f "$installed_runtime" ] && [ -r "$installed_runtime" ] \
    || return 1
  validate_owned_path "$installed_runtime" 'installed supervisor runtime'
  launcher_digest=$(file_sha256 "$installed_launcher") || return 1
  runtime_digest=$(file_sha256 "$installed_runtime") || return 1
  [ "$launcher_digest:$runtime_digest" = "$managed_launcher_sha256:$managed_runtime_sha256" ]
}

legacy_launcher_owned() {
  local digest

  [ ! -L "$launcher" ] && [ -f "$launcher" ] || return 1
  validate_owned_path "$launcher" 'legacy supervisor executable'
  digest=$("$python_bin" -c \
    'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' \
    "$launcher") || return 1
  [ "$digest" = "$legacy_executable_sha256" ]
}

new_plist_owned() {
  local mode

  [ ! -L "$plist" ] && [ -f "$plist" ] || return 1
  validate_owned_path "$plist" 'LaunchAgent plist'
  mode=$(path_mode "$plist") || return 1
  [ "$mode" = 600 ] || return 1
  render_plist | "$cmp_bin" -s - "$plist"
}

legacy_plist_owned() {
  local mode

  [ ! -L "$plist" ] && [ -f "$plist" ] || return 1
  validate_owned_path "$plist" 'legacy LaunchAgent plist'
  mode=$(path_mode "$plist") || return 1
  [ "$mode" = 600 ] || return 1
  "$python_bin" - "$plist" "$launcher" "$log_file" <<'PY'
import pathlib
import plistlib
import sys

path = pathlib.Path(sys.argv[1])
launcher = sys.argv[2]
log_file = sys.argv[3]
expected = {
    "KeepAlive": True,
    "Label": "com.nfma.traycer-quota-supervisor",
    "ProcessType": "Background",
    "ProgramArguments": [launcher, "run"],
    "RunAtLoad": True,
    "StandardErrorPath": log_file,
    "StandardOutPath": log_file,
    "ThrottleInterval": 10,
}
with path.open("rb") as source:
    actual = plistlib.load(source)
raise SystemExit(actual != expected)
PY
}

classify_targets() {
  if [ ! -e "$launcher" ] && [ ! -L "$launcher" ]; then
    launcher_state=absent
  elif new_launcher_owned; then
    launcher_state=new
  elif legacy_launcher_owned; then
    launcher_state=legacy
  else
    launcher_state=unrecognized
  fi

  if [ ! -e "$plist" ] && [ ! -L "$plist" ]; then
    plist_state=absent
  elif new_plist_owned; then
    plist_state=new
  elif legacy_plist_owned; then
    plist_state=legacy
  else
    plist_state=unrecognized
  fi

  [ "$launcher_state" != unrecognized ] \
    || die "refusing an unrecognized supervisor launcher: $launcher"
  [ "$plist_state" != unrecognized ] \
    || die "refusing an unrecognized LaunchAgent plist: $plist"
  case "$launcher_state:$plist_state" in
    legacy:legacy | new:legacy) legacy_cutover=1 ;;
    legacy:* | *:legacy) die 'refusing an incomplete or mixed legacy supervisor installation' ;;
  esac
}

validate_service_sources() {
  require_executable "$python_bin" 'Homebrew Python'
  require_executable "$plutil_bin" plutil
  require_executable "$sed_bin" sed
  require_executable "$cmp_bin" cmp
  require_executable "$mktemp_bin" mktemp
  require_executable "$date_bin" date
  require_executable "$shasum_bin" shasum
  [ -x "$launcher_source" ] || die "tracked launcher is unavailable at $launcher_source"
  [ -r "$runtime" ] && [ ! -L "$runtime" ] \
    || die "tracked runtime is unavailable at $runtime"
  [ -r "$plist_template" ] \
    || die "LaunchAgent template is unavailable at $plist_template"
  "$plutil_bin" -lint "$plist_template" >/dev/null \
    || die 'LaunchAgent template is invalid'
  [ "$(file_sha256 "$launcher_source")" = "$managed_launcher_sha256" ] \
    || die 'tracked launcher digest does not match the reviewed manager release'
  [ "$(file_sha256 "$runtime")" = "$managed_runtime_sha256" ] \
    || die 'tracked runtime digest does not match the reviewed manager release'
  HOME="$install_home" \
    TRAYCER_QUOTA_SUPERVISOR_PYTHON_BIN="$python_bin" \
    TRAYCER_QUOTA_SUPERVISOR_ID_BIN="$id_bin" \
    TRAYCER_QUOTA_SUPERVISOR_STAT_BIN="$stat_bin" \
    "$launcher_source" check >/dev/null \
    || die 'tracked launcher validation failed'
}

service_loaded() {
  "$launchctl_bin" print "$service_target" >/dev/null 2>&1
}

backup_legacy_installation() {
  local backup_dir
  local timestamp

  [ "$legacy_cutover" -eq 1 ] || return 0
  if [ "$dry_run" -eq 1 ]; then
    if [ "$launcher_state" = legacy ] && [ "$plist_state" = legacy ]; then
      note "Would back up the verified legacy launcher and plist under: $backup_root"
    elif [ "$launcher_state" = legacy ]; then
      note "Would back up the verified legacy launcher under: $backup_root"
    else
      note "Would back up the verified legacy plist under: $backup_root"
    fi
    return 0
  fi
  ensure_directory "$install_home/.agents" 'agent data directory' 0
  ensure_directory "$install_home/.agents/service-backups" 'service backup directory' 0
  ensure_directory "$backup_root" 'legacy backup directory' 1
  timestamp=$("$date_bin" +%Y%m%d-%H%M%S) || die 'cannot create a backup timestamp'
  backup_dir=$("$mktemp_bin" -d "$backup_root/cutover-$timestamp.XXXXXX") \
    || die 'cannot create a legacy backup directory'
  /bin/chmod 700 "$backup_dir"
  if [ "$launcher_state" = legacy ]; then
    /bin/cp -p "$launcher" "$backup_dir/traycer-quota-supervisor"
    "$cmp_bin" -s "$launcher" "$backup_dir/traycer-quota-supervisor" \
      || die 'legacy launcher backup verification failed'
  fi
  if [ "$plist_state" = legacy ]; then
    /bin/cp -p "$plist" "$backup_dir/$service_label.plist"
    "$cmp_bin" -s "$plist" "$backup_dir/$service_label.plist" \
      || die 'legacy plist backup verification failed'
  fi
  note "Backed up the verified legacy installation: $backup_dir"
}

install_launcher() {
  local temporary_link

  if [ "$launcher_state" = new ]; then
    note "Supervisor launcher is current: $launcher"
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    note "Would install supervisor launcher: $launcher -> $launcher_source"
    return 0
  fi
  temporary_link="$launcher.new.$$"
  [ ! -e "$temporary_link" ] && [ ! -L "$temporary_link" ] \
    || die "temporary launcher path already exists: $temporary_link"
  /bin/ln -s "$launcher_source" "$temporary_link"
  if ! /bin/mv -f "$temporary_link" "$launcher"; then
    /bin/rm -f "$temporary_link"
    die 'cannot atomically install the supervisor launcher'
  fi
  note "Installed supervisor launcher: $launcher -> $launcher_source"
}

install_plist() {
  local temporary_plist

  if [ "$plist_state" = new ]; then
    note "LaunchAgent plist is current: $plist"
    return 0
  fi
  if [ "$dry_run" -eq 1 ]; then
    note "Would install LaunchAgent plist: $plist"
    return 0
  fi
  temporary_plist=$("$mktemp_bin" "$plist.tmp.XXXXXX") \
    || die 'cannot create a temporary LaunchAgent plist'
  if ! render_plist >"$temporary_plist" \
    || ! "$plutil_bin" -lint "$temporary_plist" >/dev/null; then
    /bin/rm -f "$temporary_plist"
    die 'cannot render a valid LaunchAgent plist'
  fi
  /bin/chmod 600 "$temporary_plist"
  /bin/mv -f "$temporary_plist" "$plist"
  note "Installed LaunchAgent plist: $plist"
}

setup_service() {
  local files_changed=0
  local was_loaded=0

  validate_platform
  validate_setup_source
  validate_target_directories
  validate_service_sources
  classify_targets
  service_loaded && was_loaded=1
  if [ "$launcher_state" != new ] || [ "$plist_state" != new ]; then
    files_changed=1
  fi

  ensure_setup_directories
  ensure_log
  backup_legacy_installation
  if [ "$was_loaded" -eq 1 ] && [ "$files_changed" -eq 1 ]; then
    if [ "$dry_run" -eq 1 ]; then
      note "Would stop supervisor service before replacing managed files: $service_target"
      quote_command "$launchctl_bin" bootout "$service_target"
    else
      "$launchctl_bin" bootout "$service_target"
    fi
  fi
  install_launcher
  install_plist

  if [ "$dry_run" -eq 1 ]; then
    if [ "$was_loaded" -eq 1 ] && [ "$files_changed" -eq 1 ]; then
      note "Would bootstrap the updated supervisor service: $service_target"
      quote_command "$launchctl_bin" bootstrap "$service_domain" "$plist"
    elif [ "$was_loaded" -eq 1 ]; then
      note "Supervisor service is already loaded and current: $service_target"
    else
      note "Would bootstrap supervisor service in $service_domain"
      quote_command "$launchctl_bin" bootstrap "$service_domain" "$plist"
    fi
    note 'Setup dry run complete; no files or services were changed.'
    return 0
  fi

  if [ "$was_loaded" -eq 1 ] && [ "$files_changed" -eq 1 ]; then
    "$launchctl_bin" bootstrap "$service_domain" "$plist"
  elif [ "$was_loaded" -eq 1 ]; then
    note "Supervisor service is already loaded and current: $service_target"
  else
    "$launchctl_bin" bootstrap "$service_domain" "$plist"
  fi
  service_loaded || die "supervisor service did not load: $service_target"
  note "Supervisor service is ready: $service_target"
}

uninstall_service() {
  local was_loaded=0
  local launcher_owned=0

  validate_platform
  require_executable "$python_bin" 'Homebrew Python'
  require_executable "$sed_bin" sed
  require_executable "$cmp_bin" cmp
  require_executable "$shasum_bin" shasum
  validate_target_directories
  [ -r "$plist_template" ] \
    || die "LaunchAgent template is unavailable at $plist_template"
  if [ -e "$launcher" ] || [ -L "$launcher" ]; then
    new_launcher_owned \
      || die "refusing to remove an unrecognized supervisor launcher: $launcher"
    launcher_owned=1
  fi
  if [ -e "$plist" ] || [ -L "$plist" ]; then
    if ! new_plist_owned; then
      if [ "$launcher_owned" -eq 1 ] && legacy_plist_owned; then
        die 'verified resumable cutover detected; run setup to complete it, then rerun uninstall'
      fi
      die "refusing to remove an unrecognized LaunchAgent plist: $plist"
    fi
  fi
  service_loaded && was_loaded=1

  if [ "$was_loaded" -eq 1 ]; then
    if [ "$dry_run" -eq 1 ]; then
      note "Would stop supervisor service: $service_target"
      quote_command "$launchctl_bin" bootout "$service_target"
    else
      "$launchctl_bin" bootout "$service_target"
    fi
  else
    note "Supervisor service is not loaded: $service_target"
  fi
  if [ -f "$plist" ]; then
    if [ "$dry_run" -eq 1 ]; then
      note "Would remove LaunchAgent plist: $plist"
    else
      /bin/rm -f "$plist"
      note "Removed LaunchAgent plist: $plist"
    fi
  fi
  if [ -L "$launcher" ]; then
    if [ "$dry_run" -eq 1 ]; then
      note "Would remove supervisor launcher link: $launcher"
    else
      /bin/rm -f "$launcher"
      note "Removed supervisor launcher link: $launcher"
    fi
  fi
  if [ "$dry_run" -eq 1 ]; then
    note 'Uninstall dry run complete; no files or services were changed.'
  else
    note 'Supervisor uninstalled; state, logs, and legacy backups were preserved.'
  fi
}

show_status() {
  validate_platform
  require_executable "$shasum_bin" shasum
  validate_local_target_directories
  [ -e "$launcher" ] || [ -L "$launcher" ] \
    || die "supervisor launcher is not installed: $launcher"
  new_launcher_owned || die "refusing an unrecognized supervisor launcher: $launcher"
  service_loaded || die "supervisor service is not loaded: $service_target"
  note "Supervisor service is loaded: $service_target"
  HOME="$install_home" \
    TRAYCER_QUOTA_SUPERVISOR_PYTHON_BIN="$python_bin" \
    TRAYCER_QUOTA_SUPERVISOR_ID_BIN="$id_bin" \
    TRAYCER_QUOTA_SUPERVISOR_STAT_BIN="$stat_bin" \
    "$launcher" status
}

case "$action" in
  setup) setup_service ;;
  uninstall) uninstall_service ;;
  status) show_status ;;
esac
