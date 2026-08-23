from __future__ import annotations

import re
from typing import Any

from .validation import canonical_digest, non_empty_string

GRADE_OUTCOMES = {"pass", "fail", "unknown"}
GRADING_STATUSES = {"performed", "not-required", "unavailable"}
RUBRIC_WEIGHTS = {1, 2, 3, 4}


def canonical_model_identity(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    without_namespace = value.casefold().strip().rsplit("/", maxsplit=1)[-1]
    without_modifiers = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", without_namespace)
    canonical = re.sub(r"[^a-z0-9]+", "-", without_modifiers).strip("-")
    while re.search(r"-(?:(?:19|20)\d{6}|\d+[km])$", canonical):
        canonical = re.sub(r"-(?:(?:19|20)\d{6}|\d+[km])$", "", canonical)
    return canonical or None


def validate_external_key(
    key: dict[str, Any],
    *,
    suite: dict[str, Any],
    key_manifest: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    fields = {
        "schema_version",
        "skill_name",
        "suite_canonical_sha256",
        "rubric_uniformity_justification",
        "cases",
    }
    if set(key) != fields:
        errors.append(f"external key fields must be exactly {sorted(fields)}")
    if key.get("schema_version") != 1:
        errors.append("external key schema_version must be 1")
    if key.get("skill_name") != suite.get("skill_name"):
        errors.append("external key skill_name must match the suite")
    if key.get("suite_canonical_sha256") != canonical_digest(suite):
        errors.append("external key suite_canonical_sha256 does not match the suite")
    uniformity_justification = key.get("rubric_uniformity_justification")
    if uniformity_justification is not None and not non_empty_string(uniformity_justification):
        errors.append("external key rubric_uniformity_justification must be null or a non-empty string")
    manifest_counts = {
        case.get("task_id"): case.get("criterion_count")
        for case in key_manifest.get("cases", [])
        if isinstance(case, dict)
    }
    expected_ids = {
        task["id"] for task in suite.get("tasks", []) if isinstance(task, dict) and task.get("kind") == "positive"
    }
    cases = key.get("cases")
    seen: set[str] = set()
    criterion_text_locations: dict[str, str] = {}
    rubric_signatures: list[tuple[tuple[int, bool], ...]] = []
    rubric_weights: list[int] = []
    if not isinstance(cases, list):
        return [*errors, "external key cases must be a list"]
    for index, case in enumerate(cases):
        location = f"external key cases[{index}]"
        if not isinstance(case, dict) or set(case) != {
            "task_id",
            "reference_summary",
            "criteria",
        }:
            errors.append(f"{location} fields must be task_id, reference_summary, and criteria")
            continue
        task_id = case.get("task_id")
        if not isinstance(task_id, str) or task_id not in expected_ids:
            errors.append(f"{location}.task_id is not a positive suite task")
        elif task_id in seen:
            errors.append(f"{location}.task_id is duplicated")
        else:
            seen.add(task_id)
        if not non_empty_string(case.get("reference_summary")):
            errors.append(f"{location}.reference_summary must be a non-empty string")
        criteria = case.get("criteria")
        if not isinstance(criteria, list):
            errors.append(f"{location}.criteria must be a list")
            continue
        if isinstance(task_id, str) and len(criteria) != manifest_counts.get(task_id):
            errors.append(f"{location}.criteria count does not match the key manifest")
        criterion_ids: set[str] = set()
        case_signature: list[tuple[int, bool]] = []
        critical_count = 0
        for criterion_index, criterion in enumerate(criteria):
            criterion_location = f"{location}.criteria[{criterion_index}]"
            criterion_fields = {"id", "text", "kind", "weight", "critical"}
            if not isinstance(criterion, dict) or set(criterion) != criterion_fields:
                errors.append(f"{criterion_location} fields must be exactly {sorted(criterion_fields)}")
                continue
            criterion_id = criterion.get("id")
            if not non_empty_string(criterion_id):
                errors.append(f"{criterion_location}.id must be a non-empty string")
            elif criterion_id in criterion_ids:
                errors.append(f"{criterion_location}.id is duplicated")
            else:
                criterion_ids.add(criterion_id)
            if not non_empty_string(criterion.get("text")):
                errors.append(f"{criterion_location}.text must be a non-empty string")
            else:
                normalized_text = " ".join(criterion["text"].casefold().split())
                previous_location = criterion_text_locations.get(normalized_text)
                if previous_location is not None:
                    errors.append(f"{criterion_location}.text duplicates {previous_location}")
                else:
                    criterion_text_locations[normalized_text] = criterion_location
            if criterion.get("kind") not in {"deterministic", "semantic"}:
                errors.append(f"{criterion_location}.kind is invalid")
            weight = criterion.get("weight")
            if isinstance(weight, bool) or not isinstance(weight, int) or weight not in RUBRIC_WEIGHTS:
                errors.append(f"{criterion_location}.weight must be an integer from 1 to 4")
            critical = criterion.get("critical")
            if not isinstance(critical, bool):
                errors.append(f"{criterion_location}.critical must be boolean")
            elif isinstance(weight, int) and not isinstance(weight, bool):
                if critical and weight < 3:
                    errors.append(f"{criterion_location}.critical criteria must have weight 3 or 4")
                if not critical and weight == 4:
                    errors.append(f"{criterion_location}.weight 4 criteria must be critical")
                critical_count += int(critical)
                case_signature.append((weight, critical))
                rubric_weights.append(weight)
        if critical_count == 0:
            errors.append(f"{location}.criteria must include at least one critical criterion")
        if len(case_signature) == len(criteria):
            rubric_signatures.append(tuple(case_signature))
    if seen != expected_ids:
        errors.append("external key cases must cover every positive task exactly once")
    uniform_signatures = len(rubric_signatures) > 1 and len(set(rubric_signatures)) == 1
    uniform_weights = len(rubric_weights) > 1 and len(set(rubric_weights)) == 1
    if (uniform_signatures or uniform_weights) and not non_empty_string(uniformity_justification):
        reasons = []
        if uniform_signatures:
            reasons.append("every case uses the same weight/critical signature")
        if uniform_weights:
            reasons.append("every criterion has the same weight")
        errors.append(
            "external key rubric is degenerate ("
            + "; ".join(reasons)
            + "); vary the task-specific rubric or provide rubric_uniformity_justification"
        )
    return errors


def validate_grading_assignment(
    assignment: Any,
    *,
    location: str,
    disallowed_models: set[str | None],
) -> tuple[list[str], str | None, float | None, str | None]:
    errors: list[str] = []
    fields = {"status", "grader_model", "grader_context", "outcome", "score", "evidence", "reason"}
    if not isinstance(assignment, dict) or set(assignment) != fields:
        return [f"{location} fields must be exactly {sorted(fields)}"], None, None, None
    status = assignment.get("status")
    if status not in GRADING_STATUSES:
        errors.append(f"{location}.status is invalid")
        return errors, None, None, None
    if status != "performed":
        if not non_empty_string(assignment.get("reason")):
            errors.append(f"{location}.reason must explain {status}")
        for field in ("grader_model", "grader_context", "outcome", "score", "evidence"):
            if assignment.get(field) is not None:
                errors.append(f"{location}.{field} must be null when status is {status}")
        return errors, status, None, None

    if assignment.get("reason") is not None:
        errors.append(f"{location}.reason must be null when grading is performed")
    grader_model = assignment.get("grader_model")
    identity = canonical_model_identity(grader_model)
    if identity is None:
        errors.append(f"{location}.grader_model must be a non-empty string")
    elif identity in disallowed_models:
        errors.append(f"{location}.grader_model is not independent after alias normalization")
    if not non_empty_string(assignment.get("grader_context")):
        errors.append(f"{location}.grader_context must be a non-empty fresh-context id")
    outcome = assignment.get("outcome")
    if outcome not in GRADE_OUTCOMES:
        errors.append(f"{location}.outcome is invalid")
        outcome = None
    score = assignment.get("score")
    if outcome == "unknown":
        if score is not None:
            errors.append(f"{location}.score must be null for unknown outcomes")
    elif not isinstance(score, (int, float)) or not 0 <= score <= 1:
        errors.append(f"{location}.score must be between 0 and 1")
        score = None
    evidence = assignment.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(non_empty_string(item) for item in evidence):
        errors.append(f"{location}.evidence must contain non-empty strings")
    return errors, status, score, outcome


def validate_grade_report(
    plan: dict[str, Any],
    run_manifest: dict[str, Any],
    report: dict[str, Any],
    *,
    key_sha256: str,
) -> list[str]:
    errors: list[str] = []
    fields = {
        "schema_version",
        "plan_sha256",
        "run_manifest_sha256",
        "key_sha256",
        "arm_labels_anonymized",
        "graded_after_both_arms",
        "grades",
    }
    if set(report) != fields:
        errors.append(f"grade report fields must be exactly {sorted(fields)}")
    if report.get("schema_version") != 1:
        errors.append("grade report schema_version must be 1")
    if report.get("plan_sha256") != canonical_digest(plan):
        errors.append("grade report plan_sha256 does not match the plan")
    if report.get("run_manifest_sha256") != canonical_digest(run_manifest):
        errors.append("grade report run_manifest_sha256 does not match the run manifest")
    if report.get("key_sha256") != key_sha256:
        errors.append("grade report key_sha256 does not match the sealed key")
    if report.get("arm_labels_anonymized") is not True:
        errors.append("grade report arm_labels_anonymized must be true")
    if report.get("graded_after_both_arms") is not True:
        errors.append("grade report graded_after_both_arms must be true")

    plan_trials = {trial["trial_id"]: trial for trial in plan.get("trials", [])}
    run_trials = {trial["trial_id"]: trial for trial in run_manifest.get("trials", [])}
    expected_ids = {
        trial_id
        for trial_id, trial in plan_trials.items()
        if trial.get("task_kind") == "positive" and run_trials.get(trial_id, {}).get("status") == "completed"
    }
    grades = report.get("grades")
    if not isinstance(grades, list):
        return [*errors, "grade report grades must be a list"]
    seen: set[str] = set()
    grade_fields = {
        "trial_id",
        "response_sha256",
        "primary",
        "secondary",
        "final_outcome",
        "final_score",
        "critical_failure",
    }
    for index, grade in enumerate(grades):
        location = f"grade report grades[{index}]"
        if not isinstance(grade, dict) or set(grade) != grade_fields:
            errors.append(f"{location} fields must be exactly {sorted(grade_fields)}")
            continue
        trial_id = grade.get("trial_id")
        if not isinstance(trial_id, str) or trial_id not in expected_ids:
            errors.append(f"{location}.trial_id is not a completed positive trial")
            continue
        if trial_id in seen:
            errors.append(f"{location}.trial_id is duplicated")
            continue
        seen.add(trial_id)
        run_trial = run_trials[trial_id]
        if grade.get("response_sha256") != run_trial.get("response_sha256"):
            errors.append(f"{location}.response_sha256 does not match the frozen response")
        lane_identity = canonical_model_identity(run_trial.get("model"))
        primary_errors, primary_status, primary_score, primary_outcome = validate_grading_assignment(
            grade.get("primary"),
            location=f"{location}.primary",
            disallowed_models={lane_identity, None},
        )
        errors.extend(primary_errors)
        if primary_status != "performed":
            errors.append(f"{location}.primary must be performed")
        primary_model = canonical_model_identity(
            grade.get("primary", {}).get("grader_model") if isinstance(grade.get("primary"), dict) else None
        )
        secondary_errors, secondary_status, secondary_score, secondary_outcome = validate_grading_assignment(
            grade.get("secondary"),
            location=f"{location}.secondary",
            disallowed_models={lane_identity, primary_model, None},
        )
        errors.extend(secondary_errors)

        expected_outcome: str | None
        expected_score: float | None
        if secondary_status == "performed":
            if primary_outcome == "unknown":
                expected_outcome = secondary_outcome
                expected_score = secondary_score
            elif secondary_outcome == primary_outcome:
                expected_outcome = primary_outcome
                if primary_score is not None and secondary_score is not None:
                    expected_score = (primary_score + secondary_score) / 2
                else:
                    expected_score = None
            else:
                expected_outcome = "unknown"
                expected_score = None
        elif secondary_status == "not-required":
            if primary_outcome == "unknown":
                errors.append(f"{location}.secondary must be performed or unavailable after unknown")
            expected_outcome = primary_outcome
            expected_score = primary_score
        else:
            if primary_outcome != "unknown":
                errors.append(f"{location}.secondary may be unavailable only after unknown")
            expected_outcome = "unknown"
            expected_score = None
        if grade.get("final_outcome") != expected_outcome:
            errors.append(f"{location}.final_outcome does not follow blind-grade resolution")
        final_score = grade.get("final_score")
        if expected_score is None:
            if final_score is not None:
                errors.append(f"{location}.final_score must be null for an unresolved outcome")
        elif not isinstance(final_score, (int, float)) or abs(final_score - expected_score) > 1e-9:
            errors.append(f"{location}.final_score does not follow blind-grade resolution")
        if not isinstance(grade.get("critical_failure"), bool):
            errors.append(f"{location}.critical_failure must be boolean")
    if seen != expected_ids:
        errors.append("grade report must cover every completed positive trial exactly once")
    return errors
