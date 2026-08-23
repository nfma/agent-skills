from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.skill_evals.materialize import generated_files
from scripts.skill_evals.planning import build_plan, deployed_bundle_digest, write_external_plan
from scripts.skill_evals.validation import (
    PENDING_DIGEST,
    canonical_digest,
    validate_calibration_manifest,
    validate_evidence_manifest,
    validate_harness_profile,
    validate_key_manifest,
    validate_suite,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((REPOSITORY_ROOT / "evals" / "registry.json").read_text())


def walk_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in walk_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in walk_keys(child)}
    return set()


class SkillEvalProductionContractTests(unittest.TestCase):
    def test_all_generated_artifacts_are_current(self) -> None:
        stale = []
        for path, expected in generated_files(REPOSITORY_ROOT).items():
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(str(path.relative_to(REPOSITORY_ROOT)))

        self.assertEqual(stale, [])

    def test_every_skill_has_balanced_twenty_task_bank(self) -> None:
        for entry in REGISTRY["skills"]:
            suite = json.loads((REPOSITORY_ROOT / entry["suite"]).read_text())
            kinds = [task["kind"] for task in suite["tasks"]]
            self.assertEqual(len(kinds), 20, entry["name"])
            self.assertEqual(kinds.count("positive"), 10, entry["name"])
            self.assertEqual(kinds.count("near-miss"), 10, entry["name"])
            self.assertGreaterEqual(
                sum(task["class"] == "regression" for task in suite["tasks"]),
                4,
                entry["name"],
            )

    def test_runner_visible_suites_do_not_contain_answer_key_fields(self) -> None:
        forbidden = {
            "answer",
            "checks",
            "criteria",
            "expected",
            "expected_answer",
            "reference_solution",
            "rubric",
        }
        for entry in REGISTRY["skills"]:
            suite = json.loads((REPOSITORY_ROOT / entry["suite"]).read_text())
            leaked = forbidden & walk_keys(suite)
            self.assertEqual(leaked, set(), entry["name"])

    def test_key_manifests_bind_canonical_suite_and_positive_ids(self) -> None:
        for entry in REGISTRY["skills"]:
            suite_path = REPOSITORY_ROOT / entry["suite"]
            suite = json.loads(suite_path.read_text())
            manifest = json.loads((suite_path.parent / "key-manifest.json").read_text())
            self.assertEqual(manifest["suite_canonical_sha256"], canonical_digest(suite))
            self.assertEqual(manifest["key_sha256"], PENDING_DIGEST)
            self.assertEqual(
                {case["task_id"] for case in manifest["cases"]},
                {task["id"] for task in suite["tasks"] if task["kind"] == "positive"},
            )

    def test_complete_key_validation_rejects_pending_or_mutated_suite(self) -> None:
        suite_path = REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json"
        suite = json.loads(suite_path.read_text())
        manifest = json.loads((suite_path.parent / "key-manifest.json").read_text())

        pending_errors = validate_key_manifest(
            manifest,
            suite=suite,
            expected_name="coding-preferences",
            require_sealed=True,
        )
        mutated = copy.deepcopy(suite)
        mutated["tasks"][0]["prompt"] += " Changed after sealing."
        mutation_errors = validate_key_manifest(
            manifest,
            suite=mutated,
            expected_name="coding-preferences",
            require_sealed=False,
        )

        self.assertTrue(any("unsealed" in error for error in pending_errors))
        self.assertTrue(any("does not match" in error for error in mutation_errors))

    def test_calibration_gate_enforces_agreement_expiry_and_key_binding(self) -> None:
        reviewed = date(2026, 8, 16)
        key_digest = "a" * 64
        valid = {
            "schema_version": 1,
            "skill_name": "coding-preferences",
            "status": "passed",
            "reviewer": "nfma",
            "reviewed_at": reviewed.isoformat(),
            "expires_at": (reviewed + timedelta(days=183)).isoformat(),
            "sample_size": 40,
            "clean_case_count": 10,
            "seeded_case_count": 30,
            "binary_agreement": 0.95,
            "ordinal_weighted_agreement": 0.8,
            "critical_failure_recall": 1.0,
            "noncritical_failure_agreement": 0.9,
            "clean_acceptance_rate": 0.9,
            "critical_disagreements": 0,
            "key_sha256": key_digest,
            "calibration_set_sha256": "b" * 64,
            "calibration_report_sha256": "c" * 64,
        }
        self.assertEqual(
            validate_calibration_manifest(
                valid,
                expected_name="coding-preferences",
                key_sha256=key_digest,
                require_passed=True,
                recheck_days=183,
                expected_clean_count=10,
                expected_seeded_count=30,
                today=reviewed,
            ),
            [],
        )

        stale = {
            **valid,
            "critical_failure_recall": 0.99,
            "clean_acceptance_rate": 0.89,
            "critical_disagreements": 1,
        }
        errors = validate_calibration_manifest(
            stale,
            expected_name="coding-preferences",
            key_sha256="b" * 64,
            require_passed=True,
            recheck_days=183,
            expected_clean_count=10,
            expected_seeded_count=30,
            today=reviewed + timedelta(days=184),
        )
        self.assertTrue(any("must match" in error for error in errors))
        self.assertTrue(any("is stale" in error for error in errors))
        self.assertTrue(any("critical_failure_recall must be 1" in error for error in errors))
        self.assertTrue(any("clean_acceptance_rate must be at least 0.9" in error for error in errors))
        self.assertTrue(any("must be zero" in error for error in errors))

    def test_fixture_directory_symlinks_are_rejected(self) -> None:
        suite_path = REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json"
        suite = json.loads(suite_path.read_text())
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root / "outside"
            outside.mkdir()
            suite_root = root / "suite"
            suite_root.mkdir()
            (suite_root / "fixtures").symlink_to(outside, target_is_directory=True)
            suite["tasks"][0]["fixture_root"] = "fixtures"
            errors = validate_suite(
                suite,
                expected_name="coding-preferences",
                expected_status="draft",
                minimum_tasks=20,
                trials_per_harness=3,
                suite_root=suite_root,
            )

        self.assertTrue(any("resolve to a directory inside" in error for error in errors))

    def test_plan_pairs_positive_arms_and_runs_near_misses_with_skill_only(self) -> None:
        plan = build_plan(
            REPOSITORY_ROOT,
            skill_names=["coding-preferences"],
            harnesses=["claude-code"],
        )
        positive = [trial for trial in plan["trials"] if trial["task_kind"] == "positive"]
        near_miss = [trial for trial in plan["trials"] if trial["task_kind"] == "near-miss"]

        self.assertEqual(len(plan["trials"]), 90)
        self.assertEqual(len(positive), 60)
        self.assertEqual(len(near_miss), 30)
        self.assertEqual({trial["arm"] for trial in positive}, {"baseline", "with-skill"})
        self.assertEqual({trial["arm"] for trial in near_miss}, {"with-skill"})
        self.assertEqual(plan["lanes"][0]["harness"], "claude-code")
        self.assertEqual(plan["lanes"][0]["status"], "verified")
        self.assertEqual(sorted(trial["sequence"] for trial in plan["trials"]), list(range(1, 91)))

    def test_deployed_bundle_digest_excludes_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            skill_root = repository_root / "skills" / "sample"
            tests_root = skill_root / "tests"
            tests_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text("production instructions")
            (tests_root / "test_sample.py").write_text("first")
            before = deployed_bundle_digest(skill_root, repository_root)
            (tests_root / "test_sample.py").write_text("changed but not deployed")
            after = deployed_bundle_digest(skill_root, repository_root)

        self.assertEqual(before, after)

    def test_external_plan_writer_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "plan.json"
            write_external_plan({"schema_version": 1}, output, REPOSITORY_ROOT)
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                write_external_plan({"schema_version": 1}, output, REPOSITORY_ROOT)

    def test_harness_profile_is_fail_closed_and_rejects_bypass_flags(self) -> None:
        profile = json.loads((REPOSITORY_ROOT / "evals" / "harnesses.json").read_text())
        self.assertEqual(validate_harness_profile(profile, today=date(2026, 8, 16)), [])
        self.assertEqual(
            {lane["status"] for lane in profile["lanes"] if lane["harness"] != "claude-code"},
            {"unavailable"},
        )

        unsafe = copy.deepcopy(profile)
        unsafe["lanes"][-1]["arguments"].append("--dangerously-skip-permissions")
        errors = validate_harness_profile(unsafe, today=date(2026, 8, 16))
        self.assertTrue(any("trust-bypass" in error for error in errors))

        stale_errors = validate_harness_profile(profile, today=date(2026, 11, 17))
        self.assertTrue(any("qualification is stale" in error for error in stale_errors))

    def test_production_evidence_requires_all_four_current_harnesses(self) -> None:
        suite = json.loads((REPOSITORY_ROOT / "evals" / "coding-preferences" / "suite.json").read_text())
        profile = json.loads((REPOSITORY_ROOT / "evals" / "harnesses.json").read_text())
        manifest = {
            "schema_version": 1,
            "skill_name": "coding-preferences",
            "status": "passed",
            "suite_canonical_sha256": canonical_digest(suite),
            "harness_profile_canonical_sha256": canonical_digest(profile),
            "aggregate_report_sha256": "a" * 64,
            "evaluated_at": "2026-08-16",
            "expires_at": "2026-11-16",
            "harnesses": [
                {
                    "harness": harness,
                    "status": "passed",
                    "evidence_sha256": chr(98 + index) * 64,
                    "reason": None,
                }
                for index, harness in enumerate(REGISTRY["defaults"]["required_harnesses"])
            ],
        }
        self.assertEqual(
            validate_evidence_manifest(
                manifest,
                suite=suite,
                harness_profile=profile,
                expected_name="coding-preferences",
                require_passed=True,
                required_harnesses=set(REGISTRY["defaults"]["required_harnesses"]),
                today=date(2026, 8, 16),
            ),
            [],
        )

        partial = copy.deepcopy(manifest)
        partial["status"] = "not-proven"
        partial["harnesses"][0]["status"] = "unavailable"
        partial["harnesses"][0]["reason"] = "native load observation unsupported"
        errors = validate_evidence_manifest(
            partial,
            suite=suite,
            harness_profile=profile,
            expected_name="coding-preferences",
            require_passed=True,
            required_harnesses=set(REGISTRY["defaults"]["required_harnesses"]),
            today=date(2026, 8, 16),
        )
        self.assertTrue(any("has not passed every required harness" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
