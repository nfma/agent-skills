from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
HARNESSES = {"cursor", "antigravity", "codex", "claude-code"}
STATUSES = {"draft", "provisional", "production", "stale", "unavailable"}
RISKS = {"low", "medium", "high"}
SOURCES = {"first-party", "vendored"}
TASK_KINDS = {"positive", "near-miss"}
TASK_CLASSES = {"capability", "regression"}
GRADER_TYPES = {"deterministic", "blinded-model"}
GRADER_DIMENSIONS = {"trigger", "safety", "outcome"}
CALIBRATION_STATUSES = {"pending", "passed", "failed"}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PENDING_DIGEST = "PENDING-COORDINATOR-SEAL"
REQUIRED_PROHIBITED_EFFECTS = {
    "credential-access",
    "external-write",
    "live-network",
    "outside-root-write",
}


@dataclass(frozen=True)
class RegistrySummary:
    discovered: int
    registered: int
    statuses: Counter[str]
    suites_present: int


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def discover_skills(repository_root: Path) -> dict[str, Path]:
    skills_root = repository_root / "skills"
    discovered: dict[str, Path] = {}
    pending = [skills_root]
    visited: set[Path] = set()
    while pending:
        directory = pending.pop()
        physical_directory = directory.resolve()
        if physical_directory in visited:
            continue
        visited.add(physical_directory)
        for candidate in directory.iterdir():
            if candidate.name in {"node_modules", ".git"}:
                continue
            resolved = candidate.resolve()
            if not is_within(resolved, repository_root):
                raise ValueError(f"skill path escapes repository: {candidate}")
            if not resolved.is_dir():
                continue
            if (resolved / "SKILL.md").is_file():
                name = candidate.name
                previous = discovered.get(name)
                if previous is not None and previous.resolve() != resolved:
                    raise ValueError(f"duplicate discovered skill name: {name}")
                discovered[name] = candidate
                continue
            pending.append(candidate)
    return discovered


def non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()


def path_has_symlink(path: Path, boundary: Path) -> bool:
    current = path
    boundary = boundary.resolve()
    while True:
        if current.is_symlink():
            return True
        if current.resolve() == boundary:
            return False
        parent = current.parent
        if parent == current or not is_within(parent, boundary):
            return True
        current = parent


def validate_defaults(defaults: Any) -> list[str]:
    if not isinstance(defaults, dict):
        return ["registry.defaults must be an object"]
    errors: list[str] = []
    expected = {
        "owner",
        "canonical_harness",
        "required_harnesses",
        "minimum_tasks",
        "trials_per_harness",
        "human_calibration_required",
        "recheck_days",
    }
    unexpected = set(defaults) - expected
    missing = expected - set(defaults)
    if unexpected:
        errors.append(f"registry.defaults has unexpected fields: {sorted(unexpected)}")
    if missing:
        errors.append(f"registry.defaults is missing fields: {sorted(missing)}")
    if not non_empty_string(defaults.get("owner")):
        errors.append("registry.defaults.owner must be a non-empty string")
    if defaults.get("canonical_harness") not in HARNESSES:
        errors.append("registry.defaults.canonical_harness is invalid")
    required = defaults.get("required_harnesses")
    if not isinstance(required, list) or set(required) != HARNESSES or len(required) != len(HARNESSES):
        errors.append("registry.defaults.required_harnesses must list each supported harness exactly once")
    if not isinstance(defaults.get("minimum_tasks"), int) or defaults["minimum_tasks"] < 20:
        errors.append("registry.defaults.minimum_tasks must be at least 20")
    if not isinstance(defaults.get("trials_per_harness"), int) or defaults["trials_per_harness"] < 3:
        errors.append("registry.defaults.trials_per_harness must be at least 3")
    if defaults.get("human_calibration_required") is not True:
        errors.append("registry.defaults.human_calibration_required must be true")
    recheck_days = defaults.get("recheck_days")
    if not isinstance(recheck_days, int) or not 1 <= recheck_days <= 183:
        errors.append("registry.defaults.recheck_days must be between 1 and 183")
    return errors


def validate_harness_profile(profile: dict[str, Any], *, today: date | None = None) -> list[str]:
    errors: list[str] = []
    today = today or date.today()
    if set(profile) != {"schema_version", "snapshot_date", "recheck_date", "lanes"}:
        errors.append("harness profile fields must be schema_version, snapshot_date, recheck_date, and lanes")
    if profile.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"harness profile schema_version must be {SCHEMA_VERSION}")
    snapshot = parse_date(profile.get("snapshot_date"))
    recheck = parse_date(profile.get("recheck_date"))
    if snapshot is None:
        errors.append("harness profile snapshot_date must be an ISO date")
    if recheck is None:
        errors.append("harness profile recheck_date must be an ISO date")
    if snapshot is not None and recheck is not None and (recheck < snapshot or recheck > snapshot + timedelta(days=92)):
        errors.append("harness profile recheck_date must be within 92 days of the snapshot")
    lanes = profile.get("lanes")
    if not isinstance(lanes, list):
        return [*errors, "harness profile lanes must be a list"]
    seen: set[str] = set()
    dangerous_arguments = {
        "--add-dir",
        "--approve-mcps",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-skip-permissions",
        "--force",
        "--trust",
        "--yolo",
        "acceptEdits",
        "auto",
        "bypassPermissions",
        "danger-full-access",
        "disabled",
        "workspace-write",
    }
    lane_fields = {
        "harness",
        "model",
        "reasoning",
        "status",
        "runner",
        "arguments",
        "native_skill_root",
        "allowed_tool_classes",
        "allow_live_side_effects",
        "qualification_evidence_uri",
        "qualification_evidence_sha256",
        "reason",
    }
    for index, lane in enumerate(lanes):
        location = f"harness profile lanes[{index}]"
        if not isinstance(lane, dict):
            errors.append(f"{location} must be an object")
            continue
        if set(lane) != lane_fields:
            errors.append(f"{location} fields must be exactly {sorted(lane_fields)}")
        harness = lane.get("harness")
        if harness not in HARNESSES:
            errors.append(f"{location}.harness is invalid")
        elif harness in seen:
            errors.append(f"{location}.harness is duplicated")
        else:
            seen.add(harness)
        for field in ("model", "reasoning", "native_skill_root", "qualification_evidence_uri", "reason"):
            if not non_empty_string(lane.get(field)):
                errors.append(f"{location}.{field} must be a non-empty string")
        status = lane.get("status")
        if status not in {"verified", "unavailable"}:
            errors.append(f"{location}.status is invalid")
        runner = lane.get("runner")
        arguments = lane.get("arguments")
        if status == "verified" and not non_empty_string(runner):
            errors.append(f"{location}.runner must be set for a verified lane")
        if runner is not None and not non_empty_string(runner):
            errors.append(f"{location}.runner must be null or a non-empty string")
        if not isinstance(arguments, list) or not all(isinstance(value, str) for value in arguments):
            errors.append(f"{location}.arguments must contain strings")
        elif dangerous_arguments & set(arguments):
            errors.append(f"{location}.arguments contains a mutation or trust-bypass flag")
        allowed_tools = lane.get("allowed_tool_classes")
        if (
            not isinstance(allowed_tools, list)
            or not allowed_tools
            or not all(non_empty_string(value) for value in allowed_tools)
        ):
            errors.append(f"{location}.allowed_tool_classes must contain non-empty strings")
        if lane.get("allow_live_side_effects") is not False:
            errors.append(f"{location}.allow_live_side_effects must be false")
        digest = lane.get("qualification_evidence_sha256")
        if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
            errors.append(f"{location}.qualification_evidence_sha256 must be a SHA-256 digest")
        if status == "verified" and recheck is not None and recheck < today:
            errors.append(f"{location} qualification is stale")
    if seen != HARNESSES:
        errors.append("harness profile must contain each supported harness exactly once")
    return errors


def validate_suite(
    suite: dict[str, Any],
    *,
    expected_name: str,
    expected_status: str,
    minimum_tasks: int,
    trials_per_harness: int,
    suite_root: Path,
) -> list[str]:
    location = f"suite {expected_name}"
    errors: list[str] = []
    expected_fields = {
        "schema_version",
        "skill_name",
        "suite_version",
        "status",
        "owner",
        "snapshot_date",
        "drift_signals",
        "execution_policy",
        "thresholds",
        "graders",
        "tasks",
    }
    if set(suite) != expected_fields:
        errors.append(f"{location} fields must be exactly {sorted(expected_fields)}")
    if suite.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{location}.schema_version must be {SCHEMA_VERSION}")
    if suite.get("skill_name") != expected_name:
        errors.append(f"{location}.skill_name must be {expected_name}")
    if suite.get("status") != expected_status:
        errors.append(f"{location}.status must match registry status {expected_status}")
    if not isinstance(suite.get("suite_version"), int) or suite["suite_version"] < 1:
        errors.append(f"{location}.suite_version must be a positive integer")
    if not non_empty_string(suite.get("owner")):
        errors.append(f"{location}.owner must be a non-empty string")
    snapshot_date = parse_date(suite.get("snapshot_date"))
    if snapshot_date is None:
        errors.append(f"{location}.snapshot_date must be an ISO date")
    elif snapshot_date > date.today():
        errors.append(f"{location}.snapshot_date must not be in the future")
    drift_signals = suite.get("drift_signals")
    if (
        not isinstance(drift_signals, list)
        or len(drift_signals) < 2
        or not all(non_empty_string(signal) for signal in drift_signals)
    ):
        errors.append(f"{location}.drift_signals must contain at least two non-empty strings")

    policy = suite.get("execution_policy")
    if not isinstance(policy, dict):
        errors.append(f"{location}.execution_policy must be an object")
    else:
        policy_fields = {
            "baseline",
            "trials_per_harness",
            "live_side_effects",
            "complete_trace_required",
            "pre_post_hash_required",
        }
        if set(policy) != policy_fields:
            errors.append(f"{location}.execution_policy fields must be exactly {sorted(policy_fields)}")
        if policy.get("baseline") not in {"no-skill", "previous-skill"}:
            errors.append(f"{location}.execution_policy.baseline is invalid")
        if policy.get("trials_per_harness") != trials_per_harness:
            errors.append(f"{location}.execution_policy.trials_per_harness must equal {trials_per_harness}")
        for field in ("complete_trace_required", "pre_post_hash_required"):
            if policy.get(field) is not True:
                errors.append(f"{location}.execution_policy.{field} must be true")
        if policy.get("live_side_effects") is not False:
            errors.append(f"{location}.execution_policy.live_side_effects must be false")

    thresholds = suite.get("thresholds")
    threshold_fields = {
        "positive_trigger_recall",
        "near_miss_abstention",
        "paired_delta_ci_lower",
        "critical_regressions",
    }
    if not isinstance(thresholds, dict) or set(thresholds) != threshold_fields:
        errors.append(f"{location}.thresholds fields must be exactly {sorted(threshold_fields)}")
    else:
        positive_recall = thresholds.get("positive_trigger_recall")
        near_miss_abstention = thresholds.get("near_miss_abstention")
        lower_bound = thresholds.get("paired_delta_ci_lower")
        if not isinstance(positive_recall, (int, float)) or not 0.9 <= positive_recall <= 1:
            errors.append(f"{location}.thresholds.positive_trigger_recall must be between 0.9 and 1")
        if not isinstance(near_miss_abstention, (int, float)) or not 0.95 <= near_miss_abstention <= 1:
            errors.append(f"{location}.thresholds.near_miss_abstention must be between 0.95 and 1")
        if not isinstance(lower_bound, (int, float)) or lower_bound < 0:
            errors.append(f"{location}.thresholds.paired_delta_ci_lower must be non-negative")
        if thresholds.get("critical_regressions") != 0:
            errors.append(f"{location}.thresholds.critical_regressions must be zero")

    graders = suite.get("graders")
    grader_definitions: dict[str, dict[str, Any]] = {}
    if not isinstance(graders, list) or not graders:
        errors.append(f"{location}.graders must be a non-empty list")
    else:
        for index, grader in enumerate(graders):
            grader_location = f"{location}.graders[{index}]"
            if not isinstance(grader, dict):
                errors.append(f"{grader_location} must be an object")
                continue
            grader_fields = {"id", "type", "dimension", "implementation"}
            if set(grader) != grader_fields:
                errors.append(f"{grader_location} fields must be exactly {sorted(grader_fields)}")
            grader_id = grader.get("id")
            if not isinstance(grader_id, str) or not NAME_PATTERN.fullmatch(grader_id):
                errors.append(f"{grader_location}.id is invalid")
                continue
            if grader_id in grader_definitions:
                errors.append(f"{grader_location}.id is duplicated")
                continue
            grader_definitions[grader_id] = grader
            if grader.get("type") not in GRADER_TYPES:
                errors.append(f"{grader_location}.type is invalid")
            if grader.get("dimension") not in GRADER_DIMENSIONS:
                errors.append(f"{grader_location}.dimension is invalid")
            if not non_empty_string(grader.get("implementation")):
                errors.append(f"{grader_location}.implementation must be a non-empty string")
        present_dimensions = {grader.get("dimension") for grader in grader_definitions.values()}
        missing_dimensions = GRADER_DIMENSIONS - present_dimensions
        if missing_dimensions:
            errors.append(f"{location}.graders is missing dimensions: {sorted(missing_dimensions)}")

    tasks = suite.get("tasks")
    if not isinstance(tasks, list):
        return [*errors, f"{location}.tasks must be a list"]
    if len(tasks) < minimum_tasks:
        errors.append(f"{location}.tasks must contain at least {minimum_tasks} tasks")
    seen: set[str] = set()
    counts: Counter[str] = Counter()
    class_counts: Counter[tuple[str, str]] = Counter()
    for index, task in enumerate(tasks):
        task_location = f"{location}.tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{task_location} must be an object")
            continue
        task_fields = {"id", "kind", "class", "prompt", "fixture_root", "graders", "prohibited_effects"}
        if set(task) != task_fields:
            errors.append(f"{task_location} fields must be exactly {sorted(task_fields)}")
        task_id = task.get("id")
        if not isinstance(task_id, str) or not NAME_PATTERN.fullmatch(task_id):
            errors.append(f"{task_location}.id is invalid")
        elif task_id in seen:
            errors.append(f"{task_location}.id is duplicated")
        else:
            seen.add(task_id)
        kind = task.get("kind")
        if kind not in TASK_KINDS:
            errors.append(f"{task_location}.kind is invalid")
        else:
            counts[kind] += 1
        if task.get("class") not in TASK_CLASSES:
            errors.append(f"{task_location}.class is invalid")
        elif kind in TASK_KINDS:
            class_counts[(kind, task["class"])] += 1
        prompt = task.get("prompt")
        if not non_empty_string(prompt) or len(prompt.strip()) < 20:
            errors.append(f"{task_location}.prompt must contain at least 20 characters")
        elif expected_name.casefold() in prompt.casefold():
            errors.append(f"{task_location}.prompt must not name the skill")
        fixture_root = task.get("fixture_root")
        if fixture_root is not None:
            if not non_empty_string(fixture_root):
                errors.append(f"{task_location}.fixture_root must be null or a non-empty string")
            else:
                fixture_path = (suite_root / fixture_root).resolve()
                if not is_within(fixture_path, suite_root) or not fixture_path.is_dir():
                    errors.append(f"{task_location}.fixture_root must resolve to a directory inside the suite")
                elif path_has_symlink(suite_root / fixture_root, suite_root) or any(
                    path.is_symlink() for path in fixture_path.rglob("*")
                ):
                    errors.append(f"{task_location}.fixture_root must be symlink-free")
        task_graders = task.get("graders")
        if not isinstance(task_graders, list) or not task_graders:
            errors.append(f"{task_location}.graders must be a list and positives need at least one grader")
        elif not all(non_empty_string(grader) for grader in task_graders):
            errors.append(f"{task_location}.graders contains an invalid grader id")
        else:
            unknown_graders = sorted(set(task_graders) - set(grader_definitions))
            if unknown_graders:
                errors.append(f"{task_location}.graders references unknown ids: {unknown_graders}")
            dimensions = {
                grader_definitions[grader]["dimension"] for grader in task_graders if grader in grader_definitions
            }
            required_dimensions = {"trigger", "safety"}
            if kind == "positive":
                required_dimensions.add("outcome")
            missing_dimensions = required_dimensions - dimensions
            if missing_dimensions:
                errors.append(f"{task_location}.graders is missing dimensions: {sorted(missing_dimensions)}")
        effects = task.get("prohibited_effects")
        if not isinstance(effects, list) or not effects or not all(non_empty_string(effect) for effect in effects):
            errors.append(f"{task_location}.prohibited_effects must contain non-empty strings")
        elif not REQUIRED_PROHIBITED_EFFECTS.issubset(effects):
            errors.append(f"{task_location}.prohibited_effects must include {sorted(REQUIRED_PROHIBITED_EFFECTS)}")

    minimum_per_kind = minimum_tasks // 2
    for kind in TASK_KINDS:
        if counts[kind] < minimum_per_kind:
            errors.append(f"{location} needs at least {minimum_per_kind} {kind} tasks")
        if class_counts[(kind, "regression")] < 2:
            errors.append(f"{location} needs at least two {kind} regression tasks")
    return errors


def validate_key_manifest(
    manifest: dict[str, Any],
    *,
    suite: dict[str, Any],
    expected_name: str,
    require_sealed: bool,
) -> list[str]:
    location = f"key manifest {expected_name}"
    errors: list[str] = []
    expected_fields = {
        "schema_version",
        "skill_name",
        "suite_canonical_sha256",
        "key_sha256",
        "cases",
    }
    if set(manifest) != expected_fields:
        errors.append(f"{location} fields must be exactly {sorted(expected_fields)}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{location}.schema_version must be {SCHEMA_VERSION}")
    if manifest.get("skill_name") != expected_name:
        errors.append(f"{location}.skill_name must be {expected_name}")
    expected_suite_digest = canonical_digest(suite)
    if manifest.get("suite_canonical_sha256") != expected_suite_digest:
        errors.append(f"{location}.suite_canonical_sha256 does not match suite.json")
    key_digest = manifest.get("key_sha256")
    if key_digest != PENDING_DIGEST and (not isinstance(key_digest, str) or not DIGEST_PATTERN.fullmatch(key_digest)):
        errors.append(f"{location}.key_sha256 must be a SHA-256 digest or {PENDING_DIGEST}")
    if require_sealed and key_digest == PENDING_DIGEST:
        errors.append(f"{location} is unsealed")

    cases = manifest.get("cases")
    expected_ids = {
        task["id"]
        for task in suite.get("tasks", [])
        if isinstance(task, dict) and task.get("kind") == "positive" and isinstance(task.get("id"), str)
    }
    seen: set[str] = set()
    if not isinstance(cases, list):
        errors.append(f"{location}.cases must be a list")
    else:
        for index, case in enumerate(cases):
            case_location = f"{location}.cases[{index}]"
            if not isinstance(case, dict) or set(case) != {"task_id", "criterion_count"}:
                errors.append(f"{case_location} fields must be task_id and criterion_count")
                continue
            task_id = case.get("task_id")
            if not isinstance(task_id, str) or task_id not in expected_ids:
                errors.append(f"{case_location}.task_id is not a positive suite task")
            elif task_id in seen:
                errors.append(f"{case_location}.task_id is duplicated")
            else:
                seen.add(task_id)
            criterion_count = case.get("criterion_count")
            if not isinstance(criterion_count, int) or criterion_count < 1:
                errors.append(f"{case_location}.criterion_count must be a positive integer")
        if seen != expected_ids:
            errors.append(f"{location}.cases must cover every positive task exactly once")
    return errors


def validate_calibration_manifest(
    manifest: dict[str, Any],
    *,
    expected_name: str,
    key_sha256: Any,
    require_passed: bool,
    recheck_days: int,
    expected_clean_count: int,
    expected_seeded_count: int,
    today: date | None = None,
) -> list[str]:
    location = f"calibration manifest {expected_name}"
    errors: list[str] = []
    today = today or date.today()
    expected_fields = {
        "schema_version",
        "skill_name",
        "status",
        "reviewer",
        "reviewed_at",
        "expires_at",
        "sample_size",
        "clean_case_count",
        "seeded_case_count",
        "binary_agreement",
        "ordinal_weighted_agreement",
        "critical_failure_recall",
        "noncritical_failure_agreement",
        "clean_acceptance_rate",
        "critical_disagreements",
        "key_sha256",
        "calibration_set_sha256",
        "calibration_report_sha256",
    }
    if set(manifest) != expected_fields:
        errors.append(f"{location} fields must be exactly {sorted(expected_fields)}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{location}.schema_version must be {SCHEMA_VERSION}")
    if manifest.get("skill_name") != expected_name:
        errors.append(f"{location}.skill_name must be {expected_name}")
    status = manifest.get("status")
    if status not in CALIBRATION_STATUSES:
        errors.append(f"{location}.status is invalid")
    if manifest.get("key_sha256") != key_sha256:
        errors.append(f"{location}.key_sha256 must match the key manifest")

    if status == "pending":
        pending_values = {
            "reviewer": None,
            "reviewed_at": None,
            "expires_at": None,
            "sample_size": 0,
            "clean_case_count": 0,
            "seeded_case_count": 0,
            "binary_agreement": None,
            "ordinal_weighted_agreement": None,
            "critical_failure_recall": None,
            "noncritical_failure_agreement": None,
            "clean_acceptance_rate": None,
            "critical_disagreements": None,
            "calibration_set_sha256": PENDING_DIGEST,
            "calibration_report_sha256": PENDING_DIGEST,
        }
        for field, expected in pending_values.items():
            if manifest.get(field) != expected:
                errors.append(f"{location}.{field} must be {expected!r} while pending")
        if require_passed:
            errors.append(f"{location} has not passed human calibration")
        return errors

    if not non_empty_string(manifest.get("reviewer")):
        errors.append(f"{location}.reviewer must be a non-empty string")
    reviewed_at = parse_date(manifest.get("reviewed_at"))
    expires_at = parse_date(manifest.get("expires_at"))
    if reviewed_at is None:
        errors.append(f"{location}.reviewed_at must be an ISO date")
    if expires_at is None:
        errors.append(f"{location}.expires_at must be an ISO date")
    if reviewed_at is not None and expires_at is not None:
        if expires_at < reviewed_at or expires_at > reviewed_at + timedelta(days=recheck_days):
            errors.append(f"{location}.expires_at must be within {recheck_days} days of review")
        if require_passed and expires_at < today:
            errors.append(f"{location} is stale")
    for field in ("calibration_set_sha256", "calibration_report_sha256"):
        digest = manifest.get(field)
        if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
            errors.append(f"{location}.{field} must be a SHA-256 digest after calibration")
    clean_case_count = manifest.get("clean_case_count")
    seeded_case_count = manifest.get("seeded_case_count")
    if clean_case_count != expected_clean_count:
        errors.append(f"{location}.clean_case_count must be {expected_clean_count}")
    if seeded_case_count != expected_seeded_count:
        errors.append(f"{location}.seeded_case_count must be {expected_seeded_count}")
    sample_size = manifest.get("sample_size")
    expected_sample_size = expected_clean_count + expected_seeded_count
    if sample_size != expected_sample_size:
        errors.append(f"{location}.sample_size must be {expected_sample_size}")
    binary_agreement = manifest.get("binary_agreement")
    if not isinstance(binary_agreement, (int, float)) or not 0 <= binary_agreement <= 1:
        errors.append(f"{location}.binary_agreement must be between 0 and 1")
    ordinal_agreement = manifest.get("ordinal_weighted_agreement")
    if not isinstance(ordinal_agreement, (int, float)) or not 0 <= ordinal_agreement <= 1:
        errors.append(f"{location}.ordinal_weighted_agreement must be between 0 and 1")
    critical_disagreements = manifest.get("critical_disagreements")
    if not isinstance(critical_disagreements, int) or critical_disagreements < 0:
        errors.append(f"{location}.critical_disagreements must be a non-negative integer")
    critical_recall = manifest.get("critical_failure_recall")
    if not isinstance(critical_recall, (int, float)) or not 0 <= critical_recall <= 1:
        errors.append(f"{location}.critical_failure_recall must be between 0 and 1")
    noncritical_agreement = manifest.get("noncritical_failure_agreement")
    if not isinstance(noncritical_agreement, (int, float)) or not 0 <= noncritical_agreement <= 1:
        errors.append(f"{location}.noncritical_failure_agreement must be between 0 and 1")
    clean_acceptance = manifest.get("clean_acceptance_rate")
    if not isinstance(clean_acceptance, (int, float)) or not 0 <= clean_acceptance <= 1:
        errors.append(f"{location}.clean_acceptance_rate must be between 0 and 1")
    if status == "passed":
        if isinstance(ordinal_agreement, (int, float)) and ordinal_agreement < 0.8:
            errors.append(f"{location}.ordinal_weighted_agreement must be at least 0.8 to pass")
        if critical_recall != 1:
            errors.append(f"{location}.critical_failure_recall must be 1 to pass")
        if isinstance(noncritical_agreement, (int, float)) and noncritical_agreement < 0.9:
            errors.append(f"{location}.noncritical_failure_agreement must be at least 0.9 to pass")
        if isinstance(clean_acceptance, (int, float)) and clean_acceptance < 0.9:
            errors.append(f"{location}.clean_acceptance_rate must be at least 0.9 to pass")
        if critical_disagreements != 0:
            errors.append(f"{location}.critical_disagreements must be zero to pass")
    if require_passed and status != "passed":
        errors.append(f"{location} has not passed human calibration")
    return errors


def validate_evidence_manifest(
    manifest: dict[str, Any],
    *,
    suite: dict[str, Any],
    harness_profile: dict[str, Any],
    expected_name: str,
    require_passed: bool,
    required_harnesses: set[str],
    today: date | None = None,
) -> list[str]:
    location = f"evidence manifest {expected_name}"
    errors: list[str] = []
    today = today or date.today()
    fields = {
        "schema_version",
        "skill_name",
        "status",
        "suite_canonical_sha256",
        "harness_profile_canonical_sha256",
        "aggregate_report_sha256",
        "evaluated_at",
        "expires_at",
        "harnesses",
    }
    if set(manifest) != fields:
        errors.append(f"{location} fields must be exactly {sorted(fields)}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{location}.schema_version must be {SCHEMA_VERSION}")
    if manifest.get("skill_name") != expected_name:
        errors.append(f"{location}.skill_name must be {expected_name}")
    if manifest.get("suite_canonical_sha256") != canonical_digest(suite):
        errors.append(f"{location}.suite_canonical_sha256 does not match suite.json")
    if manifest.get("harness_profile_canonical_sha256") != canonical_digest(harness_profile):
        errors.append(f"{location}.harness_profile_canonical_sha256 does not match harnesses.json")
    status = manifest.get("status")
    if status not in {"pending", "passed", "failed", "not-proven", "unavailable"}:
        errors.append(f"{location}.status is invalid")
    aggregate_digest = manifest.get("aggregate_report_sha256")
    if aggregate_digest != PENDING_DIGEST and (
        not isinstance(aggregate_digest, str) or not DIGEST_PATTERN.fullmatch(aggregate_digest)
    ):
        errors.append(f"{location}.aggregate_report_sha256 must be a SHA-256 digest or {PENDING_DIGEST}")
    evaluated_at = parse_date(manifest.get("evaluated_at"))
    expires_at = parse_date(manifest.get("expires_at"))
    if status == "pending":
        if aggregate_digest != PENDING_DIGEST:
            errors.append(f"{location}.aggregate_report_sha256 must be pending")
        if manifest.get("evaluated_at") is not None or manifest.get("expires_at") is not None:
            errors.append(f"{location} dates must be null while pending")
    else:
        if aggregate_digest == PENDING_DIGEST:
            errors.append(f"{location} must bind an aggregate report")
        if evaluated_at is None or expires_at is None:
            errors.append(f"{location} evaluated_at and expires_at must be ISO dates")
        elif expires_at < evaluated_at or expires_at > evaluated_at + timedelta(days=92):
            errors.append(f"{location}.expires_at must be within 92 days of evaluation")
        elif require_passed and expires_at < today:
            errors.append(f"{location} is stale")

    lanes = manifest.get("harnesses")
    seen: set[str] = set()
    lane_statuses: list[str] = []
    if not isinstance(lanes, list):
        errors.append(f"{location}.harnesses must be a list")
    else:
        for index, lane in enumerate(lanes):
            lane_location = f"{location}.harnesses[{index}]"
            if not isinstance(lane, dict) or set(lane) != {
                "harness",
                "status",
                "evidence_sha256",
                "reason",
            }:
                errors.append(f"{lane_location} fields must be harness, status, evidence_sha256, and reason")
                continue
            harness = lane.get("harness")
            if harness not in required_harnesses:
                errors.append(f"{lane_location}.harness is invalid")
            elif harness in seen:
                errors.append(f"{lane_location}.harness is duplicated")
            else:
                seen.add(harness)
            lane_status = lane.get("status")
            if lane_status not in {"pending", "passed", "failed", "unavailable"}:
                errors.append(f"{lane_location}.status is invalid")
            else:
                lane_statuses.append(lane_status)
            lane_digest = lane.get("evidence_sha256")
            if lane_status == "pending":
                if lane_digest != PENDING_DIGEST or lane.get("reason") is not None:
                    errors.append(f"{lane_location} must remain empty while pending")
            else:
                if not isinstance(lane_digest, str) or not DIGEST_PATTERN.fullmatch(lane_digest):
                    errors.append(f"{lane_location}.evidence_sha256 must be a SHA-256 digest")
                if lane_status in {"failed", "unavailable"} and not non_empty_string(lane.get("reason")):
                    errors.append(f"{lane_location}.reason must explain {lane_status}")
                if lane_status == "passed" and lane.get("reason") is not None:
                    errors.append(f"{lane_location}.reason must be null when passed")
        if seen != required_harnesses:
            errors.append(f"{location}.harnesses must cover every required harness exactly once")
    derived_status = "pending"
    lane_status_set = set(lane_statuses)
    if lane_statuses and "pending" not in lane_status_set:
        if lane_status_set == {"passed"}:
            derived_status = "passed"
        elif lane_status_set == {"unavailable"}:
            derived_status = "unavailable"
        elif "failed" in lane_status_set:
            derived_status = "failed"
        else:
            derived_status = "not-proven"
    if status != derived_status:
        errors.append(f"{location}.status must be {derived_status} from its harness outcomes")
    if require_passed and status != "passed":
        errors.append(f"{location} has not passed every required harness")
    return errors


def validate_registry(
    repository_root: Path, registry_path: Path, *, require_production: bool = False
) -> tuple[list[str], RegistrySummary]:
    registry = read_object(registry_path)
    discovered = discover_skills(repository_root)
    errors: list[str] = []
    harness_profile: dict[str, Any] | None = None
    harness_profile_path = repository_root / "evals" / "harnesses.json"
    try:
        harness_profile = read_object(harness_profile_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"could not read evals/harnesses.json: {error}")
    else:
        errors.extend(validate_harness_profile(harness_profile))
    if set(registry) != {"schema_version", "defaults", "skills"}:
        errors.append("registry fields must be exactly schema_version, defaults, and skills")
    if registry.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"registry.schema_version must be {SCHEMA_VERSION}")
    defaults = registry.get("defaults")
    errors.extend(validate_defaults(defaults))
    if not isinstance(defaults, dict):
        defaults = {}
    entries = registry.get("skills")
    if not isinstance(entries, list):
        summary = RegistrySummary(len(discovered), 0, Counter(), 0)
        return [*errors, "registry.skills must be a list"], summary

    registered: dict[str, dict[str, Any]] = {}
    statuses: Counter[str] = Counter()
    suites_present = 0
    for index, raw_entry in enumerate(entries):
        location = f"registry.skills[{index}]"
        if not isinstance(raw_entry, dict):
            errors.append(f"{location} must be an object")
            continue
        expected_fields = {"name", "source", "risk", "status", "suite"}
        if set(raw_entry) != expected_fields:
            errors.append(f"{location} fields must be exactly {sorted(expected_fields)}")
        name = raw_entry.get("name")
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
            errors.append(f"{location}.name is invalid")
            continue
        if name in registered:
            errors.append(f"duplicate registry skill: {name}")
            continue
        registered[name] = raw_entry
        source = raw_entry.get("source")
        risk = raw_entry.get("risk")
        status = raw_entry.get("status")
        suite_relative = raw_entry.get("suite")
        if source not in SOURCES:
            errors.append(f"{location}.source is invalid")
        if risk not in RISKS:
            errors.append(f"{location}.risk is invalid")
        if status not in STATUSES:
            errors.append(f"{location}.status is invalid")
        else:
            statuses[status] += 1
        expected_suite = f"evals/{name}/suite.json"
        if suite_relative != expected_suite:
            errors.append(f"{location}.suite must be {expected_suite}")
            continue
        suite_path = (repository_root / expected_suite).resolve()
        if not is_within(suite_path, repository_root / "evals"):
            errors.append(f"{location}.suite escapes evals root")
            continue
        if suite_path.is_file():
            suites_present += 1
            suite: dict[str, Any] | None = None
            try:
                suite = read_object(suite_path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                errors.append(f"could not read {expected_suite}: {error}")
            else:
                errors.extend(
                    validate_suite(
                        suite,
                        expected_name=name,
                        expected_status=str(status),
                        minimum_tasks=int(defaults.get("minimum_tasks", 20)),
                        trials_per_harness=int(defaults.get("trials_per_harness", 3)),
                        suite_root=suite_path.parent,
                    )
                )
            key_manifest_path = suite_path.parent / "key-manifest.json"
            calibration_manifest_path = suite_path.parent / "calibration-manifest.json"
            evidence_manifest_path = suite_path.parent / "evidence-manifest.json"
            key_manifest: dict[str, Any] | None = None
            if not key_manifest_path.is_file():
                errors.append(f"{key_manifest_path.relative_to(repository_root)} is required")
            elif suite is not None:
                try:
                    key_manifest = read_object(key_manifest_path)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    errors.append(f"could not read {key_manifest_path.relative_to(repository_root)}: {error}")
                else:
                    errors.extend(
                        validate_key_manifest(
                            key_manifest,
                            suite=suite,
                            expected_name=name,
                            require_sealed=status in {"provisional", "production", "stale"},
                        )
                    )
            if not calibration_manifest_path.is_file():
                errors.append(f"{calibration_manifest_path.relative_to(repository_root)} is required")
            elif key_manifest is not None:
                try:
                    calibration_manifest = read_object(calibration_manifest_path)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    errors.append(f"could not read {calibration_manifest_path.relative_to(repository_root)}: {error}")
                else:
                    errors.extend(
                        validate_calibration_manifest(
                            calibration_manifest,
                            expected_name=name,
                            key_sha256=key_manifest.get("key_sha256"),
                            require_passed=status == "production",
                            recheck_days=int(defaults.get("recheck_days", 183)),
                            expected_clean_count=len(key_manifest.get("cases", [])),
                            expected_seeded_count=sum(
                                case.get("criterion_count", 0)
                                for case in key_manifest.get("cases", [])
                                if isinstance(case, dict) and isinstance(case.get("criterion_count"), int)
                            ),
                        )
                    )
            if not evidence_manifest_path.is_file():
                errors.append(f"{evidence_manifest_path.relative_to(repository_root)} is required")
            elif suite is not None and harness_profile is not None:
                try:
                    evidence_manifest = read_object(evidence_manifest_path)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    errors.append(f"could not read {evidence_manifest_path.relative_to(repository_root)}: {error}")
                else:
                    errors.extend(
                        validate_evidence_manifest(
                            evidence_manifest,
                            suite=suite,
                            harness_profile=harness_profile,
                            expected_name=name,
                            require_passed=status == "production",
                            required_harnesses=set(defaults.get("required_harnesses", HARNESSES)),
                        )
                    )
        else:
            errors.append(f"{expected_suite} is required for status {status}")
        if require_production and status != "production":
            errors.append(f"{name} is {status}, not production")

    missing = sorted(set(discovered) - set(registered))
    stale = sorted(set(registered) - set(discovered))
    if missing:
        errors.append(f"discovered skills missing from registry: {missing}")
    if stale:
        errors.append(f"registry skills not discovered: {stale}")
    summary = RegistrySummary(len(discovered), len(registered), statuses, suites_present)
    return errors, summary
