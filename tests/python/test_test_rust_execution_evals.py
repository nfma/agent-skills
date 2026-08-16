from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPO_ROOT / "skills" / "test-rust" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import run_execution_evals  # noqa: E402


class TestRustExecutionEvalsTests(unittest.TestCase):
    def test_script_parses_as_python_3_9(self) -> None:
        source = (SCRIPT_ROOT / "run_execution_evals.py").read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 9))

    def test_committed_suite_and_fixtures_are_valid(self) -> None:
        suite, cases = run_execution_evals.validated_suite(run_execution_evals.DEFAULT_SUITE)

        self.assertEqual(suite["suite"], run_execution_evals.SUITE_NAME)
        self.assertEqual(
            {case["id"] for case in cases},
            {"pbt-mutation", "tla-protocol"},
        )
        for case in cases:
            fixture = Path(case["fixture_root"])
            self.assertTrue((fixture / "Cargo.toml").is_file())
            self.assertTrue((fixture / "Cargo.lock").is_file())
            self.assertTrue((fixture / "src/lib.rs").is_file())
            self.assertNotIn(run_execution_evals.SKILL_NAME, case["prompt"].casefold())

    def test_suite_rejects_prompt_that_names_the_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixtures = root / "fixtures"
            for case_id in ("pbt-mutation", "tla-protocol"):
                fixture = fixtures / case_id
                fixture.mkdir(parents=True)
                (fixture / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
            suite = {
                "schema_version": 1,
                "suite": run_execution_evals.SUITE_NAME,
                "skill_name": run_execution_evals.SKILL_NAME,
                "cases": [
                    {
                        "id": "pbt-mutation",
                        "fixture": "fixtures/pbt-mutation",
                        "prompt": "Use test-rust.",
                    },
                    {
                        "id": "tla-protocol",
                        "fixture": "fixtures/tla-protocol",
                        "prompt": "Write external tests.",
                    },
                ],
            }
            suite_path = root / "suite.json"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")

            with self.assertRaisesRegex(run_execution_evals.EvalError, "explicitly names"):
                run_execution_evals.validated_suite(suite_path)

    def test_tree_hash_detects_content_and_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "one").write_text("same", encoding="utf-8")
            initial = run_execution_evals.sha256_tree(root)

            (root / "one").write_text("different", encoding="utf-8")
            self.assertNotEqual(run_execution_evals.sha256_tree(root), initial)

            (root / "one").write_text("same", encoding="utf-8")
            (root / "one").rename(root / "two")
            self.assertNotEqual(run_execution_evals.sha256_tree(root), initial)

    def test_unique_seed_replacement_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "lib.rs"
            path.write_text("before before", encoding="utf-8")

            with self.assertRaisesRegex(run_execution_evals.EvalError, "exactly once"):
                run_execution_evals.apply_unique_replacement(path, "before", "after")

    def test_proptest_failure_signature_normalizes_minimal_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stdout = root / "stdout"
            stderr = root / "stderr"
            stdout.write_text("", encoding="utf-8")
            stderr.write_text(
                'minimal failing input:\n    payload = "ABC"\n\tsuccesses: 0\n',
                encoding="utf-8",
            )

            signature = run_execution_evals.proptest_failure_signature(
                {"stdout_path": str(stdout), "stderr_path": str(stderr)}
            )

            self.assertEqual(signature, 'minimal failing input: payload = "ABC"')

    def test_mutant_classification_separates_known_survivors(self) -> None:
        classification = run_execution_evals.classify_mutants(
            ["replace != with == in parse"],
            [
                "replace + with - in normalize_checksum",
                "replace > with < in diagnostic_bucket",
                "unknown survivor",
            ],
            ["replace parse -> Result with Ok(Default::default())"],
            [],
        )

        self.assertEqual(len(classification["caught"]), 1)
        self.assertEqual(len(classification["equivalent"]), 1)
        self.assertEqual(len(classification["boundary_unreachable"]), 1)
        self.assertEqual(classification["unexplained_survivors"], ["unknown survivor"])
        self.assertEqual(len(classification["unviable"]), 1)

    def test_distinct_state_parser_uses_largest_progress_value(self) -> None:
        output = "Progress: 3 states generated, 3 distinct states found\n10 states generated, 8 distinct states found\n"

        self.assertEqual(run_execution_evals.parse_distinct_states(output), 8)
        self.assertIsNone(run_execution_evals.parse_distinct_states("no statistics"))

    def test_external_output_cannot_be_inside_repository(self) -> None:
        with self.assertRaisesRegex(run_execution_evals.EvalError, "outside"):
            run_execution_evals.require_external_directory(REPO_ROOT / "forbidden-output")


if __name__ == "__main__":
    unittest.main()
