#!/bin/sh

set -eu

# Assigning CDPATH only for cd prevents inherited values from changing output.
# shellcheck disable=SC1007
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
verifier="$repo_root/scripts/verify-skill-audit-release.mjs"

node "$verifier" --cleanup-legacy
node "$verifier" --install
