#!/usr/bin/env python3
"""Prove that Codex exposes exactly one intended skill candidate without a model call."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess  # nosec B404 - fixed Codex diagnostic commands require a child process.
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROMPT = "A Traycer ticket changed status; reconcile it with Nuno's Notion Task List."
ROOT_PATTERN = re.compile(r"^- `(?P<alias>r\d+)` = `(?P<path>[^`]+)`$")


class PreflightError(RuntimeError):
    """Raised when the single-candidate invariant cannot be proven."""


@dataclass(frozen=True)
class SkillEntry:
    name: str
    description: str
    path: Path

    def as_record(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
        }


@dataclass(frozen=True)
class Diagnostic:
    raw_sha256: str
    entries: tuple[SkillEntry, ...]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_bundle(bundle: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in bundle.rglob("*") if item.is_file()):
        relative_path = path.relative_to(bundle).as_posix().encode()
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def parse_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise PreflightError(f"invalid double-quoted frontmatter scalar: {error}") from error
        if not isinstance(parsed, str):
            raise PreflightError("frontmatter scalar must be a string")
        return parsed
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def read_frontmatter(skill_path: Path) -> tuple[str, str]:
    text = skill_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise PreflightError(f"missing YAML frontmatter in {skill_path}")
    try:
        closing_index = lines.index("---", 1)
    except ValueError as error:
        raise PreflightError(f"unterminated YAML frontmatter in {skill_path}") from error

    fields: dict[str, str] = {}
    for line in lines[1:closing_index]:
        key, separator, value = line.partition(":")
        if separator and key in {"name", "description"}:
            fields[key] = parse_scalar(value)
    if not fields.get("name") or not fields.get("description"):
        raise PreflightError(f"frontmatter in {skill_path} must define name and description")
    return fields["name"], fields["description"]


def require_physical_candidate(workspace: Path, candidate: Path, skill_name: str) -> tuple[Path, Path]:
    try:
        resolved_workspace = workspace.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise PreflightError(f"workspace and candidate must already exist: {error.filename}") from error

    expected = resolved_workspace / ".agents" / "skills" / skill_name / "SKILL.md"
    if resolved_candidate != expected:
        raise PreflightError(f"candidate must be the physical project copy at {expected}")
    if not resolved_workspace.is_dir() or not resolved_candidate.is_file():
        raise PreflightError("workspace must be a directory and candidate must be a regular file")

    bundle = resolved_candidate.parent
    for path in (bundle, *bundle.rglob("*")):
        if path.is_symlink():
            raise PreflightError(f"candidate bundle must not contain symlinks: {path}")
    return resolved_workspace, resolved_candidate


def developer_texts(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        raise PreflightError("Codex prompt-input output must be a JSON message array")
    texts: list[str] = []
    for message in payload:
        if not isinstance(message, dict) or message.get("role") != "developer":
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "input_text":
                continue
            text = item.get("text")
            if isinstance(text, str):
                texts.append(text)
    return texts


def parse_skill_entries(raw: bytes, skill_name: str) -> tuple[SkillEntry, ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PreflightError(f"Codex prompt-input returned invalid JSON: {error}") from error

    skill_blocks = [text for text in developer_texts(payload) if "<skills_instructions>" in text]
    if len(skill_blocks) != 1:
        raise PreflightError(f"expected one skills instruction block, found {len(skill_blocks)}")
    block = skill_blocks[0]
    roots: dict[str, Path] = {}
    for line in block.splitlines():
        match = ROOT_PATTERN.match(line)
        if match:
            roots[match.group("alias")] = Path(match.group("path")).resolve(strict=False)

    entry_pattern = re.compile(rf"^- {re.escape(skill_name)}: (?P<description>.*) \(file: (?P<locator>[^)]+)\)$")
    entries: list[SkillEntry] = []
    for line in block.splitlines():
        match = entry_pattern.match(line)
        if not match:
            continue
        locator = match.group("locator")
        located_path = Path(locator)
        if not located_path.is_absolute():
            alias, separator, remainder = locator.partition("/")
            if not separator or alias not in roots:
                raise PreflightError(f"unresolved skill locator in Codex prompt: {locator}")
            located_path = roots[alias] / remainder
        entries.append(
            SkillEntry(
                name=skill_name,
                description=match.group("description"),
                path=located_path.resolve(strict=False),
            )
        )
    return tuple(entries)


def run_codex(
    codex_path: Path,
    workspace: Path,
    prompt: str,
    config_override: str | None,
    skill_name: str,
) -> Diagnostic:
    command = [str(codex_path), "debug", "prompt-input"]
    if config_override is not None:
        command.extend(["-c", config_override])
    command.append(prompt)
    try:
        completed = subprocess.run(  # nosec B603 - argv is fixed and shell execution is disabled.
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise PreflightError("Codex prompt-input diagnostic timed out") from error
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PreflightError(f"Codex prompt-input diagnostic failed with exit {completed.returncode}: {stderr}")
    return Diagnostic(
        raw_sha256=sha256_bytes(completed.stdout),
        entries=parse_skill_entries(completed.stdout, skill_name),
    )


def codex_version(codex_path: Path) -> str:
    completed = subprocess.run(  # nosec B603 - argv is fixed and shell execution is disabled.
        [str(codex_path), "--version"],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise PreflightError("could not read the Codex CLI version")
    return completed.stdout.decode("utf-8", errors="replace").strip()


def build_config_override(paths: list[Path]) -> str | None:
    if not paths:
        return None
    entries = ",".join(f"{{path={json.dumps(str(path))},enabled=false}}" for path in paths)
    return f"skills.config=[{entries}]"


def run_preflight(workspace: Path, candidate: Path, skill_name: str, prompt: str) -> dict[str, Any]:
    resolved_workspace, resolved_candidate = require_physical_candidate(workspace, candidate, skill_name)
    frontmatter_name, frontmatter_description = read_frontmatter(resolved_candidate)
    if frontmatter_name != skill_name:
        raise PreflightError(f"candidate declares name {frontmatter_name!r}, expected {skill_name!r}")

    located_codex = shutil.which("codex")
    if located_codex is None:
        raise PreflightError("codex executable was not found on PATH")
    codex_path = Path(located_codex).resolve(strict=True)

    before = run_codex(codex_path, resolved_workspace, prompt, None, skill_name)
    candidate_entries = [entry for entry in before.entries if entry.path == resolved_candidate]
    if len(candidate_entries) != 1:
        raise PreflightError(
            f"Codex must expose the intended candidate exactly once before filtering; found {len(candidate_entries)}"
        )

    competing_paths = sorted({entry.path for entry in before.entries if entry.path != resolved_candidate})
    config_override = build_config_override(competing_paths)
    after = run_codex(codex_path, resolved_workspace, prompt, config_override, skill_name)
    if len(after.entries) != 1 or after.entries[0].path != resolved_candidate:
        remaining = ", ".join(str(entry.path) for entry in after.entries) or "none"
        raise PreflightError(f"Codex did not converge to the intended single candidate; remaining: {remaining}")
    if after.entries[0].description != frontmatter_description:
        raise PreflightError("Codex prompt description does not match the candidate frontmatter")

    return {
        "schema_version": 1,
        "passed": True,
        "model_calls": 0,
        "codex_version": codex_version(codex_path),
        "skill_name": skill_name,
        "workspace": str(resolved_workspace),
        "candidate": {
            "path": str(resolved_candidate),
            "sha256": sha256_file(resolved_candidate),
            "bundle_sha256": sha256_bundle(resolved_candidate.parent),
            "description": frontmatter_description,
        },
        "before": {
            "prompt_input_sha256": before.raw_sha256,
            "candidates": [entry.as_record() for entry in before.entries],
        },
        "filter": {
            "disabled_paths": [str(path) for path in competing_paths],
            "config_override": config_override,
        },
        "after": {
            "prompt_input_sha256": after.raw_sha256,
            "candidates": [entry.as_record() for entry in after.entries],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--skill-name", default="sync-traycer-notion")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        result = run_preflight(
            workspace=arguments.workspace,
            candidate=arguments.candidate,
            skill_name=arguments.skill_name,
            prompt=arguments.prompt,
        )
    except (OSError, PreflightError) as error:
        print(json.dumps({"schema_version": 1, "passed": False, "model_calls": 0, "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
