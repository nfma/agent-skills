#!/usr/bin/env bash
# sync-agent-skills — fan out ~/.agents/skills to Claude, AGY, and Codex.
#
# Canonical store: ~/.agents/skills/<skill>/SKILL.md
# Cursor reads ~/.agents/skills natively — leave ~/.cursor/skills alone.
# Never touches Codex ~/.codex/skills/.system or Cursor skills-cursor.

set -euo pipefail

CANONICAL="${AGENT_SKILLS_HOME:-$HOME/.agents/skills}"
DRY_RUN=0
VERBOSE=0

usage() {
  cat <<'USAGE'
Usage: sync-agent-skills [--dry-run] [--verbose] [--help]

Re-fan-out skills from the canonical store (~/.agents/skills) to:
  ~/.claude/skills              → symlink to canonical
  ~/.gemini/config/skills       → symlink to canonical
  ~/.gemini/antigravity/skills  → symlink to canonical
  ~/.gemini/antigravity-cli/skills → symlink to canonical
  ~/.gemini/config/skills.json  → entries pointing at ~/.agents/skills
  ~/.codex/skills/<name>        → per-skill symlink (keeps .system/)

Does not modify ~/.cursor/skills (Cursor discovers ~/.agents/skills natively).
Does not touch product system skill trees.

Options:
  --dry-run   Print actions without changing the filesystem
  --verbose   Print each skill link
  --help      Show this help

Env:
  AGENT_SKILLS_HOME   Override canonical directory (default: ~/.agents/skills)
USAGE
}

log() { printf '%s\n' "$*"; }
vlog() { [[ "$VERBOSE" -eq 1 ]] && printf '  %s\n' "$*" || true; }

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY: $*"
  else
    "$@"
  fi
}

# Resolve to absolute path without requiring the target to exist yet.
abs_path() {
  local p="$1"
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "$p" 2>/dev/null || python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$p"
  else
    python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$p"
  fi
}

# Ensure path is a symlink to $want (absolute). Replace empty dirs or wrong links.
ensure_dir_symlink() {
  local link_path="$1"
  local want="$2"
  local parent
  parent="$(dirname "$link_path")"

  run mkdir -p "$parent"

  if [[ -L "$link_path" ]]; then
    local current
    current="$(readlink "$link_path")"
    # Normalise relative readlink targets
    if [[ "$current" != /* ]]; then
      current="$(abs_path "$parent/$current")"
    fi
    if [[ "$current" == "$want" ]]; then
      log "ok  $link_path -> $want"
      return 0
    fi
    log "fix $link_path (was -> $current)"
    run rm "$link_path"
  elif [[ -d "$link_path" ]]; then
    if [[ -z "$(ls -A "$link_path" 2>/dev/null || true)" ]]; then
      log "replace empty dir $link_path"
      run rmdir "$link_path"
    else
      log "ERROR: $link_path is a non-empty directory; refuse to replace" >&2
      return 1
    fi
  elif [[ -e "$link_path" ]]; then
    log "ERROR: $link_path exists and is not a symlink/dir; refuse to replace" >&2
    return 1
  fi

  run ln -s "$want" "$link_path"
  log "link $link_path -> $want"
}

write_agy_skills_json() {
  local json_path="$HOME/.gemini/config/skills.json"
  local desired
  desired=$(cat <<'JSON'
{
  "entries": [
    { "path": "~/.agents/skills" }
  ]
}
JSON
)
  run mkdir -p "$(dirname "$json_path")"
  if [[ -f "$json_path" ]] && [[ "$(cat "$json_path")" == "$desired" ]]; then
    log "ok  $json_path"
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY: write $json_path"
  else
    printf '%s\n' "$desired" > "$json_path"
    log "write $json_path"
  fi
}

sync_codex_skill_links() {
  local codex_skills="$HOME/.codex/skills"
  run mkdir -p "$codex_skills"

  local -a skills=()
  local d name
  for d in "$CANONICAL"/*/; do
    [[ -d "$d" ]] || continue
    name="$(basename "$d")"
    # Skip hidden / reserved
    [[ "$name" == .* ]] && continue
    [[ -f "$d/SKILL.md" ]] || {
      log "skip $name (no SKILL.md)"
      continue
    }
    skills+=("$name")
  done

  if [[ ${#skills[@]} -eq 0 ]]; then
    log "warn: no skills with SKILL.md under $CANONICAL"
    return 0
  fi

  local link current want
  for name in "${skills[@]}"; do
    want="$CANONICAL/$name"
    link="$codex_skills/$name"
    if [[ -L "$link" ]]; then
      current="$(readlink "$link")"
      if [[ "$current" != /* ]]; then
        current="$(abs_path "$(dirname "$link")/$current")"
      fi
      if [[ "$current" == "$want" ]]; then
        vlog "ok  $link"
        continue
      fi
      run rm "$link"
    elif [[ -e "$link" ]]; then
      if [[ -d "$link" && ! -L "$link" ]]; then
        log "ERROR: $link is a real directory (not a symlink); refuse to replace" >&2
        return 1
      fi
      run rm -f "$link"
    fi
    run ln -s "$want" "$link"
    vlog "link $link -> $want"
  done

  # Remove stale Codex user skill symlinks that point into canonical but whose
  # skill was deleted — only our symlinks into CANONICAL.
  for link in "$codex_skills"/*; do
    [[ -e "$link" || -L "$link" ]] || continue
    name="$(basename "$link")"
    [[ "$name" == .* ]] && continue
    [[ -L "$link" ]] || continue
    current="$(readlink "$link")"
    if [[ "$current" != /* ]]; then
      current="$(abs_path "$(dirname "$link")/$current")"
    fi
    case "$current" in
      "$CANONICAL"/*)
        if [[ ! -d "$CANONICAL/$name" ]]; then
          log "remove stale $link"
          run rm "$link"
        fi
        ;;
    esac
  done

  log "codex: ${#skills[@]} skill link(s) under $codex_skills"
}

# --- main ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --verbose|-v) VERBOSE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *)
      log "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

CANONICAL="$(abs_path "$CANONICAL")"

if [[ ! -d "$CANONICAL" ]]; then
  log "ERROR: canonical skills dir missing: $CANONICAL" >&2
  exit 1
fi

log "canonical: $CANONICAL"
[[ "$DRY_RUN" -eq 1 ]] && log "(dry-run)"

ensure_dir_symlink "$HOME/.claude/skills" "$CANONICAL"
ensure_dir_symlink "$HOME/.gemini/config/skills" "$CANONICAL"
ensure_dir_symlink "$HOME/.gemini/antigravity/skills" "$CANONICAL"
ensure_dir_symlink "$HOME/.gemini/antigravity-cli/skills" "$CANONICAL"
write_agy_skills_json
sync_codex_skill_links

# Cursor-only skills live in ~/.cursor/skills (e.g. aikido/chrome MCP wrappers).
# Shared portable skills stay in canonical. Do not merge the two.
if [[ -d "$HOME/.cursor/skills" ]]; then
  count=$(find "$HOME/.cursor/skills" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
  log "ok  ~/.cursor/skills ($count Cursor-only skill dir(s); not fanned out)"
else
  log "ok  ~/.cursor/skills absent (Cursor still reads canonical)"
fi

log "done"
