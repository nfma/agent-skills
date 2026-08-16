#!/usr/bin/env python3
"""Run split trigger qualification and paired behavior evaluations."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil

# This runner invokes one user-selected local executable with fixed argv and no shell.
import subprocess  # nosec B404
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1
SKILL_NAME = "write-production-rust"
SUITE_NAME = "write-production-rust-trigger-behavior"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_PACK = SKILL_ROOT / "assets/trigger-behavior-evals.json"
DEFAULT_KEY_MANIFEST = REPOSITORY_ROOT / "evals/write-production-rust/semantic-key-manifest.json"
DEFAULT_PROOF_REPORT = REPOSITORY_ROOT / "evals/write-production-rust/proof-report.json"
ARMS = ("baseline", "with_skill")
VARIANTS = ("positive", "near_miss")
TRIGGER_TOOLS = ("Read", "Skill")


class EvalError(RuntimeError):
    """Raised when evaluation inputs or evidence violate the proof contract."""


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvalError(f"expected a JSON object in {path}")
    return value


def format_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def create_external_output_directory(parent: Path | None = None) -> Path:
    output_directory = Path(tempfile.mkdtemp(prefix=f"{SUITE_NAME}-", dir=parent)).resolve()
    if path_is_within(output_directory, REPOSITORY_ROOT):
        shutil.rmtree(output_directory)
        raise EvalError("the system temporary directory must be outside the repository")
    return output_directory


def require_external_input(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise EvalError(f"{label} must be outside the repository")
    return resolved


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvalError(f"{label} must be a non-empty string")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvalError(f"{label} must be a list")
    return value


def validated_case_pack(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    pack = read_json(path)
    if (
        pack.get("schema_version") != SCHEMA_VERSION
        or pack.get("suite") != SUITE_NAME
        or pack.get("skill_name") != SKILL_NAME
    ):
        raise EvalError("case pack schema, suite, or skill mismatch")

    trigger_cases: list[dict[str, Any]] = []
    behavior_cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(require_list(pack.get("trigger_cases"), "trigger_cases")):
        if not isinstance(raw_case, dict):
            raise EvalError(f"trigger case {index} must be an object")
        case_id = require_string(raw_case.get("id"), f"trigger case {index}.id")
        if case_id in seen_ids:
            raise EvalError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        case: dict[str, Any] = {"id": case_id}
        for variant in VARIANTS:
            prompt = require_string(raw_case.get(f"{variant}_prompt"), f"{case_id}.{variant}_prompt")
            if SKILL_NAME.casefold() in prompt.casefold():
                raise EvalError(f"{case_id}.{variant} explicitly names the skill")
            case[variant] = prompt
        trigger_cases.append(case)

    seen_ids.clear()
    for index, raw_case in enumerate(require_list(pack.get("behavior_cases"), "behavior_cases")):
        if not isinstance(raw_case, dict):
            raise EvalError(f"behavior case {index} must be an object")
        case_id = require_string(raw_case.get("id"), f"behavior case {index}.id")
        if case_id in seen_ids:
            raise EvalError(f"duplicate behavior case id: {case_id}")
        seen_ids.add(case_id)
        prompt = require_string(raw_case.get("prompt"), f"{case_id}.prompt")
        if SKILL_NAME.casefold() in prompt.casefold():
            raise EvalError(f"{case_id}.prompt explicitly names the skill")
        raw_files = require_list(raw_case.get("guidance_files"), f"{case_id}.guidance_files")
        guidance_files: list[str] = []
        for raw_file in raw_files:
            relative = require_string(raw_file, f"{case_id}.guidance_file")
            candidate = (SKILL_ROOT / relative).resolve(strict=True)
            if not path_is_within(candidate, SKILL_ROOT) or candidate.suffix not in {".md", ".txt"}:
                raise EvalError(f"invalid guidance path: {relative}")
            guidance_files.append(relative)
        if "SKILL.md" not in guidance_files:
            raise EvalError(f"{case_id} must inject SKILL.md")
        behavior_cases.append({"id": case_id, "prompt": prompt, "guidance_files": guidance_files})

    if len(trigger_cases) < 3 or len(behavior_cases) < 3:
        raise EvalError("case pack must contain at least three trigger and three behavior cases")
    return pack, trigger_cases, behavior_cases


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
    matches = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    if len(matches) != 1:
        raise EvalError(f"expected exactly one system/init event, found {len(matches)}")
    return matches[0]


def result_event(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    matches = [event for event in events if event.get("type") == "result"]
    if len(matches) != 1:
        raise EvalError(f"expected exactly one result event, found {len(matches)}")
    return matches[0]


def tool_uses(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    uses: list[dict[str, Any]] = []
    for event in events:
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        uses.extend(item for item in content if isinstance(item, dict) and item.get("type") == "tool_use")
    return uses


def skill_name_from_use(tool_use: Mapping[str, Any]) -> str | None:
    if tool_use.get("name") != "Skill":
        return None
    inputs = tool_use.get("input")
    if not isinstance(inputs, dict):
        return None
    skill = inputs.get("skill")
    return skill if isinstance(skill, str) else None


def trace_summary(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    initialization = init_event(events)
    result = result_event(events)
    uses = tool_uses(events)
    skills = initialization.get("skills", [])
    tools = initialization.get("tools", [])
    mcp_servers = initialization.get("mcp_servers", [])
    if not isinstance(skills, list) or not all(isinstance(skill, str) for skill in skills):
        raise EvalError("init skills field is invalid")
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        raise EvalError("init tools field is invalid")
    if not isinstance(mcp_servers, list):
        raise EvalError("init mcp_servers field is invalid")
    response = result.get("result")
    if not isinstance(response, str):
        raise EvalError("result response is missing")
    return {
        "advertised_skills": skills,
        "advertised_tools": tools,
        "invoked_skills": [skill for use in uses if (skill := skill_name_from_use(use)) is not None],
        "mcp_server_count": len(mcp_servers),
        "model": initialization.get("model"),
        "response": response,
        "session_id": initialization.get("session_id"),
        "success": result.get("subtype") == "success" and result.get("is_error") is False,
        "tools_used": [str(use.get("name", "")) for use in uses],
    }


def validate_trigger_trace(summary: Mapping[str, Any], arm: str, variant: str) -> list[str]:
    errors: list[str] = []
    skills = summary.get("advertised_skills")
    invoked = summary.get("invoked_skills")
    discovered = isinstance(skills, list) and SKILL_NAME in skills
    triggered = isinstance(invoked, list) and SKILL_NAME in invoked
    expected_discovery = arm == "with_skill"
    expected_trigger = expected_discovery and variant == "positive"
    if discovered is not expected_discovery:
        errors.append(f"target discovery mismatch: expected {expected_discovery}")
    if triggered is not expected_trigger:
        errors.append(f"target trigger mismatch: expected {expected_trigger}")
    if isinstance(invoked, list) and any(skill != SKILL_NAME for skill in invoked):
        errors.append("an unrelated skill was invoked")
    if summary.get("advertised_tools") != list(TRIGGER_TOOLS):
        errors.append("advertised tools were not exactly Read and Skill")
    if summary.get("mcp_server_count") != 0:
        errors.append("MCP servers were available")
    if any(tool not in TRIGGER_TOOLS for tool in cast(list[str], summary.get("tools_used", []))):
        errors.append("an unexpected tool was used")
    if summary.get("success") is not True:
        errors.append("Claude result was not successful")
    return errors


def validate_zero_tool_trace(summary: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if summary.get("advertised_tools") != []:
        errors.append("zero-tool run advertised tools")
    if summary.get("tools_used") != []:
        errors.append("zero-tool run used a tool")
    if summary.get("mcp_server_count") != 0:
        errors.append("zero-tool run exposed MCP servers")
    if summary.get("success") is not True:
        errors.append("Claude result was not successful")
    return errors


def claude_command(
    *, claude_bin: str, effort: str, max_budget_usd: str, model: str, prompt: str, tools: str
) -> list[str]:
    return [
        claude_bin,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--effort",
        effort,
        "--setting-sources",
        "project",
        "--tools",
        tools,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--permission-mode",
        "dontAsk",
        "--max-budget-usd",
        max_budget_usd,
        "--no-session-persistence",
        prompt,
    ]


def run_claude(
    *,
    command: Sequence[str],
    run_id: str,
    run_root: Path,
    workspace: Path,
    timeout_seconds: int,
) -> tuple[dict[str, Any], list[str]]:
    invocation_errors: list[str] = []
    try:
        completed = subprocess.run(  # nosec B603
            list(command), capture_output=True, check=False, cwd=workspace, timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired as error:
        invocation_errors.append(f"Claude timed out after {timeout_seconds} seconds")
        completed = subprocess.CompletedProcess(command, 124, stdout=error.stdout or b"", stderr=error.stderr or b"")

    trace_path = run_root / "traces" / f"{run_id}.jsonl"
    stderr_path = run_root / "traces" / f"{run_id}.stderr"
    response_path = run_root / "responses" / f"{run_id}.txt"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)

    summary: dict[str, Any] = {}
    errors = list(invocation_errors)
    try:
        summary = trace_summary(parse_stream_json(completed.stdout))
    except EvalError as error:
        errors.append(str(error))
    response = summary.pop("response", "")
    response_path.write_text(response if isinstance(response, str) else "", encoding="utf-8")
    if completed.returncode != 0:
        errors.append(f"Claude exited with status {completed.returncode}")
    evidence = {
        **summary,
        "response_path": str(response_path),
        "response_sha256": sha256_file(response_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "trace_path": str(trace_path),
        "trace_sha256": sha256_file(trace_path),
    }
    return evidence, errors


def render_trigger_prompt(case: Mapping[str, Any], variant: str) -> str:
    return (
        "Work in plan-only mode. Do not edit files, run commands, contact services, or delegate.\n\n"
        f"Task:\n{case[variant]}\n\n"
        "Return a concise plan and any scope constraint that governs the request."
    )


def guidance_bundle(case: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    sections: list[str] = []
    digests: dict[str, str] = {}
    for relative in cast(list[str], case["guidance_files"]):
        path = (SKILL_ROOT / relative).resolve(strict=True)
        content = path.read_text(encoding="utf-8")
        sections.append(f"### {relative}\n\n{content}")
        digests[relative] = sha256_file(path)
    return "\n\n".join(sections), digests


def render_behavior_prompt(case: Mapping[str, Any], arm: str) -> tuple[str, str | None, dict[str, str]]:
    task = require_string(case.get("prompt"), "behavior prompt")
    prefix = (
        "Use only the text supplied in this message. No tools are available. Produce the requested final answer "
        "without claiming to inspect or change a workspace.\n\n"
    )
    if arm == "baseline":
        return prefix + f"Task:\n{task}", None, {}
    guidance, digests = guidance_bundle(case)
    injection = (
        "The coordinator supplies the following frozen production guidance. Apply it where relevant; the task "
        "still controls the requested outcome.\n\n" + guidance
    )
    return prefix + f"Task:\n{task}\n\n{injection}", sha256_bytes(injection.encode()), digests


def install_project_skill(workspace: Path) -> None:
    destination = workspace / ".claude/skills" / SKILL_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_ROOT, destination)


def run_trigger_case(
    *, arguments: argparse.Namespace, arm: str, case: Mapping[str, Any], run_root: Path, variant: str
) -> dict[str, Any]:
    case_id = require_string(case.get("id"), "trigger case id")
    run_id = f"trigger--{case_id}--{variant}--{arm}"
    workspace = run_root / "workspaces" / run_id
    workspace.mkdir(parents=True)
    if arm == "with_skill":
        install_project_skill(workspace)
    prompt = render_trigger_prompt(case, variant)
    command = claude_command(
        claude_bin=arguments.claude_bin,
        effort=arguments.effort,
        max_budget_usd=arguments.max_budget_usd,
        model=arguments.model,
        prompt=prompt,
        tools=",".join(TRIGGER_TOOLS),
    )
    evidence, errors = run_claude(
        command=command,
        run_id=run_id,
        run_root=run_root,
        workspace=workspace,
        timeout_seconds=arguments.timeout_seconds,
    )
    errors.extend(validate_trigger_trace(evidence, arm, variant))
    return {
        "arm": arm,
        "case_id": case_id,
        "errors": errors,
        "prompt_sha256": sha256_bytes(prompt.encode()),
        "stage": "trigger",
        "variant": variant,
        **evidence,
    }


def run_behavior_case(
    *, arguments: argparse.Namespace, arm: str, case: Mapping[str, Any], run_root: Path
) -> dict[str, Any]:
    case_id = require_string(case.get("id"), "behavior case id")
    run_id = f"behavior--{case_id}--{arm}"
    workspace = run_root / "workspaces" / run_id
    workspace.mkdir(parents=True)
    workspace.chmod(0o555)
    prompt, injection_sha256, guidance_digests = render_behavior_prompt(case, arm)
    command = claude_command(
        claude_bin=arguments.claude_bin,
        effort=arguments.effort,
        max_budget_usd=arguments.max_budget_usd,
        model=arguments.model,
        prompt=prompt,
        tools="",
    )
    evidence, errors = run_claude(
        command=command,
        run_id=run_id,
        run_root=run_root,
        workspace=workspace,
        timeout_seconds=arguments.timeout_seconds,
    )
    errors.extend(validate_zero_tool_trace(evidence))
    return {
        "arm": arm,
        "case_id": case_id,
        "errors": errors,
        "guidance_digests": guidance_digests,
        "injection_sha256": injection_sha256,
        "stage": "behavior",
        "task_prompt_sha256": sha256_bytes(require_string(case.get("prompt"), "prompt").encode()),
        **evidence,
    }


def claude_version(claude_bin: str) -> str:
    completed = subprocess.run(  # nosec B603
        [claude_bin, "--version"], capture_output=True, check=False, text=True, timeout=30
    )
    if completed.returncode != 0:
        raise EvalError(f"could not read Claude version: {completed.stderr.strip()}")
    return completed.stdout.strip()


def run_suite(arguments: argparse.Namespace) -> int:
    case_pack_path = arguments.case_pack.expanduser().resolve(strict=True)
    _pack, trigger_cases, behavior_cases = validated_case_pack(case_pack_path)
    run_root = create_external_output_directory()
    records: list[dict[str, Any]] = []

    for case in trigger_cases:
        for variant in VARIANTS:
            for arm in ARMS:
                print(f"trigger {case['id']} {variant} {arm}", flush=True)
                records.append(
                    run_trigger_case(arguments=arguments, arm=arm, case=case, run_root=run_root, variant=variant)
                )
    for case in behavior_cases:
        for arm in ARMS:
            print(f"behavior {case['id']} {arm}", flush=True)
            records.append(run_behavior_case(arguments=arguments, arm=arm, case=case, run_root=run_root))

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "case_pack_sha256": sha256_file(case_pack_path),
        "claude_version": claude_version(arguments.claude_bin),
        "completed_at": utc_now(),
        "profile": {
            "effort": arguments.effort,
            "mcp_servers": [],
            "model_alias": arguments.model,
            "trigger_tools": list(TRIGGER_TOOLS),
            "behavior_tools": [],
        },
        "records": records,
        "skill_sha256": sha256_file(SKILL_ROOT / "SKILL.md"),
    }
    manifest_path = run_root / "run-manifest.json"
    manifest_path.write_text(format_json(manifest), encoding="utf-8")
    failures = sum(bool(record["errors"]) for record in records)
    print(f"wrote {manifest_path}")
    print(f"trace contract failures: {failures}")
    return 1 if failures else 0


def indexed_records(run_manifest: Mapping[str, Any]) -> dict[tuple[str, str, str, str], Mapping[str, Any]]:
    indexed: dict[tuple[str, str, str, str], Mapping[str, Any]] = {}
    for raw_record in require_list(run_manifest.get("records"), "run records"):
        if not isinstance(raw_record, dict):
            raise EvalError("run record must be an object")
        key = (
            require_string(raw_record.get("stage"), "record.stage"),
            require_string(raw_record.get("case_id"), "record.case_id"),
            require_string(raw_record.get("variant", "none"), "record.variant"),
            require_string(raw_record.get("arm"), "record.arm"),
        )
        if key in indexed:
            raise EvalError(f"duplicate run record: {key}")
        indexed[key] = raw_record
    return indexed


def read_frozen_response(record: Mapping[str, Any]) -> str:
    path = require_external_input(Path(require_string(record.get("response_path"), "response path")), "response")
    if sha256_file(path) != record.get("response_sha256"):
        raise EvalError(f"response digest mismatch: {path}")
    return path.read_text(encoding="utf-8")


def validate_key(
    *, case_pack_sha256: str, key_manifest_path: Path, key_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = read_json(key_path)
    manifest = read_json(key_manifest_path)
    if key.get("schema_version") != SCHEMA_VERSION or key.get("suite") != SUITE_NAME:
        raise EvalError("semantic key schema or suite mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("suite") != SUITE_NAME:
        raise EvalError("key manifest schema or suite mismatch")
    if sha256_file(key_path) != manifest.get("key_sha256"):
        raise EvalError("semantic key digest does not match the committed manifest")
    if case_pack_sha256 != manifest.get("case_pack_sha256") or case_pack_sha256 != key.get("case_pack_sha256"):
        raise EvalError("case pack digest is not sealed consistently")
    raw_cases = key.get("cases")
    if not isinstance(raw_cases, dict):
        raise EvalError("semantic key cases must be an object")
    counts: dict[str, int] = {}
    for case_id, raw_case in raw_cases.items():
        if not isinstance(case_id, str) or not isinstance(raw_case, dict):
            raise EvalError("semantic key case is invalid")
        criteria = require_list(raw_case.get("criteria"), f"key.{case_id}.criteria")
        counts[case_id] = len(criteria)
    if counts != manifest.get("criterion_counts") or sorted(counts) != manifest.get("case_ids"):
        raise EvalError("semantic key shape does not match the committed manifest")
    return key, manifest


def candidate_mapping(case_id: str, run_manifest_sha256: str) -> dict[str, str]:
    digest = hashlib.sha256(f"{case_id}:{run_manifest_sha256}".encode()).digest()
    return {"A": "baseline", "B": "with_skill"} if digest[0] % 2 == 0 else {"A": "with_skill", "B": "baseline"}


def grader_prompt(*, criteria: list[Any], response_a: str, response_b: str) -> str:
    return (
        "You are a blind semantic evaluator. No tools are available. Score only the two frozen responses against "
        "the supplied response-content criteria. Do not infer which response used guidance. For every criterion, "
        "assign 0 (absent or wrong), 1 (partly correct but incomplete), or 2 (correct, specific, and actionable). "
        'Return only JSON with shape {"scores":{"A":{"criterion-id":0},"B":{...}},'
        '"winner":"A|B|tie","rationale":"brief"}.\n\n'
        f"Criteria:\n{json.dumps(criteria, indent=2, sort_keys=True)}\n\n"
        f"Candidate A:\n{response_a}\n\nCandidate B:\n{response_b}"
    )


def parse_grade(response: str, criteria: list[Any]) -> dict[str, Any]:
    payload = response.strip()
    if payload.startswith("```"):
        lines = payload.splitlines()
        if len(lines) < 3 or lines[0] not in {"```", "```json"} or lines[-1] != "```":
            raise EvalError("grader response has an invalid JSON fence")
        payload = "\n".join(lines[1:-1])
        if "```" in payload:
            raise EvalError("grader response contains a nested fence")
    try:
        value: Any = json.loads(payload)
    except json.JSONDecodeError as error:
        raise EvalError(f"grader response is not exact JSON: {error.msg}") from error
    if not isinstance(value, dict):
        raise EvalError("grader response must be an object")
    criterion_ids = {
        require_string(criterion.get("id"), "criterion.id") for criterion in criteria if isinstance(criterion, dict)
    }
    if len(criterion_ids) != len(criteria):
        raise EvalError("criterion ids must be unique")
    scores = value.get("scores")
    if not isinstance(scores, dict) or set(scores) != {"A", "B"}:
        raise EvalError("grader scores must contain exactly A and B")
    totals: dict[str, int] = {}
    for candidate in ("A", "B"):
        candidate_scores = scores.get(candidate)
        if not isinstance(candidate_scores, dict) or set(candidate_scores) != criterion_ids:
            raise EvalError(f"grader {candidate} score keys do not match criteria")
        if any(not isinstance(score, int) or score not in {0, 1, 2} for score in candidate_scores.values()):
            raise EvalError(f"grader {candidate} scores must be integers from 0 to 2")
        totals[candidate] = sum(cast(dict[str, int], candidate_scores).values())
    expected_winner = "tie" if totals["A"] == totals["B"] else ("A" if totals["A"] > totals["B"] else "B")
    if value.get("winner") != expected_winner:
        raise EvalError("grader winner disagrees with criterion totals")
    require_string(value.get("rationale"), "grader rationale")
    return {"scores": scores, "totals": totals, "winner": expected_winner}


def grade_case(
    *,
    arguments: argparse.Namespace,
    case_id: str,
    criteria: list[Any],
    records: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    run_manifest_sha256: str,
    run_root: Path,
) -> dict[str, Any]:
    baseline_record = records.get(("behavior", case_id, "none", "baseline"))
    treatment_record = records.get(("behavior", case_id, "none", "with_skill"))
    if baseline_record is None or treatment_record is None:
        raise EvalError(f"missing behavior response pair for {case_id}")
    responses = {
        "baseline": read_frozen_response(baseline_record),
        "with_skill": read_frozen_response(treatment_record),
    }
    mapping = candidate_mapping(case_id, run_manifest_sha256)
    prompt = grader_prompt(
        criteria=criteria,
        response_a=responses[mapping["A"]],
        response_b=responses[mapping["B"]],
    )
    workspace = run_root / "grader-workspaces" / case_id
    workspace.mkdir(parents=True)
    workspace.chmod(0o555)
    command = claude_command(
        claude_bin=arguments.claude_bin,
        effort=arguments.grader_effort,
        max_budget_usd=arguments.grader_max_budget_usd,
        model=arguments.grader_model,
        prompt=prompt,
        tools="",
    )
    evidence, errors = run_claude(
        command=command,
        run_id=f"grade--{case_id}",
        run_root=run_root,
        workspace=workspace,
        timeout_seconds=arguments.timeout_seconds,
    )
    errors.extend(validate_zero_tool_trace(evidence))
    if errors:
        return {"case_id": case_id, "errors": errors, "evidence": evidence}
    grade = parse_grade(read_frozen_response(evidence), criteria)
    scores = cast(dict[str, dict[str, int]], grade["scores"])
    arm_scores = {mapping[label]: scores[label] for label in ("A", "B")}
    arm_totals = {mapping[label]: cast(dict[str, int], grade["totals"])[label] for label in ("A", "B")}
    winner = "tie" if grade["winner"] == "tie" else mapping[cast(str, grade["winner"])]
    return {
        "case_id": case_id,
        "errors": [],
        "grader_model": evidence.get("model"),
        "grader_trace_sha256": evidence.get("trace_sha256"),
        "scores": arm_scores,
        "totals": arm_totals,
        "winner": winner,
    }


def trigger_proof(records: Mapping[tuple[str, str, str, str], Mapping[str, Any]]) -> dict[str, bool]:
    trigger_records = [record for key, record in records.items() if key[0] == "trigger"]
    positive = [
        record
        for key, record in records.items()
        if key[0] == "trigger" and key[2] == "positive" and key[3] == "with_skill"
    ]
    near = [
        record
        for key, record in records.items()
        if key[0] == "trigger" and key[2] == "near_miss" and key[3] == "with_skill"
    ]
    baseline = [record for key, record in records.items() if key[0] == "trigger" and key[3] == "baseline"]
    return {
        "baseline_isolated": all(
            SKILL_NAME not in cast(list[str], record.get("advertised_skills", [])) for record in baseline
        ),
        "near_miss_non_trigger": all(
            SKILL_NAME not in cast(list[str], record.get("invoked_skills", [])) for record in near
        ),
        "positive_automatic_trigger": all(
            SKILL_NAME in cast(list[str], record.get("invoked_skills", [])) for record in positive
        ),
        "trace_contract_passed": all(not record.get("errors") for record in trigger_records),
    }


def grade_suite(arguments: argparse.Namespace) -> int:
    run_manifest_path = require_external_input(arguments.run_manifest, "run manifest")
    key_path = require_external_input(arguments.key, "semantic key")
    key_manifest_path = arguments.key_manifest.expanduser().resolve(strict=True)
    run_manifest = read_json(run_manifest_path)
    if run_manifest.get("schema_version") != SCHEMA_VERSION or run_manifest.get("suite") != SUITE_NAME:
        raise EvalError("run manifest schema or suite mismatch")
    profile = run_manifest.get("profile")
    if not isinstance(profile, dict) or profile.get("model_alias") == arguments.grader_model:
        raise EvalError("grader model must differ from the behavior model")
    key, key_manifest = validate_key(
        case_pack_sha256=require_string(run_manifest.get("case_pack_sha256"), "case pack digest"),
        key_manifest_path=key_manifest_path,
        key_path=key_path,
    )
    records = indexed_records(run_manifest)
    behavior_records = [record for key_tuple, record in records.items() if key_tuple[0] == "behavior"]
    if any(record.get("errors") for record in behavior_records):
        raise EvalError("behavior trace contract failed; grading is inadmissible")
    raw_cases = key.get("cases")
    if not isinstance(raw_cases, dict):
        raise EvalError("semantic key cases are invalid")

    run_manifest_sha256 = sha256_file(run_manifest_path)
    grade_run_id = require_string(arguments.grade_run_id, "grade run id")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", grade_run_id) is None:
        raise EvalError("grade run id must be a safe path component")
    grade_root = run_manifest_path.parent / grade_run_id
    if grade_root.exists():
        raise EvalError(f"semantic grade directory already exists: {grade_root}")
    grade_root.mkdir()
    grades: list[dict[str, Any]] = []
    for case_id, raw_case in raw_cases.items():
        if not isinstance(case_id, str) or not isinstance(raw_case, dict):
            raise EvalError("semantic key case is invalid")
        criteria = require_list(raw_case.get("criteria"), f"key.{case_id}.criteria")
        print(f"grading {case_id}", flush=True)
        grades.append(
            grade_case(
                arguments=arguments,
                case_id=case_id,
                criteria=criteria,
                records=records,
                run_manifest_sha256=run_manifest_sha256,
                run_root=grade_root,
            )
        )

    grade_errors = [error for grade in grades for error in cast(list[str], grade.get("errors", []))]
    baseline_total = sum(cast(dict[str, int], grade.get("totals", {})).get("baseline", 0) for grade in grades)
    treatment_total = sum(cast(dict[str, int], grade.get("totals", {})).get("with_skill", 0) for grade in grades)
    no_regressions = all(
        cast(dict[str, int], grade.get("totals", {})).get("with_skill", -1)
        >= cast(dict[str, int], grade.get("totals", {})).get("baseline", 0)
        for grade in grades
    )
    treatment_wins = sum(grade.get("winner") == "with_skill" for grade in grades)
    trigger = trigger_proof(records)
    passed = (
        all(trigger.values())
        and not grade_errors
        and no_regressions
        and treatment_total > baseline_total
        and treatment_wins >= 2
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "claim_scope": "Claude Code trigger qualification and zero-tool behavior comparison; not cross-harness portability",
        "generated_at": utc_now(),
        "run_manifest_sha256": run_manifest_sha256,
        "sealed_inputs": {
            "case_pack_sha256": run_manifest.get("case_pack_sha256"),
            "key_manifest_sha256": sha256_file(key_manifest_path),
            "key_sha256": key_manifest.get("key_sha256"),
            "skill_sha256": run_manifest.get("skill_sha256"),
        },
        "runner_profile": profile,
        "grader_profile": {"effort": arguments.grader_effort, "model_alias": arguments.grader_model, "tools": []},
        "trigger_proof": trigger,
        "behavior": {
            "baseline_total": baseline_total,
            "with_skill_total": treatment_total,
            "improvement_points": treatment_total - baseline_total,
            "no_case_regressions": no_regressions,
            "with_skill_case_wins": treatment_wins,
            "grades": grades,
        },
        "passed": passed,
    }
    DEFAULT_PROOF_REPORT.write_text(format_json(report), encoding="utf-8")
    print(f"baseline semantic score: {baseline_total}")
    print(f"with-skill semantic score: {treatment_total}")
    print(f"automatic positive trigger: {trigger['positive_automatic_trigger']}")
    print(f"near-miss non-trigger: {trigger['near_miss_non_trigger']}")
    print(f"wrote {DEFAULT_PROOF_REPORT}")
    return 0 if passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run fresh trigger and behavior sessions")
    run_parser.add_argument("--case-pack", type=Path, default=DEFAULT_CASE_PACK)
    run_parser.add_argument("--claude-bin", default="claude")
    run_parser.add_argument("--model", default="sonnet")
    run_parser.add_argument("--effort", default="high")
    run_parser.add_argument("--max-budget-usd", default="0.75")
    run_parser.add_argument("--timeout-seconds", type=int, default=360)
    run_parser.set_defaults(handler=run_suite)

    grade_parser = subparsers.add_parser("grade", help="blind-grade frozen response pairs")
    grade_parser.add_argument("--run-manifest", type=Path, required=True)
    grade_parser.add_argument("--key", type=Path, required=True)
    grade_parser.add_argument("--key-manifest", type=Path, default=DEFAULT_KEY_MANIFEST)
    grade_parser.add_argument("--claude-bin", default="claude")
    grade_parser.add_argument("--grader-model", default="opus")
    grade_parser.add_argument("--grader-effort", default="high")
    grade_parser.add_argument("--grader-max-budget-usd", default="0.75")
    grade_parser.add_argument("--grade-run-id", default="semantic-grades")
    grade_parser.add_argument("--timeout-seconds", type=int, default=360)
    grade_parser.set_defaults(handler=grade_suite)
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
