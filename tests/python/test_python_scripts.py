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


if __name__ == "__main__":
    unittest.main()
