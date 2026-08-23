import ast
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPOSITORY_ROOT / "skills"
SERVICES_ROOT = REPOSITORY_ROOT / "services"


class PythonScriptTests(unittest.TestCase):
    def test_every_python_script_compiles(self) -> None:
        scripts = sorted([*SERVICES_ROOT.rglob("*.py"), *SKILLS_ROOT.rglob("*.py")])
        self.assertGreater(len(scripts), 0, "expected Python scripts under skills/")

        for script in scripts:
            with self.subTest(script=script.relative_to(REPOSITORY_ROOT)):
                source = script.read_text(encoding="utf-8")
                compile(source, str(script), "exec")

    def test_exception_handlers_do_not_silently_pass(self) -> None:
        scripts = sorted(SKILLS_ROOT.rglob("*.py"))

        for script in scripts:
            with self.subTest(script=script.relative_to(REPOSITORY_ROOT)):
                tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
                silent_handlers = [
                    node.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ExceptHandler)
                    and len(node.body) == 1
                    and isinstance(node.body[0], ast.Pass)
                ]
                self.assertEqual(silent_handlers, [])

    def test_training_examples_share_trackio_dashboard_helper(self) -> None:
        scripts_dir = SKILLS_ROOT / "train-sentence-transformers" / "scripts"
        examples = sorted(scripts_dir.glob("train_*_example.py"))

        self.assertEqual(len(examples), 12)
        for script in examples:
            with self.subTest(script=script.name):
                tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
                imports_helper = any(
                    isinstance(node, ast.ImportFrom)
                    and node.module == "trackio_dashboard"
                    and any(alias.name == "log_trackio_dashboard" for alias in node.names)
                    for node in tree.body
                )
                defines_helper = any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "log_trackio_dashboard"
                    for node in tree.body
                )

                self.assertTrue(imports_helper)
                self.assertFalse(defines_helper)


if __name__ == "__main__":
    unittest.main()
