#!/usr/bin/env python3
"""Run and grade paired automatic-trigger evaluations for this skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess  # nosec B404 - fixed argv, shell=False, and trace capture are required for the eval harness.
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1
SKILL_NAME = "sync-traycer-notion"
SUITE_NAME = "sync-traycer-notion-trigger-behavior"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / SKILL_NAME
DEFAULT_CASE_PACK = SKILL_ROOT / "assets/trigger-behavior-evals.json"
DEFAULT_KEY_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/key-manifest.json"
ARMS = ("baseline", "with_skill")
VARIANTS = ("positive", "near_miss")
ALLOWED_TOOL_NAMES = frozenset({"Skill", "Read"})
FORBIDDEN_PROMPT_TERMS = (SKILL_NAME.casefold(), f"${SKILL_NAME}".casefold())


class EvalError(RuntimeError):
    """Raised when an evaluation artifact or trace is invalid."""


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvalError(f"expected a JSON object in {path}")
    return value


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvalError(f"{label} must be a non-empty string")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvalError(f"{label} must be a list")
    return value


def validated_case_pack(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    pack = read_json(path)
    if pack.get("schema_version") != SCHEMA_VERSION or pack.get("suite") != SUITE_NAME:
        raise EvalError("case pack schema or suite mismatch")

    contract = pack.get("response_contract")
    if not isinstance(contract, dict) or contract.get("format") != "json":
        raise EvalError("case pack requires a JSON response contract")
    fields = require_list(contract.get("fields"), "response_contract.fields")
    if not fields or not all(isinstance(field, str) and field for field in fields):
        raise EvalError("response contract fields must be non-empty strings")

    cases: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(require_list(pack.get("cases"), "cases")):
        if not isinstance(raw_case, dict):
            raise EvalError(f"case {index} must be an object")
        case_id = require_string(raw_case.get("id"), f"case {index}.id")
        if case_id in seen_ids:
            raise EvalError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        case = {"id": case_id}
        for variant in VARIANTS:
            prompt = require_string(raw_case.get(f"{variant}_prompt"), f"{case_id}.{variant}_prompt")
            folded = prompt.casefold()
            if any(term in folded for term in FORBIDDEN_PROMPT_TERMS):
                raise EvalError(f"{case_id}.{variant} explicitly names the skill")
            case[variant] = prompt
        cases.append(case)
    if not cases:
        raise EvalError("case pack must contain at least one case")
    return pack, cases


def render_prompt(pack: Mapping[str, Any], case: Mapping[str, str], variant: str) -> str:
    contract = pack["response_contract"]
    if not isinstance(contract, dict):
        raise EvalError("invalid response contract")
    fields = require_list(contract.get("fields"), "response_contract.fields")
    field_text = ", ".join(str(field) for field in fields)
    return (
        "You are in a fresh, plan-only agent session. Use only the supplied runtime context and snapshot. "
        "Do not execute changes or contact external services.\n\n"
        f"Task:\n{case[variant]}\n\n"
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
    return {
        "discovered_target_skill": SKILL_NAME in skills,
        "invoked_skills": invoked_skills,
        "model": initialization.get("model"),
        "mcp_server_count": len(mcp_servers),
        "response": response,
        "session_id": initialization.get("session_id"),
        "success": result.get("subtype") == "success" and result.get("is_error") is False,
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
    if summary.get("tools_advertised") != ["Read", "Skill"]:
        errors.append("advertised tools were not exactly Read and Skill")
    if summary.get("unexpected_tools"):
        errors.append("an unexpected tool was used")
    if summary.get("success") is not True:
        errors.append("Claude result was not successful")
    return errors


def claude_command(claude_bin: str, max_budget_usd: str, prompt: str) -> list[str]:
    return [
        claude_bin,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        "opus",
        "--effort",
        "xhigh",
        "--setting-sources",
        "project",
        "--tools",
        "Skill,Read",
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
    run_root: Path,
    skill_root: Path,
    claude_bin: str,
    max_budget_usd: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    run_id = f"{case_id}--{variant}--{arm}"
    workspace = run_root / "workspaces" / run_id
    workspace.mkdir(parents=True)
    (workspace / ".claude").mkdir(exist_ok=True)
    if arm == "with_skill":
        install_project_skill(workspace, skill_root)

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
    trace_path = run_root / "traces" / f"{run_id}.jsonl"
    stderr_path = run_root / "traces" / f"{run_id}.stderr"
    response_path = run_root / "responses" / f"{run_id}.txt"
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
    response_path.write_text(response, encoding="utf-8")
    if completed.returncode != 0:
        errors.append(f"Claude exited with status {completed.returncode}")

    return {
        "arm": arm,
        "case_id": case_id,
        "errors": errors,
        "prompt_sha256": sha256_bytes(prompt.encode()),
        "response_path": str(response_path),
        "response_sha256": sha256_file(response_path),
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "trace_path": str(trace_path),
        "trace_sha256": sha256_file(trace_path),
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


def run_suite(arguments: argparse.Namespace) -> int:
    case_pack_path = arguments.case_pack.expanduser().resolve(strict=True)
    pack, cases = validated_case_pack(case_pack_path)
    skill_root = arguments.skill_dir.expanduser().resolve(strict=True)
    if skill_root.name != SKILL_NAME or not (skill_root / "SKILL.md").is_file():
        raise EvalError(f"invalid target skill directory: {skill_root}")
    run_root = require_external_output_directory(arguments.output_dir)

    version = claude_version(arguments.claude_bin)
    records: list[dict[str, Any]] = []
    for case in cases:
        for variant in VARIANTS:
            prompt = render_prompt(pack, case, variant)
            for arm in ARMS:
                print(f"running {case['id']} {variant} {arm}", flush=True)
                records.append(
                    run_one(
                        arm=arm,
                        case_id=case["id"],
                        variant=variant,
                        prompt=prompt,
                        run_root=run_root,
                        skill_root=skill_root,
                        claude_bin=arguments.claude_bin,
                        max_budget_usd=arguments.max_budget_usd,
                        timeout_seconds=arguments.timeout_seconds,
                    )
                )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "case_pack_path": str(case_pack_path),
        "case_pack_sha256": sha256_file(case_pack_path),
        "claude_version": version,
        "completed_at": utc_now(),
        "profile": {
            "effort": "xhigh",
            "mcp_servers": [],
            "model_alias": "opus",
            "permission_mode": "dontAsk",
            "setting_sources": ["project"],
            "tools": sorted(ALLOWED_TOOL_NAMES),
        },
        "records": records,
        "skill_sha256": sha256_file(skill_root / "SKILL.md"),
        "started_from_clean_sessions": True,
    }
    manifest_path = run_root / "run-manifest.json"
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
        counts[case_id] = len(checks)
    return counts


def validate_key_and_manifest(
    key_path: Path, key_manifest_path: Path, run_manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    key = read_json(key_path)
    manifest = read_json(key_manifest_path)
    if key.get("schema_version") != SCHEMA_VERSION or key.get("suite") != SUITE_NAME:
        raise EvalError("key schema or suite mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("suite") != SUITE_NAME:
        raise EvalError("key manifest schema or suite mismatch")
    if sha256_file(key_path) != manifest.get("key_sha256"):
        raise EvalError("hidden key digest does not match committed manifest")
    pack_digest = run_manifest.get("case_pack_sha256")
    if pack_digest != manifest.get("case_pack_sha256") or pack_digest != key.get("case_pack_sha256"):
        raise EvalError("case pack digest is not sealed consistently")
    counts = count_manifest_checks(key)
    if counts != manifest.get("check_counts"):
        raise EvalError("hidden key check counts do not match committed manifest")
    if sorted(counts) != manifest.get("case_ids"):
        raise EvalError("hidden key case ids do not match committed manifest")
    return key, manifest


def evaluate_check(response: str, check: Mapping[str, Any]) -> bool:
    kind = require_string(check.get("kind"), "check.kind")
    folded = response.casefold()
    if kind == "contains":
        return require_string(check.get("value"), "check.value").casefold() in folded
    if kind == "contains_none":
        values = require_list(check.get("values"), "check.values")
        return all(isinstance(value, str) and value.casefold() not in folded for value in values)
    if kind == "regex":
        pattern = require_string(check.get("pattern"), "check.pattern")
        return re.search(pattern, response, flags=re.IGNORECASE | re.DOTALL) is not None
    raise EvalError(f"unsupported check kind: {kind}")


def indexed_records(run_manifest: Mapping[str, Any]) -> dict[tuple[str, str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for raw_record in require_list(run_manifest.get("records"), "run manifest records"):
        if not isinstance(raw_record, dict):
            raise EvalError("run record must be an object")
        key = (
            require_string(raw_record.get("case_id"), "record.case_id"),
            require_string(raw_record.get("variant"), "record.variant"),
            require_string(raw_record.get("arm"), "record.arm"),
        )
        if key in result:
            raise EvalError(f"duplicate run record: {key}")
        result[key] = raw_record
    return result


def read_frozen_response(record: Mapping[str, Any]) -> str:
    response_path = require_external_input(
        Path(require_string(record.get("response_path"), "response_path")), "response"
    )
    if sha256_file(response_path) != record.get("response_sha256"):
        raise EvalError(f"response digest mismatch: {response_path}")
    return response_path.read_text(encoding="utf-8")


def grade_arm(
    *,
    arm: str,
    key_cases: Mapping[str, Any],
    records: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    results: list[dict[str, Any]] = []
    passed = 0
    total = 0
    for case_id, raw_case in key_cases.items():
        if not isinstance(raw_case, dict):
            raise EvalError(f"invalid key case: {case_id}")
        record = records.get((case_id, "positive", arm))
        if record is None:
            raise EvalError(f"missing positive record for {case_id} {arm}")
        response = read_frozen_response(record)
        case_checks: list[dict[str, Any]] = []
        for raw_check in require_list(raw_case.get("checks"), f"key.{case_id}.checks"):
            if not isinstance(raw_check, dict):
                raise EvalError(f"invalid check in {case_id}")
            check_id = require_string(raw_check.get("id"), f"key.{case_id}.check.id")
            check_passed = evaluate_check(response, raw_check)
            case_checks.append({"id": check_id, "passed": check_passed})
            passed += int(check_passed)
            total += 1
        results.append({"case_id": case_id, "checks": case_checks})
    return results, passed, total


def proof_record(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "arm": record.get("arm"),
        "case_id": record.get("case_id"),
        "discovered_target_skill": record.get("discovered_target_skill"),
        "errors": record.get("errors"),
        "invoked_skills": record.get("invoked_skills"),
        "model": record.get("model"),
        "prompt_sha256": record.get("prompt_sha256"),
        "response_sha256": record.get("response_sha256"),
        "trace_sha256": record.get("trace_sha256"),
        "variant": record.get("variant"),
    }


def grade_suite(arguments: argparse.Namespace) -> int:
    run_manifest_path = require_external_input(arguments.run_manifest, "run manifest")
    key_path = require_external_input(arguments.key, "grading key")
    key_manifest_path = arguments.key_manifest.expanduser().resolve(strict=True)
    run_manifest = read_json(run_manifest_path)
    if run_manifest.get("schema_version") != SCHEMA_VERSION or run_manifest.get("suite") != SUITE_NAME:
        raise EvalError("run manifest schema or suite mismatch")
    key, key_manifest = validate_key_and_manifest(key_path, key_manifest_path, run_manifest)
    records = indexed_records(run_manifest)
    key_cases = key.get("cases")
    if not isinstance(key_cases, dict):
        raise EvalError("key cases must be an object")

    expected_record_count = len(key_cases) * len(ARMS) * len(VARIANTS)
    if len(records) != expected_record_count:
        raise EvalError(f"expected {expected_record_count} records, found {len(records)}")
    baseline_results, baseline_passed, total = grade_arm(arm="baseline", key_cases=key_cases, records=records)
    treatment_results, treatment_passed, treatment_total = grade_arm(
        arm="with_skill", key_cases=key_cases, records=records
    )
    if treatment_total != total:
        raise EvalError("arm check totals differ")

    positive_treatment = [records[(case_id, "positive", "with_skill")] for case_id in key_cases]
    near_treatment = [records[(case_id, "near_miss", "with_skill")] for case_id in key_cases]
    baseline_records = [record for record in records.values() if record.get("arm") == "baseline"]
    all_records = list(records.values())
    auto_trigger_passed = all(
        record.get("discovered_target_skill") is True and SKILL_NAME in record.get("invoked_skills", [])
        for record in positive_treatment
    )
    near_miss_passed = all(
        record.get("discovered_target_skill") is True and SKILL_NAME not in record.get("invoked_skills", [])
        for record in near_treatment
    )
    baseline_isolated = all(
        record.get("discovered_target_skill") is False and SKILL_NAME not in record.get("invoked_skills", [])
        for record in baseline_records
    )
    trace_contract_passed = all(not record.get("errors") for record in all_records)
    improvement = treatment_passed - baseline_passed
    overall_passed = all(
        (
            auto_trigger_passed,
            near_miss_passed,
            baseline_isolated,
            trace_contract_passed,
            treatment_passed > baseline_passed,
        )
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "claim_scope": "Claude Code automatic triggering and behavior; not cross-harness portability",
        "generated_at": utc_now(),
        "run_manifest_sha256": sha256_file(run_manifest_path),
        "sealed_inputs": {
            "case_pack_sha256": run_manifest.get("case_pack_sha256"),
            "key_manifest_sha256": sha256_file(key_manifest_path),
            "key_sha256": key_manifest.get("key_sha256"),
        },
        "profile": run_manifest.get("profile"),
        "claude_version": run_manifest.get("claude_version"),
        "skill_sha256": run_manifest.get("skill_sha256"),
        "trigger_proof": {
            "baseline_isolated": baseline_isolated,
            "positive_automatic_trigger": auto_trigger_passed,
            "near_miss_non_trigger": near_miss_passed,
            "trace_contract_passed": trace_contract_passed,
        },
        "behavior": {
            "baseline": {
                "checks_passed": baseline_passed,
                "checks_total": total,
                "score_percent": round(100 * baseline_passed / total, 1),
                "results": baseline_results,
            },
            "with_skill": {
                "checks_passed": treatment_passed,
                "checks_total": total,
                "score_percent": round(100 * treatment_passed / total, 1),
                "results": treatment_results,
            },
            "improvement_checks": improvement,
            "improvement_percentage_points": round(100 * improvement / total, 1),
        },
        "records": [proof_record(records[key]) for key in sorted(records)],
        "passed": overall_passed,
    }
    write_json(arguments.output.expanduser().resolve(), report)
    print(f"baseline behavior: {baseline_passed}/{total}")
    print(f"with-skill behavior: {treatment_passed}/{total}")
    print(f"automatic positive trigger: {auto_trigger_passed}")
    print(f"near-miss non-trigger: {near_miss_passed}")
    print(f"wrote {arguments.output.expanduser().resolve()}")
    return 0 if overall_passed else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run paired fresh-session evaluations")
    run_parser.add_argument("--case-pack", type=Path, default=DEFAULT_CASE_PACK)
    run_parser.add_argument("--skill-dir", type=Path, default=SKILL_ROOT)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument("--claude-bin", default="claude")
    run_parser.add_argument("--max-budget-usd", default="1.50")
    run_parser.add_argument("--timeout-seconds", type=int, default=360)
    run_parser.set_defaults(handler=run_suite)

    grade_parser = subparsers.add_parser("grade", help="grade frozen responses with an external key")
    grade_parser.add_argument("--run-manifest", type=Path, required=True)
    grade_parser.add_argument("--key", type=Path, required=True)
    grade_parser.add_argument("--key-manifest", type=Path, default=DEFAULT_KEY_MANIFEST)
    grade_parser.add_argument("--output", type=Path, required=True)
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
