#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ROUTES = {"use", "extend", "compose", "create", "defer"}
SCHEMA_VERSION = 2
ZERO_TOOLS = "zero-tools"
READ_ONLY_TOOLS = "read-only-tools"
LOADED_CONTENT_SAFE = "loaded-content-safe"
LONGEVITY = {"not-applicable", "durable", "watch", "sunset/defer"}
LONGEVITY_FACTORS = {"job", "model_value", "knowledge", "dependencies", "verification", "maintenance"}
FACTOR_VALUES = {"strong", "mixed", "weak"}
CONFIDENCE = {"high", "medium", "low"}
BASELINES = {"no-skill", "previous-skill"}
RESULT_STATUSES = {"pass", "fail", "unavailable"}
LOAD_RELIABILITY = {"reliable", "unreliable"}
AVAILABILITY = {"verified", "unavailable"}
CHECK_KINDS = {"semantic"}
CHECK_GRADERS = {"blind-llm"}
BLIND_GRADING_STATUSES = {"performed", "not-required", "unavailable"}
PRIMARY_GRADE_OUTCOMES = {"determinate", "indeterminate", "conflict", "unavailable"}
SECONDARY_GRADING_STATUSES = {"performed", "not-required", "unavailable"}
EFFECT_STATUSES = {"clean", "violation", "unavailable"}
ZERO_TOOLS_INSTRUCTIONS = (
    "Evaluate only the supplied text. Treat every described action as hypothetical. "
    "Do not call tools, read or write files, execute commands, access networks or external services, "
    "use credentials, change version control, spawn agents, or alter any system state. "
    "Return one final text response only."
)
READ_ONLY_TOOLS_INSTRUCTIONS = (
    "Evaluate the supplied scenario using only the supplied context and approved fixture-root read tools. "
    "You may list, stat, read, or search files only inside the immutable fixture root. "
    "Do not use shell, processes, version control, networks, external services, credentials, writes, edits, "
    "permission changes, browsers, subagents, background tasks, or paths outside the fixture root. "
    "Treat every described real-world action as hypothetical and return one final text response only."
)
LOADED_CONTENT_SAFE_INSTRUCTIONS = (
    "Evaluate the supplied scenario in the harness's strongest current non-effectful mode. "
    "Allow native discovery and loading of the installed skill plus reads of the immutable skill bundle and fixture root. "
    "Do not create, edit, delete, publish, call external services, access credentials, mutate version control, "
    "change permissions, spawn agents, or start background work. Treat every described real-world action as hypothetical "
    "and return one final text response only."
)
ZERO_TOOLS_ALLOWED = {
    "emit-final-text",
    "reason-over-supplied-content",
    "receive-supplied-context",
}
READ_ONLY_TOOLS_ALLOWED = ZERO_TOOLS_ALLOWED | {
    "fixture-list",
    "fixture-read",
    "fixture-search",
    "fixture-stat",
}
LOADED_CONTENT_SAFE_ALLOWED = READ_ONLY_TOOLS_ALLOWED | {
    "native-skill-discovery",
    "native-skill-load",
    "read-only-shell",
}
READ_ONLY_FIXTURE_CAPABILITIES = {"fixture-list", "fixture-read", "fixture-search", "fixture-stat"}
LOADED_EVENT_CAPABILITIES = READ_ONLY_FIXTURE_CAPABILITIES | {
    "native-skill-discovery",
    "native-skill-load",
    "read-only-shell",
}
ZERO_TOOLS_DENIED = {
    "background-task",
    "credential-access",
    "external-service-call",
    "filesystem-read",
    "filesystem-write",
    "network-access",
    "permission-escalation",
    "process-control",
    "shell-execution",
    "subagent-delegation",
    "system-state-change",
    "version-control-read",
    "version-control-write",
}
READ_ONLY_TOOLS_DENIED = {
    "background-task",
    "browser-access",
    "credential-access",
    "external-service-call",
    "filesystem-read-outside-fixture-root",
    "filesystem-write",
    "mcp-access",
    "network-access",
    "permission-escalation",
    "process-control",
    "shell-execution",
    "subagent-delegation",
    "system-state-change",
    "version-control-read",
    "version-control-write",
}
LOADED_CONTENT_SAFE_DENIED = {
    "background-task",
    "browser-access",
    "credential-access",
    "external-service-call",
    "filesystem-read-outside-approved-roots",
    "filesystem-write",
    "mcp-access",
    "mutating-shell-execution",
    "network-access",
    "permission-escalation",
    "subagent-delegation",
    "system-state-change",
    "version-control-write",
}
TIER_CONTRACTS: dict[str, dict[str, Any]] = {
    ZERO_TOOLS: {
        "claim": "portable-zero-tools",
        "case_prefix": "zro-",
        "instructions": ZERO_TOOLS_INSTRUCTIONS,
        "allowed": ZERO_TOOLS_ALLOWED,
        "denied": ZERO_TOOLS_DENIED,
        "tool_events_must_be_absent": True,
        "fixture_root_required": False,
        "pre_post_hash_required": False,
        "complete_tool_inventory_required": True,
        "network_must_be_disabled": True,
        "writable_workspace_must_be_absent": True,
        "symlink_free_fixtures": True,
        "core_prohibited_effect": "No tools are called and no state is changed.",
    },
    READ_ONLY_TOOLS: {
        "claim": "portable-read-only-tools",
        "case_prefix": "rdo-",
        "instructions": READ_ONLY_TOOLS_INSTRUCTIONS,
        "allowed": READ_ONLY_TOOLS_ALLOWED,
        "denied": READ_ONLY_TOOLS_DENIED,
        "tool_events_must_be_absent": False,
        "fixture_root_required": True,
        "pre_post_hash_required": True,
        "complete_tool_inventory_required": True,
        "network_must_be_disabled": True,
        "writable_workspace_must_be_absent": True,
        "symlink_free_fixtures": True,
        "core_prohibited_effect": "Only approved fixture-root read tools are used and no state is changed.",
    },
    LOADED_CONTENT_SAFE: {
        "claim": "portable-loaded-content-safe",
        "case_prefix": "lds-",
        "instructions": LOADED_CONTENT_SAFE_INSTRUCTIONS,
        "allowed": LOADED_CONTENT_SAFE_ALLOWED,
        "denied": LOADED_CONTENT_SAFE_DENIED,
        "tool_events_must_be_absent": False,
        "fixture_root_required": True,
        "pre_post_hash_required": True,
        "complete_tool_inventory_required": False,
        "network_must_be_disabled": False,
        "writable_workspace_must_be_absent": False,
        "symlink_free_fixtures": True,
        "core_prohibited_effect": (
            "Only native skill loading and approved reads occur; no task-surface or external state is changed."
        ),
    },
}
INDEPENDENT_GRADER_TIERS = {READ_ONLY_TOOLS, LOADED_CONTENT_SAFE}
# Compatibility aliases for callers importing the original zero-tools constants.
EXECUTION_MODE = ZERO_TOOLS
RUNNER_INSTRUCTIONS = ZERO_TOOLS_INSTRUCTIONS
ALLOWED_CAPABILITIES = ZERO_TOOLS_ALLOWED
DENIED_CAPABILITIES = ZERO_TOOLS_DENIED
EXECUTION_POLICY_FIELDS = {
    "tier",
    "mode",
    "runner_instructions",
    "allowed_capabilities",
    "denied_capabilities",
    "artifact_capture",
    "grading_scope",
    "tool_events_must_be_absent",
    "network_must_be_disabled",
    "writable_workspace_must_be_absent",
    "fixture_root_required",
    "symlink_free_fixtures",
    "pre_post_hash_required",
    "complete_tool_inventory_required",
}
CORE_PROHIBITED_EFFECT = TIER_CONTRACTS[ZERO_TOOLS]["core_prohibited_effect"]
RUNNER_CASE_FIELDS = {
    "id",
    "purpose",
    "positive_prompt",
    "near_miss_prompt",
    "baseline",
    "setup",
    "prohibited_effects",
}
PENDING_KEY_DIGEST = "PENDING-COORDINATOR-SEAL"
DIGEST = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_URI_PREFIX = "coordinator-evidence://"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize and validate frozen Agent Skill proof artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cases = subparsers.add_parser("validate-cases", help="Validate an evaluation case pack")
    cases.add_argument("--manifest", type=Path, help="Digest/count manifest for the runner pack")
    cases.add_argument("--key", type=Path, help="Disclosed grader key outside Git and skill bundles")
    cases.add_argument("cases", type=Path)

    initialize = subparsers.add_parser("init-report", help="Create a non-overwriting pending proof report")
    initialize.add_argument("--cases", required=True, type=Path)
    initialize.add_argument("--profile", required=True, type=Path)
    initialize.add_argument("--manifest", required=True, type=Path)
    initialize.add_argument("--grader-profile", type=Path, help="Independent zero-tools grader profile")
    initialize.add_argument("--output", required=True, type=Path)

    report = subparsers.add_parser("validate-report", help="Validate a proof report and its frozen inputs")
    report.add_argument("--cases", required=True, type=Path)
    report.add_argument("--profile", required=True, type=Path)
    report.add_argument("--manifest", required=True, type=Path)
    report.add_argument("--grader-profile", type=Path, help="Independent zero-tools grader profile")
    report.add_argument(
        "--evidence-root",
        type=Path,
        help="Coordinator evidence root used to verify qualification and grader evidence digests",
    )
    report.add_argument("--key", type=Path, help="Disclosed grader key outside Git and skill bundles")
    report.add_argument("--complete", action="store_true", help="Require every proof lane to have final evidence")
    report.add_argument("report", type=Path)
    return parser.parse_args()


def load_json(path: Path) -> tuple[Any | None, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except OSError as exc:
        return None, [f"cannot read {path}: {exc}"]
    except (UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"invalid JSON in {path}: {exc}"]


def require_nonempty_string(mapping: dict[str, Any], field: str, location: str, errors: list[str]) -> None:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{location}.{field} must be a non-empty string")


def validate_string_list(value: Any, location: str, *, nonempty: bool, errors: list[str]) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        errors.append(f"{location} must be an array of non-empty strings")
    elif nonempty and not value:
        errors.append(f"{location} must not be empty")


def validate_exact_string_set(value: Any, expected: set[str], location: str, errors: list[str]) -> None:
    validate_string_list(value, location, nonempty=True, errors=errors)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return
    if len(value) != len(set(value)):
        errors.append(f"{location} must not contain duplicates")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        errors.append(f"{location} differs from the tier contract; missing={missing}, extra={extra}")


def validate_identity(value: dict[str, Any], location: str, errors: list[str]) -> str | None:
    if value.get("schema_version") != SCHEMA_VERSION:
        if value.get("schema_version") == 1:
            errors.append(f"{location} schema_version 1 requires migration to schema_version 2")
        else:
            errors.append(f"{location} schema_version must be {SCHEMA_VERSION}")
    tier = value.get("tier")
    if tier not in TIER_CONTRACTS:
        errors.append(f"{location}.tier must be one of {sorted(TIER_CONTRACTS)}")
        tier = None
    require_nonempty_string(value, "suite", location, errors)
    return tier if isinstance(tier, str) else None


def validate_execution_policy(value: Any, tier: str | None, location: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    unexpected = set(value) - EXECUTION_POLICY_FIELDS
    missing = EXECUTION_POLICY_FIELDS - set(value)
    if missing:
        errors.append(f"{location} missing key(s): {', '.join(sorted(missing))}")
    if unexpected:
        errors.append(f"{location} has unexpected key(s): {', '.join(sorted(unexpected))}")
    if value.get("tier") != tier:
        errors.append(f"{location}.tier differs from the containing artifact tier '{tier}'")
    contract = TIER_CONTRACTS.get(tier) if tier is not None else None
    if contract is None:
        return
    if value.get("mode") != tier:
        errors.append(f"{location}.mode must be '{tier}'")
    if value.get("runner_instructions") != contract["instructions"]:
        errors.append(f"{location}.runner_instructions differs from the required {tier} instructions")
    validate_exact_string_set(
        value.get("allowed_capabilities"), contract["allowed"], f"{location}.allowed_capabilities", errors
    )
    validate_exact_string_set(
        value.get("denied_capabilities"), contract["denied"], f"{location}.denied_capabilities", errors
    )
    if value.get("artifact_capture") != "coordinator-captured-final-response":
        errors.append(f"{location}.artifact_capture must be 'coordinator-captured-final-response'")
    if value.get("grading_scope") != "response-content-only":
        errors.append(f"{location}.grading_scope must be 'response-content-only'")
    expected_booleans = {
        "tool_events_must_be_absent": contract["tool_events_must_be_absent"],
        "network_must_be_disabled": contract["network_must_be_disabled"],
        "writable_workspace_must_be_absent": contract["writable_workspace_must_be_absent"],
        "fixture_root_required": contract["fixture_root_required"],
        "symlink_free_fixtures": contract["symlink_free_fixtures"],
        "pre_post_hash_required": contract["pre_post_hash_required"],
        "complete_tool_inventory_required": contract["complete_tool_inventory_required"],
    }
    for field, expected in expected_booleans.items():
        if value.get(field) is not expected:
            errors.append(f"{location}.{field} must be {str(expected).lower()}")


def validate_cases(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["case pack must be a JSON object"]
    tier = validate_identity(value, "case pack", errors)
    validate_execution_policy(value.get("execution_policy"), tier, "case pack.execution_policy", errors)
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        return errors + ["case pack.cases must be a non-empty array"]

    seen: set[str] = set()
    for index, case in enumerate(cases):
        location = f"case pack.cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{location} must be an object")
            continue
        case_id = case.get("id")
        label = case_id if isinstance(case_id, str) and case_id else f"index {index}"
        for forbidden in ("expected", "checks"):
            if forbidden in case:
                errors.append(f"case '{label}' must not contain runner-hidden field '{forbidden}'")
        unexpected = set(case) - RUNNER_CASE_FIELDS - {"expected", "checks"}
        if unexpected:
            errors.append(f"case '{label}' has unexpected field(s): {', '.join(sorted(unexpected))}")
        for field in ("id", "purpose", "positive_prompt", "near_miss_prompt"):
            require_nonempty_string(case, field, location, errors)
        if isinstance(case_id, str):
            contract = TIER_CONTRACTS.get(tier) if isinstance(tier, str) else None
            prefix = contract.get("case_prefix") if contract is not None else None
            if isinstance(prefix, str) and not case_id.startswith(prefix):
                errors.append(f"{location}.id must start with tier prefix '{prefix}'")
            if case_id in seen:
                errors.append(f"duplicate case id: {case_id}")
            seen.add(case_id)
        if case.get("baseline") not in BASELINES:
            errors.append(f"{location}.baseline must be one of {sorted(BASELINES)}")
        validate_string_list(case.get("setup"), f"{location}.setup", nonempty=True, errors=errors)
        validate_string_list(
            case.get("prohibited_effects"), f"{location}.prohibited_effects", nonempty=True, errors=errors
        )
        prohibited_effects = case.get("prohibited_effects")
        contract = TIER_CONTRACTS.get(tier) if isinstance(tier, str) else None
        core_boundary = contract.get("core_prohibited_effect") if contract is not None else None
        if (
            isinstance(prohibited_effects, list)
            and isinstance(core_boundary, str)
            and core_boundary not in prohibited_effects
        ):
            errors.append(f"{location}.prohibited_effects must include the exact {tier} boundary '{core_boundary}'")
    return errors


def validate_load_state(value: Any, location: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    for field in ("method", "positive_signal", "negative_signal"):
        require_nonempty_string(value, field, location, errors)
    if value.get("reliability") not in LOAD_RELIABILITY:
        errors.append(f"{location}.reliability must be one of {sorted(LOAD_RELIABILITY)}")


def validate_evidence_reference_fields(
    value: dict[str, Any],
    path_field: str,
    digest_field: str,
    location: str,
    errors: list[str],
    *,
    required: bool,
) -> None:
    path_value = value.get(path_field)
    digest_value = value.get(digest_field)
    if required or path_value is not None or digest_value is not None:
        require_nonempty_string(value, path_field, location, errors)
        require_digest(value, digest_field, location, errors)
    if isinstance(path_value, str) and path_value and not path_value.startswith(EVIDENCE_URI_PREFIX):
        errors.append(f"{location}.{path_field} must start with '{EVIDENCE_URI_PREFIX}'")


def validate_inventory_locator(value: Any, location: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    expected_fields = {"record_match", "field"}
    if set(value) != expected_fields:
        errors.append(f"{location} must contain exactly {sorted(expected_fields)}")
    record_match = value.get("record_match")
    if not isinstance(record_match, dict) or not record_match:
        errors.append(f"{location}.record_match must be a non-empty object")
    elif not all(isinstance(key, str) and key and isinstance(item, str) and item for key, item in record_match.items()):
        errors.append(f"{location}.record_match keys and values must be non-empty strings")
    require_nonempty_string(value, "field", location, errors)


def validate_tool_boundary(
    value: Any,
    snapshot_date: Any,
    lane_availability: Any,
    location: str,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    for field in (
        "tool_catalog_method",
        "fixture_root_enforcement",
        "network_isolation",
        "writable_workspace_isolation",
        "trace_method",
        "qualification_evidence_path",
        "recheck_date",
    ):
        require_nonempty_string(value, field, location, errors)
    validate_inventory_locator(value.get("tool_inventory_locator"), f"{location}.tool_inventory_locator", errors)
    qualification_status = value.get("qualification_status")
    if qualification_status not in AVAILABILITY:
        errors.append(f"{location}.qualification_status must be one of {sorted(AVAILABILITY)}")
    if qualification_status != lane_availability:
        errors.append(f"{location}.qualification_status must equal lane availability '{lane_availability}'")
    validate_evidence_reference_fields(
        value,
        "qualification_evidence_path",
        "qualification_evidence_sha256",
        location,
        errors,
        required=True,
    )
    validate_string_list(
        value.get("qualification_notes"),
        f"{location}.qualification_notes",
        nonempty=qualification_status == "unavailable",
        errors=errors,
    )
    if value.get("reliability") not in LOAD_RELIABILITY:
        errors.append(f"{location}.reliability must be one of {sorted(LOAD_RELIABILITY)}")
    mapping = value.get("host_tool_map")
    if not isinstance(mapping, dict):
        errors.append(f"{location}.host_tool_map must be an object")
        return
    actual_capabilities = set(mapping)
    if actual_capabilities != READ_ONLY_FIXTURE_CAPABILITIES:
        errors.append(f"{location}.host_tool_map must map exactly {sorted(READ_ONLY_FIXTURE_CAPABILITIES)}")
    seen_tools: set[str] = set()
    for capability, tools in mapping.items():
        tool_location = f"{location}.host_tool_map.{capability}"
        validate_string_list(tools, tool_location, nonempty=True, errors=errors)
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, str):
                    if tool in seen_tools:
                        errors.append(f"{tool_location} repeats host tool '{tool}' across capabilities")
                    seen_tools.add(tool)
    validate_recheck_bound(snapshot_date, value.get("recheck_date"), f"{location}.recheck_date", 183, errors)
    validate_not_stale(value.get("recheck_date"), f"{location}.recheck_date", errors)


def validate_safety_controls(
    value: Any,
    snapshot_date: Any,
    lane_availability: Any,
    location: str,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    for field in (
        "host_mode",
        "sandbox",
        "trace_method",
        "task_surface",
        "skill_install",
        "qualification_evidence_path",
        "recheck_date",
    ):
        require_nonempty_string(value, field, location, errors)
    validate_exact_string_set(
        value.get("allowed_read_surfaces"),
        {"fixture-root", "skill-bundle"},
        f"{location}.allowed_read_surfaces",
        errors,
    )
    validate_string_list(
        value.get("forbidden_event_kinds"),
        f"{location}.forbidden_event_kinds",
        nonempty=True,
        errors=errors,
    )
    qualification_status = value.get("qualification_status")
    if qualification_status not in AVAILABILITY:
        errors.append(f"{location}.qualification_status must be one of {sorted(AVAILABILITY)}")
    if qualification_status != lane_availability:
        errors.append(f"{location}.qualification_status must equal lane availability '{lane_availability}'")
    validate_evidence_reference_fields(
        value,
        "qualification_evidence_path",
        "qualification_evidence_sha256",
        location,
        errors,
        required=True,
    )
    validate_string_list(
        value.get("qualification_notes"),
        f"{location}.qualification_notes",
        nonempty=qualification_status == "unavailable",
        errors=errors,
    )
    if value.get("reliability") not in LOAD_RELIABILITY:
        errors.append(f"{location}.reliability must be one of {sorted(LOAD_RELIABILITY)}")
    validate_recheck_bound(snapshot_date, value.get("recheck_date"), f"{location}.recheck_date", 183, errors)
    validate_not_stale(value.get("recheck_date"), f"{location}.recheck_date", errors)


def validate_profile(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["target profile must be a JSON object"]
    tier = validate_identity(value, "target profile", errors)
    if value.get("runtime_verification_required") is not True:
        errors.append("target profile must require runtime verification")
    require_nonempty_string(value, "snapshot_date", "target profile", errors)
    require_nonempty_string(value, "notice", "target profile", errors)
    lanes = value.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        return errors + ["target profile.lanes must be a non-empty array"]
    seen: set[str] = set()
    for index, lane in enumerate(lanes):
        location = f"target profile.lanes[{index}]"
        if not isinstance(lane, dict):
            errors.append(f"{location} must be an object")
            continue
        for field in ("lane_id", "host", "model", "availability"):
            require_nonempty_string(lane, field, location, errors)
        if lane.get("availability") not in AVAILABILITY:
            errors.append(f"{location}.availability must be one of {sorted(AVAILABILITY)}")
        lane_id = lane.get("lane_id")
        if isinstance(lane_id, str):
            if lane_id in seen:
                errors.append(f"duplicate lane id: {lane_id}")
            seen.add(lane_id)
        reasoning = lane.get("reasoning")
        if reasoning is not None and (not isinstance(reasoning, str) or not reasoning.strip()):
            errors.append(f"{location}.reasoning must be null or a non-empty string")
        load_state = lane.get("load_state_observation")
        validate_load_state(load_state, f"{location}.load_state_observation", errors)
        if (
            lane.get("availability") == "verified"
            and isinstance(load_state, dict)
            and load_state.get("reliability") != "reliable"
        ):
            errors.append(f"{location}.availability must be 'unavailable' when load-state reliability is unreliable")
        if tier == READ_ONLY_TOOLS:
            tool_boundary = lane.get("tool_boundary")
            validate_tool_boundary(
                tool_boundary,
                value.get("snapshot_date"),
                lane.get("availability"),
                f"{location}.tool_boundary",
                errors,
            )
            if (
                lane.get("availability") == "verified"
                and isinstance(tool_boundary, dict)
                and tool_boundary.get("reliability") != "reliable"
            ):
                errors.append(
                    f"{location}.availability must be 'unavailable' when tool-boundary reliability is unreliable"
                )
        elif tier == LOADED_CONTENT_SAFE:
            safety_controls = lane.get("safety_controls")
            validate_safety_controls(
                safety_controls,
                value.get("snapshot_date"),
                lane.get("availability"),
                f"{location}.safety_controls",
                errors,
            )
            if "tool_boundary" in lane:
                errors.append(f"{location}.tool_boundary is forbidden for the loaded-content-safe profile")
            if (
                lane.get("availability") == "verified"
                and isinstance(safety_controls, dict)
                and safety_controls.get("reliability") != "reliable"
            ):
                errors.append(
                    f"{location}.availability must be 'unavailable' when safety-control reliability is unreliable"
                )
        else:
            if "tool_boundary" in lane:
                errors.append(f"{location}.tool_boundary is forbidden for the zero-tools profile")
            validate_evidence_reference_fields(
                lane,
                "qualification_evidence_path",
                "qualification_evidence_sha256",
                location,
                errors,
                required=lane.get("availability") == "verified",
            )
            validate_string_list(
                lane.get("qualification_notes"),
                f"{location}.qualification_notes",
                nonempty=lane.get("availability") == "unavailable",
                errors=errors,
            )
    return errors


def case_pack_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest(value: Any, cases: dict[str, Any], digest: str, *, complete: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["key manifest must be a JSON object"]
    tier = validate_identity(value, "key manifest", errors)
    if tier != cases.get("tier"):
        errors.append("key manifest tier differs from the runner pack")
    if value.get("suite") != cases.get("suite"):
        errors.append("key manifest suite differs from the runner pack")
    manifest_digest = value.get("case_pack_sha256")
    if manifest_digest != digest:
        errors.append(
            f"key manifest case_pack_sha256 '{manifest_digest}' differs from live runner-pack digest '{digest}'"
        )
    key_digest = value.get("key_sha256")
    if key_digest == PENDING_KEY_DIGEST:
        if complete:
            errors.append("key manifest is unsealed: key_sha256 is PENDING-COORDINATOR-SEAL")
    elif not isinstance(key_digest, str) or DIGEST.fullmatch(key_digest) is None:
        errors.append("key manifest key_sha256 must be 64 lowercase hexadecimal characters or PENDING-COORDINATOR-SEAL")

    manifest_cases = value.get("cases")
    if not isinstance(manifest_cases, list):
        return errors + ["key manifest.cases must be an array"]
    expected_ids = {case["id"] for case in cases["cases"]}
    actual_ids: list[str] = []
    for index, entry in enumerate(manifest_cases):
        location = f"key manifest.cases[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{location} must be an object")
            continue
        require_nonempty_string(entry, "case_id", location, errors)
        case_id = entry.get("case_id")
        if isinstance(case_id, str):
            actual_ids.append(case_id)
        check_count = entry.get("check_count")
        if not isinstance(check_count, int) or isinstance(check_count, bool) or check_count < 1:
            errors.append(f"{location}.check_count must be a positive integer")
    if set(actual_ids) != expected_ids or len(actual_ids) != len(expected_ids):
        missing = sorted(expected_ids - set(actual_ids))
        extra = sorted(set(actual_ids) - expected_ids)
        errors.append(f"key manifest case ids differ from runner pack; missing={missing}, extra={extra}")
    return errors


def validate_key(value: Any, cases: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["grader key must be a JSON object"]
    tier = validate_identity(value, "grader key", errors)
    if tier != cases.get("tier"):
        errors.append("grader key tier differs from the runner pack")
    if tier != manifest.get("tier"):
        errors.append("grader key tier differs from the key manifest")
    if value.get("suite") != cases.get("suite"):
        errors.append("grader key suite differs from the runner pack")
    if value.get("suite") != manifest.get("suite"):
        errors.append("grader key suite differs from the key manifest")
    key_cases = value.get("cases")
    if not isinstance(key_cases, list):
        return errors + ["grader key.cases must be an array"]

    runner_ids = {case["id"] for case in cases["cases"]}
    manifest_by_id = {
        entry["case_id"]: entry for entry in manifest.get("cases", []) if isinstance(entry, dict) and "case_id" in entry
    }
    actual_ids: list[str] = []
    for index, case in enumerate(key_cases):
        location = f"grader key.cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{location} must be an object")
            continue
        require_nonempty_string(case, "case_id", location, errors)
        case_id = case.get("case_id")
        if isinstance(case_id, str):
            actual_ids.append(case_id)
        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{location}.expected must be an object")
        else:
            if expected.get("route") not in ROUTES:
                errors.append(f"{location}.expected.route must be one of {sorted(ROUTES)}")
            if expected.get("longevity") not in LONGEVITY:
                errors.append(f"{location}.expected.longevity must be one of {sorted(LONGEVITY)}")
        checks = case.get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append(f"{location}.checks must be a non-empty array")
            continue
        seen_checks: set[str] = set()
        for check_index, check in enumerate(checks):
            check_location = f"{location}.checks[{check_index}]"
            if not isinstance(check, dict):
                errors.append(f"{check_location} must be an object")
                continue
            for field in ("id", "text"):
                require_nonempty_string(check, field, check_location, errors)
            check_id = check.get("id")
            if isinstance(check_id, str):
                if check_id in seen_checks:
                    errors.append(f"duplicate grader check id '{check_id}' in case '{case_id}'")
                seen_checks.add(check_id)
            kind = check.get("kind")
            if kind not in CHECK_KINDS:
                errors.append(
                    f"{check_location}.kind must be 'semantic'; content-only evals cannot execute grader commands"
                )
            if "command" in check:
                errors.append(f"{check_location}.command is forbidden for content-only grading")
        manifest_entry = manifest_by_id.get(case_id)
        if manifest_entry is not None and len(checks) != manifest_entry.get("check_count"):
            errors.append(
                f"grader key case '{case_id}' has {len(checks)} checks; manifest requires {manifest_entry.get('check_count')}"
            )
    if set(actual_ids) != runner_ids or len(actual_ids) != len(runner_ids):
        missing = sorted(runner_ids - set(actual_ids))
        extra = sorted(set(actual_ids) - runner_ids)
        errors.append(f"grader key case ids differ from runner pack; missing={missing}, extra={extra}")
    return errors


def nearest_git_root(path: Path) -> Path | None:
    for directory in (path, *path.parents):
        if (directory / ".git").exists():
            return directory
    return None


def first_nonshared_symlink(path: Path, reference: Path) -> Path | None:
    """Find a symlink unique to path while ignoring shared platform aliases."""
    shared_ancestors = {reference.parent, *reference.parent.parents}
    for candidate in (path, *path.parents):
        if candidate in shared_ancestors:
            break
        if candidate.is_symlink():
            return candidate
    return None


def validate_key_path(key_path: Path, manifest_path: Path) -> tuple[Path | None, list[str]]:
    lexical_key = Path(os.path.abspath(key_path.expanduser()))
    if lexical_key.is_symlink():
        return None, [f"grader key path must not be a symlink: {lexical_key}"]
    lexical_manifest = Path(os.path.abspath(manifest_path.expanduser()))
    symlink_component = first_nonshared_symlink(lexical_key, lexical_manifest)
    if symlink_component is not None:
        return None, [f"grader key path must not contain symlinks; found {symlink_component}"]

    for directory in (lexical_key.parent, *lexical_key.parent.parents):
        skill_md = directory / "SKILL.md"
        if skill_md.is_file():
            return None, [f"grader key must be outside every skill bundle; found {skill_md}"]

    try:
        resolved_manifest = manifest_path.expanduser().resolve(strict=True)
    except OSError as exc:
        return None, [f"key manifest is unavailable: {exc}"]
    lexical_repository_root = nearest_git_root(lexical_manifest.parent)
    if lexical_repository_root is not None and lexical_key.is_relative_to(lexical_repository_root):
        return None, [f"grader key {lexical_key} must be outside Git repository {lexical_repository_root}"]
    repository_root = nearest_git_root(resolved_manifest.parent)

    try:
        resolved_key = key_path.expanduser().resolve(strict=True)
    except OSError as exc:
        return None, [f"grader key is unavailable: {exc}"]
    if not resolved_key.is_file():
        return None, [f"grader key is not a file: {resolved_key}"]

    for directory in (resolved_key.parent, *resolved_key.parent.parents):
        skill_md = directory / "SKILL.md"
        if skill_md.is_file():
            return None, [f"grader key must be outside every skill bundle; found {skill_md}"]

    if repository_root is not None and resolved_key.is_relative_to(repository_root):
        return None, [f"grader key {resolved_key} must be outside Git repository {repository_root}"]
    return resolved_key, []


def validate_key_digest(key_path: Path, manifest: dict[str, Any]) -> list[str]:
    expected = manifest.get("key_sha256")
    if expected == PENDING_KEY_DIGEST:
        return ["key manifest is unsealed: key_sha256 is PENDING-COORDINATOR-SEAL"]
    try:
        actual = hashlib.sha256(key_path.read_bytes()).hexdigest()
    except OSError as exc:
        return [f"cannot hash grader key: {exc}"]
    if actual != expected:
        return [f"grader key digest '{actual}' differs from manifest key_sha256 '{expected}'"]
    return []


def evidence_relative_path(locator: str) -> PurePosixPath | None:
    if not locator.startswith(EVIDENCE_URI_PREFIX):
        return None
    relative = PurePosixPath(locator.removeprefix(EVIDENCE_URI_PREFIX))
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    return relative


def evidence_references(profile: dict[str, Any], grader_profile: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    references: list[tuple[str, str, str]] = []
    tier = profile.get("tier")
    for index, lane in enumerate(profile.get("lanes", [])):
        if not isinstance(lane, dict):
            continue
        if tier == READ_ONLY_TOOLS:
            source = lane.get("tool_boundary")
        elif tier == LOADED_CONTENT_SAFE:
            source = lane.get("safety_controls")
        else:
            source = lane
        if not isinstance(source, dict):
            continue
        locator = source.get("qualification_evidence_path")
        digest = source.get("qualification_evidence_sha256")
        if isinstance(locator, str) and isinstance(digest, str):
            references.append((locator, digest, f"target profile.lanes[{index}] qualification evidence"))
    if isinstance(grader_profile, dict):
        for index, grader in enumerate(grader_profile.get("graders", [])):
            if not isinstance(grader, dict) or grader.get("availability") != "verified":
                continue
            locator = grader.get("evidence_path")
            digest = grader.get("evidence_sha256")
            if isinstance(locator, str) and isinstance(digest, str):
                references.append((locator, digest, f"grader profile.graders[{index}] evidence"))
    return references


def validate_evidence_files(
    profile: dict[str, Any],
    grader_profile: dict[str, Any] | None,
    evidence_root: Path | None,
) -> list[str]:
    references = evidence_references(profile, grader_profile)
    if not references:
        return []
    if evidence_root is None:
        return ["complete validation requires --evidence-root to verify qualification evidence digests"]
    try:
        root = evidence_root.expanduser().resolve(strict=True)
    except OSError as exc:
        return [f"coordinator evidence root is unavailable: {exc}"]
    if not root.is_dir():
        return [f"coordinator evidence root is not a directory: {root}"]
    errors: list[str] = []
    for locator, expected, location in references:
        relative = evidence_relative_path(locator)
        if relative is None:
            errors.append(f"{location} locator is invalid: {locator}")
            continue
        lexical = root.joinpath(*relative.parts)
        symlink_component = next(
            (candidate for candidate in (lexical, *lexical.parents) if candidate != root and candidate.is_symlink()),
            None,
        )
        if symlink_component is not None:
            errors.append(f"{location} path must not contain symlinks; found {symlink_component}")
            continue
        try:
            evidence_path = lexical.resolve(strict=True)
        except OSError as exc:
            errors.append(f"{location} is unavailable: {exc}")
            continue
        if not evidence_path.is_file() or not evidence_path.is_relative_to(root):
            errors.append(f"{location} must resolve to a regular file inside {root}")
            continue
        try:
            actual = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"cannot hash {location}: {exc}")
            continue
        if actual != expected:
            errors.append(f"{location} digest '{actual}' differs from recorded SHA-256 '{expected}'")
    return errors


def pending_evidence(method: str | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"status": "pending", "evidence": []}
    if method is not None:
        block["method"] = method
    return block


def pending_longevity() -> dict[str, Any]:
    return {
        "verdict": "pending",
        "confidence": "pending",
        "factors": {factor: "pending" for factor in sorted(LONGEVITY_FACTORS)},
        "rationale": [],
        "death_modes": [],
        "drift_signals": [],
        "owner": None,
        "recheck_date": None,
    }


def pending_effect_observation(tier: str) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "mode": tier,
        "status": "pending",
        "baseline_trace": None,
        "with_skill_trace": None,
        "forbidden_events": [],
        "notes": [],
    }
    if tier in {READ_ONLY_TOOLS, LOADED_CONTENT_SAFE}:
        observation.update(
            {
                "baseline_trace_sha256": None,
                "with_skill_trace_sha256": None,
                "fixture_root_id": None,
                "baseline_pre_sha256": None,
                "baseline_post_sha256": None,
                "with_skill_pre_sha256": None,
                "with_skill_post_sha256": None,
                "exposed_tools": [],
                "tool_events": [],
            }
        )
    if tier == LOADED_CONTENT_SAFE:
        observation.update({"baseline_complete": False, "with_skill_complete": False})
    return observation


def pending_isolation(tier: str) -> dict[str, Any]:
    return {
        "tier": tier,
        "status": "pending",
        "verified_at": None,
        "raw_trace": None,
        "raw_trace_sha256": None,
        "exposed_tools": [],
        "tool_events": [],
        "fixture_root_id": None,
        "pre_sha256": None,
        "post_sha256": None,
        "forbidden_events": [],
        "notes": [],
    }


def pending_secondary_grading() -> dict[str, Any]:
    return {
        "status": "pending",
        "grader_model": None,
        "grader_lane_id": None,
        "grader_id": None,
        "grader_context": None,
        "arm_labels_anonymized": False,
        "graded_after_both_arms": False,
        "evidence": [],
    }


def initialized_report(
    cases: dict[str, Any],
    profile: dict[str, Any],
    manifest: dict[str, Any],
    digest: str,
    grader_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tier = cases["tier"]
    manifest_by_id = {entry["case_id"]: entry for entry in manifest["cases"]}
    lanes: list[dict[str, Any]] = []
    for profile_lane in profile["lanes"]:
        case_results = []
        for case in cases["cases"]:
            check_count = manifest_by_id[case["id"]]["check_count"]
            case_results.append(
                {
                    "case_id": case["id"],
                    "status": "pending",
                    "observed_route": "pending",
                    "observed_longevity": "pending",
                    "baseline": case["baseline"],
                    "baseline_artifact": None,
                    "with_skill_artifact": None,
                    "effect_observation": pending_effect_observation(tier),
                    "checks": [
                        {
                            "check_id": f"c{index}",
                            "check": None,
                            "status": "pending",
                            "evidence": [],
                            "grader": None,
                        }
                        for index in range(1, check_count + 1)
                    ],
                }
            )
        load_state = profile_lane["load_state_observation"]
        lanes.append(
            {
                "lane_id": profile_lane["lane_id"],
                "host": profile_lane["host"],
                "model": profile_lane["model"],
                "reasoning": profile_lane.get("reasoning"),
                "availability": profile_lane["availability"],
                "load_state_observation": load_state.copy(),
                "isolation": pending_isolation(tier),
                "profile": {"status": "pending", "verified_at": None, "evidence": []},
                "discovery": pending_evidence(load_state["method"]),
                "positive_trigger": pending_evidence(load_state["method"]),
                "near_miss": pending_evidence(load_state["method"]),
                "behavior": {"status": "pending", "evidence": [], "case_results": case_results},
                "blind_grading": {
                    "status": "pending",
                    "primary_outcome": "pending",
                    "grader_model": None,
                    "grader_lane_id": None,
                    "grader_id": None,
                    "grader_context": None,
                    "arm_labels_anonymized": False,
                    "graded_after_both_arms": False,
                    "key_custody": "external-coordinator",
                    "key_sha256": manifest["key_sha256"],
                    "evidence": [],
                    "secondary_grading": pending_secondary_grading(),
                },
                "efficiency": {
                    "status": "pending",
                    "baseline": None,
                    "with_skill": None,
                    "unit": None,
                    "notes": [],
                },
                "caveats": [],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "tier": tier,
        "suite": cases["suite"],
        "case_pack_sha256": digest,
        "profile_snapshot_date": profile["snapshot_date"],
        "grader_profile_snapshot_date": (grader_profile.get("snapshot_date") if grader_profile is not None else None),
        "execution_policy": cases["execution_policy"].copy(),
        "claim": "not-proven",
        "evidence_namespace": None,
        "longevity": pending_longevity(),
        "lanes": lanes,
    }


def is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value and parsed.tzinfo is not None


def parse_calendar_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def validate_recheck_bound(
    snapshot_value: Any, recheck_value: Any, location: str, max_days: int, errors: list[str]
) -> None:
    snapshot = parse_calendar_date(snapshot_value)
    recheck = parse_calendar_date(recheck_value)
    if snapshot is None:
        errors.append(f"{location} cannot be bounded because the snapshot date is invalid")
    if recheck is None:
        errors.append(f"{location} must be an ISO calendar date")
    if snapshot is not None and recheck is not None:
        delta = (recheck - snapshot).days
        if delta < 0 or delta > max_days:
            errors.append(f"{location} must be 0 to {max_days} days after the snapshot date")


def validate_not_stale(recheck_value: Any, location: str, errors: list[str]) -> None:
    recheck = parse_calendar_date(recheck_value)
    if recheck is not None and recheck < date.today():
        errors.append(f"{location} is stale; re-qualification was due {recheck.isoformat()}")


def validate_grader_profile(
    value: Any,
    expected_suite: str | None = None,
    expected_tier: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["grader profile must be a JSON object"]
    tier = validate_identity(value, "grader profile", errors)
    required_tier = expected_tier or READ_ONLY_TOOLS
    if required_tier not in INDEPENDENT_GRADER_TIERS:
        errors.append(f"grader profile expected tier must be one of {sorted(INDEPENDENT_GRADER_TIERS)}")
    elif tier != required_tier:
        errors.append(f"grader profile tier must be '{required_tier}'")
    if expected_suite is not None and value.get("suite") != expected_suite:
        errors.append("grader profile suite differs from the runner pack")
    if value.get("profile_type") != "independent-zero-tools-graders":
        errors.append("grader profile.profile_type must be 'independent-zero-tools-graders'")
    require_nonempty_string(value, "snapshot_date", "grader profile", errors)
    if value.get("runtime_verification_required") is not True:
        errors.append("grader profile must require runtime verification")
    graders = value.get("graders")
    if not isinstance(graders, list) or not graders:
        return errors + ["grader profile.graders must be a non-empty array"]
    seen_ids: set[str] = set()
    for index, grader in enumerate(graders):
        location = f"grader profile.graders[{index}]"
        if not isinstance(grader, dict):
            errors.append(f"{location} must be an object")
            continue
        for field in (
            "grader_id",
            "host",
            "model",
            "api_surface",
            "trace_method",
            "availability",
        ):
            require_nonempty_string(grader, field, location, errors)
        grader_id = grader.get("grader_id")
        if isinstance(grader_id, str):
            if grader_id in seen_ids:
                errors.append(f"duplicate grader id: {grader_id}")
            seen_ids.add(grader_id)
        availability = grader.get("availability")
        if availability not in AVAILABILITY:
            errors.append(f"{location}.availability must be one of {sorted(AVAILABILITY)}")
        if grader.get("execution_tier") != ZERO_TOOLS:
            errors.append(f"{location}.execution_tier must be '{ZERO_TOOLS}'")
        validate_string_list(grader.get("exposed_tools"), f"{location}.exposed_tools", nonempty=False, errors=errors)
        if grader.get("exposed_tools") != []:
            errors.append(f"{location}.exposed_tools must be empty")
        if availability == "verified":
            for field in (
                "explicit_empty_tool_set",
                "model_network_disabled",
                "writable_workspace_absent",
                "complete_trace",
            ):
                if grader.get(field) is not True:
                    errors.append(f"{location}.{field} must be true when availability is verified")
            if not is_timestamp(grader.get("verified_at")):
                errors.append(f"{location}.verified_at must be an ISO-8601 timestamp")
            validate_evidence_reference_fields(
                grader,
                "evidence_path",
                "evidence_sha256",
                location,
                errors,
                required=True,
            )
        validate_string_list(
            grader.get("notes"), f"{location}.notes", nonempty=availability == "unavailable", errors=errors
        )
        validate_recheck_bound(
            value.get("snapshot_date"), grader.get("recheck_date"), f"{location}.recheck_date", 183, errors
        )
        validate_not_stale(grader.get("recheck_date"), f"{location}.recheck_date", errors)
    return errors


def validate_longevity(value: Any, snapshot_date: Any, complete: bool, errors: list[str]) -> None:
    location = "proof report.longevity"
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    verdict = value.get("verdict")
    confidence = value.get("confidence")
    allowed_verdicts = LONGEVITY | (set() if complete else {"pending"})
    allowed_confidence = CONFIDENCE | (set() if complete else {"pending"})
    if verdict not in allowed_verdicts:
        errors.append(f"{location}.verdict must be one of {sorted(allowed_verdicts)}")
    if confidence not in allowed_confidence:
        errors.append(f"{location}.confidence must be one of {sorted(allowed_confidence)}")

    factors = value.get("factors")
    if not isinstance(factors, dict):
        errors.append(f"{location}.factors must be an object")
    else:
        missing = LONGEVITY_FACTORS - set(factors)
        extra = set(factors) - LONGEVITY_FACTORS
        if missing:
            errors.append(f"{location}.factors missing key(s): {', '.join(sorted(missing))}")
        if extra:
            errors.append(f"{location}.factors has unexpected key(s): {', '.join(sorted(extra))}")
        allowed_factors = FACTOR_VALUES | (set() if complete else {"pending"})
        for factor in sorted(LONGEVITY_FACTORS & set(factors)):
            if factors[factor] not in allowed_factors:
                errors.append(f"{location}.factors.{factor} must be one of {sorted(allowed_factors)}")

    for field in ("rationale", "death_modes", "drift_signals"):
        validate_string_list(value.get(field), f"{location}.{field}", nonempty=complete, errors=errors)
    owner = value.get("owner")
    if complete:
        require_nonempty_string(value, "owner", location, errors)
    elif owner is not None and (not isinstance(owner, str) or not owner.strip()):
        errors.append(f"{location}.owner must be null or a non-empty string")

    recheck = value.get("recheck_date")
    if not complete and recheck is None:
        return
    parsed_recheck = parse_calendar_date(recheck)
    if parsed_recheck is None:
        errors.append(f"{location}.recheck_date must be a valid YYYY-MM-DD date")
        return
    parsed_snapshot = parse_calendar_date(snapshot_date)
    if parsed_snapshot is None:
        errors.append("proof report.profile_snapshot_date must be a valid YYYY-MM-DD date")
        return
    days = (parsed_recheck - parsed_snapshot).days
    if days <= 0:
        errors.append(f"{location}.recheck_date must be after profile_snapshot_date")
    maximum = 92 if verdict == "watch" else 183
    if days > maximum:
        errors.append(f"{location}.recheck_date is {days} days after profile_snapshot_date; maximum is {maximum}")


def validate_evidence_block(
    value: Any,
    location: str,
    complete: bool,
    errors: list[str],
    *,
    expected_method: str | None = None,
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    status = value.get("status")
    evidence = value.get("evidence")
    if complete and status not in RESULT_STATUSES:
        errors.append(f"{location}.status must be one of {sorted(RESULT_STATUSES)}")
    elif not complete and status not in RESULT_STATUSES | {"pending"}:
        errors.append(f"{location}.status is invalid")
    validate_string_list(evidence, f"{location}.evidence", nonempty=complete, errors=errors)
    if complete and status in {"pass", "fail"} and expected_method is not None:
        actual_method = value.get("method")
        if actual_method != expected_method:
            errors.append(f"{location}.method '{actual_method}' differs from load-state method '{expected_method}'")


def validate_report_check(
    check: Any,
    frozen: dict[str, Any] | None,
    location: str,
    complete: bool,
    errors: list[str],
) -> None:
    if not isinstance(check, dict):
        errors.append(f"{location} must be an object")
        return
    require_nonempty_string(check, "check_id", location, errors)
    text = check.get("check")
    if text is not None and (not isinstance(text, str) or not text.strip()):
        errors.append(f"{location}.check must be null or a non-empty string")
    grader = check.get("grader")
    if grader is not None and grader not in CHECK_GRADERS:
        errors.append(f"{location}.grader must be null or one of {sorted(CHECK_GRADERS)}")
    validate_evidence_block(check, location, complete, errors)
    if frozen is None:
        return
    check_id = frozen["id"]
    if check.get("check_id") != check_id:
        errors.append(f"{location}.check_id differs from disclosed key check '{check_id}'")
    if check.get("check") != frozen["text"]:
        errors.append(f"{location}.check differs from disclosed key check '{check_id}'")
    if not complete:
        return
    if grader != "blind-llm":
        errors.append(f"{location}.grader for check '{check_id}' must be 'blind-llm'")


def validate_normalized_event(value: Any, location: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return False
    for field in ("host_tool", "capability", "resolved_path", "scope", "status"):
        require_nonempty_string(value, field, location, errors)
    capability = value.get("capability")
    scope = value.get("scope")
    status = value.get("status")
    if capability not in READ_ONLY_FIXTURE_CAPABILITIES:
        errors.append(f"{location}.capability must be one of {sorted(READ_ONLY_FIXTURE_CAPABILITIES)}")
    if scope not in {"inside-fixture-root", "outside-fixture-root", "no-path"}:
        errors.append(f"{location}.scope is invalid")
    if status not in {"allowed", "denied", "error"}:
        errors.append(f"{location}.status is invalid")
    if status == "allowed" and scope != "inside-fixture-root":
        errors.append(f"{location} cannot allow a tool event outside the fixture root")
    return capability in READ_ONLY_FIXTURE_CAPABILITIES and not (status == "allowed" and scope != "inside-fixture-root")


def validate_loaded_event(value: Any, location: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return False
    for field in ("arm", "host_tool", "capability", "resolved_path", "scope", "status"):
        require_nonempty_string(value, field, location, errors)
    arm = value.get("arm")
    capability = value.get("capability")
    scope = value.get("scope")
    status = value.get("status")
    valid = True
    if arm not in {"baseline", "with-skill"}:
        errors.append(f"{location}.arm must be 'baseline' or 'with-skill'")
        valid = False
    if capability not in LOADED_EVENT_CAPABILITIES:
        errors.append(f"{location}.capability must be one of {sorted(LOADED_EVENT_CAPABILITIES)}")
        valid = False
    allowed_scopes = {"inside-fixture-root", "inside-skill-bundle", "outside-approved-roots", "no-path"}
    if scope not in allowed_scopes:
        errors.append(f"{location}.scope is invalid")
        valid = False
    if status not in {"allowed", "denied", "error"}:
        errors.append(f"{location}.status is invalid")
        valid = False
    if status == "allowed" and scope == "outside-approved-roots":
        errors.append(f"{location} cannot allow an event outside the approved roots")
        valid = False
    if capability == "native-skill-load" and (arm != "with-skill" or scope != "inside-skill-bundle"):
        errors.append(f"{location}.native-skill-load must be a with-skill event inside the skill bundle")
        valid = False
    if capability in READ_ONLY_FIXTURE_CAPABILITIES and status == "allowed" and scope != "inside-fixture-root":
        errors.append(f"{location}.{capability} must stay inside the fixture root")
        valid = False
    if (
        capability == "read-only-shell"
        and status == "allowed"
        and scope
        not in {
            "inside-fixture-root",
            "inside-skill-bundle",
        }
    ):
        errors.append(f"{location}.read-only-shell must stay inside an approved read root")
        valid = False
    return valid


def require_digest(mapping: dict[str, Any], field: str, location: str, errors: list[str]) -> None:
    digest = mapping.get(field)
    if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
        errors.append(f"{location}.{field} must be 64 lowercase hexadecimal characters")


def validate_effect_observation(
    value: Any,
    tier: str,
    location: str,
    complete: bool,
    errors: list[str],
    host_tool_map: dict[str, Any] | None = None,
    expected_fixture_root_id: str | None = None,
    expected_fixture_sha256: str | None = None,
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return False
    if value.get("mode") != tier:
        errors.append(f"{location}.mode must be '{tier}'")
    status = value.get("status")
    allowed = EFFECT_STATUSES | (set() if complete else {"pending"})
    if status not in allowed:
        errors.append(f"{location}.status must be one of {sorted(allowed)}")
    for field in ("baseline_trace", "with_skill_trace"):
        trace = value.get(field)
        if complete and status in {"clean", "violation"}:
            require_nonempty_string(value, field, location, errors)
        elif trace is not None and (not isinstance(trace, str) or not trace.strip()):
            errors.append(f"{location}.{field} must be null or a non-empty string")
    forbidden_events = value.get("forbidden_events")
    validate_string_list(
        forbidden_events,
        f"{location}.forbidden_events",
        nonempty=complete and status == "violation",
        errors=errors,
    )
    notes = value.get("notes")
    validate_string_list(notes, f"{location}.notes", nonempty=complete and status == "unavailable", errors=errors)
    if status == "clean" and forbidden_events:
        errors.append(f"{location}.forbidden_events must be empty when status is 'clean'")
    isolation_clean = status == "clean" and forbidden_events == []
    if tier == ZERO_TOOLS:
        for forbidden_field in (
            "exposed_tools",
            "tool_events",
            "fixture_root_id",
            "baseline_pre_sha256",
            "baseline_post_sha256",
            "with_skill_pre_sha256",
            "with_skill_post_sha256",
        ):
            if forbidden_field in value:
                errors.append(f"{location}.{forbidden_field} is forbidden for zero-tools evidence")
        return isolation_clean

    if tier == LOADED_CONTENT_SAFE:
        if complete and status in {"clean", "violation"}:
            for field in (
                "baseline_trace_sha256",
                "with_skill_trace_sha256",
                "baseline_pre_sha256",
                "baseline_post_sha256",
                "with_skill_pre_sha256",
                "with_skill_post_sha256",
            ):
                require_digest(value, field, location, errors)
            require_nonempty_string(value, "fixture_root_id", location, errors)
            for field in ("baseline_complete", "with_skill_complete"):
                if value.get(field) is not True:
                    errors.append(f"{location}.{field} must be true for complete loaded-content evidence")
                    isolation_clean = False
        validate_string_list(value.get("exposed_tools"), f"{location}.exposed_tools", nonempty=False, errors=errors)
        tool_events = value.get("tool_events")
        events_clean = True
        if not isinstance(tool_events, list):
            errors.append(f"{location}.tool_events must be an array")
            events_clean = False
            tool_events = []
        for index, event in enumerate(tool_events):
            events_clean = validate_loaded_event(event, f"{location}.tool_events[{index}]", errors) and events_clean
        if complete and status == "clean":
            skill_loads = [
                event
                for event in tool_events
                if isinstance(event, dict)
                and event.get("arm") == "with-skill"
                and event.get("capability") == "native-skill-load"
                and event.get("status") == "allowed"
                and event.get("scope") == "inside-skill-bundle"
            ]
            if not skill_loads:
                errors.append(f"{location}.tool_events must prove native skill loading in the with-skill arm")
                events_clean = False
            baseline_loads = [
                event
                for event in tool_events
                if isinstance(event, dict)
                and event.get("arm") == "baseline"
                and event.get("capability") == "native-skill-load"
                and event.get("status") == "allowed"
            ]
            if baseline_loads:
                errors.append(f"{location}.tool_events must not load the skill in the baseline arm")
                events_clean = False
            for before, after in (
                ("baseline_pre_sha256", "baseline_post_sha256"),
                ("with_skill_pre_sha256", "with_skill_post_sha256"),
            ):
                if value.get(before) != value.get(after):
                    errors.append(f"{location}.{before} differs from {after}")
                    isolation_clean = False
            if value.get("fixture_root_id") != expected_fixture_root_id:
                errors.append(
                    f"{location}.fixture_root_id '{value.get('fixture_root_id')}' differs from lane isolation "
                    f"fixture_root_id '{expected_fixture_root_id}'"
                )
                isolation_clean = False
            for field in (
                "baseline_pre_sha256",
                "baseline_post_sha256",
                "with_skill_pre_sha256",
                "with_skill_post_sha256",
            ):
                if value.get(field) != expected_fixture_sha256:
                    errors.append(
                        f"{location}.{field} differs from lane isolation fixture digest '{expected_fixture_sha256}'"
                    )
                    isolation_clean = False
        return isolation_clean and events_clean

    if complete and status in {"clean", "violation"}:
        for field in (
            "baseline_trace_sha256",
            "with_skill_trace_sha256",
            "baseline_pre_sha256",
            "baseline_post_sha256",
            "with_skill_pre_sha256",
            "with_skill_post_sha256",
        ):
            require_digest(value, field, location, errors)
        require_nonempty_string(value, "fixture_root_id", location, errors)
    exposed_tools = value.get("exposed_tools")
    validate_string_list(exposed_tools, f"{location}.exposed_tools", nonempty=False, errors=errors)
    expected_by_tool = {
        tool: capability
        for capability, tools in (host_tool_map or {}).items()
        if isinstance(tools, list)
        for tool in tools
        if isinstance(tool, str)
    }
    if complete and status in {"clean", "violation"}:
        actual_tools = set(exposed_tools) if isinstance(exposed_tools, list) else set()
        if actual_tools != set(expected_by_tool):
            missing = sorted(set(expected_by_tool) - actual_tools)
            extra = sorted(actual_tools - set(expected_by_tool))
            errors.append(
                f"{location}.exposed_tools differs from the exact profile allowlist; missing={missing}, extra={extra}"
            )
            isolation_clean = False
    tool_events = value.get("tool_events")
    events_clean = True
    if not isinstance(tool_events, list):
        errors.append(f"{location}.tool_events must be an array")
        events_clean = False
    else:
        for index, event in enumerate(tool_events):
            events_clean = validate_normalized_event(event, f"{location}.tool_events[{index}]", errors) and events_clean
            if isinstance(event, dict):
                host_tool = event.get("host_tool")
                expected_capability = expected_by_tool.get(host_tool) if isinstance(host_tool, str) else None
                if expected_capability is None:
                    errors.append(f"{location}.tool_events[{index}].host_tool is absent from the profile mapping")
                    events_clean = False
                elif event.get("capability") != expected_capability:
                    errors.append(
                        f"{location}.tool_events[{index}].capability differs from the profile mapping for "
                        f"host tool '{host_tool}'"
                    )
                    events_clean = False
        if complete and status == "clean":
            allowed_fixture_events = [
                event
                for event in tool_events
                if isinstance(event, dict)
                and event.get("status") == "allowed"
                and event.get("scope") == "inside-fixture-root"
                and event.get("capability") in READ_ONLY_FIXTURE_CAPABILITIES
            ]
            if not allowed_fixture_events:
                errors.append(f"{location}.tool_events must include at least one allowed in-root fixture read event")
                events_clean = False
    if complete and status == "clean":
        for before, after in (
            ("baseline_pre_sha256", "baseline_post_sha256"),
            ("with_skill_pre_sha256", "with_skill_post_sha256"),
        ):
            if value.get(before) != value.get(after):
                errors.append(f"{location}.{before} differs from {after}")
                isolation_clean = False
        if value.get("fixture_root_id") != expected_fixture_root_id:
            errors.append(
                f"{location}.fixture_root_id '{value.get('fixture_root_id')}' differs from lane isolation "
                f"fixture_root_id '{expected_fixture_root_id}'"
            )
            isolation_clean = False
        for field in (
            "baseline_pre_sha256",
            "baseline_post_sha256",
            "with_skill_pre_sha256",
            "with_skill_post_sha256",
        ):
            if value.get(field) != expected_fixture_sha256:
                errors.append(
                    f"{location}.{field} differs from lane isolation fixture digest '{expected_fixture_sha256}'"
                )
                isolation_clean = False
    return isolation_clean and events_clean


def validate_case_results(
    value: Any,
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
    key: dict[str, Any] | None,
    tier: str,
    profile_lane: dict[str, Any],
    location: str,
    complete: bool,
    errors: list[str],
    expected_fixture_root_id: str | None = None,
    expected_fixture_sha256: str | None = None,
) -> None:
    if not isinstance(value, list):
        errors.append(f"{location} must be an array")
        return
    cases_by_id = {case["id"]: case for case in cases}
    manifest_by_id = {entry["case_id"]: entry for entry in manifest["cases"]}
    key_by_id = {case["case_id"]: case for case in key["cases"]} if key is not None else {}
    actual_ids = [result.get("case_id") for result in value if isinstance(result, dict)]
    if set(actual_ids) != set(cases_by_id) or len(actual_ids) != len(cases_by_id):
        errors.append(f"{location} must contain every frozen case exactly once")
    for index, result in enumerate(value):
        result_location = f"{location}[{index}]"
        if not isinstance(result, dict):
            errors.append(f"{result_location} must be an object")
            continue
        case_id = result.get("case_id")
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        if result.get("baseline") != case["baseline"]:
            errors.append(f"{result_location}.baseline differs from the frozen runner case")
        status = result.get("status")
        if complete and status not in RESULT_STATUSES:
            errors.append(f"{result_location}.status must be one of {sorted(RESULT_STATUSES)}")
        elif not complete and status not in RESULT_STATUSES | {"pending"}:
            errors.append(f"{result_location}.status is invalid")
        allowed_routes = ROUTES | (set() if complete else {"pending"})
        observed_route = result.get("observed_route")
        if observed_route not in allowed_routes:
            errors.append(f"{result_location}.observed_route must be one of {sorted(allowed_routes)}")
        allowed_longevity = LONGEVITY | (set() if complete else {"pending"})
        observed_longevity = result.get("observed_longevity")
        if observed_longevity not in allowed_longevity:
            errors.append(f"{result_location}.observed_longevity must be one of {sorted(allowed_longevity)}")
        key_case = key_by_id.get(case_id)
        expected = key_case.get("expected") if isinstance(key_case, dict) else None
        if complete and isinstance(expected, dict):
            if observed_route != expected.get("route"):
                errors.append(
                    f"{result_location}.observed_route '{observed_route}' differs from disclosed key "
                    f"expectation '{expected.get('route')}'"
                )
            if observed_longevity != expected.get("longevity"):
                errors.append(
                    f"{result_location}.observed_longevity '{observed_longevity}' differs from disclosed key "
                    f"expectation '{expected.get('longevity')}'"
                )
        if complete and status != "unavailable":
            for artifact in ("baseline_artifact", "with_skill_artifact"):
                require_nonempty_string(result, artifact, result_location, errors)
        effects_clean = validate_effect_observation(
            result.get("effect_observation"),
            tier,
            f"{result_location}.effect_observation",
            complete,
            errors,
            (
                profile_lane.get("tool_boundary", {}).get("host_tool_map")
                if tier == READ_ONLY_TOOLS and isinstance(profile_lane.get("tool_boundary"), dict)
                else None
            ),
            expected_fixture_root_id,
            expected_fixture_sha256,
        )
        checks = result.get("checks")
        manifest_entry = manifest_by_id.get(case_id)
        if manifest_entry is None:
            errors.append(f"{result_location}.case_id '{case_id}' is absent from the key manifest")
            continue
        expected_count = manifest_entry["check_count"]
        if not isinstance(checks, list) or len(checks) != expected_count:
            errors.append(f"{result_location}.checks must contain the manifest check_count of {expected_count}")
            continue
        key_checks = key_by_id.get(case_id, {}).get("checks", [])
        for check_index, actual in enumerate(checks):
            frozen = key_checks[check_index] if check_index < len(key_checks) else None
            validate_report_check(actual, frozen, f"{result_location}.checks[{check_index}]", complete, errors)
        if status == "pass" and any(not isinstance(check, dict) or check.get("status") != "pass" for check in checks):
            errors.append(f"{result_location}.status cannot pass unless every frozen check passes")
        if status == "pass" and not effects_clean:
            errors.append(f"{result_location}.status cannot pass unless effect_observation.status is 'clean'")


def validate_efficiency(value: Any, location: str, complete: bool, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return
    status = value.get("status")
    allowed = {"observed", "unavailable"} | (set() if complete else {"pending"})
    if status not in allowed:
        errors.append(f"{location}.status must be one of {sorted(allowed)}")
    notes = value.get("notes")
    validate_string_list(notes, f"{location}.notes", nonempty=complete and status == "unavailable", errors=errors)
    if status == "observed":
        if not isinstance(value.get("baseline"), (int, float)) or not isinstance(value.get("with_skill"), (int, float)):
            errors.append(f"{location} observed metrics require numeric baseline and with_skill values")
        require_nonempty_string(value, "unit", location, errors)


def all_available_results_have_artifacts(results: Any) -> bool:
    if not isinstance(results, list):
        return False
    return all(
        isinstance(result, dict)
        and (
            result.get("status") == "unavailable"
            or (
                isinstance(result.get("baseline_artifact"), str)
                and bool(result["baseline_artifact"].strip())
                and isinstance(result.get("with_skill_artifact"), str)
                and bool(result["with_skill_artifact"].strip())
            )
        )
        for result in results
    )


def validate_lane_isolation(
    value: Any,
    tier: str,
    profile_lane: dict[str, Any],
    location: str,
    complete: bool,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return False
    if value.get("tier") != tier:
        errors.append(f"{location}.tier must be '{tier}'")
    status = value.get("status")
    allowed = {"verified", "unavailable"} | (set() if complete else {"pending"})
    if status not in allowed:
        errors.append(f"{location}.status must be one of {sorted(allowed)}")
    if complete and profile_lane.get("availability") == "verified" and status != "verified":
        errors.append(f"{location}.status must be 'verified' for an available target lane")
    if complete and profile_lane.get("availability") == "unavailable" and status != "unavailable":
        errors.append(f"{location}.status must be 'unavailable' for an unavailable target lane")
    exposed_tools = value.get("exposed_tools")
    validate_string_list(exposed_tools, f"{location}.exposed_tools", nonempty=False, errors=errors)
    tool_events = value.get("tool_events")
    if not isinstance(tool_events, list):
        errors.append(f"{location}.tool_events must be an array")
        tool_events = []
    forbidden_events = value.get("forbidden_events")
    validate_string_list(
        forbidden_events,
        f"{location}.forbidden_events",
        nonempty=False,
        errors=errors,
    )
    validate_string_list(
        value.get("notes"), f"{location}.notes", nonempty=complete and status == "unavailable", errors=errors
    )
    if status != "verified":
        return False
    if not is_timestamp(value.get("verified_at")):
        errors.append(f"{location}.verified_at must be an ISO-8601 timestamp")
    require_nonempty_string(value, "raw_trace", location, errors)
    require_digest(value, "raw_trace_sha256", location, errors)
    if forbidden_events:
        errors.append(f"{location}.forbidden_events must be empty when status is 'verified'")
    if tier == ZERO_TOOLS:
        if exposed_tools != []:
            errors.append(f"{location}.exposed_tools must be empty for zero-tools")
        if tool_events:
            errors.append(f"{location}.tool_events must be empty for zero-tools")
        return exposed_tools == [] and tool_events == [] and forbidden_events == []

    if tier == LOADED_CONTENT_SAFE:
        events_clean = True
        for index, event in enumerate(tool_events):
            events_clean = validate_loaded_event(event, f"{location}.tool_events[{index}]", errors) and events_clean
        skill_loads = [
            event
            for event in tool_events
            if isinstance(event, dict)
            and event.get("arm") == "with-skill"
            and event.get("capability") == "native-skill-load"
            and event.get("status") == "allowed"
            and event.get("scope") == "inside-skill-bundle"
        ]
        if not skill_loads:
            errors.append(f"{location}.tool_events must prove native skill loading")
            events_clean = False
        require_nonempty_string(value, "fixture_root_id", location, errors)
        require_digest(value, "pre_sha256", location, errors)
        require_digest(value, "post_sha256", location, errors)
        hashes_match = value.get("pre_sha256") == value.get("post_sha256")
        if not hashes_match:
            errors.append(f"{location}.pre_sha256 differs from post_sha256")
        return not forbidden_events and events_clean and hashes_match

    boundary = profile_lane.get("tool_boundary", {})
    mapping = boundary.get("host_tool_map", {}) if isinstance(boundary, dict) else {}
    allowed_tools = {
        tool for tools in mapping.values() if isinstance(tools, list) for tool in tools if isinstance(tool, str)
    }
    if isinstance(exposed_tools, list):
        missing = sorted(allowed_tools - set(exposed_tools))
        unknown = sorted(set(exposed_tools) - allowed_tools)
        if missing or unknown:
            errors.append(
                f"{location}.exposed_tools differs from the exact profile allowlist; missing={missing}, extra={unknown}"
            )
    events_clean = True
    for index, event in enumerate(tool_events):
        events_clean = validate_normalized_event(event, f"{location}.tool_events[{index}]", errors) and events_clean
        if isinstance(event, dict) and event.get("host_tool") not in allowed_tools:
            errors.append(f"{location}.tool_events[{index}].host_tool is absent from the profile mapping")
            events_clean = False
        elif isinstance(event, dict):
            expected_capability = next(
                (
                    capability
                    for capability, tools in mapping.items()
                    if isinstance(tools, list) and event.get("host_tool") in tools
                ),
                None,
            )
            if event.get("capability") != expected_capability:
                errors.append(
                    f"{location}.tool_events[{index}].capability differs from the profile mapping for "
                    f"host tool '{event.get('host_tool')}'"
                )
                events_clean = False
    require_nonempty_string(value, "fixture_root_id", location, errors)
    require_digest(value, "pre_sha256", location, errors)
    require_digest(value, "post_sha256", location, errors)
    hashes_match = value.get("pre_sha256") == value.get("post_sha256")
    if not hashes_match:
        errors.append(f"{location}.pre_sha256 differs from post_sha256")
    exact_inventory = isinstance(exposed_tools, list) and set(exposed_tools) == allowed_tools
    return not forbidden_events and exact_inventory and events_clean and hashes_match


def canonical_model_identity(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    without_namespace = value.casefold().strip().rsplit("/", maxsplit=1)[-1]
    without_modifiers = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", without_namespace)
    canonical = re.sub(r"[^a-z0-9]+", "-", without_modifiers).strip("-")
    while re.search(r"-(?:(?:19|20)\d{6}|\d+[km])$", canonical):
        canonical = re.sub(r"-(?:(?:19|20)\d{6}|\d+[km])$", "", canonical)
    return canonical or None


def has_eligible_secondary_grader(
    tier: str,
    lane: dict[str, Any],
    profile_by_id: dict[str, dict[str, Any]],
    grader_profile: dict[str, Any] | None,
    primary_grader_model: Any,
) -> bool:
    disallowed = {
        canonical_model_identity(lane.get("model")),
        canonical_model_identity(primary_grader_model),
        None,
    }
    if tier == ZERO_TOOLS:
        models = (
            candidate.get("model")
            for candidate in profile_by_id.values()
            if candidate.get("availability") == "verified"
        )
    else:
        graders = grader_profile.get("graders", []) if isinstance(grader_profile, dict) else []
        models = (
            grader.get("model")
            for grader in graders
            if isinstance(grader, dict) and grader.get("availability") == "verified"
        )
    return any(canonical_model_identity(model) not in disallowed for model in models)


def validate_grader_assignment(
    value: dict[str, Any],
    tier: str,
    lane: dict[str, Any],
    profile_by_id: dict[str, dict[str, Any]],
    grader_profile: dict[str, Any] | None,
    case_results: Any,
    location: str,
    errors: list[str],
    *,
    disallowed_grader_model: Any = None,
) -> bool:
    if value.get("arm_labels_anonymized") is not True:
        errors.append(f"{location}.arm_labels_anonymized must be true when blind grading is performed")
    if value.get("graded_after_both_arms") is not True:
        errors.append(f"{location}.graded_after_both_arms must be true when blind grading is performed")
    require_nonempty_string(value, "grader_model", location, errors)
    require_nonempty_string(value, "grader_context", location, errors)
    validate_string_list(value.get("evidence"), f"{location}.evidence", nonempty=True, errors=errors)
    grader_model = value.get("grader_model")
    grader_identity = canonical_model_identity(grader_model)
    lane_model = lane.get("model")
    if grader_identity is not None and grader_identity == canonical_model_identity(lane_model):
        errors.append(
            f"{location}.grader_model '{grader_model}' must differ from lane model '{lane_model}' after alias normalization"
        )
    if disallowed_grader_model is not None and grader_identity == canonical_model_identity(disallowed_grader_model):
        errors.append(
            f"{location}.grader_model '{grader_model}' must differ from primary grader model "
            f"'{disallowed_grader_model}' after alias normalization"
        )
    valid_assignment = True
    if tier == ZERO_TOOLS:
        require_nonempty_string(value, "grader_lane_id", location, errors)
        if value.get("grader_id") is not None:
            errors.append(f"{location}.grader_id must be null for zero-tools cross-lane grading")
            valid_assignment = False
        grader_lane_id = value.get("grader_lane_id")
        profile_models = {profile_lane["model"] for profile_lane in profile_by_id.values()}
        if grader_model not in profile_models:
            errors.append(f"{location}.grader_model '{grader_model}' is absent from the target profile")
            valid_assignment = False
        grader_lane = profile_by_id.get(grader_lane_id) if isinstance(grader_lane_id, str) else None
        if grader_lane is None:
            errors.append(f"{location}.grader_lane_id '{grader_lane_id}' is absent from the target profile")
            valid_assignment = False
        elif grader_lane.get("model") != grader_model:
            errors.append(
                f"{location}.grader_model '{grader_model}' differs from grader lane model '{grader_lane.get('model')}'"
            )
            valid_assignment = False
    else:
        require_nonempty_string(value, "grader_id", location, errors)
        if value.get("grader_lane_id") is not None:
            errors.append(f"{location}.grader_lane_id must be null for independent grading")
            valid_assignment = False
        graders = grader_profile.get("graders", []) if isinstance(grader_profile, dict) else []
        graders_by_id = {grader.get("grader_id"): grader for grader in graders if isinstance(grader, dict)}
        grader = graders_by_id.get(value.get("grader_id"))
        if grader is None:
            errors.append(f"{location}.grader_id is absent from the independent grader profile")
            valid_assignment = False
        elif grader.get("availability") != "verified":
            errors.append(f"{location}.grader_id is not verified in the independent grader profile")
            valid_assignment = False
        elif grader.get("model") != grader_model:
            errors.append(
                f"{location}.grader_model '{grader_model}' differs from independent grader model "
                f"'{grader.get('model')}'"
            )
            valid_assignment = False
    if not all_available_results_have_artifacts(case_results):
        errors.append(f"{location} cannot be performed before both artifacts are frozen for every available case")
        valid_assignment = False
    return valid_assignment


def validate_secondary_grading(
    value: Any,
    tier: str,
    lane: dict[str, Any],
    profile_by_id: dict[str, dict[str, Any]],
    grader_profile: dict[str, Any] | None,
    case_results: Any,
    primary_outcome: Any,
    primary_grader_model: Any,
    location: str,
    complete: bool,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return False
    status = value.get("status")
    allowed = SECONDARY_GRADING_STATUSES | (set() if complete else {"pending"})
    if status not in allowed:
        errors.append(f"{location}.status must be one of {sorted(allowed)}")
    secondary_required = primary_outcome in {"indeterminate", "conflict"}
    eligible_secondary = has_eligible_secondary_grader(
        tier,
        lane,
        profile_by_id,
        grader_profile,
        primary_grader_model,
    )
    unavailable_fallback = status == "unavailable" and not eligible_secondary
    if complete and secondary_required and status != "performed" and not unavailable_fallback:
        errors.append(f"{location}.status must be 'performed' when primary_outcome is '{primary_outcome}'")
    if complete and primary_outcome == "determinate" and status != "not-required":
        errors.append(f"{location}.status must be 'not-required' when primary_outcome is 'determinate'")
    if status == "performed":
        assignment_valid = validate_grader_assignment(
            value,
            tier,
            lane,
            profile_by_id,
            grader_profile,
            case_results,
            location,
            errors,
            disallowed_grader_model=primary_grader_model,
        )
        return assignment_valid and secondary_required
    validate_string_list(
        value.get("evidence"),
        f"{location}.evidence",
        nonempty=complete and secondary_required and status == "unavailable",
        errors=errors,
    )
    if status in {"not-required", "unavailable"}:
        for field in ("grader_model", "grader_lane_id", "grader_id", "grader_context"):
            if value.get(field) is not None:
                errors.append(f"{location}.{field} must be null when secondary grading is not performed")
        if value.get("arm_labels_anonymized") is not False:
            errors.append(f"{location}.arm_labels_anonymized must be false when secondary grading is not performed")
        if value.get("graded_after_both_arms") is not False:
            errors.append(f"{location}.graded_after_both_arms must be false when secondary grading is not performed")
    return not secondary_required and status != "unavailable"


def validate_blind_grading(
    value: Any,
    tier: str,
    lane: dict[str, Any],
    profile_by_id: dict[str, dict[str, Any]],
    grader_profile: dict[str, Any] | None,
    semantic_checks: bool,
    case_results: Any,
    manifest: dict[str, Any],
    location: str,
    complete: bool,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{location} must be an object")
        return False
    status = value.get("status")
    allowed = BLIND_GRADING_STATUSES | (set() if complete else {"pending"})
    if status not in allowed:
        errors.append(f"{location}.status must be one of {sorted(allowed)}")
    if value.get("key_custody") != "external-coordinator":
        errors.append(f"{location}.key_custody must be 'external-coordinator'")
    if complete and value.get("key_sha256") != manifest.get("key_sha256"):
        errors.append(f"{location}.key_sha256 differs from the key manifest")
    gradable_results = isinstance(case_results, list) and any(
        isinstance(result, dict) and result.get("status") != "unavailable" for result in case_results
    )
    if complete and semantic_checks and gradable_results and status != "performed":
        errors.append(f"{location}.status must be 'performed' when gradable semantic checks exist")
    primary_outcome = value.get("primary_outcome")
    allowed_outcomes = PRIMARY_GRADE_OUTCOMES | (set() if complete else {"pending"})
    if primary_outcome not in allowed_outcomes:
        errors.append(f"{location}.primary_outcome must be one of {sorted(allowed_outcomes)}")
    primary_valid = status != "unavailable"
    if status == "performed":
        if complete and primary_outcome not in PRIMARY_GRADE_OUTCOMES - {"unavailable"}:
            errors.append(f"{location}.primary_outcome must describe a performed primary grade")
            primary_valid = False
        primary_valid = (
            validate_grader_assignment(
                value,
                tier,
                lane,
                profile_by_id,
                grader_profile,
                case_results,
                location,
                errors,
            )
            and primary_valid
        )
    else:
        validate_string_list(value.get("evidence"), f"{location}.evidence", nonempty=False, errors=errors)
        if complete and status == "unavailable" and primary_outcome != "unavailable":
            errors.append(f"{location}.primary_outcome must be 'unavailable' when blind grading is unavailable")
    secondary_valid = validate_secondary_grading(
        value.get("secondary_grading"),
        tier,
        lane,
        profile_by_id,
        grader_profile,
        case_results,
        primary_outcome,
        value.get("grader_model"),
        f"{location}.secondary_grading",
        complete,
        errors,
    )
    return primary_valid and secondary_valid


def validate_report(
    value: Any,
    cases: dict[str, Any],
    profile: dict[str, Any],
    manifest: dict[str, Any],
    digest: str,
    complete: bool,
    key: dict[str, Any] | None = None,
    grader_profile: dict[str, Any] | None = None,
    evidence_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["proof report must be a JSON object"]
    tier = validate_identity(value, "proof report", errors)
    if tier != cases.get("tier"):
        errors.append("proof report tier differs from the runner pack")
    if value.get("suite") != cases.get("suite"):
        errors.append("proof report suite differs from the runner pack")
    if profile.get("tier") != cases.get("tier"):
        errors.append("target profile tier differs from the runner pack")
    if profile.get("suite") != cases.get("suite"):
        errors.append("target profile suite differs from the runner pack")
    report_digest = value.get("case_pack_sha256")
    manifest_digest = manifest.get("case_pack_sha256")
    if report_digest != digest:
        errors.append(
            f"proof report case_pack_sha256 '{report_digest}' differs from live runner-pack digest '{digest}'"
        )
    if manifest_digest != digest:
        errors.append(
            f"key manifest case_pack_sha256 '{manifest_digest}' differs from live runner-pack digest '{digest}'"
        )
    if report_digest != manifest_digest:
        errors.append("proof report and key manifest case_pack_sha256 values differ")
    if value.get("profile_snapshot_date") != profile.get("snapshot_date"):
        errors.append("proof report profile_snapshot_date does not match the target profile")
    if tier in INDEPENDENT_GRADER_TIERS:
        if grader_profile is None:
            errors.append(f"{tier} validation requires an independent grader profile")
        else:
            errors.extend(validate_grader_profile(grader_profile, cases.get("suite"), tier))
            if value.get("grader_profile_snapshot_date") != grader_profile.get("snapshot_date"):
                errors.append("proof report grader_profile_snapshot_date does not match the grader profile")
    elif value.get("grader_profile_snapshot_date") is not None:
        errors.append("zero-tools proof report must not name an independent grader profile snapshot")
    if complete:
        errors.extend(validate_evidence_files(profile, grader_profile, evidence_root))
    if value.get("execution_policy") != cases.get("execution_policy"):
        errors.append("proof report execution_policy differs from the frozen runner pack")
    validate_execution_policy(value.get("execution_policy"), tier, "proof report.execution_policy", errors)
    claim = value.get("claim")
    contract = TIER_CONTRACTS.get(tier) if isinstance(tier, str) else None
    expected_claim = contract.get("claim") if contract is not None else None
    allowed_claims = {"not-proven"} | ({expected_claim} if isinstance(expected_claim, str) else set())
    if claim == "portable":
        errors.append("proof report claim 'portable' is ambiguous and requires schema-v2 tier migration")
    elif claim not in allowed_claims:
        errors.append(f"proof report claim must be one of {sorted(allowed_claims)}")
    if claim == expected_claim and not complete:
        errors.append(f"claim '{expected_claim}' requires --complete validation")
    evidence_namespace = value.get("evidence_namespace")
    if complete:
        if not isinstance(evidence_namespace, str) or not evidence_namespace.startswith(f"{tier}/"):
            errors.append(f"proof report evidence_namespace must start with '{tier}/'")
    elif evidence_namespace is not None and (
        not isinstance(evidence_namespace, str) or not evidence_namespace.startswith(f"{tier}/")
    ):
        errors.append(f"proof report evidence_namespace must be null or start with '{tier}/'")
    if complete and key is None:
        errors.append("complete validation requires the disclosed key")
    errors.extend(validate_manifest(manifest, cases, digest, complete=complete))
    validated_key = key
    if key is not None:
        key_errors = validate_key(key, cases, manifest)
        errors.extend(key_errors)
        if key_errors:
            validated_key = None

    validate_longevity(value.get("longevity"), value.get("profile_snapshot_date"), complete, errors)
    lanes = value.get("lanes")
    if not isinstance(lanes, list):
        return errors + ["proof report.lanes must be an array"]
    profile_by_id = {lane["lane_id"]: lane for lane in profile["lanes"]}
    actual_ids = [lane.get("lane_id") for lane in lanes if isinstance(lane, dict)]
    if set(actual_ids) != set(profile_by_id) or len(actual_ids) != len(profile_by_id):
        errors.append("proof report must contain every target profile lane exactly once")

    key_cases = validated_key.get("cases", []) if validated_key is not None else []
    semantic_checks = any(
        check.get("kind") == "semantic"
        for case in key_cases
        if isinstance(case, dict)
        for check in case.get("checks", [])
        if isinstance(check, dict)
    )
    portable = complete
    for index, lane in enumerate(lanes):
        location = f"proof report.lanes[{index}]"
        if not isinstance(lane, dict):
            errors.append(f"{location} must be an object")
            portable = False
            continue
        expected = profile_by_id.get(lane.get("lane_id"))
        if expected is None:
            portable = False
            continue
        for field in ("host", "model", "reasoning", "availability", "load_state_observation"):
            if lane.get(field) != expected.get(field):
                errors.append(f"{location}.{field} differs from the target profile")
        expected_availability = expected["availability"]
        if expected_availability == "unavailable":
            portable = False

        profile_record = lane.get("profile")
        if not isinstance(profile_record, dict):
            errors.append(f"{location}.profile must be an object")
            portable = False
        else:
            profile_status = profile_record.get("status")
            allowed_profile = {"verified", "unavailable"} | (set() if complete else {"pending"})
            if profile_status not in allowed_profile:
                errors.append(f"{location}.profile.status must be one of {sorted(allowed_profile)}")
            if complete and expected_availability == "unavailable" and profile_status != "unavailable":
                errors.append(
                    f"{location}.profile.status must be 'unavailable' because the target profile lane is unavailable"
                )
            if complete and not is_timestamp(profile_record.get("verified_at")):
                errors.append(f"{location}.profile.verified_at must be an ISO-8601 timestamp")
            validate_string_list(
                profile_record.get("evidence"), f"{location}.profile.evidence", nonempty=complete, errors=errors
            )
            portable = portable and expected_availability == "verified" and profile_status == "verified"

        isolation = lane.get("isolation")
        isolation_available = validate_lane_isolation(
            isolation, tier or "", expected, f"{location}.isolation", complete, errors
        )
        portable = portable and isolation_available
        expected_fixture_root_id = (
            isolation.get("fixture_root_id")
            if tier in {READ_ONLY_TOOLS, LOADED_CONTENT_SAFE} and isinstance(isolation, dict) and isolation_available
            else None
        )
        expected_fixture_sha256 = (
            isolation.get("pre_sha256")
            if tier in {READ_ONLY_TOOLS, LOADED_CONTENT_SAFE} and isinstance(isolation, dict) and isolation_available
            else None
        )

        load_state = expected["load_state_observation"]
        method = load_state["method"]
        trigger_blocks: list[dict[str, Any] | None] = []
        for field in ("discovery", "positive_trigger", "near_miss"):
            block = lane.get(field)
            validate_evidence_block(block, f"{location}.{field}", complete, errors, expected_method=method)
            trigger_blocks.append(block if isinstance(block, dict) else None)
            portable = portable and isinstance(block, dict) and block.get("status") == "pass"
        if load_state["reliability"] == "unreliable":
            for field, block in zip(("discovery", "positive_trigger", "near_miss"), trigger_blocks, strict=True):
                status = block.get("status") if isinstance(block, dict) else None
                if status in {"pass", "fail"}:
                    errors.append(
                        f"lane '{lane.get('lane_id')}' has unreliable load-state observation; {field}.status must be 'unavailable', got '{status}'"
                    )
            portable = False

        behavior = lane.get("behavior")
        case_results: Any = None
        if not isinstance(behavior, dict):
            errors.append(f"{location}.behavior must be an object")
            portable = False
        else:
            validate_evidence_block(behavior, f"{location}.behavior", complete, errors)
            case_results = behavior.get("case_results")
            validate_case_results(
                case_results,
                cases["cases"],
                manifest,
                validated_key,
                tier or "",
                expected,
                f"{location}.behavior.case_results",
                complete,
                errors,
                expected_fixture_root_id,
                expected_fixture_sha256,
            )
            portable = portable and behavior.get("status") == "pass"
            if isinstance(case_results, list):
                results_pass = all(
                    isinstance(result, dict)
                    and result.get("status") == "pass"
                    and isinstance(result.get("checks"), list)
                    and all(isinstance(check, dict) and check.get("status") == "pass" for check in result["checks"])
                    for result in case_results
                )
                if behavior.get("status") == "pass" and not results_pass:
                    errors.append(f"{location}.behavior.status cannot pass unless every frozen case and check passes")
                portable = portable and results_pass

        blind_available = validate_blind_grading(
            lane.get("blind_grading"),
            tier or "",
            lane,
            profile_by_id,
            grader_profile,
            semantic_checks,
            case_results,
            manifest,
            f"{location}.blind_grading",
            complete,
            errors,
        )
        portable = portable and blind_available
        validate_efficiency(lane.get("efficiency"), f"{location}.efficiency", complete, errors)
        validate_string_list(lane.get("caveats"), f"{location}.caveats", nonempty=False, errors=errors)

    if claim == expected_claim and (not portable or errors):
        errors.append(
            f"claim cannot be {expected_claim} unless every lane and frozen case passes all complete proof gates"
        )
    return errors


def print_errors(errors: list[str]) -> int:
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


def load_disclosed_key(
    key_path: Path | None,
    manifest_path: Path | None,
    cases: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, list[str]]:
    if key_path is None:
        return None, []
    if manifest_path is None or manifest is None:
        return None, ["--key requires --manifest"]
    resolved_key, errors = validate_key_path(key_path, manifest_path)
    if errors or resolved_key is None:
        return None, errors
    errors.extend(validate_key_digest(resolved_key, manifest))
    value, load_errors = load_json(resolved_key)
    errors.extend(load_errors)
    if load_errors or not isinstance(value, dict):
        return None, errors
    errors.extend(validate_key(value, cases, manifest))
    return value, errors


def validate_report_identity_alignment(
    report: Any,
    cases: dict[str, Any],
    profile: dict[str, Any],
    manifest: dict[str, Any],
    grader_profile: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(report, dict):
        return ["proof report must be a JSON object"]
    errors: list[str] = []
    tier = cases.get("tier")
    suite = cases.get("suite")
    for label, artifact in (
        ("proof report", report),
        ("target profile", profile),
        ("key manifest", manifest),
    ):
        if artifact.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{label} schema_version differs from the runner pack schema-v2 contract")
        if artifact.get("tier") != tier:
            errors.append(f"{label} tier differs from the runner pack")
        if artifact.get("suite") != suite:
            errors.append(f"{label} suite differs from the runner pack")
    if tier in INDEPENDENT_GRADER_TIERS:
        if grader_profile is None:
            errors.append(f"{tier} validation requires an independent grader profile")
        else:
            if grader_profile.get("schema_version") != SCHEMA_VERSION:
                errors.append("grader profile schema_version differs from the runner pack schema-v2 contract")
            if grader_profile.get("tier") != tier:
                errors.append(f"grader profile tier differs from the {tier} contract")
            if grader_profile.get("suite") != suite:
                errors.append("grader profile suite differs from the runner pack")
    elif grader_profile is not None:
        errors.append("zero-tools validation must not load an independent grader profile")
    return errors


def main() -> int:
    args = parse_args()
    cases_value, errors = load_json(args.cases)
    if errors:
        return print_errors(errors)
    case_errors = validate_cases(cases_value)
    if case_errors or not isinstance(cases_value, dict):
        return print_errors(case_errors)
    try:
        digest = case_pack_digest(args.cases)
    except OSError as exc:
        return print_errors([f"cannot hash case pack: {exc}"])

    manifest_path = getattr(args, "manifest", None)
    manifest_value: dict[str, Any] | None = None
    manifest_errors: list[str] = []
    if manifest_path is not None:
        loaded_manifest, manifest_errors = load_json(manifest_path)
        if not manifest_errors and isinstance(loaded_manifest, dict):
            manifest_value = loaded_manifest
            manifest_errors = validate_manifest(manifest_value, cases_value, digest, complete=False)
        elif not manifest_errors:
            manifest_errors = ["key manifest must be a JSON object"]

    if args.command == "validate-cases":
        key_value, key_errors = load_disclosed_key(
            getattr(args, "key", None), manifest_path, cases_value, manifest_value
        )
        all_errors = manifest_errors + key_errors
        if not all_errors:
            print("case pack is valid")
        return print_errors(all_errors)

    if manifest_value is None:
        return print_errors(manifest_errors or ["key manifest is required"])
    if manifest_errors:
        return print_errors(manifest_errors)

    profile_value, profile_errors = load_json(args.profile)
    if profile_errors:
        return print_errors(profile_errors)
    profile_errors = validate_profile(profile_value)
    if profile_errors or not isinstance(profile_value, dict):
        return print_errors(profile_errors)
    if profile_value.get("tier") != cases_value.get("tier"):
        return print_errors(["target profile tier differs from the runner pack"])
    if profile_value.get("suite") != cases_value.get("suite"):
        return print_errors(["target profile suite differs from the runner pack"])

    grader_profile_value: dict[str, Any] | None = None
    grader_profile_path = getattr(args, "grader_profile", None)
    if cases_value.get("tier") in INDEPENDENT_GRADER_TIERS:
        if grader_profile_path is None:
            return print_errors([f"{cases_value.get('tier')} reports require --grader-profile"])
        loaded_grader_profile, grader_profile_errors = load_json(grader_profile_path)
        if not grader_profile_errors and isinstance(loaded_grader_profile, dict):
            grader_profile_value = loaded_grader_profile
            grader_profile_errors = validate_grader_profile(
                grader_profile_value,
                cases_value.get("suite"),
                cases_value.get("tier"),
            )
        elif not grader_profile_errors:
            grader_profile_errors = ["grader profile must be a JSON object"]
        if grader_profile_errors:
            return print_errors(grader_profile_errors)
    elif grader_profile_path is not None:
        return print_errors(["zero-tools reports must not use --grader-profile"])

    if args.command == "init-report":
        report = initialized_report(cases_value, profile_value, manifest_value, digest, grader_profile_value)
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
                handle.write("\n")
        except FileExistsError:
            return print_errors([f"refusing to overwrite existing report: {args.output}"])
        except OSError as exc:
            return print_errors([f"cannot write proof report: {exc}"])
        print(f"created incomplete proof report: {args.output}")
        return 0

    report_value, report_errors = load_json(args.report)
    if report_errors:
        return print_errors(report_errors)
    alignment_errors = validate_report_identity_alignment(
        report_value,
        cases_value,
        profile_value,
        manifest_value,
        grader_profile_value,
    )
    if alignment_errors:
        return print_errors(alignment_errors)
    key_value, key_errors = load_disclosed_key(getattr(args, "key", None), manifest_path, cases_value, manifest_value)
    if key_errors:
        return print_errors(key_errors)
    report_errors = validate_report(
        report_value,
        cases_value,
        profile_value,
        manifest_value,
        digest,
        args.complete,
        key_value,
        grader_profile_value,
        getattr(args, "evidence_root", None),
    )
    if not report_errors:
        qualifier = "complete " if args.complete else ""
        print(f"{qualifier}proof report is valid")
    return print_errors(report_errors)


if __name__ == "__main__":
    raise SystemExit(main())
