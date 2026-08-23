from __future__ import annotations

from hashlib import sha256
from typing import Any

from .grading import canonical_model_identity
from .validation import canonical_digest, non_empty_string

CALIBRATION_KINDS = {"clean", "seeded-failure"}
CALIBRATION_OUTCOMES = {"pass", "fail", "unknown"}


def response_digest(response: str) -> str:
    return sha256(response.encode("utf-8")).hexdigest()


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _key_criteria(key: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for case in key.get("cases", []):
        if not isinstance(case, dict) or not isinstance(case.get("task_id"), str):
            continue
        for criterion in case.get("criteria", []):
            if isinstance(criterion, dict) and isinstance(criterion.get("id"), str):
                result[(case["task_id"], criterion["id"])] = criterion
    return result


def validate_calibration_set(
    calibration_set: dict[str, Any],
    *,
    suite: dict[str, Any],
    key: dict[str, Any],
    key_sha256: str,
) -> list[str]:
    errors: list[str] = []
    fields = {
        "schema_version",
        "skill_name",
        "suite_canonical_sha256",
        "key_sha256",
        "reviewer",
        "reviewed_at",
        "source_models",
        "cases",
    }
    if set(calibration_set) != fields:
        errors.append(f"calibration set fields must be exactly {sorted(fields)}")
    if calibration_set.get("schema_version") != 1:
        errors.append("calibration set schema_version must be 1")
    if calibration_set.get("skill_name") != suite.get("skill_name"):
        errors.append("calibration set skill_name must match the suite")
    if calibration_set.get("suite_canonical_sha256") != canonical_digest(suite):
        errors.append("calibration set suite_canonical_sha256 does not match the suite")
    if calibration_set.get("key_sha256") != key_sha256:
        errors.append("calibration set key_sha256 does not match the sealed key")
    if not non_empty_string(calibration_set.get("reviewer")):
        errors.append("calibration set reviewer must identify the human reviewer")
    if not _is_timestamp(calibration_set.get("reviewed_at")):
        errors.append("calibration set reviewed_at must be a timezone-aware ISO timestamp")
    source_models = calibration_set.get("source_models")
    if not isinstance(source_models, list) or not all(non_empty_string(model) for model in source_models):
        errors.append("calibration set source_models must be a list of non-empty model names")
    elif len(source_models) != len(set(source_models)):
        errors.append("calibration set source_models must not contain duplicates")

    positive_ids = {
        task["id"]
        for task in suite.get("tasks", [])
        if isinstance(task, dict) and task.get("kind") == "positive" and isinstance(task.get("id"), str)
    }
    criteria = _key_criteria(key)
    cases = calibration_set.get("cases")
    if not isinstance(cases, list):
        return [*errors, "calibration set cases must be a list"]
    seen_ids: set[str] = set()
    clean_coverage: set[str] = set()
    seeded_coverage: set[tuple[str, str]] = set()
    case_fields = {
        "calibration_id",
        "task_id",
        "kind",
        "criterion_id",
        "response",
        "expected_criterion_failed",
        "expected_outcome",
        "expected_score",
        "expected_critical_failure",
    }
    for index, case in enumerate(cases):
        location = f"calibration set cases[{index}]"
        if not isinstance(case, dict) or set(case) != case_fields:
            errors.append(f"{location} fields must be exactly {sorted(case_fields)}")
            continue
        calibration_id = case.get("calibration_id")
        if not non_empty_string(calibration_id):
            errors.append(f"{location}.calibration_id must be a non-empty string")
        elif calibration_id in seen_ids:
            errors.append(f"{location}.calibration_id is duplicated")
        else:
            seen_ids.add(calibration_id)
        task_id = case.get("task_id")
        if task_id not in positive_ids:
            errors.append(f"{location}.task_id is not a positive suite task")
        kind = case.get("kind")
        if kind not in CALIBRATION_KINDS:
            errors.append(f"{location}.kind is invalid")
        response = case.get("response")
        if not non_empty_string(response):
            errors.append(f"{location}.response must be a non-empty frozen response")
        expected_outcome = case.get("expected_outcome")
        if expected_outcome not in {"pass", "fail"}:
            errors.append(f"{location}.expected_outcome must be pass or fail")
        expected_score = case.get("expected_score")
        if (
            not isinstance(expected_score, (int, float))
            or isinstance(expected_score, bool)
            or not 0 <= expected_score <= 1
        ):
            errors.append(f"{location}.expected_score must be between 0 and 1")
        expected_failed = case.get("expected_criterion_failed")
        expected_critical = case.get("expected_critical_failure")
        if not isinstance(expected_failed, bool):
            errors.append(f"{location}.expected_criterion_failed must be boolean")
        if not isinstance(expected_critical, bool):
            errors.append(f"{location}.expected_critical_failure must be boolean")

        criterion_id = case.get("criterion_id")
        if kind == "clean":
            if criterion_id is not None:
                errors.append(f"{location}.criterion_id must be null for a clean case")
            if isinstance(task_id, str) and task_id in clean_coverage:
                errors.append(f"{location} duplicates clean coverage for {task_id}")
            elif isinstance(task_id, str):
                clean_coverage.add(task_id)
            if expected_failed is not False or expected_outcome != "pass" or expected_score != 1:
                errors.append(f"{location} clean labels must be criterion_failed=false, outcome=pass, and score=1")
            if expected_critical is not False:
                errors.append(f"{location}.expected_critical_failure must be false for a clean case")
            continue

        criterion_key = (task_id, criterion_id)
        criterion = criteria.get(criterion_key) if isinstance(task_id, str) and isinstance(criterion_id, str) else None
        if criterion is None:
            errors.append(f"{location}.criterion_id does not match the task rubric")
            continue
        if criterion_key in seeded_coverage:
            errors.append(f"{location} duplicates seeded coverage for {task_id}/{criterion_id}")
        else:
            seeded_coverage.add(criterion_key)
        if expected_failed is not True:
            errors.append(f"{location}.expected_criterion_failed must be true for a seeded failure")
        if expected_critical is not criterion.get("critical"):
            errors.append(f"{location}.expected_critical_failure must match the rubric criterion")
        if criterion.get("critical") is True and expected_outcome != "fail":
            errors.append(f"{location}.expected_outcome must be fail for a critical seeded failure")
        if expected_score == 1:
            errors.append(f"{location}.expected_score must be below 1 for a seeded failure")

    if clean_coverage != positive_ids:
        missing = sorted(positive_ids - clean_coverage)
        extra = sorted(clean_coverage - positive_ids)
        errors.append(f"calibration set clean coverage mismatch: missing={missing}, extra={extra}")
    expected_seeded = set(criteria)
    if seeded_coverage != expected_seeded:
        missing = sorted(expected_seeded - seeded_coverage)
        extra = sorted(seeded_coverage - expected_seeded)
        errors.append(f"calibration set seeded coverage mismatch: missing={missing}, extra={extra}")
    return errors


def validate_calibration_report(
    calibration_set: dict[str, Any],
    report: dict[str, Any],
    *,
    key: dict[str, Any],
    key_sha256: str,
    calibration_set_sha256: str,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    fields = {
        "schema_version",
        "skill_name",
        "calibration_set_sha256",
        "key_sha256",
        "grader_model",
        "grader_context",
        "graded_after_set_frozen",
        "grades",
    }
    if set(report) != fields:
        errors.append(f"calibration report fields must be exactly {sorted(fields)}")
    if report.get("schema_version") != 1:
        errors.append("calibration report schema_version must be 1")
    if report.get("skill_name") != calibration_set.get("skill_name"):
        errors.append("calibration report skill_name must match the calibration set")
    if report.get("calibration_set_sha256") != calibration_set_sha256:
        errors.append("calibration report calibration_set_sha256 does not match the frozen set")
    if report.get("key_sha256") != key_sha256:
        errors.append("calibration report key_sha256 does not match the sealed key")
    grader_model = report.get("grader_model")
    grader_identity = canonical_model_identity(grader_model)
    if grader_identity is None:
        errors.append("calibration report grader_model must be a non-empty model name")
    source_identities = {
        canonical_model_identity(model) for model in calibration_set.get("source_models", []) if non_empty_string(model)
    }
    if grader_identity in source_identities:
        errors.append("calibration report grader_model must not have authored calibration examples")
    if not non_empty_string(report.get("grader_context")):
        errors.append("calibration report grader_context must be a non-empty fresh-context id")
    if report.get("graded_after_set_frozen") is not True:
        errors.append("calibration report graded_after_set_frozen must be true")

    cases = {
        case["calibration_id"]: case
        for case in calibration_set.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("calibration_id"), str)
    }
    criteria = _key_criteria(key)
    grades = report.get("grades")
    if not isinstance(grades, list):
        return [*errors, "calibration report grades must be a list"], {}
    seen: set[str] = set()
    observed: dict[str, dict[str, Any]] = {}
    grade_fields = {
        "calibration_id",
        "response_sha256",
        "outcome",
        "score",
        "criterion_failed",
        "critical_failure",
        "evidence",
    }
    for index, grade in enumerate(grades):
        location = f"calibration report grades[{index}]"
        if not isinstance(grade, dict) or set(grade) != grade_fields:
            errors.append(f"{location} fields must be exactly {sorted(grade_fields)}")
            continue
        calibration_id = grade.get("calibration_id")
        case = cases.get(calibration_id) if isinstance(calibration_id, str) else None
        if case is None:
            errors.append(f"{location}.calibration_id is not in the calibration set")
            continue
        if calibration_id in seen:
            errors.append(f"{location}.calibration_id is duplicated")
            continue
        seen.add(calibration_id)
        if grade.get("response_sha256") != response_digest(case["response"]):
            errors.append(f"{location}.response_sha256 does not match the frozen response")
        outcome = grade.get("outcome")
        if outcome not in CALIBRATION_OUTCOMES:
            errors.append(f"{location}.outcome is invalid")
        if outcome == "unknown":
            errors.append(f"{location}.outcome must be determinate for calibration")
        score = grade.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 1:
            errors.append(f"{location}.score must be between 0 and 1")
        if not isinstance(grade.get("criterion_failed"), bool):
            errors.append(f"{location}.criterion_failed must be boolean")
        if not isinstance(grade.get("critical_failure"), bool):
            errors.append(f"{location}.critical_failure must be boolean")
        evidence = grade.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(non_empty_string(item) for item in evidence):
            errors.append(f"{location}.evidence must contain non-empty strings")
        observed[calibration_id] = grade
    if seen != set(cases):
        errors.append("calibration report must grade every calibration case exactly once")

    clean_total = 0
    clean_accepted = 0
    critical_total = 0
    critical_detected = 0
    noncritical_total = 0
    noncritical_detected = 0
    binary_matches = 0
    critical_disagreements = 0
    weighted_agreement_numerator = 0.0
    weighted_agreement_denominator = 0.0
    for calibration_id, case in cases.items():
        grade = observed.get(calibration_id)
        if grade is None:
            continue
        binary_matches += int(grade.get("outcome") == case.get("expected_outcome"))
        expected_score = case.get("expected_score")
        observed_score = grade.get("score")
        criterion = criteria.get((case.get("task_id"), case.get("criterion_id")))
        weight = criterion.get("weight", 4) if criterion is not None else 4
        if isinstance(expected_score, (int, float)) and isinstance(observed_score, (int, float)):
            weighted_agreement_numerator += weight * max(0.0, 1 - abs(expected_score - observed_score))
            weighted_agreement_denominator += weight
        if grade.get("critical_failure") is not case.get("expected_critical_failure"):
            critical_disagreements += 1
        if case.get("kind") == "clean":
            clean_total += 1
            clean_accepted += int(
                grade.get("outcome") == "pass"
                and grade.get("criterion_failed") is False
                and grade.get("critical_failure") is False
            )
        elif case.get("expected_critical_failure") is True:
            critical_total += 1
            critical_detected += int(
                grade.get("outcome") == "fail"
                and grade.get("criterion_failed") is True
                and grade.get("critical_failure") is True
            )
        else:
            noncritical_total += 1
            noncritical_detected += int(
                grade.get("criterion_failed") is True and grade.get("critical_failure") is False
            )

    sample_size = len(cases)
    metrics = {
        "sample_size": sample_size,
        "clean_case_count": clean_total,
        "seeded_case_count": critical_total + noncritical_total,
        "binary_agreement": binary_matches / sample_size if sample_size else 0.0,
        "ordinal_weighted_agreement": (
            weighted_agreement_numerator / weighted_agreement_denominator if weighted_agreement_denominator else 0.0
        ),
        "critical_failure_recall": critical_detected / critical_total if critical_total else 1.0,
        "noncritical_failure_agreement": noncritical_detected / noncritical_total if noncritical_total else 1.0,
        "clean_acceptance_rate": clean_accepted / clean_total if clean_total else 0.0,
        "critical_disagreements": critical_disagreements,
    }
    threshold_failures: list[str] = []
    if metrics["critical_failure_recall"] != 1:
        threshold_failures.append("critical-failure-recall")
    if metrics["noncritical_failure_agreement"] < 0.9:
        threshold_failures.append("noncritical-failure-agreement")
    if metrics["clean_acceptance_rate"] < 0.9:
        threshold_failures.append("clean-acceptance-rate")
    if metrics["ordinal_weighted_agreement"] < 0.8:
        threshold_failures.append("ordinal-weighted-agreement")
    if metrics["critical_disagreements"] != 0:
        threshold_failures.append("critical-disagreements")
    metrics["threshold_failures"] = threshold_failures
    metrics["status"] = "passed" if not errors and not threshold_failures else "failed"
    return errors, metrics
