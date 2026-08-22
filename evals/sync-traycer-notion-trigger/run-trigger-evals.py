#!/usr/bin/env python3
"""Run and grade paired automatic-trigger evaluations for this skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import shutil
import stat
import subprocess  # nosec B404 - fixed argv, shell=False, and trace capture are required for the eval harness.
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, cast

SCHEMA_VERSION = 1
EVIDENCE_SCHEMA_VERSION = 2
SKILL_NAME = "sync-traycer-notion"
SUITE_NAME = "sync-traycer-notion-trigger-behavior"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / SKILL_NAME
DEFAULT_KEY_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/key-manifest.json"
DEFAULT_PROOF_REPORT = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/proof-report.json"
DEFAULT_CUSTODY_RUNBOOK = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/CUSTODY.md"
DEFAULT_EVIDENCE_CONTRACT = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/evidence_contract.py"
DEFAULT_PRIVATE_VERIFIER = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/verify_private_evidence.py"
ARMS = ("baseline", "with_skill")
VARIANTS = ("positive", "near_miss")
DEFAULT_CASE_PACK = REPOSITORY_ROOT / "evals/sync-traycer-notion/suite.json"
RESPONSE_FIELDS = (
    "classification",
    "ordered_plan",
    "notion_changes",
    "artifact_changes",
    "stop_conditions",
)
ALLOWED_TOOL_NAMES = frozenset({"Glob", "Grep", "Read", "Skill"})
FORBIDDEN_PROMPT_TERMS = (SKILL_NAME.casefold(), f"${SKILL_NAME}".casefold())
EXPECTED_SKILL_TREE = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/notion-task-list.md",
)
THRESHOLD_FIELDS = (
    "positive_trigger_recall_bps",
    "near_miss_abstention_bps",
    "paired_delta_ci_lower_bps",
    "critical_regressions",
)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
CASE_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
GPG_FINGERPRINT_PATTERN = re.compile(r"[0-9A-F]{40}")
MAX_SAFE_INTEGER = (1 << 53) - 1


class EvalError(RuntimeError):
    """Raised when an evaluation artifact or trace is invalid."""


class EvidenceContractError(ValueError):
    """Raised when evidence does not satisfy the portable JSON contract."""


def _reject_float(value: str) -> float:
    raise EvidenceContractError(f"floating-point value is not allowed: {value}")


def _reject_constant(value: str) -> None:
    raise EvidenceContractError(f"non-finite value is not allowed: {value}")


def _object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceContractError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _validate_string(value: str, label: str) -> None:
    for character in value:
        if 0xD800 <= ord(character) <= 0xDFFF:
            raise EvidenceContractError(f"{label} contains a lone surrogate")


def validate_canonical_value(value: Any, label: str = "value") -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise EvidenceContractError(f"{label} integer is outside the safe range")
        return
    if isinstance(value, str):
        _validate_string(value, label)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_canonical_value(item, f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvidenceContractError(f"{label} has a non-string object key")
            _validate_string(key, f"{label} key")
            validate_canonical_value(item, f"{label}.{key}")
        return
    raise EvidenceContractError(f"{label} has unsupported type {type(value).__name__}")


def parse_canonical_json(raw: bytes, label: str = "JSON") -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise EvidenceContractError(f"{label} is not valid UTF-8") from error
    try:
        value: Any = json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise EvidenceContractError(f"{label} is not valid JSON: {error.msg}") from error
    validate_canonical_value(value, label)
    return value


def _canonical_ordered(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_ordered(value[key]) for key in sorted(value, key=lambda item: item.encode("utf-8"))}
    if isinstance(value, list):
        return [_canonical_ordered(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    validate_canonical_value(value)
    return json.dumps(
        _canonical_ordered(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def read_canonical_json(path: Path) -> Any:
    return parse_canonical_json(path.read_bytes(), str(path))


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = read_canonical_json(path)
    except EvidenceContractError as error:
        raise EvalError(str(error)) from error
    if not isinstance(value, dict):
        raise EvalError(f"expected a JSON object in {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def directory_sha256(root: Path) -> str:
    records: list[bytes] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            records.append(path.relative_to(root).as_posix().encode() + b"\0" + sha256_file(path).encode() + b"\n")
    return sha256_bytes(b"".join(records))


def skill_tree_sha256(root: Path) -> str:
    records: list[bytes] = []
    actual_paths: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise EvalError(f"skill tree contains a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise EvalError(f"skill tree contains a non-regular file: {relative}")
        actual_paths.append(relative)
    if sorted(actual_paths, key=lambda item: item.encode("utf-8")) != list(EXPECTED_SKILL_TREE):
        raise EvalError(f"skill tree paths do not match the contract: {actual_paths}")
    for relative in EXPECTED_SKILL_TREE:
        digest = sha256_file(root / relative)
        records.append(relative.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
    return sha256_bytes(b"".join(records))


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def require_external_output_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise EvalError("raw eval output must be outside the repository")
    if resolved.exists() and any(resolved.iterdir()):
        raise EvalError(f"output directory must be absent or empty: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def require_external_input(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise EvalError(f"{label} must be outside the repository")
    return resolved


def require_external_output_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise EvalError(f"{label} must be outside the repository")
    if resolved.exists():
        raise EvalError(f"{label} must not already exist: {resolved}")
    return resolved


def require_disjoint_roots(evidence_root: Path, workspace_root: Path) -> None:
    evidence = evidence_root.resolve()
    workspace = workspace_root.resolve()
    if evidence == workspace or path_is_within(evidence, workspace) or path_is_within(workspace, evidence):
        raise EvalError("workspace and evidence roots must be disjoint")


def relative_evidence_path(path: Path, evidence_root: Path) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(evidence_root.resolve(strict=True))
    except ValueError as error:
        raise EvalError(f"evidence path escapes its root: {path}") from error
    return relative.as_posix()


def resolve_evidence_path(manifest_root: Path, raw_path: Any, label: str) -> Path:
    relative = PurePosixPath(require_string(raw_path, label))
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise EvalError(f"{label} must be a normalized relative path")
    if "\\" in str(relative):
        raise EvalError(f"{label} must use POSIX separators")
    root = manifest_root.resolve(strict=True)
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise EvalError(f"{label} traverses a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except ValueError as error:
        raise EvalError(f"{label} escapes the evidence archive") from error
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise EvalError(f"{label} is not a regular file")
    return resolved


def require_commit_sha(value: Any, label: str) -> str:
    commit = require_string(value, label)
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise EvalError(f"{label} must be a 40-character lowercase commit SHA")
    return commit


def require_sha256(value: Any, label: str) -> str:
    digest = require_string(value, label)
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise EvalError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def require_normalized_posix_path(value: Any, label: str) -> str:
    raw_path = require_string(value, label)
    path = PurePosixPath(raw_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise EvalError(f"{label} must be a normalized relative POSIX path")
    if "\\" in raw_path:
        raise EvalError(f"{label} must use POSIX separators")
    return raw_path


def require_iso_timestamp(value: Any, label: str) -> str:
    timestamp = require_string(value, label)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvalError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise EvalError(f"{label} must include a timezone")
    return timestamp


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvalError(f"{label} must be a non-empty string")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvalError(f"{label} must be a list")
    return value


def require_integer(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EvalError(f"{label} must be an integer from {minimum} through {maximum}")
    return value


def cost_microusd(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise EvalError("result total_cost_usd is missing or invalid")
    try:
        cost = Decimal(str(value))
    except InvalidOperation as error:
        raise EvalError("result total_cost_usd is invalid") from error
    if not cost.is_finite() or cost < 0:
        raise EvalError("result total_cost_usd must be finite and non-negative")
    return int((cost * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validated_max_budget(value: Any) -> str:
    if not isinstance(value, str):
        raise EvalError("max budget must be a decimal string")
    try:
        budget = Decimal(value)
    except InvalidOperation as error:
        raise EvalError("max budget is invalid") from error
    if not budget.is_finite() or budget <= 0 or budget > Decimal("1.00"):
        raise EvalError("max budget must be greater than zero and no more than 1.00 USD")
    return format(budget, "f")


def validated_thresholds(pack: Mapping[str, Any]) -> dict[str, int]:
    raw_thresholds = pack.get("thresholds")
    if not isinstance(raw_thresholds, dict) or set(raw_thresholds) != set(THRESHOLD_FIELDS):
        raise EvalError(f"suite thresholds must be exactly {THRESHOLD_FIELDS}")
    thresholds = {
        "positive_trigger_recall_bps": require_integer(
            raw_thresholds.get("positive_trigger_recall_bps"),
            "thresholds.positive_trigger_recall_bps",
            minimum=0,
            maximum=10000,
        ),
        "near_miss_abstention_bps": require_integer(
            raw_thresholds.get("near_miss_abstention_bps"),
            "thresholds.near_miss_abstention_bps",
            minimum=0,
            maximum=10000,
        ),
        "paired_delta_ci_lower_bps": require_integer(
            raw_thresholds.get("paired_delta_ci_lower_bps"),
            "thresholds.paired_delta_ci_lower_bps",
            minimum=-10000,
            maximum=10000,
        ),
        "critical_regressions": require_integer(
            raw_thresholds.get("critical_regressions"),
            "thresholds.critical_regressions",
            minimum=0,
            maximum=1000000,
        ),
    }
    return thresholds


def validated_confidence_profile(pack: Mapping[str, Any]) -> dict[str, Any]:
    profile = pack.get("paired_confidence")
    if not isinstance(profile, dict):
        raise EvalError("suite paired_confidence must be an object")
    if profile.get("method") != "case-cluster-bootstrap-v1":
        raise EvalError("paired confidence method must be case-cluster-bootstrap-v1")
    return {
        "method": "case-cluster-bootstrap-v1",
        "confidence_bps": require_integer(
            profile.get("confidence_bps"), "paired_confidence.confidence_bps", minimum=1, maximum=9999
        ),
        "resamples": require_integer(
            profile.get("resamples"), "paired_confidence.resamples", minimum=1000, maximum=1000000
        ),
        "schedule_seed": require_string(profile.get("schedule_seed"), "paired_confidence.schedule_seed"),
    }


def validated_case_pack(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    pack = read_json(path)
    if pack.get("schema_version") != SCHEMA_VERSION or pack.get("skill_name") != SKILL_NAME:
        raise EvalError("production suite schema or skill mismatch")
    policy = pack.get("execution_policy")
    if not isinstance(policy, dict) or policy.get("trials_per_harness") != 3:
        raise EvalError("production suite must require three trials per harness")
    validated_thresholds(pack)
    validated_confidence_profile(pack)

    cases: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    counts = {"positive": 0, "near_miss": 0}
    for index, raw_case in enumerate(require_list(pack.get("tasks"), "tasks")):
        if not isinstance(raw_case, dict):
            raise EvalError(f"case {index} must be an object")
        case_id = require_string(raw_case.get("id"), f"case {index}.id")
        if CASE_ID_PATTERN.fullmatch(case_id) is None:
            raise EvalError(f"case {index}.id must be a lowercase hyphenated identifier")
        if case_id in seen_ids:
            raise EvalError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        raw_kind = raw_case.get("kind")
        if raw_kind not in {"positive", "near-miss"}:
            raise EvalError(f"{case_id}.kind is invalid")
        variant = "near_miss" if raw_kind == "near-miss" else "positive"
        prompt = require_string(raw_case.get("prompt"), f"{case_id}.prompt")
        folded = prompt.casefold()
        if any(term in folded for term in FORBIDDEN_PROMPT_TERMS):
            raise EvalError(f"{case_id} explicitly names the skill")
        counts[variant] += 1
        case = {"id": case_id, "variant": variant, "prompt": prompt}
        cases.append(case)
    if counts != {"positive": 12, "near_miss": 8}:
        raise EvalError(f"production suite must contain 12 positive and 8 near-miss tasks: {counts}")
    return pack, cases


def render_prompt(_pack: Mapping[str, Any], case: Mapping[str, str]) -> str:
    field_text = ", ".join(RESPONSE_FIELDS)
    return (
        "You are in a fresh, plan-only agent session. Use only the supplied runtime context and snapshot. "
        "Do not execute changes or contact external services. The CLI is already in plan permission mode; "
        "do not call Write or create a plan file. Return the requested JSON directly.\n\n"
        f"Task:\n{case['prompt']}\n\n"
        f"Return only one JSON object with these top-level fields: {field_text}. "
        "Make every intended change and stop condition explicit."
    )


def parse_stream_json(raw: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            value: Any = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise EvalError(f"invalid stream JSON on line {line_number}: {error.msg}") from error
        if not isinstance(value, dict):
            raise EvalError(f"stream event on line {line_number} is not an object")
        events.append(value)
    if not events:
        raise EvalError("Claude emitted no stream events")
    return events


def init_event(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    raise EvalError("trace has no system/init event")


def tool_uses(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    uses: list[dict[str, Any]] = []
    for event in events:
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_use":
                uses.append(item)
    return uses


def result_event(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    results = [event for event in events if event.get("type") == "result"]
    if len(results) != 1:
        raise EvalError(f"expected exactly one result event, found {len(results)}")
    return results[0]


def skill_name_from_use(tool_use: Mapping[str, Any]) -> str | None:
    if tool_use.get("name") != "Skill":
        return None
    value = tool_use.get("input")
    if not isinstance(value, dict):
        return None
    skill = value.get("skill")
    return skill if isinstance(skill, str) else None


def trace_summary(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    initialization = init_event(events)
    uses = tool_uses(events)
    result = result_event(events)
    skills = initialization.get("skills", [])
    tools = initialization.get("tools", [])
    mcp_servers = initialization.get("mcp_servers", [])
    if not isinstance(skills, list) or not all(isinstance(skill, str) for skill in skills):
        raise EvalError("init skills field is invalid")
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        raise EvalError("init tools field is invalid")
    if not isinstance(mcp_servers, list):
        raise EvalError("init mcp_servers field is invalid")
    used_names = [str(use.get("name", "")) for use in uses]
    unexpected_tools = sorted(set(used_names) - ALLOWED_TOOL_NAMES)
    invoked_skills = [skill for use in uses if (skill := skill_name_from_use(use)) is not None]
    response = result.get("result")
    if not isinstance(response, str):
        raise EvalError("result response is missing")
    model_usage = result.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        raise EvalError("result modelUsage is missing or invalid")
    input_tokens = 0
    output_tokens = 0
    for name, record in model_usage.items():
        if not isinstance(record, dict):
            raise EvalError(f"result modelUsage.{name} is invalid")
        input_tokens += sum(
            require_integer(record.get(field, 0), f"modelUsage.{name}.{field}", minimum=0, maximum=MAX_SAFE_INTEGER)
            for field in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        )
        output_tokens += require_integer(
            record.get("outputTokens", 0), f"modelUsage.{name}.outputTokens", minimum=0, maximum=MAX_SAFE_INTEGER
        )
    return {
        "cost_microusd": cost_microusd(result.get("total_cost_usd")),
        "discovered_target_skill": SKILL_NAME in skills,
        "input_tokens": input_tokens,
        "invoked_skills": invoked_skills,
        "latency_ms": require_integer(
            result.get("duration_ms"), "result.duration_ms", minimum=0, maximum=MAX_SAFE_INTEGER
        ),
        "model": require_string(initialization.get("model"), "init.model"),
        "mcp_server_count": len(mcp_servers),
        "response": response,
        "session_id": require_string(initialization.get("session_id"), "init.session_id"),
        "success": result.get("subtype") == "success" and result.get("is_error") is False,
        "output_tokens": output_tokens,
        "tools_advertised": tools,
        "tools_used": used_names,
        "unexpected_tools": unexpected_tools,
    }


def expected_trigger_state(arm: str, variant: str) -> tuple[bool, bool]:
    discovered = arm == "with_skill"
    triggered = discovered and variant == "positive"
    return discovered, triggered


def validate_trace_state(summary: Mapping[str, Any], arm: str, variant: str) -> list[str]:
    errors: list[str] = []
    expected_discovery, expected_trigger = expected_trigger_state(arm, variant)
    invoked_skills = summary.get("invoked_skills")
    triggered = isinstance(invoked_skills, list) and SKILL_NAME in invoked_skills
    if summary.get("discovered_target_skill") is not expected_discovery:
        errors.append(f"target discovery mismatch: expected {expected_discovery}")
    if triggered is not expected_trigger:
        errors.append(f"target trigger mismatch: expected {expected_trigger}")
    if isinstance(invoked_skills, list) and any(skill != SKILL_NAME for skill in invoked_skills):
        errors.append("an unrelated skill was invoked")
    if summary.get("mcp_server_count") != 0:
        errors.append("MCP servers were available")
    advertised = summary.get("tools_advertised")
    if not isinstance(advertised, list) or set(advertised) != ALLOWED_TOOL_NAMES:
        errors.append("advertised tools did not match the qualified Claude profile")
    if summary.get("unexpected_tools") != []:
        errors.append(f"unexpected tools were used: {summary.get('unexpected_tools')}")
    if summary.get("success") is not True:
        errors.append("Claude result was not successful")
    cost = summary.get("cost_microusd")
    if isinstance(cost, bool) or not isinstance(cost, int) or not 0 <= cost <= 1_000_000:
        errors.append("Claude session cost was missing, invalid, or exceeded 1 USD")
    return errors


def claude_command(claude_bin: str, max_budget_usd: str, prompt: str) -> list[str]:
    return [
        claude_bin,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "claude-opus-5[1m]",
        "--effort",
        "xhigh",
        "--setting-sources",
        "project",
        "--tools",
        "Skill,Read,Glob,Grep",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--permission-mode",
        "plan",
        "--max-budget-usd",
        max_budget_usd,
        "--no-session-persistence",
        prompt,
    ]


def install_project_skill(workspace: Path, skill_root: Path) -> None:
    destination = workspace / ".claude/skills" / SKILL_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_root, destination)


def run_one(
    *,
    arm: str,
    case_id: str,
    variant: str,
    prompt: str,
    evidence_root: Path,
    workspace_root: Path,
    skill_root: Path,
    claude_bin: str,
    max_budget_usd: str,
    timeout_seconds: int,
    trial_number: int = 1,
) -> dict[str, Any]:
    run_id = f"{case_id}--{variant}--t{trial_number}--{arm}"
    workspace = workspace_root / run_id
    workspace.mkdir(parents=True)
    (workspace / ".claude").mkdir(exist_ok=True)
    if arm == "with_skill":
        install_project_skill(workspace, skill_root)
    before_state_sha256 = directory_sha256(workspace)

    command = claude_command(claude_bin, max_budget_usd, prompt)
    invocation_errors: list[str] = []
    try:
        completed = subprocess.run(  # nosec B603 - command is an argv list and never uses a shell.
            command,
            capture_output=True,
            check=False,
            cwd=workspace,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        invocation_errors.append(f"Claude timed out after {timeout_seconds} seconds")
        completed = subprocess.CompletedProcess(
            command,
            124,
            stdout=error.stdout or b"",
            stderr=error.stderr or b"",
        )
    trace_path = evidence_root / "traces" / f"{run_id}.jsonl"
    stderr_path = evidence_root / "traces" / f"{run_id}.stderr"
    response_path = evidence_root / "responses" / f"{run_id}.txt"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)

    errors = invocation_errors
    summary: dict[str, Any] = {}
    try:
        summary = trace_summary(parse_stream_json(completed.stdout))
        errors.extend(validate_trace_state(summary, arm, variant))
    except EvalError as error:
        errors.append(str(error))
    response = summary.pop("response", "")
    if not isinstance(response, str):
        errors.append("parsed response is not text")
        response = ""
    with response_path.open("x", encoding="utf-8") as output:
        output.write(response)
    if completed.returncode != 0:
        errors.append(f"Claude exited with status {completed.returncode}")
    after_state_sha256 = directory_sha256(workspace)
    if before_state_sha256 != after_state_sha256:
        errors.append("workspace state changed during the plan-only trial")

    return {
        "after_state_sha256": after_state_sha256,
        "arm": arm,
        "before_state_sha256": before_state_sha256,
        "case_id": case_id,
        "errors": errors,
        "prompt_sha256": sha256_bytes(prompt.encode()),
        "response_path": relative_evidence_path(response_path, evidence_root),
        "response_sha256": sha256_file(response_path),
        "stderr_path": relative_evidence_path(stderr_path, evidence_root),
        "stderr_sha256": sha256_file(stderr_path),
        "trace_path": relative_evidence_path(trace_path, evidence_root),
        "trace_sha256": sha256_file(trace_path),
        "trial_number": trial_number,
        "variant": variant,
        **summary,
    }


def claude_version(claude_bin: str) -> str:
    completed = subprocess.run(  # nosec B603 - command is an argv list and never uses a shell.
        [claude_bin, "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise EvalError(f"could not read Claude version: {completed.stderr.strip()}")
    return completed.stdout.strip()


def expected_record_keys(
    cases: Sequence[Mapping[str, str]],
) -> set[tuple[str, str, int, str]]:
    expected: set[tuple[str, str, int, str]] = set()
    for case in cases:
        arms = ARMS if case["variant"] == "positive" else ("with_skill",)
        for trial_number in range(1, 4):
            expected.update((case["id"], case["variant"], trial_number, arm) for arm in arms)
    return expected


def validate_public_key_manifest(
    manifest: Mapping[str, Any],
    *,
    pack: Mapping[str, Any],
    cases: Sequence[Mapping[str, str]],
    case_pack_path: Path,
    skill_root: Path,
) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("suite") != SUITE_NAME:
        raise EvalError("key manifest schema or suite mismatch")
    require_commit_sha(manifest.get("bundle_commit"), "key manifest bundle_commit")
    require_sha256(manifest.get("key_sha256"), "key manifest key_sha256")
    require_sha256(manifest.get("ciphertext_sha256"), "key manifest ciphertext_sha256")
    fingerprint = require_string(manifest.get("recipient_fingerprint"), "key manifest recipient_fingerprint")
    if GPG_FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise EvalError("key manifest recipient_fingerprint must be a 40-character uppercase GPG fingerprint")
    author = require_string(manifest.get("key_author"), "key manifest key_author")
    reviewer = require_string(manifest.get("key_reviewer"), "key manifest key_reviewer")
    if author == reviewer:
        raise EvalError("key author and reviewer must be independent")
    require_iso_timestamp(manifest.get("sealed_at"), "key manifest sealed_at")
    if manifest.get("private_evidence_repository") != "nfma/agent-skills-evidence":
        raise EvalError("key manifest private_evidence_repository is invalid")
    encrypted_path = require_normalized_posix_path(
        manifest.get("encrypted_key_path"), "key manifest encrypted_key_path"
    )
    if not encrypted_path.endswith(".gpg"):
        raise EvalError("key manifest encrypted_key_path must name a GPG ciphertext")
    expected_digests = {
        "skill_sha256": sha256_file(skill_root / "SKILL.md"),
        "skill_tree_sha256": skill_tree_sha256(skill_root),
        "case_pack_sha256": sha256_file(case_pack_path),
        "case_pack_canonical_sha256": canonical_json_sha256(pack),
        "runner_sha256": sha256_file(Path(__file__)),
    }
    for field, expected in expected_digests.items():
        if manifest.get(field) != expected:
            raise EvalError(f"key manifest {field} does not bind the current bundle")
    positive_ids = sorted(case["id"] for case in cases if case["variant"] == "positive")
    near_miss_ids = sorted(case["id"] for case in cases if case["variant"] == "near_miss")
    if manifest.get("positive_case_ids") != positive_ids:
        raise EvalError("key manifest positive case ids do not match the suite")
    if manifest.get("near_miss_case_ids") != near_miss_ids:
        raise EvalError("key manifest near-miss case ids do not match the suite")
    check_counts = manifest.get("check_counts")
    if not isinstance(check_counts, dict) or sorted(check_counts) != positive_ids:
        raise EvalError("key manifest check counts do not match the positive cases")
    validated_counts = {
        case_id: require_integer(count, f"check_counts.{case_id}", minimum=1, maximum=1000)
        for case_id, count in check_counts.items()
    }
    if sum(validated_counts.values()) != manifest.get("total_checks"):
        raise EvalError("key manifest total_checks does not match check_counts")
    if manifest.get("thresholds") != validated_thresholds(pack):
        raise EvalError("key manifest thresholds do not match the suite")
    if manifest.get("paired_confidence") != validated_confidence_profile(pack):
        raise EvalError("key manifest paired confidence profile does not match the suite")
    execution = manifest.get("execution")
    if not isinstance(execution, dict) or execution != {
        "near_miss_arms": ["with_skill"],
        "positive_arms": list(ARMS),
        "session_count": 96,
        "trials_per_arm": 3,
    }:
        raise EvalError("key manifest execution policy is invalid")


def schedule_record(case: Mapping[str, str], trial_number: int, arm: str, sequence: int) -> dict[str, Any]:
    return {
        "arm": arm,
        "case_id": case["id"],
        "sequence": sequence,
        "trial_number": trial_number,
        "variant": case["variant"],
    }


def run_suite(arguments: argparse.Namespace) -> int:
    case_pack_path = arguments.case_pack.expanduser().resolve(strict=True)
    pack, cases = validated_case_pack(case_pack_path)
    skill_root = arguments.skill_dir.expanduser().resolve(strict=True)
    if skill_root.name != SKILL_NAME or not (skill_root / "SKILL.md").is_file():
        raise EvalError(f"invalid target skill directory: {skill_root}")
    key_manifest_path = arguments.key_manifest.expanduser().resolve(strict=True)
    key_manifest = read_json(key_manifest_path)
    validate_public_key_manifest(
        key_manifest,
        pack=pack,
        cases=cases,
        case_pack_path=case_pack_path,
        skill_root=skill_root,
    )
    freeze_commit = require_commit_sha(arguments.freeze_commit, "freeze_commit")
    evidence_root = require_external_output_directory(arguments.output_dir)
    workspace_root = require_external_output_directory(arguments.workspace_root)
    require_disjoint_roots(evidence_root, workspace_root)
    max_budget_usd = validated_max_budget(arguments.max_budget_usd)

    version = claude_version(arguments.claude_bin)
    trials: list[tuple[dict[str, str], int, str]] = []
    for case in cases:
        arms = ARMS if case["variant"] == "positive" else ("with_skill",)
        for trial_number in range(1, 4):
            trials.extend((case, trial_number, arm) for arm in arms)
    secrets.SystemRandom().shuffle(trials)
    execution_uuid = str(uuid.uuid4())
    schedule = {
        "execution_uuid": execution_uuid,
        "records": [
            schedule_record(case, trial_number, arm, sequence)
            for sequence, (case, trial_number, arm) in enumerate(trials, start=1)
        ],
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
    }
    schedule_path = evidence_root / "execution-schedule.json"
    write_json(schedule_path, schedule)
    shutil.copy2(key_manifest_path, evidence_root / "key-manifest.json")
    started_at = utc_now()

    def execute(trial: tuple[dict[str, str], int, str]) -> dict[str, Any]:
        case, trial_number, arm = trial
        print(f"running {case['id']} {case['variant']} t{trial_number} {arm}", flush=True)
        return run_one(
            arm=arm,
            case_id=case["id"],
            variant=case["variant"],
            prompt=render_prompt(pack, case),
            evidence_root=evidence_root,
            workspace_root=workspace_root,
            skill_root=skill_root,
            claude_bin=arguments.claude_bin,
            max_budget_usd=max_budget_usd,
            timeout_seconds=arguments.timeout_seconds,
            trial_number=trial_number,
        )

    records: list[dict[str, Any]] = []
    completion_order: list[str] = []
    with ThreadPoolExecutor(max_workers=arguments.jobs) as executor:
        futures = {executor.submit(execute, trial): trial for trial in trials}
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            completion_order.append(
                f"{record['case_id']}--{record['variant']}--t{record['trial_number']}--{record['arm']}"
            )
    records.sort(key=lambda item: (item["case_id"], item["variant"], item["trial_number"], item["arm"]))
    observed_keys = {
        (record["case_id"], record["variant"], record["trial_number"], record["arm"]) for record in records
    }
    if observed_keys != expected_record_keys(cases):
        raise EvalError("completed record set does not match the precomputed execution schedule")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "actual_completion_order": completion_order,
        "case_pack_canonical_sha256": canonical_json_sha256(pack),
        "case_pack_sha256": sha256_file(case_pack_path),
        "claude_version": version,
        "completed_at": utc_now(),
        "execution_schedule_sha256": canonical_json_sha256(schedule),
        "execution_uuid": execution_uuid,
        "freeze_commit": freeze_commit,
        "key_manifest_sha256": canonical_json_sha256(key_manifest),
        "paired_confidence": validated_confidence_profile(pack),
        "profile": {
            "effort": "xhigh",
            "mcp_servers": [],
            "model_alias": "opus",
            "model": "claude-opus-5[1m]",
            "permission_mode": "plan",
            "setting_sources": ["project"],
            "tools": sorted(ALLOWED_TOOL_NAMES),
            "trials_per_harness": 3,
        },
        "records": records,
        "runner_sha256": sha256_file(Path(__file__)),
        "skill_sha256": sha256_file(skill_root / "SKILL.md"),
        "skill_tree_sha256": skill_tree_sha256(skill_root),
        "started_from_clean_sessions": True,
        "started_at": started_at,
        "thresholds": validated_thresholds(pack),
    }
    try:
        validate_canonical_value(manifest, "run manifest")
    except EvidenceContractError as error:
        raise EvalError(str(error)) from error
    manifest_path = evidence_root / "run-manifest.json"
    write_json(manifest_path, manifest)
    failures = sum(bool(record["errors"]) for record in records)
    print(f"wrote {manifest_path}")
    print(f"trace contract failures: {failures}")
    return 1 if failures else 0


def count_manifest_checks(key: Mapping[str, Any]) -> dict[str, int]:
    raw_cases = key.get("cases")
    if not isinstance(raw_cases, dict):
        raise EvalError("key cases must be an object")
    counts: dict[str, int] = {}
    for case_id, raw_case in raw_cases.items():
        if not isinstance(case_id, str) or not isinstance(raw_case, dict):
            raise EvalError("key cases are invalid")
        checks = require_list(raw_case.get("checks"), f"key.{case_id}.checks")
        seen_check_ids: set[str] = set()
        for raw_check in checks:
            if not isinstance(raw_check, dict):
                raise EvalError(f"key.{case_id} contains an invalid check")
            check_id = require_string(raw_check.get("id"), f"key.{case_id}.check.id")
            if check_id in seen_check_ids:
                raise EvalError(f"key.{case_id} contains duplicate check id {check_id}")
            seen_check_ids.add(check_id)
            if not isinstance(raw_check.get("critical"), bool):
                raise EvalError(f"key.{case_id}.{check_id}.critical must be a boolean")
            validate_check_definition(raw_check, f"key.{case_id}.{check_id}")
        counts[case_id] = len(checks)
    return counts


def repository_evidence_report() -> dict[str, Any]:
    pack, cases = validated_case_pack(DEFAULT_CASE_PACK)
    sealed_inputs = {
        "case_pack_canonical_sha256": canonical_json_sha256(pack),
        "case_pack_sha256": sha256_file(DEFAULT_CASE_PACK),
        "custody_runbook_sha256": sha256_file(DEFAULT_CUSTODY_RUNBOOK),
        "evidence_contract_sha256": sha256_file(DEFAULT_EVIDENCE_CONTRACT),
        "private_verifier_sha256": sha256_file(DEFAULT_PRIVATE_VERIFIER),
        "runner_sha256": sha256_file(Path(__file__)),
        "skill_sha256": sha256_file(SKILL_ROOT / "SKILL.md"),
        "skill_tree_sha256": skill_tree_sha256(SKILL_ROOT),
    }
    private_evidence = {
        "encrypted_key": "pending",
        "key_manifest": "pending",
        "raw_archive": "pending",
    }
    if DEFAULT_KEY_MANIFEST.exists():
        key_manifest = read_json(DEFAULT_KEY_MANIFEST)
        validate_public_key_manifest(
            key_manifest,
            pack=pack,
            cases=cases,
            case_pack_path=DEFAULT_CASE_PACK,
            skill_root=SKILL_ROOT,
        )
        sealed_inputs["key_manifest_sha256"] = canonical_json_sha256(key_manifest)
        private_evidence["encrypted_key"] = "sealed"
        private_evidence["key_manifest"] = "sealed"

    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "status": "pending",
        "passed": False,
        "claim_scope": (
            "Repository binding only; no target-skill evaluation, automatic-trigger proof, "
            "or cross-harness portability claim"
        ),
        "generation": {
            "kind": "deterministic-repository-snapshot",
            "model_calls": 0,
            "paid_canaries_run": False,
        },
        "private_evidence": private_evidence,
        "sealed_inputs": sealed_inputs,
        "production_contract": {
            "human_calibration": "pending",
            "harnesses": {
                "antigravity": "pending",
                "claude-code": "pending",
                "codex": "pending",
                "cursor": "pending",
            },
            "overall_status": "not-proven",
            "suite_status": "draft",
        },
        "trigger_proof": {
            "baseline_isolated": None,
            "near_miss_non_trigger": None,
            "positive_automatic_trigger": None,
            "trace_contract_passed": None,
        },
    }


def refresh_repository_evidence(arguments: argparse.Namespace) -> int:
    output = arguments.output.expanduser().resolve()
    write_json(output, repository_evidence_report())
    print(f"wrote pending repository evidence to {output}")
    print("model calls: 0")
    return 0


def validate_check_definition(check: Mapping[str, Any], label: str) -> None:
    kind = require_string(check.get("kind"), f"{label}.kind")
    if kind == "contains":
        require_string(check.get("value"), f"{label}.value")
        return
    if kind == "contains_none":
        values = require_list(check.get("values"), f"{label}.values")
        if not values:
            raise EvalError(f"{label}.values must not be empty")
        for value in values:
            require_string(value, f"{label}.values item")
        return
    if kind == "regex":
        pattern = require_string(check.get("pattern"), f"{label}.pattern")
        try:
            re.compile(pattern)
        except re.error as error:
            raise EvalError(f"{label}.pattern is invalid: {error}") from error
        return
    raise EvalError(f"{label}.kind is unsupported: {kind}")


def validate_key_and_manifest(
    key_path: Path, key_manifest_path: Path, run_manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = read_json(key_path)
    manifest = read_json(key_manifest_path)
    if key.get("schema_version") != SCHEMA_VERSION or key.get("suite") != SUITE_NAME:
        raise EvalError("key schema or suite mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("suite") != SUITE_NAME:
        raise EvalError("key manifest schema or suite mismatch")
    require_commit_sha(manifest.get("bundle_commit"), "key manifest bundle_commit")
    require_sha256(manifest.get("key_sha256"), "key manifest key_sha256")
    require_sha256(manifest.get("ciphertext_sha256"), "key manifest ciphertext_sha256")
    fingerprint = require_string(manifest.get("recipient_fingerprint"), "key manifest recipient_fingerprint")
    if GPG_FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise EvalError("key manifest recipient_fingerprint is invalid")
    if canonical_json_sha256(key) != manifest.get("key_sha256"):
        raise EvalError("hidden key digest does not match committed manifest")
    pack_digest = run_manifest.get("case_pack_sha256")
    if pack_digest != manifest.get("case_pack_sha256") or pack_digest != key.get("case_pack_sha256"):
        raise EvalError("case pack digest is not sealed consistently")
    canonical_pack_digest = run_manifest.get("case_pack_canonical_sha256")
    if canonical_pack_digest != manifest.get("case_pack_canonical_sha256") or canonical_pack_digest != key.get(
        "case_pack_canonical_sha256"
    ):
        raise EvalError("canonical case pack digest is not sealed consistently")
    if canonical_json_sha256(manifest) != run_manifest.get("key_manifest_sha256"):
        raise EvalError("run manifest does not bind the supplied key manifest")
    for field in ("skill_sha256", "skill_tree_sha256", "runner_sha256"):
        if run_manifest.get(field) != manifest.get(field):
            raise EvalError(f"run manifest {field} does not match the sealed key manifest")
    if run_manifest.get("thresholds") != manifest.get("thresholds"):
        raise EvalError("run thresholds do not match the sealed key manifest")
    if run_manifest.get("paired_confidence") != manifest.get("paired_confidence"):
        raise EvalError("run paired confidence profile does not match the sealed key manifest")
    require_commit_sha(run_manifest.get("freeze_commit"), "run manifest freeze_commit")
    positive_ids = manifest_string_list(manifest.get("positive_case_ids"), "positive_case_ids")
    near_miss_ids = manifest_string_list(manifest.get("near_miss_case_ids"), "near_miss_case_ids")
    if len(positive_ids) != 12 or len(near_miss_ids) != 8:
        raise EvalError("key manifest must bind exactly 12 positive and 8 near-miss cases")
    if manifest.get("execution") != {
        "near_miss_arms": ["with_skill"],
        "positive_arms": list(ARMS),
        "session_count": 96,
        "trials_per_arm": 3,
    }:
        raise EvalError("key manifest execution policy is invalid")
    counts = count_manifest_checks(key)
    if counts != manifest.get("check_counts"):
        raise EvalError("hidden key check counts do not match committed manifest")
    if sorted(counts) != positive_ids:
        raise EvalError("hidden key case ids do not match committed manifest")
    if sum(counts.values()) != manifest.get("total_checks"):
        raise EvalError("hidden key total check count does not match committed manifest")
    return key, manifest


def evaluate_check(response: str, check: Mapping[str, Any]) -> bool:
    validate_check_definition(check, "check")
    kind = require_string(check.get("kind"), "check.kind")
    folded = response.casefold()
    if kind == "contains":
        return require_string(check.get("value"), "check.value").casefold() in folded
    if kind == "contains_none":
        values = require_list(check.get("values"), "check.values")
        if not values:
            raise EvalError("check.values must not be empty")
        forbidden = [require_string(value, "check.values item") for value in values]
        return all(value.casefold() not in folded for value in forbidden)
    if kind == "regex":
        pattern = require_string(check.get("pattern"), "check.pattern")
        return re.search(pattern, response, flags=re.IGNORECASE | re.DOTALL) is not None
    raise EvalError(f"unsupported check kind: {kind}")


def indexed_records(run_manifest: Mapping[str, Any]) -> dict[tuple[str, str, int, str], Mapping[str, Any]]:
    result: dict[tuple[str, str, int, str], Mapping[str, Any]] = {}
    for raw_record in require_list(run_manifest.get("records"), "run manifest records"):
        if not isinstance(raw_record, dict):
            raise EvalError("run record must be an object")
        trial_number = raw_record.get("trial_number")
        if isinstance(trial_number, bool) or not isinstance(trial_number, int) or trial_number not in {1, 2, 3}:
            raise EvalError("record.trial_number must be 1, 2, or 3")
        key = (
            require_string(raw_record.get("case_id"), "record.case_id"),
            require_string(raw_record.get("variant"), "record.variant"),
            trial_number,
            require_string(raw_record.get("arm"), "record.arm"),
        )
        if key in result:
            raise EvalError(f"duplicate run record: {key}")
        result[key] = raw_record
    return result


def read_frozen_response(record: Mapping[str, Any], manifest_root: Path) -> str:
    response_path = resolve_evidence_path(manifest_root, record.get("response_path"), "response_path")
    if sha256_file(response_path) != record.get("response_sha256"):
        raise EvalError(f"response digest mismatch: {response_path}")
    return response_path.read_text(encoding="utf-8")


def grade_arm(
    *,
    arm: str,
    key_cases: Mapping[str, Any],
    manifest_root: Path,
    records: Mapping[tuple[str, str, int, str], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    results: list[dict[str, Any]] = []
    passed = 0
    total = 0
    for case_id, raw_case in key_cases.items():
        if not isinstance(raw_case, dict):
            raise EvalError(f"invalid key case: {case_id}")
        for trial_number in range(1, 4):
            record = records.get((case_id, "positive", trial_number, arm))
            if record is None:
                raise EvalError(f"missing positive record for {case_id} t{trial_number} {arm}")
            raw_errors = record.get("errors")
            if not isinstance(raw_errors, list) or not all(isinstance(error, str) for error in raw_errors):
                raise EvalError(f"record errors are invalid for {case_id} t{trial_number} {arm}")
            response = "" if raw_errors else read_frozen_response(record, manifest_root)
            case_checks: list[dict[str, Any]] = []
            for raw_check in require_list(raw_case.get("checks"), f"key.{case_id}.checks"):
                if not isinstance(raw_check, dict):
                    raise EvalError(f"invalid check in {case_id}")
                check_id = require_string(raw_check.get("id"), f"key.{case_id}.check.id")
                critical = raw_check.get("critical")
                if not isinstance(critical, bool):
                    raise EvalError(f"key.{case_id}.{check_id}.critical must be a boolean")
                check_passed = not raw_errors and evaluate_check(response, raw_check)
                case_checks.append({"critical": critical, "id": check_id, "passed": check_passed})
                passed += int(check_passed)
                total += 1
            results.append({"case_id": case_id, "trial_number": trial_number, "checks": case_checks})
    return results, passed, total


def proof_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "arm": record.get("arm"),
        "case_id": record.get("case_id"),
        "cost_microusd": record.get("cost_microusd"),
        "discovered_target_skill": record.get("discovered_target_skill"),
        "errors": record.get("errors"),
        "input_tokens": record.get("input_tokens"),
        "invoked_skills": record.get("invoked_skills"),
        "latency_ms": record.get("latency_ms"),
        "model": record.get("model"),
        "output_tokens": record.get("output_tokens"),
        "prompt_sha256": record.get("prompt_sha256"),
        "response_sha256": record.get("response_sha256"),
        "trace_sha256": record.get("trace_sha256"),
        "trial_number": record.get("trial_number"),
        "variant": record.get("variant"),
    }


def summarize_behavior_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for result in results:
        case_id = require_string(result.get("case_id"), "behavior result case_id")
        grouped.setdefault(case_id, []).append(result)
    summaries: list[dict[str, Any]] = []
    for case_id, trials in sorted(grouped.items()):
        failures: dict[str, int] = {}
        passed = 0
        total = 0
        for trial in trials:
            for check in require_list(trial.get("checks"), f"behavior.{case_id}.checks"):
                if not isinstance(check, dict):
                    raise EvalError(f"invalid behavior check for {case_id}")
                check_id = require_string(check.get("id"), f"behavior.{case_id}.check.id")
                check_passed = check.get("passed") is True
                passed += int(check_passed)
                total += 1
                if not check_passed:
                    failures[check_id] = failures.get(check_id, 0) + 1
        summaries.append(
            {
                "case_id": case_id,
                "check_failures": [
                    {"id": check_id, "trials_failed": count} for check_id, count in sorted(failures.items())
                ],
                "checks_passed": passed,
                "checks_total": total,
                "trials": len(trials),
            }
        )
    return summaries


def basis_point_threshold_passes(passed: int, total: int, required_bps: int) -> bool:
    if total <= 0:
        raise EvalError("threshold population must not be empty")
    return passed * 10000 >= required_bps * total


def paired_case_deltas(
    baseline_results: Sequence[Mapping[str, Any]], treatment_results: Sequence[Mapping[str, Any]]
) -> tuple[list[tuple[str, Fraction]], int]:
    def indexed(
        results: Sequence[Mapping[str, Any]],
    ) -> dict[tuple[str, int], dict[str, tuple[bool, bool]]]:
        indexed_results: dict[tuple[str, int], dict[str, tuple[bool, bool]]] = {}
        for result in results:
            case_id = require_string(result.get("case_id"), "behavior result case_id")
            trial_number = require_integer(
                result.get("trial_number"), "behavior result trial_number", minimum=1, maximum=3
            )
            checks: dict[str, tuple[bool, bool]] = {}
            for check in require_list(result.get("checks"), f"behavior.{case_id}.checks"):
                if not isinstance(check, dict):
                    raise EvalError(f"invalid behavior check for {case_id}")
                check_id = require_string(check.get("id"), f"behavior.{case_id}.check.id")
                if (
                    check_id in checks
                    or not isinstance(check.get("passed"), bool)
                    or not isinstance(check.get("critical"), bool)
                ):
                    raise EvalError(f"invalid or duplicate behavior check for {case_id}: {check_id}")
                checks[check_id] = (check["passed"], check["critical"])
            indexed_results[(case_id, trial_number)] = checks
        return indexed_results

    baseline = indexed(baseline_results)
    treatment = indexed(treatment_results)
    if set(baseline) != set(treatment):
        raise EvalError("paired behavior populations differ")
    totals: dict[str, list[int]] = {}
    critical_regressions = 0
    for record_key in sorted(baseline):
        baseline_checks = baseline[record_key]
        treatment_checks = treatment[record_key]
        if set(baseline_checks) != set(treatment_checks):
            raise EvalError(f"paired behavior checks differ for {record_key}")
        case_id, _trial_number = record_key
        case_totals = totals.setdefault(case_id, [0, 0, 0])
        for check_id in sorted(baseline_checks):
            baseline_passed, baseline_critical = baseline_checks[check_id]
            treatment_passed, treatment_critical = treatment_checks[check_id]
            if baseline_critical != treatment_critical:
                raise EvalError(f"critical marker differs for {case_id}.{check_id}")
            case_totals[0] += int(baseline_passed)
            case_totals[1] += int(treatment_passed)
            case_totals[2] += 1
            critical_regressions += int(baseline_critical and baseline_passed and not treatment_passed)
    deltas = [
        (case_id, Fraction(treatment_passed - baseline_passed, total))
        for case_id, (baseline_passed, treatment_passed, total) in sorted(totals.items())
    ]
    return deltas, critical_regressions


def bootstrap_case_schedule(case_count: int, resamples: int, seed: str) -> list[list[int]]:
    if case_count <= 0:
        raise EvalError("bootstrap requires at least one case")
    schedule: list[list[int]] = []
    for resample in range(resamples):
        sample: list[int] = []
        for position in range(case_count):
            material = f"{seed}\0{resample}\0{position}".encode()
            sample.append(int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % case_count)
        schedule.append(sample)
    return schedule


def fraction_to_basis_points(value: Fraction) -> int:
    return value.numerator * 10000 // value.denominator


def paired_lower_bound_bps(deltas: Sequence[tuple[str, Fraction]], profile: Mapping[str, Any]) -> tuple[int, str]:
    confidence_bps = require_integer(
        profile.get("confidence_bps"), "paired confidence confidence_bps", minimum=1, maximum=9999
    )
    resamples = require_integer(profile.get("resamples"), "paired confidence resamples", minimum=1000, maximum=1000000)
    seed = require_string(profile.get("schedule_seed"), "paired confidence schedule_seed")
    if profile.get("method") != "case-cluster-bootstrap-v1":
        raise EvalError("unsupported paired confidence method")
    schedule = bootstrap_case_schedule(len(deltas), resamples, seed)
    replicate_deltas = sorted(
        sum((deltas[index][1] for index in sample), start=Fraction()) / len(sample) for sample in schedule
    )
    lower_index = (10000 - confidence_bps) * len(replicate_deltas) // 10000
    lower_index = min(lower_index, len(replicate_deltas) - 1)
    schedule_digest = canonical_json_sha256(schedule)
    return fraction_to_basis_points(replicate_deltas[lower_index]), schedule_digest


def manifest_string_list(value: Any, label: str) -> list[str]:
    items = require_list(value, label)
    result = [require_string(item, f"{label} item") for item in items]
    if result != sorted(set(result)):
        raise EvalError(f"{label} must contain unique strings in sorted order")
    return result


def record_errors(record: Mapping[str, Any]) -> list[str]:
    errors = record.get("errors")
    if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
        raise EvalError("record errors must be a string list")
    return errors


def record_satisfies_trigger(record: Mapping[str, Any], *, should_trigger: bool) -> bool:
    invoked = record.get("invoked_skills")
    if not isinstance(invoked, list) or not all(isinstance(skill, str) for skill in invoked):
        raise EvalError("record invoked_skills must be a string list")
    triggered = SKILL_NAME in invoked
    return record_errors(record) == [] and record.get("discovered_target_skill") is True and triggered is should_trigger


def grade_suite(arguments: argparse.Namespace) -> int:
    run_manifest_path = require_external_input(arguments.run_manifest, "run manifest")
    key_path = require_external_input(arguments.key, "grading key")
    key_manifest_path = arguments.key_manifest.expanduser().resolve(strict=True)
    output_path = require_external_output_file(arguments.output, "proof output")
    run_manifest = read_json(run_manifest_path)
    if run_manifest.get("schema_version") != SCHEMA_VERSION or run_manifest.get("suite") != SUITE_NAME:
        raise EvalError("run manifest schema or suite mismatch")
    key, key_manifest = validate_key_and_manifest(key_path, key_manifest_path, run_manifest)
    records = indexed_records(run_manifest)
    key_cases = key.get("cases")
    if not isinstance(key_cases, dict):
        raise EvalError("key cases must be an object")

    positive_ids = manifest_string_list(key_manifest.get("positive_case_ids"), "positive_case_ids")
    near_miss_ids = manifest_string_list(key_manifest.get("near_miss_case_ids"), "near_miss_case_ids")
    manifest_cases = [
        *({"id": case_id, "variant": "positive"} for case_id in positive_ids),
        *({"id": case_id, "variant": "near_miss"} for case_id in near_miss_ids),
    ]
    expected_keys = expected_record_keys(manifest_cases)
    if set(records) != expected_keys:
        missing = sorted(expected_keys - set(records))
        unexpected = sorted(set(records) - expected_keys)
        raise EvalError(f"run record set mismatch; missing={missing}, unexpected={unexpected}")
    manifest_root = run_manifest_path.parent
    baseline_results, baseline_passed, total = grade_arm(
        arm="baseline", key_cases=key_cases, manifest_root=manifest_root, records=records
    )
    treatment_results, treatment_passed, treatment_total = grade_arm(
        arm="with_skill", key_cases=key_cases, manifest_root=manifest_root, records=records
    )
    if treatment_total != total:
        raise EvalError("arm check totals differ")

    positive_treatment = [
        record
        for record in records.values()
        if record.get("variant") == "positive" and record.get("arm") == "with_skill"
    ]
    near_treatment = [
        record
        for record in records.values()
        if record.get("variant") == "near_miss" and record.get("arm") == "with_skill"
    ]
    baseline_records = [record for record in records.values() if record.get("arm") == "baseline"]
    all_records = list(records.values())
    positive_trigger_count = sum(record_satisfies_trigger(record, should_trigger=True) for record in positive_treatment)
    near_miss_abstention_count = sum(
        record_satisfies_trigger(record, should_trigger=False) for record in near_treatment
    )
    baseline_isolated = all(
        record_errors(record) == []
        and record.get("discovered_target_skill") is False
        and SKILL_NAME not in record.get("invoked_skills", [])
        for record in baseline_records
    )
    trace_contract_passed = all(record_errors(record) == [] for record in all_records)
    improvement = treatment_passed - baseline_passed
    thresholds = validated_thresholds({"thresholds": key_manifest.get("thresholds")})
    confidence_profile = validated_confidence_profile({"paired_confidence": key_manifest.get("paired_confidence")})
    paired_deltas, critical_regressions = paired_case_deltas(baseline_results, treatment_results)
    paired_ci_lower_bps, bootstrap_schedule_sha256 = paired_lower_bound_bps(paired_deltas, confidence_profile)
    threshold_results = {
        "critical_regressions": critical_regressions <= thresholds["critical_regressions"],
        "near_miss_abstention": basis_point_threshold_passes(
            near_miss_abstention_count,
            len(near_treatment),
            thresholds["near_miss_abstention_bps"],
        ),
        "paired_delta_ci_lower": paired_ci_lower_bps >= thresholds["paired_delta_ci_lower_bps"],
        "positive_trigger_recall": basis_point_threshold_passes(
            positive_trigger_count,
            len(positive_treatment),
            thresholds["positive_trigger_recall_bps"],
        ),
    }
    public_records = [proof_record(records[key]) for key in sorted(records)]
    overall_passed = baseline_isolated and trace_contract_passed and all(threshold_results.values())
    raw_evidence_sha256 = require_sha256(arguments.raw_evidence_sha256, "raw_evidence_sha256")
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "claim_scope": (
            "Claude Code verified-lane automatic triggering and deterministic behavior; "
            "the production suite remains draft and this is not a cross-harness portability claim"
        ),
        "generated_at": utc_now(),
        "bundle_commit": require_commit_sha(key_manifest.get("bundle_commit"), "bundle_commit"),
        "execution_schedule_sha256": require_sha256(
            run_manifest.get("execution_schedule_sha256"), "execution_schedule_sha256"
        ),
        "execution_uuid": require_string(run_manifest.get("execution_uuid"), "execution_uuid"),
        "freeze_commit": require_commit_sha(run_manifest.get("freeze_commit"), "freeze_commit"),
        "run_manifest_sha256": canonical_json_sha256(run_manifest),
        "sealed_inputs": {
            "case_pack_sha256": run_manifest.get("case_pack_sha256"),
            "case_pack_canonical_sha256": run_manifest.get("case_pack_canonical_sha256"),
            "ciphertext_sha256": key_manifest.get("ciphertext_sha256"),
            "key_manifest_sha256": canonical_json_sha256(key_manifest),
            "key_sha256": key_manifest.get("key_sha256"),
            "runner_sha256": run_manifest.get("runner_sha256"),
            "skill_sha256": run_manifest.get("skill_sha256"),
            "skill_tree_sha256": run_manifest.get("skill_tree_sha256"),
        },
        "profile": run_manifest.get("profile"),
        "claude_version": run_manifest.get("claude_version"),
        "status": "passed" if overall_passed else "failed",
        "production_contract": {
            "human_calibration": "pending",
            "harnesses": {
                "antigravity": "unavailable",
                "claude-code": "passed",
                "codex": "unavailable",
                "cursor": "unavailable",
            },
            "overall_status": "not-proven",
            "suite_status": "draft",
        },
        "trigger_proof": {
            "baseline_isolated": baseline_isolated,
            "near_miss_abstention": {
                "passed": near_miss_abstention_count,
                "required_bps": thresholds["near_miss_abstention_bps"],
                "threshold_passed": threshold_results["near_miss_abstention"],
                "total": len(near_treatment),
            },
            "positive_automatic_trigger": {
                "passed": positive_trigger_count,
                "required_bps": thresholds["positive_trigger_recall_bps"],
                "threshold_passed": threshold_results["positive_trigger_recall"],
                "total": len(positive_treatment),
            },
            "trace_contract_passed": trace_contract_passed,
        },
        "behavior": {
            "baseline": {
                "checks_passed": baseline_passed,
                "checks_total": total,
                "results": summarize_behavior_results(baseline_results),
            },
            "with_skill": {
                "checks_passed": treatment_passed,
                "checks_total": total,
                "results": summarize_behavior_results(treatment_results),
            },
            "bootstrap_schedule_sha256": bootstrap_schedule_sha256,
            "critical_regressions": critical_regressions,
            "improvement_checks": improvement,
            "paired_delta_ci_lower_bps": paired_ci_lower_bps,
            "threshold_results": threshold_results,
        },
        "execution": {
            "attempted_sessions": len(expected_keys),
            "completed_sessions": len(all_records),
            "error_sessions": sum(bool(record_errors(record)) for record in all_records),
            "input_tokens": sum(int(record.get("input_tokens") or 0) for record in all_records),
            "latency_ms": sum(int(record.get("latency_ms") or 0) for record in all_records),
            "output_tokens": sum(int(record.get("output_tokens") or 0) for record in all_records),
            "session_count": len(all_records),
            "total_cost_microusd": sum(int(record.get("cost_microusd") or 0) for record in all_records),
        },
        "private_evidence": {
            "archive_size": require_integer(
                arguments.raw_evidence_size, "raw_evidence_size", minimum=1, maximum=MAX_SAFE_INTEGER
            ),
            "asset_name": require_string(arguments.private_asset_name, "private_asset_name"),
            "raw_evidence_sha256": raw_evidence_sha256,
            "release_tag": require_string(arguments.private_release_tag, "private_release_tag"),
        },
        "record_count": len(public_records),
        "records_canonical_sha256": canonical_json_sha256(public_records),
        "passed": overall_passed,
    }
    try:
        validate_canonical_value(report, "proof report")
    except EvidenceContractError as error:
        raise EvalError(str(error)) from error
    write_json(output_path, report)
    print(f"baseline behavior: {baseline_passed}/{total}")
    print(f"with-skill behavior: {treatment_passed}/{total}")
    print(f"automatic positive trigger: {positive_trigger_count}/{len(positive_treatment)}")
    print(f"near-miss non-trigger: {near_miss_abstention_count}/{len(near_treatment)}")
    print(f"wrote {output_path}")
    return 0 if overall_passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run paired fresh-session evaluations")
    run_parser.add_argument("--case-pack", type=Path, default=DEFAULT_CASE_PACK)
    run_parser.add_argument("--skill-dir", type=Path, default=SKILL_ROOT)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument("--workspace-root", type=Path, required=True)
    run_parser.add_argument("--key-manifest", type=Path, required=True)
    run_parser.add_argument("--freeze-commit", required=True)
    run_parser.add_argument("--claude-bin", default="claude")
    run_parser.add_argument("--max-budget-usd", default="1.00")
    run_parser.add_argument("--timeout-seconds", type=int, default=360)
    run_parser.add_argument("--jobs", type=int, default=3)
    run_parser.set_defaults(handler=run_suite)

    grade_parser = subparsers.add_parser("grade", help="grade frozen responses with an external key")
    grade_parser.add_argument("--run-manifest", type=Path, required=True)
    grade_parser.add_argument("--key", type=Path, required=True)
    grade_parser.add_argument("--key-manifest", type=Path, required=True)
    grade_parser.add_argument("--output", type=Path, required=True)
    grade_parser.add_argument("--raw-evidence-sha256", required=True)
    grade_parser.add_argument("--raw-evidence-size", type=int, required=True)
    grade_parser.add_argument("--private-release-tag", required=True)
    grade_parser.add_argument("--private-asset-name", required=True)
    grade_parser.set_defaults(handler=grade_suite)

    refresh_parser = subparsers.add_parser(
        "refresh-evidence",
        help="write deterministic pending evidence for the current repository state",
    )
    refresh_parser.add_argument("--output", type=Path, default=DEFAULT_PROOF_REPORT)
    refresh_parser.set_defaults(handler=refresh_repository_evidence)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        handler = arguments.handler
        if not callable(handler):
            raise EvalError("subcommand has no handler")
        return cast(Callable[[argparse.Namespace], int], handler)(arguments)
    except (EvalError, OSError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
