from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPOSITORY_ROOT / "skills/write-production-rust/scripts/run_evals.py"
CASE_PACK = REPOSITORY_ROOT / "skills/write-production-rust/assets/trigger-behavior-evals.json"


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("write_production_rust_eval_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load evaluation runner from {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event_line(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def valid_trace(*, advertised_skills: list[str], tools: list[str], invoked_skill: bool) -> bytes:
    events: list[dict[str, object]] = [
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-test",
            "session_id": "session-test",
            "skills": advertised_skills,
            "tools": tools,
            "mcp_servers": [],
        }
    ]
    if invoked_skill:
        events.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "write-production-rust"},
                        }
                    ]
                },
            }
        )
    events.append(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "final response",
        }
    )
    return b"".join(event_line(event) for event in events)


class WriteProductionRustEvalTests(unittest.TestCase):
    runner: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_case_pack_has_unnamed_positive_near_miss_and_behavior_cases(self) -> None:
        _pack, trigger_cases, behavior_cases = self.runner.validated_case_pack(CASE_PACK)

        self.assertGreaterEqual(len(trigger_cases), 3)
        self.assertGreaterEqual(len(behavior_cases), 3)
        for case in trigger_cases:
            self.assertNotIn("write-production-rust", case["positive"].casefold())
            self.assertNotIn("write-production-rust", case["near_miss"].casefold())
        for case in behavior_cases:
            self.assertNotIn("write-production-rust", case["prompt"].casefold())

    def test_positive_trace_requires_host_observed_skill_invocation(self) -> None:
        events = self.runner.parse_stream_json(
            valid_trace(
                advertised_skills=["write-production-rust"],
                tools=["Read", "Skill"],
                invoked_skill=True,
            )
        )
        summary = self.runner.trace_summary(events)

        self.assertEqual(self.runner.validate_trigger_trace(summary, "with_skill", "positive"), [])

    def test_near_miss_requires_discovery_without_invocation(self) -> None:
        events = self.runner.parse_stream_json(
            valid_trace(
                advertised_skills=["write-production-rust"],
                tools=["Read", "Skill"],
                invoked_skill=False,
            )
        )
        summary = self.runner.trace_summary(events)

        self.assertEqual(self.runner.validate_trigger_trace(summary, "with_skill", "near_miss"), [])

    def test_zero_tool_behavior_rejects_any_tool_surface(self) -> None:
        clean = self.runner.trace_summary(
            self.runner.parse_stream_json(valid_trace(advertised_skills=[], tools=[], invoked_skill=False))
        )
        exposed = self.runner.trace_summary(
            self.runner.parse_stream_json(
                valid_trace(advertised_skills=[], tools=["Read", "Skill"], invoked_skill=False)
            )
        )

        self.assertEqual(self.runner.validate_zero_tool_trace(clean), [])
        self.assertTrue(self.runner.validate_zero_tool_trace(exposed))

    def test_behavior_arm_injects_guidance_only_into_treatment(self) -> None:
        _pack, _trigger_cases, behavior_cases = self.runner.validated_case_pack(CASE_PACK)
        case = behavior_cases[0]

        baseline, baseline_digest, baseline_files = self.runner.render_behavior_prompt(case, "baseline")
        treatment, treatment_digest, treatment_files = self.runner.render_behavior_prompt(case, "with_skill")

        self.assertIn(case["prompt"], baseline)
        self.assertIn(case["prompt"], treatment)
        self.assertIsNone(baseline_digest)
        self.assertEqual(baseline_files, {})
        self.assertIsNotNone(treatment_digest)
        self.assertIn("SKILL.md", treatment_files)
        self.assertNotIn("frozen production guidance", baseline)
        self.assertIn("frozen production guidance", treatment)

    def test_semantic_grade_must_match_all_criteria_and_computed_winner(self) -> None:
        criteria = [{"id": "one", "text": "First"}, {"id": "two", "text": "Second"}]
        response = json.dumps(
            {
                "scores": {"A": {"one": 2, "two": 1}, "B": {"one": 1, "two": 1}},
                "winner": "A",
                "rationale": "A is more complete.",
            }
        )

        grade = self.runner.parse_grade(response, criteria)

        self.assertEqual(grade["totals"], {"A": 3, "B": 2})
        self.assertEqual(self.runner.parse_grade(f"```json\n{response}\n```", criteria), grade)
        incorrect_winner = response.replace('"winner": "A"', '"winner": "B"')
        with self.assertRaisesRegex(self.runner.EvalError, "winner disagrees"):
            self.runner.parse_grade(incorrect_winner, criteria)
        with self.assertRaisesRegex(self.runner.EvalError, "invalid JSON fence"):
            self.runner.parse_grade(f"```json\n{response}\n```\nextra", criteria)

    def test_raw_outputs_and_semantic_key_must_stay_outside_repository(self) -> None:
        with self.assertRaisesRegex(self.runner.EvalError, "outside the repository"):
            self.runner.require_external_input(CASE_PACK, "semantic key")
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            parser = self.runner.build_parser()
            run_arguments = parser.parse_args(["run"])
            arguments = parser.parse_args(
                [
                    "grade",
                    "--run-manifest",
                    str(temporary_root / "run-manifest.json"),
                    "--key",
                    str(temporary_root / "semantic-key.json"),
                ]
            )
            self.assertFalse(hasattr(run_arguments, "output_dir"))
            self.assertFalse(hasattr(arguments, "output"))

            external = self.runner.create_external_output_directory(temporary_root)
            self.assertEqual(external.parent, temporary_root.resolve())
            self.assertTrue(external.name.startswith(f"{self.runner.SUITE_NAME}-"))


if __name__ == "__main__":
    unittest.main()
