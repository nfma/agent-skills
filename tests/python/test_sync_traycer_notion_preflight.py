from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPOSITORY_ROOT / "evals/sync-traycer-notion/codex-single-candidate-preflight.py"
CURSOR_ENVIRONMENT = REPOSITORY_ROOT / "evals/sync-traycer-notion/CURSOR_ENVIRONMENT.md"


def load_preflight() -> Any:
    spec = importlib.util.spec_from_file_location("sync_traycer_notion_codex_preflight", PREFLIGHT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Codex preflight from {PREFLIGHT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def prompt_input(
    *entries: tuple[str, str, str],
    use_aliases: bool = True,
    user_prompt: str = "test prompt",
    before_available_skills: tuple[str, ...] = (),
) -> bytes:
    roots: dict[str, str] = {}
    lines: list[str] = []
    for index, (name, description, path) in enumerate(entries):
        alias = f"r{index}"
        skill_path = Path(path)
        if use_aliases:
            roots[alias] = str(skill_path.parent.parent)
            locator = f"{alias}/{skill_path.parent.name}/SKILL.md"
        else:
            locator = str(skill_path)
        lines.append(f"- {name}: {description} (file: {locator})")
    root_lines = [f"- `{alias}` = `{path}`" for alias, path in roots.items()] if use_aliases else []
    block = "\n".join(
        [
            "<skills_instructions>",
            "## Skills",
            "### Skill roots",
            *root_lines,
            *before_available_skills,
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
            },
            {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
        ]
    ).encode()


class CodexSingleCandidatePreflightTests(unittest.TestCase):
    preflight: Any

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

    def diagnostic(self, *entries: object, label: str = "diagnostic") -> object:
        return self.preflight.Diagnostic(
            raw_sha256=f"{label}-raw",
            skills_instructions_sha256=f"{label}-skills",
            entries=entries,
        )

    def test_filters_competing_same_name_path_and_proves_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = self.make_candidate(workspace).resolve()
            global_candidate = workspace / "global/sync-traycer-notion/SKILL.md"
            bundled = self.preflight.SkillEntry(
                "skill-creator",
                "Bundled creator",
                Path("/opt/codex/skills/skill-creator/SKILL.md"),
            )
            before = self.diagnostic(
                self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate),
                self.preflight.SkillEntry("sync-traycer-notion", "Global candidate", global_candidate),
                bundled,
                label="before",
            )
            after = self.diagnostic(
                self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate),
                bundled,
                label="after",
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
            self.assertEqual(result["schema_version"], 2)
            self.assertEqual(result["before"]["inventory"]["count"], 3)
            self.assertEqual(result["after"]["inventory"]["count"], 2)
            self.assertFalse(result["verification"]["verified"])
            self.assertEqual(result["candidate"]["bundle_sha256"], self.preflight.sha256_bundle(candidate.parent))
            self.assertEqual(result["filter"]["disabled_paths"], [str(global_candidate)])
            override = run_codex.call_args_list[1].args[3]
            self.assertIn(str(global_candidate), override)
            self.assertNotIn(str(candidate), override)

    def test_prompt_parser_resolves_aliases_and_namespaced_skill_names(self) -> None:
        candidate = Path("/workspace/project/.agents/skills/sync-traycer-notion/SKILL.md")
        github_skill = Path("/plugins/github/skills/github/SKILL.md")
        raw = prompt_input(
            ("sync-traycer-notion", "Project candidate", str(candidate)),
            ("github:github", "GitHub umbrella", str(github_skill)),
        )

        entries = self.preflight.parse_diagnostic(raw).entries

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].path, candidate.resolve())
        self.assertEqual(entries[0].description, "Project candidate")
        self.assertEqual(entries[1].name, "github:github")

    def test_prompt_parser_resolves_absolute_locators(self) -> None:
        candidate = Path("/workspace/project/.agents/skills/sync-traycer-notion/SKILL.md")
        raw = prompt_input(
            ("sync-traycer-notion", "Project candidate", str(candidate)),
            use_aliases=False,
        )

        diagnostic = self.preflight.parse_diagnostic(raw)

        self.assertEqual(diagnostic.entries[0].path, candidate.resolve())
        self.assertEqual(diagnostic.entries[0].name, "sync-traycer-notion")

    def test_prompt_parser_rejects_skill_entry_before_available_skills(self) -> None:
        candidate = Path("/workspace/project/.agents/skills/sync-traycer-notion/SKILL.md")
        for locator_kind in (
            "file",
            "environment resource",
            "orchestrator resource",
            "custom resource",
        ):
            with self.subTest(locator_kind=locator_kind):
                misplaced = f"- hidden-skill: Hidden entry ({locator_kind}: {candidate})"
                raw = prompt_input(
                    ("sync-traycer-notion", "Project candidate", str(candidate)),
                    before_available_skills=(misplaced,),
                )

                with self.assertRaisesRegex(
                    self.preflight.PreflightError,
                    "non-root bullet appeared before the Available skills section",
                ):
                    self.preflight.parse_diagnostic(raw)

    def test_inventory_digest_is_independent_of_user_prompt(self) -> None:
        candidate = Path("/workspace/project/.agents/skills/sync-traycer-notion/SKILL.md")
        first = self.preflight.parse_diagnostic(
            prompt_input(
                ("sync-traycer-notion", "Project candidate", str(candidate)),
                user_prompt="first task",
            )
        )
        second = self.preflight.parse_diagnostic(
            prompt_input(
                ("sync-traycer-notion", "Project candidate", str(candidate)),
                user_prompt="different task",
            )
        )

        self.assertNotEqual(first.raw_sha256, second.raw_sha256)
        self.assertEqual(first.skills_instructions_sha256, second.skills_instructions_sha256)
        self.assertEqual(
            self.preflight.inventory_record(first.entries)["sha256"],
            self.preflight.inventory_record(second.entries)["sha256"],
        )

    def test_fails_when_intended_candidate_is_not_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = self.make_candidate(workspace).resolve()
            wrong = workspace / "global/sync-traycer-notion/SKILL.md"
            diagnostic = self.diagnostic(
                self.preflight.SkillEntry("sync-traycer-notion", "Wrong", wrong),
                label="before",
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
            duplicate_state = self.diagnostic(
                self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate),
                self.preflight.SkillEntry("sync-traycer-notion", "Competing", competing),
                label="duplicate",
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
            )

        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/codex", "debug", "prompt-input"])
        self.assertNotIn("exec", command)

    def test_codex_version_timeout_is_a_preflight_error(self) -> None:
        timeout = self.preflight.subprocess.TimeoutExpired(["codex", "--version"], 10)

        with (
            mock.patch.object(self.preflight.subprocess, "run", side_effect=timeout),
            self.assertRaisesRegex(self.preflight.PreflightError, "version check timed out"),
        ):
            self.preflight.codex_version(Path("/usr/bin/codex"))

    def test_capture_can_be_reverified_against_unchanged_full_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            candidate = self.make_candidate(workspace).resolve()
            bundled = self.preflight.SkillEntry(
                "skill-creator",
                "Bundled creator",
                Path("/opt/codex/skills/skill-creator/SKILL.md"),
            )
            state = self.diagnostic(
                self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate),
                bundled,
            )

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", return_value=state),
                mock.patch.object(self.preflight, "codex_version", return_value="codex-cli test"),
            ):
                capture = self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                )
                expected = root / "expected.json"
                expected.write_text(json.dumps(capture), encoding="utf-8")
                verified = self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    "a different evaluation prompt",
                    expected_evidence=expected,
                )

            self.assertTrue(verified["verification"]["verified"])
            self.assertEqual(verified["verification"]["mode"], "verify")
            self.assertEqual(verified["after"]["inventory"]["sha256"], capture["after"]["inventory"]["sha256"])

    def test_reverification_fails_when_unrelated_inventory_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            candidate = self.make_candidate(workspace).resolve()
            candidate_entry = self.preflight.SkillEntry(
                "sync-traycer-notion",
                "Project candidate",
                candidate,
            )
            original = self.diagnostic(candidate_entry, label="original")
            changed = self.diagnostic(
                candidate_entry,
                self.preflight.SkillEntry(
                    "skill-creator",
                    "New bundled skill",
                    Path("/opt/codex/skills/skill-creator/SKILL.md"),
                ),
                label="changed",
            )

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", return_value=original),
                mock.patch.object(self.preflight, "codex_version", return_value="codex-cli test"),
            ):
                capture = self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                )
            expected = root / "expected.json"
            expected.write_text(json.dumps(capture), encoding="utf-8")

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", return_value=changed),
                mock.patch.object(self.preflight, "codex_version", return_value="codex-cli test"),
                self.assertRaisesRegex(self.preflight.PreflightError, "frozen inventory mismatch"),
            ):
                self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                    expected_evidence=expected,
                )

    def test_reverification_rejects_non_object_inventory_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            candidate = self.make_candidate(workspace).resolve()
            state = self.diagnostic(self.preflight.SkillEntry("sync-traycer-notion", "Project candidate", candidate))

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", return_value=state),
                mock.patch.object(self.preflight, "codex_version", return_value="codex-cli test"),
            ):
                capture = self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                )

            expected = root / "expected.json"
            malformed_values: tuple[object, ...] = (None, "not an object", [])
            for section in ("before", "after"):
                for malformed in malformed_values:
                    with self.subTest(section=section, malformed=malformed):
                        record = json.loads(json.dumps(capture))
                        record[section] = malformed
                        expected.write_text(json.dumps(record), encoding="utf-8")
                        with self.assertRaisesRegex(
                            self.preflight.PreflightError,
                            "before and after sections must be JSON objects",
                        ):
                            self.preflight.verify_expected_evidence(capture, expected, workspace)

    def test_rejects_candidate_outside_project_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = workspace / ".codex/skills/sync-traycer-notion/SKILL.md"
            candidate.parent.mkdir(parents=True)
            candidate.write_text("content", encoding="utf-8")

            with self.assertRaisesRegex(self.preflight.PreflightError, "physical project copy"):
                self.preflight.require_physical_candidate(workspace, candidate, "sync-traycer-notion")

    def test_rejects_symlinked_candidate_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            source = workspace / "source"
            source.mkdir()
            (source / "SKILL.md").write_text("content", encoding="utf-8")
            candidate_bundle = workspace / ".agents/skills/sync-traycer-notion"
            candidate_bundle.parent.mkdir(parents=True)
            candidate_bundle.symlink_to(source, target_is_directory=True)

            with self.assertRaisesRegex(self.preflight.PreflightError, "physical project copy"):
                self.preflight.require_physical_candidate(
                    workspace,
                    candidate_bundle / "SKILL.md",
                    "sync-traycer-notion",
                )

    def test_rejects_nested_symlink_in_candidate_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = self.make_candidate(workspace)
            target = workspace / "outside"
            target.mkdir()
            (candidate.parent / "references").symlink_to(target, target_is_directory=True)

            with self.assertRaisesRegex(self.preflight.PreflightError, "must not contain symlinks"):
                self.preflight.require_physical_candidate(workspace, candidate, "sync-traycer-notion")

    def test_rejects_missing_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            skill_path = Path(temporary_directory) / "SKILL.md"
            skill_path.write_text("# Missing frontmatter\n", encoding="utf-8")

            with self.assertRaisesRegex(self.preflight.PreflightError, "missing YAML frontmatter"):
                self.preflight.read_frontmatter(skill_path)

    def test_rejects_rendered_description_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = self.make_candidate(workspace).resolve()
            mismatched = self.diagnostic(
                self.preflight.SkillEntry(
                    "sync-traycer-notion",
                    "Different rendered description",
                    candidate,
                )
            )

            with (
                mock.patch.object(self.preflight.shutil, "which", return_value="/usr/bin/true"),
                mock.patch.object(self.preflight, "run_codex", return_value=mismatched),
                mock.patch.object(self.preflight, "codex_version", return_value="codex-cli test"),
                self.assertRaisesRegex(self.preflight.PreflightError, "description does not match"),
            ):
                self.preflight.run_preflight(
                    workspace,
                    candidate,
                    "sync-traycer-notion",
                    self.preflight.DEFAULT_PROMPT,
                )

    def test_non_utf8_candidate_returns_json_failure_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory).resolve()
            candidate = workspace / ".agents/skills/sync-traycer-notion/SKILL.md"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"\xff")

            with (
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        str(PREFLIGHT),
                        "--workspace",
                        str(workspace),
                        "--candidate",
                        str(candidate),
                    ],
                ),
                mock.patch("builtins.print") as print_output,
            ):
                exit_code = self.preflight.main()

            record = json.loads(print_output.call_args.args[0])
            self.assertEqual(exit_code, 1)
            self.assertFalse(record["passed"])
            self.assertEqual(record["model_calls"], 0)
            self.assertIn("valid UTF-8", record["error"])

    def test_cursor_design_requires_isolated_native_roots_and_exact_read_proof(self) -> None:
        design = CURSOR_ENVIRONMENT.read_text(encoding="utf-8")

        self.assertIn("disposable macOS account or VM", design)
        for root in (
            "$EVAL_USER_HOME/.agents/skills",
            "$EVAL_USER_HOME/.cursor/skills",
            "$EVAL_USER_HOME/.claude/skills",
            "$EVAL_USER_HOME/.codex/skills",
        ):
            self.assertIn(root, design)
        self.assertIn("Do not override `HOME`", design)
        self.assertIn("exact read of the physical", design)
        self.assertIn("Do not run this gate", design)
        self.assertIn("full model-visible inventory", design)
        self.assertIn("Cursor lane unavailable; filesystem isolation alone", design)


if __name__ == "__main__":
    unittest.main()
