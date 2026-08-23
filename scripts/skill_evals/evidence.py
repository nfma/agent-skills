from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from .validation import DIGEST_PATTERN, canonical_digest, is_within, non_empty_string

TRIAL_STATUSES = {"completed", "unavailable"}


def resolved_evidence_file(relative: Any, evidence_root: Path, location: str) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        return None, [f"{location} must be a non-empty relative path"]
    candidate = evidence_root / relative
    resolved = candidate.resolve()
    if not is_within(resolved, evidence_root) or not resolved.is_file():
        return None, [f"{location} must resolve to a file inside the evidence root"]
    current = candidate
    while current != evidence_root:
        if current.is_symlink():
            errors.append(f"{location} must not contain symlinks")
            break
        parent = current.parent
        if parent == current:
            errors.append(f"{location} has an invalid parent chain")
            break
        current = parent
    return resolved, errors


def validate_artifact(
    relative: Any,
    digest: Any,
    *,
    evidence_root: Path,
    location: str,
) -> list[str]:
    path, errors = resolved_evidence_file(relative, evidence_root, location)
    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        errors.append(f"{location}_sha256 must be a SHA-256 digest")
    elif path is not None and sha256(path.read_bytes()).hexdigest() != digest:
        errors.append(f"{location}_sha256 does not match the artifact")
    return errors


def validate_run_manifest(
    plan: dict[str, Any],
    manifest: dict[str, Any],
    *,
    evidence_root: Path,
) -> list[str]:
    errors: list[str] = []
    expected_fields = {"schema_version", "run_id", "generated_at", "plan_sha256", "trials"}
    if set(manifest) != expected_fields:
        errors.append(f"run manifest fields must be exactly {sorted(expected_fields)}")
    if manifest.get("schema_version") != 1:
        errors.append("run manifest schema_version must be 1")
    if not non_empty_string(manifest.get("run_id")):
        errors.append("run manifest run_id must be a non-empty string")
    if not non_empty_string(manifest.get("generated_at")):
        errors.append("run manifest generated_at must be a non-empty string")
    if manifest.get("plan_sha256") != canonical_digest(plan):
        errors.append("run manifest plan_sha256 does not match the plan")

    plan_trials = {
        trial["trial_id"]: trial
        for trial in plan.get("trials", [])
        if isinstance(trial, dict) and isinstance(trial.get("trial_id"), str)
    }
    records = manifest.get("trials")
    if not isinstance(records, list):
        return [*errors, "run manifest trials must be a list"]
    seen: set[str] = set()
    group_statuses: dict[tuple[str, str], set[str]] = defaultdict(set)
    record_fields = {
        "trial_id",
        "status",
        "reason",
        "model",
        "harness_version",
        "trace_path",
        "trace_sha256",
        "response_path",
        "response_sha256",
        "complete_trace",
        "skill_discovered",
        "skill_loaded",
        "before_state_sha256",
        "after_state_sha256",
        "successful_effects",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    }
    for index, record in enumerate(records):
        location = f"run manifest trials[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{location} must be an object")
            continue
        if set(record) != record_fields:
            errors.append(f"{location} fields must be exactly {sorted(record_fields)}")
        trial_id = record.get("trial_id")
        if not isinstance(trial_id, str) or trial_id not in plan_trials:
            errors.append(f"{location}.trial_id is not in the plan")
            continue
        if trial_id in seen:
            errors.append(f"{location}.trial_id is duplicated")
            continue
        seen.add(trial_id)
        plan_trial = plan_trials[trial_id]
        status = record.get("status")
        if status not in TRIAL_STATUSES:
            errors.append(f"{location}.status is invalid")
            continue
        group_statuses[(plan_trial["skill_name"], plan_trial["harness"])].add(status)
        if status == "unavailable":
            if not non_empty_string(record.get("reason")):
                errors.append(f"{location}.reason must explain unavailability")
            unavailable_nulls = record_fields - {"trial_id", "status", "reason"}
            for field in unavailable_nulls:
                if record.get(field) is not None:
                    errors.append(f"{location}.{field} must be null while unavailable")
            continue

        if record.get("reason") is not None:
            errors.append(f"{location}.reason must be null for a completed trial")
        for field in ("model", "harness_version"):
            if not non_empty_string(record.get(field)):
                errors.append(f"{location}.{field} must be a non-empty string")
        errors.extend(
            validate_artifact(
                record.get("trace_path"),
                record.get("trace_sha256"),
                evidence_root=evidence_root,
                location=f"{location}.trace_path",
            )
        )
        errors.extend(
            validate_artifact(
                record.get("response_path"),
                record.get("response_sha256"),
                evidence_root=evidence_root,
                location=f"{location}.response_path",
            )
        )
        if record.get("complete_trace") is not True:
            errors.append(f"{location}.complete_trace must be true")
        for field in ("skill_discovered", "skill_loaded"):
            if not isinstance(record.get(field), bool):
                errors.append(f"{location}.{field} must be boolean")
        before_digest = record.get("before_state_sha256")
        after_digest = record.get("after_state_sha256")
        for field, digest in (
            ("before_state_sha256", before_digest),
            ("after_state_sha256", after_digest),
        ):
            if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
                errors.append(f"{location}.{field} must be a SHA-256 digest")
        if before_digest != after_digest:
            errors.append(f"{location} changed the task state")
        effects = record.get("successful_effects")
        if not isinstance(effects, list):
            errors.append(f"{location}.successful_effects must be a list")
        elif effects:
            errors.append(f"{location} contains successful prohibited effects: {effects}")
        for field in ("latency_ms", "input_tokens", "output_tokens"):
            value = record.get(field)
            if not isinstance(value, int) or value < 0:
                errors.append(f"{location}.{field} must be a non-negative integer")
        cost = record.get("cost_usd")
        if cost is not None and (not isinstance(cost, (int, float)) or cost < 0):
            errors.append(f"{location}.cost_usd must be null or non-negative")

    missing = sorted(set(plan_trials) - seen)
    if missing:
        errors.append(f"run manifest is missing planned trials: {missing}")
    for group, statuses in sorted(group_statuses.items()):
        if len(statuses) > 1:
            errors.append(f"run manifest mixes completed and unavailable trials for {group}")
    return errors
