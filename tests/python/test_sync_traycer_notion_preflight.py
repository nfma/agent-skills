from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPOSITORY_ROOT / "evals/sync-traycer-notion/codex-single-candidate-preflight.py"
CURSOR_ENVIRONMENT = REPOSITORY_ROOT / "evals/sync-traycer-notion/CURSOR_ENVIRONMENT.md"


def load_preflight() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_traycer_notion_codex_preflight", PREFLIGHT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Codex preflight from {PREFLIGHT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prompt_input(*entries: tuple[str, str, str]) -> bytes:
    roots: dict[str, str] = {}
    lines: list[str] = []
    for index, (name, description, path) in enumerate(entries):
        alias = f"r{index}"
        skill_path = Path(path)
        roots[alias] = str(skill_path.parent.parent)
        lines.append(f"- {name}: {description} (file: {alias}/{skill_path.parent.name}/SKILL.md)")
    root_lines = [f"- `{alias}` = `{path}`" for alias, path in roots.items()]
    block = "\n".join(
        [
            "<skills_instructions>",
            "## Skills",
            "### Skill roots",
            *root_lines,
            "### Available skills",
            *lines,
            "</skills_instructions>",
        ]
    )
    return json.dumps(
        [
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": block}],
            }
        ]
    ).encode()


class CodexSingleCandidatePreflightTests(unittest.TestCase):
    preflight: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.preflight = load_preflight()

    def make_candidate(self, root: Path) -> Path:
        candidate = root / ".agents/skills/sync-traycer-notion/SKILL.md"
        candidate.parent.mkdir(parents=True)
        candidate.write_text(
            '---\nname: sync-traycer-notion\ndescription: "Project candidate"\n---\n\n# Test\n',
            encoding="utf-8",
        )
        return candidate

    def test_filters_competing_same_name_path_and_proves_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = self.make_candidate(workspace).resolve()
            global_candidate = workspace / "global/sync-traycer-notion/SKILL.md"
            before = self.preflight.Diagnostic(
                raw_sha256="before",
                entries=(
                    self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate),
                    self.preflight.SkillEntry("sync-traycer-notion", "Global candidate", global_candidate),
                ),
            )
            after = self.preflight.Diagnostic(
                raw_sha256="after",
                entries=(self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate),),
            )

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", side_effect=[before, after]) as run_codex,
                mock.patch.object(self.preflight, "codex_version", return_value="codex-cli test"),
            ):
                result = self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                )

            self.assertTrue(result["passed"])
            self.assertEqual(result["model_calls"], 0)
            self.assertEqual(result["candidate"]["bundle_sha256"], self.preflight.sha256_bundle(candidate.parent))
            self.assertEqual(result["filter"]["disabled_paths"], [str(global_candidate)])
            override = run_codex.call_args_list[1].args[3]
            self.assertIn(str(global_candidate), override)
            self.assertNotIn(str(candidate), override)

    def test_prompt_parser_resolves_skill_root_aliases(self) -> None:
        candidate = Path("/workspace/project/.agents/skills/sync-traycer-notion/SKILL.md")
        raw = prompt_input(("sync-traycer-notion", "Project candidate", str(candidate)))

        entries = self.preflight.parse_skill_entries(raw, "sync-traycer-notion")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path, candidate.resolve())
        self.assertEqual(entries[0].description, "Project candidate")

    def test_fails_when_intended_candidate_is_not_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = self.make_candidate(workspace).resolve()
            wrong = workspace / "global/sync-traycer-notion/SKILL.md"
            diagnostic = self.preflight.Diagnostic(
                raw_sha256="before",
                entries=(self.preflight.SkillEntry("sync-traycer-notion", "Wrong", wrong),),
            )

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", return_value=diagnostic),
                self.assertRaisesRegex(self.preflight.PreflightError, "intended candidate exactly once"),
            ):
                self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                )

    def test_fails_when_competing_candidate_remains_after_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = self.make_candidate(workspace).resolve()
            competing = workspace / "global/sync-traycer-notion/SKILL.md"
            duplicate_state = self.preflight.Diagnostic(
                raw_sha256="duplicate",
                entries=(
                    self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate),
                    self.preflight.SkillEntry("sync-traycer-notion", "Competing", competing),
                ),
            )

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", return_value=duplicate_state),
                self.assertRaisesRegex(self.preflight.PreflightError, "single candidate"),
            ):
                self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                )

    def test_codex_diagnostic_never_uses_exec(self) -> None:
        candidate = Path("/workspace/project/.agents/skills/sync-traycer-notion/SKILL.md")
        completed = self.preflight.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=prompt_input(("sync-traycer-notion", "Project candidate", str(candidate))),
            stderr=b"",
        )

        with mock.patch.object(self.preflight.subprocess, "run", return_value=completed) as run:
            self.preflight.run_codex(
                Path("/usr/bin/codex"),
                Path("/workspace/project"),
                self.preflight.DEFAULT_PROMPT,
                None,
                "sync-traycer-notion",
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/codex", "debug", "prompt-input"])
        self.assertNotIn("exec", command)

    def test_cursor_design_requires_isolated_native_roots_and_exact_read_proof(self) -> None:
        design = CURSOR_ENVIRONMENT.read_text(encoding="utf-8")

        self.assertIn("disposable macOS account or VM", design)
        for root in (".agents/skills", ".cursor/skills", ".claude/skills", ".codex/skills"):
            self.assertIn(root, design)
        self.assertIn("Do not override `HOME`", design)
        self.assertIn("exact read of the physical", design)
        self.assertIn("Do not run this gate", design)


if __name__ == "__main__":
    unittest.main()
