from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPO_ROOT / "skills" / "test-rust" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import run_execution_evals  # noqa: E402


def _write_lean_workspace(root: Path, source: str) -> Path:
    workspace = root / "workspace"
    (workspace / "tests/formal/lean").mkdir(parents=True)
    (workspace / "tests/credit_allocation.rs").write_text("// bridge\n", encoding="utf-8")
    (workspace / run_execution_evals.LEAN_SOURCE_PATH).write_text(source, encoding="utf-8")
    return workspace


def _valid_lean_source(extra: str = "") -> str:
    return (
        "def applyCredits : Nat → List Nat → Nat\n"
        "  | subtotal, [] => subtotal\n"
        "  | subtotal, credit :: rest => applyCredits (subtotal - credit) rest\n"
        "theorem applyCredits_le_subtotal : True := by trivial\n"
        f"{run_execution_evals.LEAN_MODEL_SCOPE_MARKER}\n"
        f"{extra}"
    )


class TestRustExecutionEvalsTests(unittest.TestCase):
    def test_script_parses_as_python_3_9(self) -> None:
        source = (SCRIPT_ROOT / "run_execution_evals.py").read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 9))

    def test_committed_suite_and_fixtures_are_valid(self) -> None:
        suite, cases = run_execution_evals.validated_suite(run_execution_evals.DEFAULT_SUITE)

        self.assertEqual(suite["suite"], run_execution_evals.SUITE_NAME)
        self.assertEqual(
            {case["id"] for case in cases},
            {"lean-business-logic", "pbt-mutation", "tla-protocol"},
        )
        for case in cases:
            fixture = Path(case["fixture_root"])
            self.assertTrue((fixture / "Cargo.toml").is_file())
            self.assertTrue((fixture / "Cargo.lock").is_file())
            self.assertTrue((fixture / "src/lib.rs").is_file())
            self.assertNotIn(run_execution_evals.SKILL_NAME, case["prompt"].casefold())
        lean_case = next(case for case in cases if case["id"] == "lean-business-logic")
        self.assertIn(run_execution_evals.LEAN_MODEL_SCOPE_MARKER, lean_case["prompt"])

    def test_suite_rejects_prompt_that_names_the_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixtures = root / "fixtures"
            for case_id in ("lean-business-logic", "pbt-mutation", "tla-protocol"):
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
                    {
                        "id": "lean-business-logic",
                        "fixture": "fixtures/lean-business-logic",
                        "prompt": "Prove a business invariant.",
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

    def test_tree_hash_excludes_generated_noise_but_includes_untracked_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture = root / "fixture"
            fixture.mkdir()
            (fixture / ".gitignore").write_text("build/\n", encoding="utf-8")
            (fixture / "tracked.md").write_text("guidance\n", encoding="utf-8")
            workspace = root / "workspace"
            run_execution_evals.initialize_workspace(fixture, workspace, with_skill=False)
            initial = run_execution_evals.sha256_tree(workspace)

            (workspace / "build").mkdir()
            (workspace / "build/generated.cache").write_text("noise\n", encoding="utf-8")
            (workspace / "__pycache__").mkdir()
            (workspace / "__pycache__/module.cpython-314.pyc").write_bytes(b"bytecode")
            (workspace / ".DS_Store").write_bytes(b"metadata")
            self.assertEqual(run_execution_evals.sha256_tree(workspace), initial)

            (workspace / "new-reference.md").write_text("real guidance\n", encoding="utf-8")
            self.assertNotEqual(run_execution_evals.sha256_tree(workspace), initial)

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

    def test_proptest_failure_signature_handles_long_input_without_regex_backtracking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stdout = root / "stdout"
            stderr = root / "stderr"
            stdout.write_text("", encoding="utf-8")
            stderr.write_text(
                'minimal failing input:\n    payload = "' + ("A" * 100_000) + '"\n    successes: 0\n',
                encoding="utf-8",
            )

            signature = run_execution_evals.proptest_failure_signature(
                {"stdout_path": str(stdout), "stderr_path": str(stderr)}
            )

            if signature is None:
                self.fail("expected a normalized Proptest failure signature")
            self.assertTrue(signature.startswith("minimal failing input:"))
            self.assertEqual(len(signature), run_execution_evals.PROTEST_SIGNATURE_LIMIT)

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
        self.assertIsNone(run_execution_evals.parse_distinct_states("many distinct states found"))
        self.assertIsNone(run_execution_evals.parse_distinct_states(" distinct states found"))

    def test_lean_artifact_validation_allows_compliance_terms_in_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = _write_lean_workspace(
                Path(temporary_directory),
                _valid_lean_source("-- This complete proof uses no sorry and no admit.\n"),
            )

            errors = run_execution_evals._lean_artifact_errors(workspace)

            self.assertEqual(errors, [])

    def test_lean_seed_replacement_is_scoped_to_definition_block(self) -> None:
        seed = "applyCredits (subtotal - credit) rest"
        replacement = "applyCredits (subtotal + credit) rest"
        source = (
            f'/-! The model equation /- nested -/ computes `{seed}`. -/\ndef explanation := "{seed}"\n'
            + _valid_lean_source()
            + "\n"
            + "theorem repeated_in_proof (subtotal credit : Nat) (rest : List Nat) :\n"
            + f"    {seed} ≤ subtotal := by\n"
            + f"  have h : {seed} ≤ subtotal := applyCredits_le_subtotal _ _\n"
            + "  calc\n"
            + f"    {seed} ≤ subtotal := h\n"
            + "    _ ≤ subtotal := Nat.le_refl subtotal\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            lean_source = Path(temporary_directory) / "CreditAllocation.lean"
            lean_source.write_text(source, encoding="utf-8")

            run_execution_evals.apply_unique_lean_definition_replacement(
                lean_source,
                seed,
                replacement,
            )

            updated = lean_source.read_text(encoding="utf-8")
            self.assertIn(f"/-! The model equation /- nested -/ computes `{seed}`. -/", updated)
            self.assertIn(f'def explanation := "{seed}"', updated)
            self.assertIn(f"credit :: rest => {replacement}", updated)
            self.assertEqual(updated.count(seed), source.count(seed) - 1)

    def test_lean_seed_replacement_normalizes_definition_whitespace(self) -> None:
        seed = "applyCredits (subtotal - credit) rest"
        source = _valid_lean_source().replace(
            "  | subtotal, credit :: rest => applyCredits (subtotal - credit) rest",
            "  |subtotal,credit::rest=>applyCredits (subtotal-credit) rest",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            lean_source = Path(temporary_directory) / "CreditAllocation.lean"
            lean_source.write_text(source, encoding="utf-8")

            run_execution_evals.apply_unique_lean_definition_replacement(
                lean_source,
                seed,
                "applyCredits (subtotal + credit) rest",
            )

            self.assertIn(
                "|subtotal,credit::rest=>applyCredits (subtotal + credit) rest",
                lean_source.read_text(encoding="utf-8"),
            )

    def test_lean_seed_replacement_rejects_missing_or_duplicate_definition(self) -> None:
        seed = "applyCredits (subtotal - credit) rest"
        cases = {
            "missing": "theorem applyCredits_le_subtotal : True := by trivial\n",
            "duplicate": _valid_lean_source() + "\n" + _valid_lean_source(),
        }
        for name, source in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_directory:
                lean_source = Path(temporary_directory) / "CreditAllocation.lean"
                lean_source.write_text(source, encoding="utf-8")

                with self.assertRaisesRegex(
                    run_execution_evals.EvalError,
                    "Lean definition def applyCredits",
                ):
                    run_execution_evals.apply_unique_lean_definition_replacement(
                        lean_source,
                        seed,
                        "applyCredits (subtotal + credit) rest",
                    )

    def test_lean_seed_replacement_rejects_zero_or_multiple_definition_seeds(self) -> None:
        seed = "applyCredits (subtotal - credit) rest"
        recursive_equation = f"  | subtotal, credit :: rest => {seed}"
        cases = {
            "zero": _valid_lean_source().replace(seed, "applyCredits subtotal rest"),
            "multiple": _valid_lean_source().replace(
                recursive_equation,
                recursive_equation + "\n" + recursive_equation,
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_directory:
                lean_source = Path(temporary_directory) / "CreditAllocation.lean"
                lean_source.write_text(source, encoding="utf-8")

                with self.assertRaisesRegex(
                    run_execution_evals.EvalError,
                    "Lean definition seed replacement must occur exactly once",
                ):
                    run_execution_evals.apply_unique_lean_definition_replacement(
                        lean_source,
                        seed,
                        "applyCredits (subtotal + credit) rest",
                    )

    def test_lean_artifact_required_fragments_must_be_executable(self) -> None:
        source = (
            "/-!\n"
            "def applyCredits : Nat → List Nat → Nat\n"
            "applyCredits (subtotal - credit) rest\n"
            "theorem applyCredits_le_subtotal\n"
            "-/\n"
            f"{run_execution_evals.LEAN_MODEL_SCOPE_MARKER}\n"
            "def unrelated : Nat := 0\n"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = _write_lean_workspace(Path(temporary_directory), source)

            errors = run_execution_evals._lean_artifact_errors(workspace)

            self.assertIn(
                "tests/formal/lean/CreditAllocation.lean is missing def applyCredits : Nat → List Nat → Nat",
                errors,
            )
            self.assertIn(
                "tests/formal/lean/CreditAllocation.lean is missing applyCredits (subtotal - credit) rest",
                errors,
            )
            self.assertIn(
                "tests/formal/lean/CreditAllocation.lean is missing theorem applyCredits_le_subtotal",
                errors,
            )

    def test_lean_artifact_validation_rejects_axiom_declaration_evasions(self) -> None:
        evasions = (
            "axiom /- comment -/ cheat : True\n",
            "axiom\ncheat : True\n",
        )
        for evasion in evasions:
            with self.subTest(evasion=evasion), tempfile.TemporaryDirectory() as temporary_directory:
                workspace = _write_lean_workspace(
                    Path(temporary_directory),
                    _valid_lean_source(evasion),
                )

                errors = run_execution_evals._lean_artifact_errors(workspace)

                self.assertIn(
                    "tests/formal/lean/CreditAllocation.lean contains forbidden axiom declaration",
                    errors,
                )

    def test_lean_proof_diagnostic_reports_sorry_and_admit_directly(self) -> None:
        for proof_hole in ("sorry", "admit"):
            with self.subTest(proof_hole=proof_hole), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                stdout = root / "stdout"
                stderr = root / "stderr"
                stdout.write_text(
                    json.dumps(
                        {
                            "data": "declaration uses `sorry`",
                            "kind": "hasSorry",
                            "severity": "warning",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                stderr.write_text("", encoding="utf-8")

                errors = run_execution_evals._lean_proof_errors(
                    {"stdout_path": str(stdout), "stderr_path": str(stderr)}
                )

                self.assertEqual(errors, ["Lean proof contains sorry/admit"])

    def test_lean_limitation_marker_rejects_old_keyword_bag_near_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = _valid_lean_source().replace(
                run_execution_evals.LEAN_MODEL_SCOPE_MARKER,
                "-- not rust model checked",
            )
            workspace = _write_lean_workspace(Path(temporary_directory), source)

            errors = run_execution_evals._lean_artifact_errors(workspace)

            self.assertIn(
                "tests/formal/lean/CreditAllocation.lean omits the model-to-Rust proof limitation",
                errors,
            )

    def test_pbt_only_tool_selection_needs_no_formal_tool_paths(self) -> None:
        arguments = run_execution_evals.build_parser().parse_args(["--case", "pbt-mutation"])

        paths = run_execution_evals._resolve_selected_tool_paths(arguments, {"pbt-mutation"})

        self.assertEqual(paths, {})

    def test_selected_formal_cases_require_their_own_tool_paths(self) -> None:
        cases = (
            ("lean-business-logic", "--lean-bin"),
            ("tla-protocol", "--tla2tools-jar"),
        )
        for case_id, flag in cases:
            with self.subTest(case_id=case_id):
                arguments = run_execution_evals.build_parser().parse_args(["--case", case_id])

                with self.assertRaisesRegex(
                    run_execution_evals.EvalError,
                    f"{flag} is required for case {case_id}",
                ):
                    run_execution_evals._resolve_selected_tool_paths(arguments, {case_id})

    def test_full_suite_records_all_selected_tool_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            lean = root / "lean"
            lean.write_text("#!/bin/sh\n", encoding="utf-8")
            lean.chmod(0o755)
            tla2tools = root / "tla2tools.jar"
            tla2tools.write_bytes(b"jar")
            arguments = run_execution_evals.build_parser().parse_args(
                ["--lean-bin", str(lean), "--tla2tools-jar", str(tla2tools)]
            )
            selected = set(run_execution_evals.REQUIRED_CASE_IDS)
            paths = run_execution_evals._resolve_selected_tool_paths(arguments, selected)

            with patch.object(
                run_execution_evals,
                "version_result",
                side_effect=lambda argv, _cwd, _evidence, _label: {"argv": list(argv)},
            ):
                versions = run_execution_evals._selected_tool_versions(arguments, selected, root, paths)

            self.assertEqual(
                set(versions),
                {"cargo", "cargo_mutants", "claude", "java", "lean"},
            )

    def test_external_output_cannot_be_inside_repository(self) -> None:
        with self.assertRaisesRegex(run_execution_evals.EvalError, "outside"):
            run_execution_evals.require_external_directory(REPO_ROOT / "forbidden-output")


if __name__ == "__main__":
    unittest.main()
