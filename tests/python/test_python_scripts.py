import ast
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPOSITORY_ROOT / "skills"


class PythonScriptTests(unittest.TestCase):
    def test_every_python_script_compiles(self) -> None:
        scripts = sorted(SKILLS_ROOT.rglob("*.py"))
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


if __name__ == "__main__":
    unittest.main()
