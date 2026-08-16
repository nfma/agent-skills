#!/bin/sh

set -eu

# Assigning CDPATH only for cd prevents inherited values from changing output.
# shellcheck disable=SC1007
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
skill_audit_dir="$repo_root/vendor/skill-audit/skill-audit"

if [ ! -f "$skill_audit_dir/package-lock.json" ]; then
  echo "skill-audit submodule is missing; run git submodule update --init --recursive" >&2
  exit 1
fi

cd "$skill_audit_dir"
npm ci --ignore-scripts
npm run build
node dist/index.js --version
