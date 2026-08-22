from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATION = REPOSITORY_ROOT / "scripts" / "skill_evals" / "validation.py"


def load_validation() -> ModuleType:
    spec = importlib.util.spec_from_file_location("skill_eval_validation", VALIDATION)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {VALIDATION}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SkillEvalRegistryTests(unittest.TestCase):
    validation: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.validation = load_validation()

    def test_repository_registry_covers_every_discovered_skill_without_claiming_production(self) -> None:
        errors, summary = self.validation.validate_registry(
            REPOSITORY_ROOT,
            REPOSITORY_ROOT / "evals" / "registry.json",
        )

        self.assertEqual(errors, [])
        discovered = self.validation.discover_skills(REPOSITORY_ROOT)
        self.assertEqual(summary.discovered, len(discovered))
        self.assertEqual(summary.registered, len(discovered))
        self.assertEqual(summary.statuses, {"draft": len(discovered)})

    def test_discovery_includes_materialized_skill_audit(self) -> None:
        discovered = self.validation.discover_skills(REPOSITORY_ROOT)

        self.assertIn("skill-audit", discovered)
        self.assertTrue(discovered["skill-audit"].is_dir())
        self.assertFalse(discovered["skill-audit"].is_symlink())
        self.assertTrue(discovered["skill-audit"].resolve().is_relative_to(REPOSITORY_ROOT.resolve()))

    def test_discovery_includes_repository_contained_symlinked_skills(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository_root = Path(temporary_directory)
            skills_root = repository_root / "skills"
            skill_target = repository_root / "fixtures" / "contained-skill"
            skill_target.mkdir(parents=True)
            (skill_target / "SKILL.md").write_text("---\nname: contained-skill\n---\n")
            skills_root.mkdir()
            (skills_root / "contained-skill").symlink_to(skill_target, target_is_directory=True)

            discovered = self.validation.discover_skills(repository_root)

            self.assertIn("contained-skill", discovered)
            self.assertTrue(discovered["contained-skill"].is_symlink())

    def test_discovery_rejects_skill_symlink_that_escapes_repository(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            tempfile.TemporaryDirectory() as external_directory,
        ):
            repository_root = Path(temporary_directory)
            skills_root = repository_root / "skills"
            skill_target = Path(external_directory) / "escaping-skill"
            skill_target.mkdir()
            (skill_target / "SKILL.md").write_text("---\nname: escaping-skill\n---\n")
            skills_root.mkdir()
            escaping_skill = skills_root / "escaping-skill"
            escaping_skill.symlink_to(skill_target, target_is_directory=True)

            with self.assertRaises(ValueError) as context:
                self.validation.discover_skills(repository_root)

        self.assertEqual(context.exception.args, (f"skill path escapes repository: {escaping_skill}",))

    def test_production_gate_fails_until_every_suite_is_calibrated(self) -> None:
        errors, summary = self.validation.validate_registry(
            REPOSITORY_ROOT,
            REPOSITORY_ROOT / "evals" / "registry.json",
            require_production=True,
        )

        self.assertEqual(len(errors), summary.registered)
        self.assertTrue(all("not production" in error for error in errors))

    def test_suite_rejects_unbalanced_named_or_effectful_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            suite_root = Path(temporary_directory)
            fixture_root = suite_root / "fixtures" / "case"
            fixture_root.mkdir(parents=True)
            base_task = {
                "id": "case-one",
                "kind": "positive",
                "class": "capability",
                "prompt": "Use coding-preferences to edit this module safely.",
                "fixture_root": "fixtures/case",
                "graders": ["content"],
                "prohibited_effects": ["network"],
            }
            tasks = []
            for index in range(20):
                task = {**base_task, "id": f"case-{index}"}
                tasks.append(task)
            suite = {
                "schema_version": 1,
                "skill_name": "coding-preferences",
                "suite_version": 1,
                "status": "production",
                "owner": "nfma",
                "snapshot_date": "2026-08-16",
                "execution_policy": {
                    "baseline": "no-skill",
                    "trials_per_harness": 3,
                    "live_side_effects": True,
                    "complete_trace_required": True,
                    "pre_post_hash_required": True,
                },
                "tasks": tasks,
            }

            errors = self.validation.validate_suite(
                suite,
                expected_name="coding-preferences",
                expected_status="production",
                minimum_tasks=20,
                trials_per_harness=3,
                suite_root=suite_root,
            )

        self.assertTrue(any("must not name the skill" in error for error in errors))
        self.assertTrue(any("live_side_effects must be false" in error for error in errors))
        self.assertTrue(any("near-miss" in error for error in errors))

    def test_registry_rejects_missing_and_stale_entries(self) -> None:
        registry = json.loads((REPOSITORY_ROOT / "evals" / "registry.json").read_text())
        registry["skills"] = registry["skills"][1:]
        registry["skills"].append(
            {
                "name": "missing-skill",
                "source": "first-party",
                "risk": "low",
                "status": "draft",
                "suite": "evals/missing-skill/suite.json",
            }
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            registry_path = Path(temporary_directory) / "registry.json"
            registry_path.write_text(json.dumps(registry))
            errors, _summary = self.validation.validate_registry(REPOSITORY_ROOT, registry_path)

        self.assertTrue(any("missing from registry" in error for error in errors))
        self.assertTrue(any("not discovered" in error for error in errors))

    def test_registry_rejects_unbounded_trial_counts(self) -> None:
        registry = json.loads((REPOSITORY_ROOT / "evals" / "registry.json").read_text())
        registry["defaults"]["trials_per_harness"] = self.validation.MAX_TRIALS_PER_HARNESS + 1
        with tempfile.TemporaryDirectory() as temporary_directory:
            registry_path = Path(temporary_directory) / "registry.json"
            registry_path.write_text(json.dumps(registry))
            errors, _summary = self.validation.validate_registry(REPOSITORY_ROOT, registry_path)

        self.assertTrue(any("trials_per_harness must be between" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
