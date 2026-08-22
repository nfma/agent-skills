from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/run-trigger-evals.py"
CASE_PACK = REPOSITORY_ROOT / "evals/sync-traycer-notion/suite.json"
PRODUCTION_KEY_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion/key-manifest.json"
CALIBRATION_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion/calibration-manifest.json"
EVIDENCE_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion/evidence-manifest.json"
SKILL = REPOSITORY_ROOT / "skills/sync-traycer-notion/SKILL.md"
ADAPTER = REPOSITORY_ROOT / "skills/sync-traycer-notion/references/notion-task-list.md"


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_traycer_notion_eval_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load eval runner from {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event_line(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def valid_trace(*, discovered: bool, invoke: bool, unexpected_tool: str | None = None) -> bytes:
    skills = ["sync-traycer-notion"] if discovered else []
    events: list[dict[str, object]] = [
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-opus-test",
            "session_id": "session-test",
            "skills": skills,
            "tools": ["Glob", "Grep", "Read", "Skill"],
            "mcp_servers": [],
        }
    ]
    if invoke:
        events.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Skill",
                            "input": {"skill": "sync-traycer-notion"},
                        }
                    ]
                },
            }
        )
    if unexpected_tool is not None:
        events.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": unexpected_tool,
                            "input": {},
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
            "result": '{"classification":"sync"}',
        }
    )
    return b"".join(event_line(event) for event in events)


class TriggerEvalRunnerTests(unittest.TestCase):
    runner: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_frozen_case_pack_is_valid_and_never_names_skill(self) -> None:
        pack, cases = self.runner.validated_case_pack(CASE_PACK)

        self.assertEqual(len(cases), 20)
        self.assertEqual(sum(case["variant"] == "positive" for case in cases), 10)
        self.assertEqual(sum(case["variant"] == "near_miss" for case in cases), 10)
        self.assertGreaterEqual(
            sum(task["kind"] == "positive" and task["class"] == "regression" for task in pack["tasks"]),
            2,
        )
        self.assertGreaterEqual(
            sum(task["kind"] == "near-miss" and task["class"] == "regression" for task in pack["tasks"]),
            2,
        )
        for case in cases:
            self.assertNotIn("sync-traycer-notion", case["prompt"].casefold())

    def test_production_manifests_bind_the_draft_suite(self) -> None:
        suite = json.loads(CASE_PACK.read_text(encoding="utf-8"))
        canonical = json.dumps(suite, separators=(",", ":"), sort_keys=True).encode()
        suite_digest = hashlib.sha256(canonical).hexdigest()
        key_manifest = json.loads(PRODUCTION_KEY_MANIFEST.read_text(encoding="utf-8"))
        calibration = json.loads(CALIBRATION_MANIFEST.read_text(encoding="utf-8"))
        evidence = json.loads(EVIDENCE_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(suite["status"], "draft")
        self.assertEqual(key_manifest["suite_canonical_sha256"], suite_digest)
        self.assertEqual(evidence["suite_canonical_sha256"], suite_digest)
        self.assertEqual(key_manifest["key_sha256"], "PENDING-COORDINATOR-SEAL")
        self.assertEqual(calibration["status"], "pending")
        self.assertEqual(calibration["clean_case_count"], 0)
        self.assertEqual(calibration["seeded_case_count"], 0)
        self.assertIsNone(calibration["critical_failure_recall"])
        self.assertIsNone(calibration["noncritical_failure_agreement"])
        self.assertIsNone(calibration["clean_acceptance_rate"])
        self.assertEqual(calibration["calibration_set_sha256"], "PENDING-COORDINATOR-SEAL")
        self.assertEqual(calibration["calibration_report_sha256"], "PENDING-COORDINATOR-SEAL")
        self.assertEqual(evidence["status"], "pending")
        expected_ids = {task["id"] for task in suite["tasks"] if task["kind"] == "positive"}
        self.assertEqual({case["task_id"] for case in key_manifest["cases"]}, expected_ids)

    def test_skill_uses_native_notion_subtask_relations(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        adapter = ADAPTER.read_text(encoding="utf-8")

        self.assertIn("native `Parent task`", skill)
        self.assertIn("reciprocal `Sub-task` relation", skill)
        self.assertIn("| `Parent task` | `Parent task` | relation, limit 1 |", adapter)
        self.assertIn("| `Sub-task` | `Sub-task` | reciprocal relation |", adapter)
        self.assertIn("Temporary compatibility mirror", adapter)

    def test_trace_proves_project_discovery_and_automatic_invocation(self) -> None:
        events = self.runner.parse_stream_json(valid_trace(discovered=True, invoke=True))
        summary = self.runner.trace_summary(events)

        self.assertEqual(self.runner.validate_trace_state(summary, "with_skill", "positive"), [])
        self.assertTrue(summary["discovered_target_skill"])
        self.assertEqual(summary["invoked_skills"], ["sync-traycer-notion"])

    def test_near_miss_requires_discovery_without_invocation(self) -> None:
        events = self.runner.parse_stream_json(valid_trace(discovered=True, invoke=False))
        summary = self.runner.trace_summary(events)

        self.assertEqual(self.runner.validate_trace_state(summary, "with_skill", "near_miss"), [])

    def test_baseline_rejects_leaked_skill_discovery(self) -> None:
        events = self.runner.parse_stream_json(valid_trace(discovered=True, invoke=False))
        summary = self.runner.trace_summary(events)

        errors = self.runner.validate_trace_state(summary, "baseline", "positive")

        self.assertTrue(any("discovery mismatch" in error for error in errors))

    def test_trace_rejects_unexpected_tool_use(self) -> None:
        events = self.runner.parse_stream_json(valid_trace(discovered=True, invoke=True, unexpected_tool="Bash"))
        summary = self.runner.trace_summary(events)

        errors = self.runner.validate_trace_state(summary, "with_skill", "positive")

        self.assertEqual(summary["unexpected_tools"], ["Bash"])
        self.assertTrue(any("unexpected tools" in error for error in errors))

    def test_parser_rejects_non_json_trace_lines(self) -> None:
        with self.assertRaisesRegex(self.runner.EvalError, "invalid stream JSON"):
            self.runner.parse_stream_json(b'{"type":"system"}\nnot-json\n')

    def test_check_evaluator_supports_sealed_check_kinds(self) -> None:
        response = "Update TASK-13 and do not create a replacement."

        self.assertTrue(self.runner.evaluate_check(response, {"kind": "contains", "value": "task-13"}))
        self.assertTrue(
            self.runner.evaluate_check(response, {"kind": "contains_none", "values": ["delete", "TASK-99"]})
        )
        self.assertTrue(self.runner.evaluate_check(response, {"kind": "regex", "pattern": r"update\s+TASK-13"}))

    def test_raw_output_must_be_outside_repository_and_empty(self) -> None:
        with self.assertRaisesRegex(self.runner.EvalError, "outside the repository"):
            self.runner.require_external_output_directory(REPOSITORY_ROOT / "eval-output")

        with tempfile.TemporaryDirectory() as temporary_directory:
            occupied = Path(temporary_directory) / "occupied"
            occupied.mkdir()
            (occupied / "sentinel").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(self.runner.EvalError, "absent or empty"):
                self.runner.require_external_output_directory(occupied)

    def test_timeout_is_frozen_as_a_failed_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with mock.patch.object(
                self.runner.subprocess,
                "run",
                side_effect=self.runner.subprocess.TimeoutExpired(
                    ["claude"],
                    7,
                    output=b'{"type":"system","subtype":"init"}\n',
                    stderr=b"partial",
                ),
            ):
                record = self.runner.run_one(
                    arm="baseline",
                    case_id="timeout",
                    variant="positive",
                    prompt="test prompt",
                    run_root=root,
                    skill_root=REPOSITORY_ROOT / "skills/sync-traycer-notion",
                    claude_bin="claude",
                    max_budget_usd="0.50",
                    timeout_seconds=7,
                )

            self.assertTrue(any("timed out" in error for error in record["errors"]))
            self.assertEqual(Path(record["trace_path"]).read_bytes(), b'{"type":"system","subtype":"init"}\n')


if __name__ == "__main__":
    unittest.main()
