#!/usr/bin/env python3
"""Freeze Codex's full skill inventory around one intended candidate without a model call."""

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
ENTRY_PATTERN = re.compile(r"^- (?P<label>.+) \(file: (?P<locator>[^)]+)\)$")


class PreflightError(RuntimeError):
    """Raised when the candidate or frozen-inventory invariant cannot be proven."""


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
    skills_instructions_sha256: str
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
    try:
        text = skill_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise PreflightError(f"candidate SKILL.md must be valid UTF-8: {skill_path}") from error
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


def parse_diagnostic(raw: bytes) -> Diagnostic:
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PreflightError(f"Codex prompt-input returned invalid JSON: {error}") from error

    skill_blocks = [text for text in developer_texts(payload) if "<skills_instructions>" in text]
    if len(skill_blocks) != 1:
        raise PreflightError(f"expected one skills instruction block, found {len(skill_blocks)}")
    text = skill_blocks[0]
    lines = text.splitlines()
    if lines.count("<skills_instructions>") != 1 or lines.count("</skills_instructions>") != 1:
        raise PreflightError("expected one complete skills instruction block")
    block_start = lines.index("<skills_instructions>")
    block_end = lines.index("</skills_instructions>")
    if block_end <= block_start:
        raise PreflightError("skills instruction block is malformed")
    block_lines = lines[block_start : block_end + 1]
    available_headers = [index for index, line in enumerate(block_lines) if line == "### Available skills"]
    if len(available_headers) != 1:
        raise PreflightError(f"expected one Available skills section, found {len(available_headers)}")
    available_index = available_headers[0]
    if any(line.startswith("- ") and "(file:" in line for line in block_lines[:available_index]):
        raise PreflightError("skill inventory entry appeared before the Available skills section")

    block = "\n".join(block_lines)
    roots: dict[str, Path] = {}
    for line in block_lines[:available_index]:
        match = ROOT_PATTERN.match(line)
        if match:
            roots[match.group("alias")] = Path(match.group("path")).resolve(strict=False)

    entries: list[SkillEntry] = []
    for line in block_lines[available_index + 1 : -1]:
        if not line.startswith("- "):
            continue
        match = ENTRY_PATTERN.match(line)
        if not match:
            raise PreflightError(f"unrecognized skill inventory entry: {line}")
        name, separator, description = match.group("label").partition(": ")
        if not separator or not name or not description:
            raise PreflightError(f"invalid skill inventory entry: {line}")
        locator = match.group("locator")
        located_path = Path(locator)
        if not located_path.is_absolute():
            alias, separator, remainder = locator.partition("/")
            if not separator or alias not in roots:
                raise PreflightError(f"unresolved skill locator in Codex prompt: {locator}")
            located_path = roots[alias] / remainder
        entries.append(
            SkillEntry(
                name=name,
                description=description,
                path=located_path.resolve(strict=False),
            )
        )
    if not entries:
        raise PreflightError("Codex prompt-input exposed an empty skill inventory")
    return Diagnostic(
        raw_sha256=sha256_bytes(raw),
        skills_instructions_sha256=sha256_bytes(block.encode()),
        entries=tuple(entries),
    )


def inventory_record(entries: tuple[SkillEntry, ...]) -> dict[str, Any]:
    records = sorted(
        (entry.as_record() for entry in entries),
        key=lambda entry: (entry["name"], entry["path"], entry["description"]),
    )
    canonical = json.dumps(records, separators=(",", ":"), sort_keys=True).encode()
    return {
        "count": len(records),
        "sha256": sha256_bytes(canonical),
        "entries": records,
    }


def run_codex(
    codex_path: Path,
    workspace: Path,
    prompt: str,
    config_override: str | None,
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
    return parse_diagnostic(completed.stdout)


def codex_version(codex_path: Path) -> str:
    try:
        completed = subprocess.run(  # nosec B603 - argv is fixed and shell execution is disabled.
            [str(codex_path), "--version"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        raise PreflightError("Codex CLI version check timed out") from error
    if completed.returncode != 0:
        raise PreflightError("could not read the Codex CLI version")
    return completed.stdout.decode("utf-8", errors="replace").strip()


def build_config_override(paths: list[Path]) -> str | None:
    if not paths:
        return None
    entries = ",".join(f"{{path={json.dumps(str(path))},enabled=false}}" for path in paths)
    return f"skills.config=[{entries}]"


def diagnostic_record(diagnostic: Diagnostic, skill_name: str) -> dict[str, Any]:
    return {
        "prompt_input_sha256": diagnostic.raw_sha256,
        "skills_instructions_sha256": diagnostic.skills_instructions_sha256,
        "inventory": inventory_record(diagnostic.entries),
        "candidates": [entry.as_record() for entry in diagnostic.entries if entry.name == skill_name],
    }


def read_expected_evidence(path: Path, workspace: Path) -> tuple[Path, str, dict[str, Any]]:
    try:
        resolved_path = path.resolve(strict=True)
    except FileNotFoundError as error:
        raise PreflightError(f"expected evidence does not exist: {path}") from error
    if resolved_path.is_relative_to(workspace):
        raise PreflightError("expected evidence must remain outside the evaluated workspace")
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PreflightError(f"expected evidence is not valid UTF-8 JSON: {resolved_path}") from error
    if not isinstance(payload, dict):
        raise PreflightError("expected evidence must be a JSON object")
    return resolved_path, sha256_file(resolved_path), payload


def verify_expected_evidence(
    result: dict[str, Any],
    expected_path: Path,
    workspace: Path,
) -> dict[str, Any]:
    resolved_path, evidence_sha256, expected = read_expected_evidence(expected_path, workspace)
    if expected.get("schema_version") != 2 or expected.get("passed") is not True:
        raise PreflightError("expected evidence must be a passed schema-version 2 preflight record")
    before = expected.get("before")
    after = expected.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise PreflightError("expected evidence before and after sections must be JSON objects")

    comparisons = {
        "Codex version": (expected.get("codex_version"), result["codex_version"]),
        "skill name": (expected.get("skill_name"), result["skill_name"]),
        "workspace": (expected.get("workspace"), result["workspace"]),
        "candidate": (expected.get("candidate"), result["candidate"]),
        "before inventory": (before.get("inventory"), result["before"]["inventory"]),
        "before skills block": (
            before.get("skills_instructions_sha256"),
            result["before"]["skills_instructions_sha256"],
        ),
        "filter": (expected.get("filter"), result["filter"]),
        "after inventory": (after.get("inventory"), result["after"]["inventory"]),
        "after skills block": (
            after.get("skills_instructions_sha256"),
            result["after"]["skills_instructions_sha256"],
        ),
    }
    mismatches = [label for label, (wanted, observed) in comparisons.items() if wanted != observed]
    if mismatches:
        raise PreflightError(f"frozen inventory mismatch: {', '.join(mismatches)}")
    return {
        "mode": "verify",
        "verified": True,
        "expected_evidence": {
            "path": str(resolved_path),
            "sha256": evidence_sha256,
        },
    }


def run_preflight(
    workspace: Path,
    candidate: Path,
    skill_name: str,
    prompt: str,
    expected_evidence: Path | None = None,
) -> dict[str, Any]:
    resolved_workspace, resolved_candidate = require_physical_candidate(workspace, candidate, skill_name)
    frontmatter_name, frontmatter_description = read_frontmatter(resolved_candidate)
    if frontmatter_name != skill_name:
        raise PreflightError(f"candidate declares name {frontmatter_name!r}, expected {skill_name!r}")

    located_codex = shutil.which("codex")
    if located_codex is None:
        raise PreflightError("codex executable was not found on PATH")
    codex_path = Path(located_codex).resolve(strict=True)

    before = run_codex(codex_path, resolved_workspace, prompt, None)
    candidate_entries = [
        entry for entry in before.entries if entry.name == skill_name and entry.path == resolved_candidate
    ]
    if len(candidate_entries) != 1:
        raise PreflightError(
            f"Codex must expose the intended candidate exactly once before filtering; found {len(candidate_entries)}"
        )

    competing_paths = sorted(
        {entry.path for entry in before.entries if entry.name == skill_name and entry.path != resolved_candidate}
    )
    config_override = build_config_override(competing_paths)
    after = run_codex(codex_path, resolved_workspace, prompt, config_override)
    remaining_candidates = [entry for entry in after.entries if entry.name == skill_name]
    if len(remaining_candidates) != 1 or remaining_candidates[0].path != resolved_candidate:
        remaining = ", ".join(str(entry.path) for entry in remaining_candidates) or "none"
        raise PreflightError(f"Codex did not converge to the intended single candidate; remaining: {remaining}")
    if remaining_candidates[0].description != frontmatter_description:
        raise PreflightError("Codex prompt description does not match the candidate frontmatter")

    result: dict[str, Any] = {
        "schema_version": 2,
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
        "before": diagnostic_record(before, skill_name),
        "filter": {
            "disabled_paths": [str(path) for path in competing_paths],
            "config_override": config_override,
        },
        "after": diagnostic_record(after, skill_name),
    }
    if expected_evidence is None:
        result["verification"] = {
            "mode": "capture",
            "verified": False,
            "expected_evidence": None,
        }
    else:
        result["verification"] = verify_expected_evidence(result, expected_evidence, resolved_workspace)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--skill-name", default="sync-traycer-notion")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--expected-evidence",
        type=Path,
        help="external schema-v2 capture to re-verify immediately before model execution",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        result = run_preflight(
            workspace=arguments.workspace,
            candidate=arguments.candidate,
            skill_name=arguments.skill_name,
            prompt=arguments.prompt,
            expected_evidence=arguments.expected_evidence,
        )
    except (OSError, PreflightError) as error:
        print(json.dumps({"schema_version": 2, "passed": False, "model_calls": 0, "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
