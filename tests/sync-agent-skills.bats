#!/usr/bin/env bats

setup() {
  TEST_HOME=$(mktemp -d)
  CANONICAL="$TEST_HOME/canonical"
  SCRIPT="$BATS_TEST_DIRNAME/../scripts/sync-agent-skills.sh"

  mkdir -p "$CANONICAL/example"
  printf '%s\n' '# Example' >"$CANONICAL/example/SKILL.md"
}

teardown() {
  rm -rf -- "$TEST_HOME"
}

@test "prints help without changing the filesystem" {
  run env HOME="$TEST_HOME" AGENT_SKILLS_HOME="$CANONICAL" "$SCRIPT" --help

  [ "$status" -eq 0 ]
  [[ "$output" == *'Usage: sync-agent-skills'* ]]
  [ ! -e "$TEST_HOME/.claude" ]
}

@test "dry run reports actions without changing the filesystem" {
  run env HOME="$TEST_HOME" AGENT_SKILLS_HOME="$CANONICAL" "$SCRIPT" --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *'DRY: ln -s'* ]]
  [ ! -e "$TEST_HOME/.claude" ]
  [ ! -e "$TEST_HOME/.codex" ]
  [ ! -e "$TEST_HOME/.gemini" ]
}

@test "fans canonical skills out while preserving system skills" {
  mkdir -p "$TEST_HOME/.codex/skills/.system"
  printf '%s\n' keep >"$TEST_HOME/.codex/skills/.system/sentinel"
  ln -s "$CANONICAL/removed" "$TEST_HOME/.codex/skills/removed"

  run env HOME="$TEST_HOME" AGENT_SKILLS_HOME="$CANONICAL" "$SCRIPT"

  [ "$status" -eq 0 ]
  [ "$(readlink "$TEST_HOME/.claude/skills")" = "$CANONICAL" ]
  [ "$(readlink "$TEST_HOME/.gemini/config/skills")" = "$CANONICAL" ]
  [ "$(readlink "$TEST_HOME/.gemini/antigravity/skills")" = "$CANONICAL" ]
  [ "$(readlink "$TEST_HOME/.gemini/antigravity-cli/skills")" = "$CANONICAL" ]
  [ "$(readlink "$TEST_HOME/.codex/skills/example")" = "$CANONICAL/example" ]
  [ ! -L "$TEST_HOME/.codex/skills/removed" ]
  [ "$(<"$TEST_HOME/.codex/skills/.system/sentinel")" = keep ]
  grep -F '"path": "~/.agents/skills"' "$TEST_HOME/.gemini/config/skills.json"
}

@test "refuses to replace a non-empty harness skills directory" {
  mkdir -p "$TEST_HOME/.claude/skills"
  printf '%s\n' keep >"$TEST_HOME/.claude/skills/sentinel"

  run env HOME="$TEST_HOME" AGENT_SKILLS_HOME="$CANONICAL" "$SCRIPT"

  [ "$status" -eq 1 ]
  [[ "$output" == *'non-empty directory; refuse to replace'* ]]
  [ "$(<"$TEST_HOME/.claude/skills/sentinel")" = keep ]
}
