from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import subprocess  # nosec B404  # Tests use fixed Git argv with shell=False.
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPO_ROOT / "skills" / "test-rust" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import check_tests_boundaries  # noqa: E402


class RunResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _run(*args: object) -> RunResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        returncode = check_tests_boundaries.main([str(arg) for arg in args])
    return RunResult(returncode, stdout.getvalue(), stderr.getvalue())


def _git(workspace: Path, *args: str) -> None:
    # This fixed Git test command never invokes a shell.
    subprocess.run(  # nosec B603 B607
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Test Rust",
            "-c",
            "user.email=test-rust@example.invalid",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _package(root: Path, relative: str = ".") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _git(root, "init", "-q")
    package = (root / relative).resolve()
    package.mkdir(parents=True, exist_ok=True)
    (package / "Cargo.toml").write_text(
        '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2024"\n',
        encoding="utf-8",
    )
    (package / "src").mkdir()
    (package / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", f"add package {relative}")
    return package


def _snapshot(workspace: Path, baseline: Path, *packages: Path) -> RunResult:
    args: list[object] = ["snapshot", workspace, "--output", baseline]
    for package in packages:
        args.extend(["--package-root", package])
    return _run(*args)


class TestRustBoundariesTests(unittest.TestCase):
    def test_script_parses_as_python_3_9(self) -> None:
        source = (SCRIPT_ROOT / "check_tests_boundaries.py").read_text(encoding="utf-8")
        ast.parse(source, feature_version=(3, 9))

    def test_missing_baseline_reports_clean_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            _package(workspace)
            missing_baseline = root / "missing-baseline.json"

            result = _run("verify", workspace, "--baseline", missing_baseline)

            self.assertEqual(result.returncode, 2)
            self.assertIn("error:", result.stderr)
            self.assertIn(str(missing_baseline), result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_allows_only_changes_below_concrete_package_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            (package / "tests").mkdir()
            (package / "tests" / "public_api.rs").write_text("#[test]\nfn works() {}\n", encoding="utf-8")

            result = _run("verify", workspace, "--baseline", baseline)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("boundary ok", result.stdout)

    def test_rejects_production_change_even_when_file_was_dirty_at_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            source = package / "src" / "lib.rs"
            source.write_text("pub fn answer() -> u8 { 41 }\n", encoding="utf-8")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            source.write_text("pub fn answer() -> u8 { 40 }\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("src/lib.rs", result.stderr)

    def test_virtual_workspace_root_is_not_a_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "Cargo.toml").write_text('[workspace]\nmembers = ["member"]\n', encoding="utf-8")
            member = _package(workspace, "member")
            baseline = root / "baseline.json"

            rejected = _snapshot(workspace, baseline, workspace)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("not a concrete package", rejected.stderr)

            accepted = _snapshot(workspace, baseline, member)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            payload = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(payload["allowed_test_roots"], ["member/tests"])

    def test_rejects_nested_manifest_under_tests_case_insensitively(self) -> None:
        for manifest_name in ("Cargo.toml", "cargo.toml"):
            with self.subTest(manifest_name=manifest_name), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                workspace = root / "workspace"
                package = _package(workspace)
                baseline = root / "baseline.json"
                self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

                nested = package / "tests" / "formal"
                nested.mkdir(parents=True)
                (nested / manifest_name).write_text(
                    "[package]\nname='forbidden'\nversion='0.1.0'\n",
                    encoding="utf-8",
                )
                result = _run("verify", workspace, "--baseline", baseline)

                self.assertEqual(result.returncode, 1)
                self.assertIn(f"tests/formal/{manifest_name}", result.stderr)

    def test_rejects_symlinked_tests_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            outside = root / "outside"
            outside.mkdir()
            (package / "tests").symlink_to(outside, target_is_directory=True)
            baseline = root / "baseline.json"

            result = _snapshot(workspace, baseline, package)
            self.assertEqual(result.returncode, 2)
            self.assertIn("symlinked package/test path", result.stderr)

    def test_rejects_symlink_below_tests_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            outside = root / "outside"
            outside.mkdir()
            (package / "tests").mkdir()
            (package / "tests" / "fixtures").symlink_to(outside, target_is_directory=True)
            baseline = root / "baseline.json"

            result = _snapshot(workspace, baseline, package)
            self.assertEqual(result.returncode, 2)
            self.assertIn("symlink below an allowed tests root", result.stderr)

    def test_requires_external_baseline_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            result = _snapshot(workspace, workspace / "baseline.json", package)

            self.assertEqual(result.returncode, 2)
            self.assertIn("outside the workspace", result.stderr)

    def test_refuses_to_overwrite_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            result = _snapshot(workspace, baseline, package)
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to overwrite", result.stderr)

    def test_rejects_baseline_from_another_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = root / "first"
            second = root / "second"
            first_package = _package(first)
            _package(second)
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(first, baseline, first_package).returncode, 0)

            result = _run("verify", second, "--baseline", baseline)
            self.assertEqual(result.returncode, 2)
            self.assertIn("different workspace", result.stderr)

    def test_supports_multiple_package_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            first = _package(workspace, "crates/first")
            second = _package(workspace, "crates/second")
            baseline = root / "baseline.json"

            result = _snapshot(workspace, baseline, first, second)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["allowed_test_roots"],
                ["crates/first/tests", "crates/second/tests"],
            )

    def test_ignores_preexisting_git_ignored_scratch_but_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            (workspace / ".gitignore").write_text("target/\n", encoding="utf-8")
            _git(workspace, "add", ".gitignore")
            _git(workspace, "commit", "-qm", "ignore scratch")
            target = workspace / "target"
            target.mkdir()
            ambient = target / "ambient"
            ambient.write_text("before\n", encoding="utf-8")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            ambient.write_text("editor churn\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ignored scratch excluded", result.stdout)
            self.assertIn("target", result.stdout)

    def test_rejects_new_builtin_scratch_root_created_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            (workspace / ".gitignore").write_text("target/\n", encoding="utf-8")
            _git(workspace, "add", ".gitignore")
            _git(workspace, "commit", "-qm", "ignore target")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            target = workspace / "target"
            target.mkdir()
            (target / "artifact").write_text("unexpected\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("new scratch root appeared during the run", result.stderr)

    def test_operator_can_exclude_exact_git_ignored_ambient_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            (workspace / ".gitignore").write_text(".idea/\n", encoding="utf-8")
            _git(workspace, "add", ".gitignore")
            _git(workspace, "commit", "-qm", "ignore editor state")
            idea = workspace / ".idea"
            idea.mkdir()
            state = idea / "workspace.xml"
            state.write_text("before\n", encoding="utf-8")
            baseline = root / "baseline.json"
            snapshot = _run(
                "snapshot",
                workspace,
                "--output",
                baseline,
                "--package-root",
                package,
                "--exclude-scratch",
                ".idea",
            )
            self.assertEqual(snapshot.returncode, 0, snapshot.stderr)

            state.write_text("after\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(".idea", result.stdout)

    def test_operator_can_predeclare_absent_trailing_slash_ignored_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            (workspace / ".gitignore").write_text("build/\n", encoding="utf-8")
            _git(workspace, "add", ".gitignore")
            _git(workspace, "commit", "-qm", "ignore absent build output")
            baseline = root / "baseline.json"

            snapshot = _run(
                "snapshot",
                workspace,
                "--output",
                baseline,
                "--package-root",
                package,
                "--exclude-scratch",
                "build",
            )

            self.assertEqual(snapshot.returncode, 0, snapshot.stderr)
            payload = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertIn("build", payload["scratch_exclusions"])

    def test_rejects_unignored_or_protected_scratch_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)

            unignored = _run(
                "snapshot",
                workspace,
                "--output",
                root / "unignored.json",
                "--package-root",
                package,
                "--exclude-scratch",
                "out",
            )
            self.assertEqual(unignored.returncode, 2)
            self.assertIn("not covered by Git ignore", unignored.stderr)

            protected = _run(
                "snapshot",
                workspace,
                "--output",
                root / "protected.json",
                "--package-root",
                package,
                "--exclude-scratch",
                "Cargo.lock",
            )
            self.assertEqual(protected.returncode, 2)
            self.assertIn("protected repository content", protected.stderr)

    def test_rejects_new_git_ignored_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            (workspace / ".gitignore").write_text("Cargo.lock\n", encoding="utf-8")
            _git(workspace, "add", ".gitignore")
            _git(workspace, "commit", "-qm", "ignore library lockfile")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            (workspace / "Cargo.lock").write_text("version = 4\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("Cargo.lock", result.stderr)

    def test_rejects_changes_in_git_ignored_production_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            (workspace / ".gitignore").write_text("src/generated/\n", encoding="utf-8")
            _git(workspace, "add", ".gitignore")
            _git(workspace, "commit", "-qm", "ignore generated production")
            generated = workspace / "src" / "generated"
            generated.mkdir()
            source = generated / "schema.rs"
            source.write_text("pub const VERSION: u8 = 1;\n", encoding="utf-8")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            source.write_text("pub const VERSION: u8 = 2;\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("src/generated/schema.rs", result.stderr)

    def test_rejects_changes_inside_initialized_submodule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            submodule_source = root / "submodule-source"
            _package(submodule_source)
            _git(
                workspace,
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "-q",
                str(submodule_source),
                "vendor/inner",
            )
            _git(workspace, "commit", "-qam", "add submodule")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            nested_source = workspace / "vendor" / "inner" / "src" / "lib.rs"
            nested_source.write_text("pub fn answer() -> u8 { 7 }\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("vendor/inner/src/lib.rs", result.stderr)
            self.assertIn("submodule gitlinks inspected", result.stdout)

    def test_snapshot_records_special_entries_without_aborting(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are unavailable")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            os.mkfifo(tests / "existing.fifo")
            baseline = root / "baseline.json"

            result = _snapshot(workspace, baseline, package)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"]["tests/existing.fifo"]["kind"], "other")

    def test_requires_exact_approval_to_delete_clean_tracked_test(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            test_file = tests / "redundant.rs"
            test_file.write_text("#[test]\nfn redundant() {}\n", encoding="utf-8")
            _git(workspace, "add", "tests/redundant.rs")
            _git(workspace, "commit", "-qm", "add redundant test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)
            test_file.unlink()

            unapproved = _run("verify", workspace, "--baseline", baseline)
            self.assertEqual(unapproved.returncode, 1)
            self.assertIn("unapproved clean test deletion", unapproved.stderr)

            approved = _run(
                "verify",
                workspace,
                "--baseline",
                baseline,
                "--allow-test-deletion",
                "tests/redundant.rs",
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)

    def test_clean_tracked_test_move_still_requires_deletion_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            original = tests / "old_name.rs"
            original.write_text("#[test]\nfn still_present() {}\n", encoding="utf-8")
            _git(workspace, "add", "tests/old_name.rs")
            _git(workspace, "commit", "-qm", "add movable test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            original.rename(tests / "new_name.rs")
            unapproved = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(unapproved.returncode, 1)
            self.assertIn("unapproved clean test deletion", unapproved.stderr)
            self.assertIn("clean moves=1", unapproved.stdout)
            self.assertIn("clean move: tests/old_name.rs -> tests/new_name.rs", unapproved.stdout)

            approved = _run(
                "verify",
                workspace,
                "--baseline",
                baseline,
                "--allow-test-deletion",
                "tests/old_name.rs",
            )

            self.assertEqual(approved.returncode, 0, approved.stderr)
            self.assertIn("clean move: tests/old_name.rs -> tests/new_name.rs", approved.stdout)

    def test_same_suffix_content_match_does_not_waive_deletion_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            original = tests / "old_case.rs"
            original.write_text("// SPDX-License-Identifier: MIT\n", encoding="utf-8")
            _git(workspace, "add", "tests/old_case.rs")
            _git(workspace, "commit", "-qm", "add important test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            original.unlink()
            fixtures = tests / "fixtures"
            fixtures.mkdir()
            (fixtures / "header_sample.rs").write_text("// SPDX-License-Identifier: MIT\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("unapproved clean test deletion", result.stderr)
            self.assertIn("clean move: tests/old_case.rs -> tests/fixtures/header_sample.rs", result.stdout)

    def test_identical_fixture_bytes_do_not_disguise_test_deletion_as_move(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            original = tests / "old_name.rs"
            original.write_text("same bytes\n", encoding="utf-8")
            _git(workspace, "add", "tests/old_name.rs")
            _git(workspace, "commit", "-qm", "add test source")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            original.unlink()
            fixtures = tests / "fixtures"
            fixtures.mkdir()
            (fixtures / "snippet.txt").write_text("same bytes\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("unapproved clean test deletion", result.stderr)
            self.assertIn("clean moves=0", result.stdout)

    def test_zero_byte_files_never_pair_as_moves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            original = tests / "empty.rs"
            original.write_bytes(b"")
            _git(workspace, "add", "tests/empty.rs")
            _git(workspace, "commit", "-qm", "add empty test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            original.unlink()
            (tests / "other.rs").write_bytes(b"")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("unapproved clean test deletion", result.stderr)
            self.assertIn("clean moves=0", result.stdout)

    def test_file_replaced_by_directory_requires_deletion_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            original = tests / "important_test.rs"
            original.write_text("#[test]\nfn important() {}\n", encoding="utf-8")
            _git(workspace, "add", "tests/important_test.rs")
            _git(workspace, "commit", "-qm", "add important test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            original.unlink()
            original.mkdir()
            (original / "subtest.rs").write_text("// replacement\n", encoding="utf-8")
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("unapproved clean test deletion", result.stderr)

    def test_file_replaced_by_symlink_is_classified_as_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            original = tests / "important_test.rs"
            original.write_text("#[test]\nfn important() {}\n", encoding="utf-8")
            _git(workspace, "add", "tests/important_test.rs")
            _git(workspace, "commit", "-qm", "add important test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            original.unlink()
            target = tests / "replacement.rs"
            target.write_text("// replacement\n", encoding="utf-8")
            original.symlink_to(target.name)
            result = _run("verify", workspace, "--baseline", baseline)

            self.assertEqual(result.returncode, 1)
            self.assertIn("unapproved clean test deletion", result.stderr)

    def test_approved_move_is_observed_and_not_reported_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            original = tests / "old_name.rs"
            original.write_text("#[test]\nfn moved() {}\n", encoding="utf-8")
            _git(workspace, "add", "tests/old_name.rs")
            _git(workspace, "commit", "-qm", "add test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            original.rename(tests / "new_name.rs")
            result = _run(
                "verify",
                workspace,
                "--baseline",
                baseline,
                "--allow-test-deletion",
                "tests/old_name.rs",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("clean move: tests/old_name.rs -> tests/new_name.rs", result.stdout)

    def test_stale_approval_does_not_hide_production_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            approved = tests / "approved.rs"
            approved.write_text("#[test]\nfn kept() {}\n", encoding="utf-8")
            _git(workspace, "add", "tests/approved.rs")
            _git(workspace, "commit", "-qm", "add test")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)

            (package / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 7 }\n", encoding="utf-8")
            result = _run(
                "verify",
                workspace,
                "--baseline",
                baseline,
                "--allow-test-deletion",
                "tests/approved.rs",
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("src/lib.rs", result.stderr)
            self.assertIn("approved deletion was not observed", result.stderr)

    def test_never_allows_deleting_dirty_or_untracked_test_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            package = _package(workspace)
            tests = package / "tests"
            tests.mkdir()
            test_file = tests / "draft.rs"
            test_file.write_text("#[test]\nfn draft() {}\n", encoding="utf-8")
            baseline = root / "baseline.json"
            self.assertEqual(_snapshot(workspace, baseline, package).returncode, 0)
            test_file.unlink()

            result = _run(
                "verify",
                workspace,
                "--baseline",
                baseline,
                "--allow-test-deletion",
                "tests/draft.rs",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("baseline-dirty or untracked", result.stderr)


if __name__ == "__main__":
    unittest.main()
