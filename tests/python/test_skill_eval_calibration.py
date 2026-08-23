from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.skill_evals.calibration import (
    response_digest,
    validate_calibration_report,
    validate_calibration_set,
)
from scripts.skill_evals.key_workflow import key_template

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def calibration_inputs() -> tuple[dict, dict, dict, dict, str, str]:
    suite = json.loads((REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json").read_text())
    key = key_template(suite)
    for case in key["cases"]:
        case["reference_summary"] = f"Reviewed clean outcome for {case['task_id']}."
    key_sha256 = "a" * 64
    calibration_set_sha256 = "b" * 64
    calibration_cases: list[dict] = []
    counter = 1
    for case in key["cases"]:
        calibration_cases.append(
            {
                "calibration_id": f"cal-{counter:03d}",
                "task_id": case["task_id"],
                "kind": "clean",
                "criterion_id": None,
                "response": f"Clean reference response for {case['task_id']}.",
                "expected_criterion_failed": False,
                "expected_outcome": "pass",
                "expected_score": 1,
                "expected_critical_failure": False,
            }
        )
        counter += 1
        total_weight = sum(criterion["weight"] for criterion in case["criteria"])
        for criterion in case["criteria"]:
            calibration_cases.append(
                {
                    "calibration_id": f"cal-{counter:03d}",
                    "task_id": case["task_id"],
                    "kind": "seeded-failure",
                    "criterion_id": criterion["id"],
                    "response": f"Response seeded to violate {case['task_id']} {criterion['id']}.",
                    "expected_criterion_failed": True,
                    "expected_outcome": "fail" if criterion["critical"] else "pass",
                    "expected_score": (total_weight - criterion["weight"]) / total_weight,
                    "expected_critical_failure": criterion["critical"],
                }
            )
            counter += 1
    calibration_set = {
        "schema_version": 1,
        "skill_name": suite["skill_name"],
        "suite_canonical_sha256": key["suite_canonical_sha256"],
        "key_sha256": key_sha256,
        "reviewer": "nfma",
        "reviewed_at": "2026-08-16T12:00:00+02:00",
        "source_models": ["calibration-author-model"],
        "cases": calibration_cases,
    }
    grades = [
        {
            "calibration_id": case["calibration_id"],
            "response_sha256": response_digest(case["response"]),
            "outcome": case["expected_outcome"],
            "score": case["expected_score"],
            "criterion_failed": case["expected_criterion_failed"],
            "critical_failure": case["expected_critical_failure"],
            "evidence": ["Blind grade matches the isolated calibration dimension."],
        }
        for case in calibration_cases
    ]
    report = {
        "schema_version": 1,
        "skill_name": suite["skill_name"],
        "calibration_set_sha256": calibration_set_sha256,
        "key_sha256": key_sha256,
        "grader_model": "independent-grader",
        "grader_context": "fresh-calibration-context",
        "graded_after_set_frozen": True,
        "grades": grades,
    }
    return suite, key, calibration_set, report, key_sha256, calibration_set_sha256


class SkillEvalCalibrationTests(unittest.TestCase):
    def test_complete_per_criterion_sensitivity_set_and_report_pass(self) -> None:
        suite, key, calibration_set, report, key_digest, set_digest = calibration_inputs()

        self.assertEqual(
            validate_calibration_set(calibration_set, suite=suite, key=key, key_sha256=key_digest),
            [],
        )
        errors, metrics = validate_calibration_report(
            calibration_set,
            report,
            key=key,
            key_sha256=key_digest,
            calibration_set_sha256=set_digest,
        )

        self.assertEqual(errors, [])
        self.assertEqual(metrics["status"], "passed")
        self.assertEqual(metrics["sample_size"], 40)
        self.assertEqual(metrics["clean_case_count"], 10)
        self.assertEqual(metrics["seeded_case_count"], 30)
        self.assertEqual(metrics["threshold_failures"], [])

    def test_missing_or_mislabeled_seeded_coverage_is_rejected(self) -> None:
        suite, key, calibration_set, _report, key_digest, _set_digest = calibration_inputs()
        missing = copy.deepcopy(calibration_set)
        missing["cases"].pop()

        errors = validate_calibration_set(missing, suite=suite, key=key, key_sha256=key_digest)
        self.assertTrue(any("seeded coverage mismatch" in error for error in errors))

        mislabeled = copy.deepcopy(calibration_set)
        seeded = next(case for case in mislabeled["cases"] if case["kind"] == "seeded-failure")
        seeded["expected_critical_failure"] = not seeded["expected_critical_failure"]
        errors = validate_calibration_set(mislabeled, suite=suite, key=key, key_sha256=key_digest)
        self.assertTrue(any("must match the rubric criterion" in error for error in errors))

    def test_grader_cannot_grade_its_own_examples_or_unknown_results(self) -> None:
        _suite, key, calibration_set, report, key_digest, set_digest = calibration_inputs()
        report["grader_model"] = "calibration-author-model"
        report["grades"][0]["outcome"] = "unknown"

        errors, metrics = validate_calibration_report(
            calibration_set,
            report,
            key=key,
            key_sha256=key_digest,
            calibration_set_sha256=set_digest,
        )

        self.assertTrue(any("must not have authored" in error for error in errors))
        self.assertTrue(any("must be determinate" in error for error in errors))
        self.assertEqual(metrics["status"], "failed")

    def test_missed_critical_seed_fails_zero_tolerance_threshold(self) -> None:
        _suite, key, calibration_set, report, key_digest, set_digest = calibration_inputs()
        critical_case = next(
            case
            for case in calibration_set["cases"]
            if case["kind"] == "seeded-failure" and case["expected_critical_failure"]
        )
        grade = next(item for item in report["grades"] if item["calibration_id"] == critical_case["calibration_id"])
        grade["outcome"] = "pass"
        grade["criterion_failed"] = False
        grade["critical_failure"] = False
        grade["score"] = 1

        errors, metrics = validate_calibration_report(
            calibration_set,
            report,
            key=key,
            key_sha256=key_digest,
            calibration_set_sha256=set_digest,
        )

        self.assertEqual(errors, [])
        self.assertEqual(metrics["status"], "failed")
        self.assertIn("critical-failure-recall", metrics["threshold_failures"])
        self.assertIn("critical-disagreements", metrics["threshold_failures"])

    def test_response_and_set_digest_drift_are_rejected(self) -> None:
        _suite, key, calibration_set, report, key_digest, set_digest = calibration_inputs()
        report["calibration_set_sha256"] = "c" * 64
        report["grades"][0]["response_sha256"] = "d" * 64

        errors, _metrics = validate_calibration_report(
            calibration_set,
            report,
            key=key,
            key_sha256=key_digest,
            calibration_set_sha256=set_digest,
        )

        self.assertTrue(any("does not match the frozen set" in error for error in errors))
        self.assertTrue(any("does not match the frozen response" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
