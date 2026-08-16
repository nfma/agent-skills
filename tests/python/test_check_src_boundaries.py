from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = REPOSITORY_ROOT / "skills" / "write-production-rust" / "scripts"
sys.path.insert(0, str(SCRIPT_ROOT))

import check_src_boundaries  # noqa: E402


class CheckSrcBoundariesTests(unittest.TestCase):
    def test_accepts_production_source_and_ignores_external_tests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "crate" / "src" / "lib.rs"
            external_test = root / "crate" / "tests" / "api.rs"
            fixture_source = root / "tests" / "fixtures" / "sample" / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            external_test.parent.mkdir(parents=True)
            fixture_source.parent.mkdir(parents=True)
            source.write_text("pub fn doubled(values: &[u32]) -> Vec<u32> { values.iter().map(|v| v * 2).collect() }\n")
            external_test.write_text("#[test]\nfn public_api() {}\n")
            fixture_source.write_text("#[cfg(test)]\nmod tests {}\n")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = check_src_boundaries.main([str(root)])

            self.assertEqual(result, 0)
            self.assertIn("checked 1 source file", output.getvalue())

    def test_rejects_inline_test_configuration_and_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text("#[cfg(test)]\nmod tests {\n    #[test]\n    fn example() {}\n}\n")

            violations = check_src_boundaries.scan_file(source)
            rules = {violation.rule for violation in violations}

            self.assertIn("test-cfg", rules)
            self.assertIn("test-module", rules)
            self.assertIn("test-attribute", rules)

    def test_rejects_test_module_and_path_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "adapter.rs"
            source.parent.mkdir(parents=True)
            source.write_text('use crate::tests::Fixture;\nconst DATA: &str = include_str!("../tests/data.json");\n')

            violations = check_src_boundaries.scan_file(source)
            rules = {violation.rule for violation in violations}

            self.assertIn("test-module-path", rules)
            self.assertIn("test-path-literal", rules)

    def test_rejects_test_components_in_ordinary_byte_and_raw_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "assets.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                'const ONE: &str = "../tests/data.json";\n'
                'const TWO: &[u8] = b"test";\n'
                'const THREE: &str = r#"fixtures/tests/sample"#;\n'
                'const SAFE: &str = "latest/results.json";\n'
            )

            violations = check_src_boundaries.scan_file(source)

            path_violations = [violation for violation in violations if violation.rule == "test-path-literal"]
            self.assertEqual(len(path_violations), 3)

    def test_rejects_test_subtree_below_src(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "tests" / "helper.rs"
            source.parent.mkdir(parents=True)
            source.write_text("pub fn helper() {}\n")

            violations = check_src_boundaries.scan_file(source)

            self.assertIn("test-source-path", {violation.rule for violation in violations})

    def test_rejects_explicit_rust_file_outside_src(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "tests" / "api.rs"
            source.parent.mkdir(parents=True)
            source.write_text("fn example() {}\n")

            error_output = io.StringIO()
            with contextlib.redirect_stderr(error_output):
                result = check_src_boundaries.main([str(source)])

            self.assertEqual(result, 2)
            self.assertIn("outside src/", error_output.getvalue())


if __name__ == "__main__":
    unittest.main()
