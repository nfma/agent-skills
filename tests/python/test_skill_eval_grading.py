from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.skill_evals.grading import (
    canonical_model_identity,
    validate_external_key,
    validate_grade_report,
)
from scripts.skill_evals.key_workflow import infer_criteria, key_template, write_key_packet
from scripts.skill_evals.validation import canonical_digest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def assignment(
    *,
    model: str = "independent-grader",
    outcome: str = "pass",
    score: float | None = 0.9,
) -> dict[str, object]:
    return {
        "status": "performed",
        "grader_model": model,
        "grader_context": f"fresh-{model}",
        "outcome": outcome,
        "score": score,
        "evidence": ["response satisfies the isolated rubric dimension"],
        "reason": None,
    }


def not_required() -> dict[str, object]:
    return {
        "status": "not-required",
        "grader_model": None,
        "grader_context": None,
        "outcome": None,
        "score": None,
        "evidence": None,
        "reason": "primary grade is determinate",
    }


def plan_run_report() -> tuple[dict, dict, dict]:
    trial_id = "sample.pos-01.claude.t1.with-skill"
    plan = {
        "trials": [
            {
                "trial_id": trial_id,
                "task_kind": "positive",
            }
        ]
    }
    run = {
        "trials": [
            {
                "trial_id": trial_id,
                "status": "completed",
                "model": "claude-opus-5[1m]",
                "response_sha256": "a" * 64,
            }
        ]
    }
    report = {
        "schema_version": 1,
        "plan_sha256": canonical_digest(plan),
        "run_manifest_sha256": canonical_digest(run),
        "key_sha256": "b" * 64,
        "arm_labels_anonymized": True,
        "graded_after_both_arms": True,
        "grades": [
            {
                "trial_id": trial_id,
                "response_sha256": "a" * 64,
                "primary": assignment(),
                "secondary": not_required(),
                "final_outcome": "pass",
                "final_score": 0.9,
                "critical_failure": False,
            }
        ],
    }
    return plan, run, report


class SkillEvalGradingTests(unittest.TestCase):
    def test_external_key_is_complete_and_bound_to_manifest(self) -> None:
        suite_path = REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json"
        suite = json.loads(suite_path.read_text())
        manifest = json.loads((suite_path.parent / "key-manifest.json").read_text())
        key = key_template(suite)
        for case in key["cases"]:
            case["reference_summary"] = "A scoped response that follows the supplied repository conventions."

        self.assertEqual(validate_external_key(key, suite=suite, key_manifest=manifest), [])

    def test_generated_key_packet_cannot_be_accidentally_sealed(self) -> None:
        suite_path = REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json"
        suite = json.loads(suite_path.read_text())
        manifest = json.loads((suite_path.parent / "key-manifest.json").read_text())
        template = key_template(suite)

        errors = validate_external_key(template, suite=suite, key_manifest=manifest)

        self.assertTrue(any("reference_summary" in error for error in errors))
        self.assertFalse(any(".text" in error for error in errors))
        signatures = {
            tuple((criterion["weight"], criterion["critical"]) for criterion in case["criteria"])
            for case in template["cases"]
        }
        self.assertGreater(len(signatures), 1)
        with tempfile.TemporaryDirectory() as temporary_directory:
            key_path, review_path = write_key_packet(
                suite,
                output_path=Path(temporary_directory) / "key.json",
                repository_root=REPOSITORY_ROOT,
            )
            self.assertTrue(key_path.is_file())
            review = review_path.read_text()
            self.assertIn(suite["tasks"][0]["prompt"], review)
            self.assertIn("weight 4, critical=true", review)
            self.assertIn("[required before sealing]", review)
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                write_key_packet(
                    suite,
                    output_path=key_path,
                    repository_root=REPOSITORY_ROOT,
                )

    def test_inference_uses_task_constraints_and_failure_impact(self) -> None:
        criteria = infer_criteria(
            {
                "class": "regression",
                "prompt": "Refactor the worker so at most 32 calls run, results preserve order, and failures cancel work.",
            }
        )

        self.assertEqual([criterion["weight"] for criterion in criteria], [4, 3, 3])
        self.assertEqual([criterion["critical"] for criterion in criteria], [True, True, True])
        self.assertIn("at most 32 calls", criteria[0]["text"])

    def test_degenerate_rubric_requires_explicit_justification(self) -> None:
        suite_path = REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json"
        suite = json.loads(suite_path.read_text())
        manifest = json.loads((suite_path.parent / "key-manifest.json").read_text())
        key = key_template(suite)
        for case_index, case in enumerate(key["cases"]):
            case["reference_summary"] = "A reviewed task-specific outcome."
            for criterion_index, criterion in enumerate(case["criteria"]):
                criterion["text"] = f"Case {case_index} criterion {criterion_index}"
                criterion["weight"] = 3
                criterion["critical"] = criterion_index == 0

        errors = validate_external_key(key, suite=suite, key_manifest=manifest)
        self.assertTrue(any("rubric is degenerate" in error for error in errors))

        key["rubric_uniformity_justification"] = (
            "The cases exercise the same three contractually equal dimensions by design."
        )
        self.assertEqual(validate_external_key(key, suite=suite, key_manifest=manifest), [])

    def test_invalid_failure_impact_contract_is_rejected(self) -> None:
        suite_path = REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json"
        suite = json.loads(suite_path.read_text())
        manifest = json.loads((suite_path.parent / "key-manifest.json").read_text())
        key = key_template(suite)
        for case in key["cases"]:
            case["reference_summary"] = "A reviewed task-specific outcome."

        no_critical = copy.deepcopy(key)
        for criterion in no_critical["cases"][0]["criteria"]:
            criterion["weight"] = 2
            criterion["critical"] = False
        errors = validate_external_key(no_critical, suite=suite, key_manifest=manifest)
        self.assertTrue(any("at least one critical" in error for error in errors))

        low_weight = copy.deepcopy(key)
        low_weight["cases"][0]["criteria"][0]["weight"] = 1
        errors = validate_external_key(low_weight, suite=suite, key_manifest=manifest)
        self.assertTrue(any("critical criteria must have weight" in error for error in errors))

        duplicate_text = copy.deepcopy(key)
        duplicate_text["cases"][1]["criteria"][0]["text"] = duplicate_text["cases"][0]["criteria"][0]["text"]
        errors = validate_external_key(duplicate_text, suite=suite, key_manifest=manifest)
        self.assertTrue(any(".text duplicates" in error for error in errors))

    def test_every_suite_infers_a_non_degenerate_task_specific_rubric(self) -> None:
        registry = json.loads((REPOSITORY_ROOT / "evals" / "registry.json").read_text())
        for entry in registry["skills"]:
            suite_path = REPOSITORY_ROOT / "evals" / entry["name"] / "suite.json"
            suite = json.loads(suite_path.read_text())
            manifest = json.loads((suite_path.parent / "key-manifest.json").read_text())
            key = key_template(suite)
            for case in key["cases"]:
                case["reference_summary"] = "A reviewed task-specific outcome."

            with self.subTest(skill=entry["name"]):
                self.assertEqual(validate_external_key(key, suite=suite, key_manifest=manifest), [])

    def test_valid_primary_blind_grade_passes(self) -> None:
        plan, run, report = plan_run_report()

        self.assertEqual(
            validate_grade_report(plan, run, report, key_sha256="b" * 64),
            [],
        )

    def test_model_alias_cannot_grade_its_own_lane(self) -> None:
        plan, run, report = plan_run_report()
        report["grades"][0]["primary"] = assignment(model="anthropic/claude-opus-5 (1m)")

        errors = validate_grade_report(plan, run, report, key_sha256="b" * 64)

        self.assertTrue(any("not independent" in error for error in errors))
        self.assertEqual(
            canonical_model_identity("anthropic/claude-opus-5 (1m)"),
            canonical_model_identity("claude-opus-5[1m]"),
        )

    def test_unknown_primary_requires_second_grade_or_unavailable_resolution(self) -> None:
        plan, run, report = plan_run_report()
        report["grades"][0]["primary"] = assignment(outcome="unknown", score=None)
        report["grades"][0]["final_outcome"] = "unknown"
        report["grades"][0]["final_score"] = None

        errors = validate_grade_report(plan, run, report, key_sha256="b" * 64)
        self.assertTrue(any("must be performed or unavailable" in error for error in errors))

        unavailable = copy.deepcopy(report)
        unavailable["grades"][0]["secondary"] = {
            "status": "unavailable",
            "grader_model": None,
            "grader_context": None,
            "outcome": None,
            "score": None,
            "evidence": None,
            "reason": "no independent calibrated grader is available",
        }
        self.assertEqual(
            validate_grade_report(plan, run, unavailable, key_sha256="b" * 64),
            [],
        )

    def test_conflicting_independent_grades_resolve_to_unknown(self) -> None:
        plan, run, report = plan_run_report()
        report["grades"][0]["secondary"] = assignment(model="second-independent-grader", outcome="fail", score=0.2)
        report["grades"][0]["final_outcome"] = "unknown"
        report["grades"][0]["final_score"] = None

        self.assertEqual(
            validate_grade_report(plan, run, report, key_sha256="b" * 64),
            [],
        )


if __name__ == "__main__":
    unittest.main()
