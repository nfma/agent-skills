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

    def test_cfg_feature_names_are_not_test_predicate_atoms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                '#[cfg(feature = "integration-test")]\n'
                "pub fn integration_feature() {}\n"
                '#[cfg_attr(feature = "test-util", allow(dead_code))]\n'
                "pub fn utility_feature() {}\n"
                '#[cfg(feature = "testing")]\n'
                "pub fn testing_feature() {}\n"
                '#[cfg(feature = "rstest")]\n'
                "pub fn rstest_feature() {}\n"
            )

            self.assertEqual(check_src_boundaries.scan_file(source), [])

    def test_rejects_cfg_test_atoms_in_attributes_and_macro(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                "#[cfg(test)]\n"
                "fn unit_test_configuration() {}\n"
                "#[cfg(doctest)]\n"
                "fn doctest_configuration() {}\n"
                '#[cfg_attr(feature = "x", test)]\n'
                "fn conditional_test_attribute() {}\n"
                "const TEST_CONFIGURATION: bool = cfg!(test);\n"
            )

            violations = check_src_boundaries.scan_file(source)

            self.assertEqual(sum(violation.rule == "test-cfg" for violation in violations), 3)
            self.assertEqual(sum(violation.rule == "test-cfg-macro" for violation in violations), 1)

    def test_rejects_test_attributes_with_bracketed_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                "#[test_case(&[1, 2, 3])]\n"
                "fn bracketed_case() {}\n"
                "#[rstest(cases = [1, 2])]\n"
                "fn bracketed_rstest() {}\n"
                "#[testing]\n"
                "fn production_attribute() {}\n"
                '#[serde(rename = "test")]\n'
                "fn serialized_name() {}\n"
            )

            violations = check_src_boundaries.scan_file(source)

            self.assertEqual(sum(violation.rule == "test-attribute" for violation in violations), 2)

    def test_rejects_test_framework_atoms_inside_cfg_attr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                '#[cfg_attr(feature = "extra", rstest)]\n'
                "fn conditional_rstest() {}\n"
                '#[cfg_attr(feature = "extra", proptest)]\n'
                "fn conditional_proptest() {}\n"
            )

            violations = check_src_boundaries.scan_file(source)

            self.assertEqual(sum(violation.rule == "test-cfg" for violation in violations), 2)

    def test_ignores_test_boundary_text_in_comments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                "//! Integration tests live in `tests/`; do not add `mod tests` here.\n"
                '// const DATA: &str = "../tests/data.json";\n'
                "/* #[cfg(test)]\nmod tests {}\n*/\n"
                "pub fn production() {}\n"
            )

            self.assertEqual(check_src_boundaries.scan_file(source), [])

    def test_url_string_does_not_hide_following_code_as_a_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text('const URL: &str = "https://example.com"; mod tests {}\n')

            violations = check_src_boundaries.scan_file(source)

            self.assertIn("test-module", {violation.rule for violation in violations})

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

    def test_rejects_raw_test_module_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text("mod r#tests;\nuse crate::r#tests::Helper;\n")

            violations = check_src_boundaries.scan_file(source)

            self.assertIn("test-module", {violation.rule for violation in violations})
            self.assertIn("test-module-path", {violation.rule for violation in violations})

    def test_rejects_terminal_nested_test_module_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text("use crate::submodule::tests;\nuse submodule::tests;\n")

            violations = check_src_boundaries.scan_file(source)

            self.assertEqual(sum(violation.rule == "test-module-path" for violation in violations), 2)

    def test_test_method_paths_remain_production_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                "impl Device { fn test(&self) -> bool { true } }\n"
                "fn probe(p: &Device, x: usize) {\n"
                "    P::test(p, x);\n"
                "    helpers::test();\n"
                "    Pred::test(p);\n"
                "    crate::diagnostics::test();\n"
                "    self::diagnostics::test();\n"
                "    Device::test(p);\n"
                "}\n"
                'const LABEL: &str = "::test";\n'
                "use submodule::test;\n"
                "use crate::submodule::test;\n"
            )

            self.assertEqual(check_src_boundaries.scan_file(source), [])

    def test_rejects_plural_or_nonterminal_test_module_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text(
                "use crate::submodule::tests;\n"
                "use submodule::tests;\n"
                "use crate::tests::Fixture;\n"
                "use crate::r#tests::Helper;\n"
                "fn call() { a::test::b(); }\n"
                'const LABEL: &str = "api::tests";\n'
            )

            violations = check_src_boundaries.scan_file(source)

            self.assertEqual(sum(violation.rule == "test-module-path" for violation in violations), 6)

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

    def test_char_literal_does_not_hide_following_test_path_literal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_text("""fn f<'a>(_: &'a str) { let q = '"'; let p = "../tests/data.json"; }\n""")

            violations = check_src_boundaries.scan_file(source)

            self.assertIn("test-path-literal", {violation.rule for violation in violations})

    def test_raw_string_with_arbitrary_hash_count_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            hashes = "#" * 300
            source.write_text(f'const DATA: &str = r{hashes}"fixtures/tests/sample"{hashes};\n')

            violations = check_src_boundaries.scan_file(source)

            self.assertIn("test-path-literal", {violation.rule for violation in violations})

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

    def test_non_utf8_source_is_a_concise_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "src" / "lib.rs"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"\xff")

            error_output = io.StringIO()
            with contextlib.redirect_stderr(error_output):
                result = check_src_boundaries.main([str(source)])

            message = error_output.getvalue()
            self.assertEqual(result, 2)
            self.assertIn(f"not valid UTF-8: {source}", message)
            self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
