from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess  # nosec B404 - fixed git argv is required for public commit-binding checks.
import sys
import tempfile
import unittest
import unittest.mock as mock
import zipfile
from fractions import Fraction
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/run-trigger-evals.py"
PRIVATE_VERIFIER = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/verify_private_evidence.py"
CASE_PACK = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/suite.json"
PRODUCTION_KEY_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/draft-key-manifest.json"
CALIBRATION_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/draft-calibration-manifest.json"
EVIDENCE_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/draft-evidence-manifest.json"
SKILL = REPOSITORY_ROOT / "skills/sync-traycer-notion/SKILL.md"
ADAPTER = REPOSITORY_ROOT / "skills/sync-traycer-notion/references/notion-task-list.md"
OPENAI_AGENT = REPOSITORY_ROOT / "skills/sync-traycer-notion/agents/openai.yaml"
TESTS_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/tests.yml"
TRIGGER_KEY_MANIFEST = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/key-manifest.json"
TRIGGER_PROOF_REPORT = REPOSITORY_ROOT / "evals/sync-traycer-notion-trigger/proof-report.json"


def load_runner() -> ModuleType:
    runner_directory = str(RUNNER.parent)
    if runner_directory not in sys.path:
        sys.path.insert(0, runner_directory)
    spec = importlib.util.spec_from_file_location("sync_traycer_notion_eval_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load eval runner from {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_private_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_traycer_notion_private_verifier", PRIVATE_VERIFIER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load private verifier from {PRIVATE_VERIFIER}")
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
            "duration_ms": 25,
            "modelUsage": {
                "claude-opus-test": {
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                }
            },
            "result": '{"classification":"sync"}',
            "total_cost_usd": 0.001,
        }
    )
    return b"".join(event_line(event) for event in events)


def behavior_result(*, case_id: str, passed: bool, critical: bool = False) -> dict[str, object]:
    return {
        "case_id": case_id,
        "trial_number": 1,
        "checks": [{"id": "semantic-check", "critical": critical, "passed": passed}],
    }


def git_show(commit: str, path: Path) -> bytes:
    relative = path.relative_to(REPOSITORY_ROOT).as_posix()
    completed = subprocess.run(  # nosec B603 B607 - fixed git argv, no shell.
        ["git", "show", f"{commit}:{relative}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=True,
    )
    return completed.stdout


class TriggerEvalRunnerTests(unittest.TestCase):
    runner: ModuleType
    private_verifier: ModuleType
    contract: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()
        cls.private_verifier = load_private_verifier()
        cls.contract = __import__("evidence_contract")

    def test_frozen_case_pack_is_valid_and_never_names_skill(self) -> None:
        pack, cases = self.runner.validated_case_pack(CASE_PACK)

        self.assertEqual(len(cases), 20)
        self.assertEqual(sum(case["variant"] == "positive" for case in cases), 12)
        self.assertEqual(sum(case["variant"] == "near_miss" for case in cases), 8)
        self.assertEqual(len(self.runner.expected_record_keys(cases)), 96)
        self.assertEqual(
            {case["id"] for case in cases if case["variant"] == "positive"},
            {
                "board-status-pull",
                "cycle-parent-stop",
                "duplicate-identity-stop",
                "explicit-status-push",
                "first-turn-existing-epic",
                "first-turn-new-story",
                "move-ticket-and-complete",
                "new-ticket-under-story",
                "orphan-ticket-stop",
                "partial-write-retry",
                "review-artifact-exclusion",
                "spec-artifact-exclusion",
            },
        )
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

        with tempfile.TemporaryDirectory() as temporary_directory:
            invalid_pack = json.loads(json.dumps(pack))
            invalid_pack["tasks"][0]["id"] = "../escape"
            invalid_path = Path(temporary_directory) / "suite.json"
            invalid_path.write_text(json.dumps(invalid_pack), encoding="utf-8")
            with self.assertRaisesRegex(self.runner.EvalError, "lowercase hyphenated"):
                self.runner.validated_case_pack(invalid_path)

    def test_production_manifests_bind_the_draft_suite(self) -> None:
        suite = self.runner.read_json(CASE_PACK)
        suite_digest = self.runner.canonical_json_sha256(suite)
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
        self.assertEqual(suite["thresholds"]["positive_trigger_recall_bps"], 9000)
        self.assertEqual(suite["thresholds"]["near_miss_abstention_bps"], 9500)
        self.assertNotIn("positive_trigger_recall", suite["thresholds"])
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

    def test_skill_excludes_explicit_sync_deferral(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        frontmatter = skill.split("---", maxsplit=2)[1]

        self.assertIn("takes precedence over every automatic trigger", frontmatter)
        self.assertIn("do not load, including for local-only artifact work", frontmatter)
        self.assertIn("Do not synchronize local-only artifact", skill)

    def test_skill_loads_for_epic_reconciliation_but_excludes_spec_and_review_sync(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        frontmatter = skill.split("---", maxsplit=2)[1]

        self.assertIn("load at the first safe opportunity", frontmatter)
        self.assertIn("reconcile the epic but never synchronize those artifacts", frontmatter)
        self.assertNotIn("Never load for `kind: spec`", frontmatter)
        self.assertIn("Always reconcile the current Traycer Task epic", skill)
        self.assertIn("never synchronize `spec`, `review`", skill)

    def test_skill_declares_the_final_context_contract(self) -> None:
        frontmatter = SKILL.read_text(encoding="utf-8").split("---", maxsplit=2)[1]

        self.assertIn("skill-audit-context-reads: current_traycer_epic", frontmatter)
        self.assertIn("skill-audit-context-requires: traycer_task_context, matching_sync_trigger", frontmatter)
        self.assertIn("skill-audit-context-writes: notion_epic_story_ticket_rows", frontmatter)
        self.assertIn("skill-audit-confirmation: on-risk", frontmatter)

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

        with self.assertRaisesRegex(self.runner.EvalError, "must not be empty"):
            self.runner.evaluate_check(response, {"kind": "contains_none", "values": []})
        with self.assertRaisesRegex(self.runner.EvalError, "non-empty string"):
            self.runner.evaluate_check(response, {"kind": "contains", "value": ""})

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
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            workspace_root = root / "workspaces"
            workspace_root.mkdir()
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
                    evidence_root=evidence_root,
                    workspace_root=workspace_root,
                    skill_root=REPOSITORY_ROOT / "skills/sync-traycer-notion",
                    claude_bin="claude",
                    max_budget_usd="0.50",
                    timeout_seconds=7,
                )

            self.assertTrue(any("timed out" in error for error in record["errors"]))
            self.assertFalse(Path(record["trace_path"]).is_absolute())
            self.assertEqual(
                (evidence_root / record["trace_path"]).read_bytes(), b'{"type":"system","subtype":"init"}\n'
            )

    def test_canonical_json_profile_is_portable_and_recursive(self) -> None:
        value = self.contract.parse_canonical_json(b'{"\xce\xb2":"caf\xc3\xa9","a":[true,null,7]}', "fixture")
        self.assertEqual(self.contract.canonical_json_bytes(value), b'{"a":[true,null,7],"\xce\xb2":"caf\xc3\xa9"}')

        invalid_documents = (
            b'{"duplicate":1,"duplicate":2}',
            b'{"rate":0.25}',
            b'{"value":NaN}',
            b'{"value":9007199254740992}',
            b'{"value":"\\ud800"}',
            b'{"value":"\xff"}',
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(self.contract.EvidenceContractError):
                self.contract.parse_canonical_json(document, "fixture")

    def test_every_bound_production_document_matches_the_canonical_profile(self) -> None:
        for path in (CASE_PACK, PRODUCTION_KEY_MANIFEST, CALIBRATION_MANIFEST, EVIDENCE_MANIFEST):
            with self.subTest(path=path):
                value = self.runner.read_canonical_json(path)
                self.runner.validate_canonical_value(value, str(path))

    def test_skill_tree_digest_binds_exact_membership_and_delimiters(self) -> None:
        records = []
        for path in (SKILL, OPENAI_AGENT, ADAPTER):
            relative = path.relative_to(SKILL.parent).as_posix()
            records.append(
                relative.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n"
            )
        expected = hashlib.sha256(b"".join(records)).hexdigest()

        self.assertEqual(self.runner.skill_tree_sha256(SKILL.parent), expected)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "agents").mkdir()
            (root / "references").mkdir()
            (root / "SKILL.md").write_bytes(SKILL.read_bytes())
            (root / "agents/openai.yaml").write_bytes(OPENAI_AGENT.read_bytes())
            (root / "references/notion-task-list.md").write_bytes(ADAPTER.read_bytes())
            (root / "extra.md").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(self.runner.EvalError, "paths do not match"):
                self.runner.skill_tree_sha256(root)

    def test_workspace_and_evidence_roots_must_be_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            evidence = root / "evidence"
            nested_workspace = evidence / "workspaces"
            nested_workspace.mkdir(parents=True)

            with self.assertRaisesRegex(self.runner.EvalError, "must be disjoint"):
                self.runner.require_disjoint_roots(evidence, nested_workspace)

    def test_relative_evidence_paths_reject_absolute_escape_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            response = root / "responses/result.txt"
            response.parent.mkdir()
            response.write_text("TASK-13", encoding="utf-8")
            record = {
                "response_path": "responses/result.txt",
                "response_sha256": hashlib.sha256(response.read_bytes()).hexdigest(),
            }
            self.assertEqual(self.runner.read_frozen_response(record, root), "TASK-13")

            with self.assertRaisesRegex(self.runner.EvalError, "normalized relative"):
                self.runner.resolve_evidence_path(root, str(response), "response_path")
            link = root / "responses/link.txt"
            link.symlink_to(response)
            with self.assertRaisesRegex(self.runner.EvalError, "symlink"):
                self.runner.resolve_evidence_path(root, "responses/link.txt", "response_path")

    def test_errored_record_fails_checks_before_response_is_opened(self) -> None:
        key_cases = {
            "case-1": {"checks": [{"id": "semantic-check", "critical": True, "kind": "contains", "value": "TASK-13"}]}
        }
        records = {
            ("case-1", "positive", trial, "with_skill"): {
                "errors": ["session failed"],
                "response_path": "/path/that/must/not/be/read",
                "response_sha256": "0" * 64,
            }
            for trial in range(1, 4)
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            results, passed, total = self.runner.grade_arm(
                arm="with_skill",
                key_cases=key_cases,
                manifest_root=Path(temporary_directory),
                records=records,
            )

        self.assertEqual((passed, total), (0, 3))
        self.assertTrue(all(check["passed"] is False for result in results for check in result["checks"]))

    def test_case_cluster_bootstrap_and_critical_regressions_are_pinned(self) -> None:
        baseline = [behavior_result(case_id="case-1", passed=True, critical=True)]
        treatment = [behavior_result(case_id="case-1", passed=False, critical=True)]
        deltas, critical_regressions = self.runner.paired_case_deltas(baseline, treatment)
        profile = {
            "method": "case-cluster-bootstrap-v1",
            "confidence_bps": 9500,
            "resamples": 1000,
            "schedule_seed": "test-seed",
        }
        lower_bps, schedule_digest = self.runner.paired_lower_bound_bps(deltas, profile)

        self.assertEqual(deltas, [("case-1", Fraction(-1, 1))])
        self.assertEqual(critical_regressions, 1)
        self.assertEqual(lower_bps, -10000)
        self.assertRegex(schedule_digest, r"^[0-9a-f]{64}$")
        self.assertTrue(self.runner.basis_point_threshold_passes(9, 10, 9000))

    def test_public_key_manifest_binds_the_current_bundle(self) -> None:
        pack, cases = self.runner.validated_case_pack(CASE_PACK)
        positive_ids = sorted(case["id"] for case in cases if case["variant"] == "positive")
        manifest = {
            "schema_version": 1,
            "suite": self.runner.SUITE_NAME,
            "bundle_commit": "a" * 40,
            "skill_sha256": self.runner.sha256_file(SKILL),
            "skill_tree_sha256": self.runner.skill_tree_sha256(SKILL.parent),
            "case_pack_sha256": self.runner.sha256_file(CASE_PACK),
            "case_pack_canonical_sha256": self.runner.canonical_json_sha256(pack),
            "runner_sha256": self.runner.sha256_file(RUNNER),
            "key_sha256": "b" * 64,
            "ciphertext_sha256": "c" * 64,
            "recipient_fingerprint": "D" * 40,
            "key_author": "author-agent",
            "key_reviewer": "reviewer-agent",
            "sealed_at": "2026-08-22T18:00:00Z",
            "private_evidence_repository": "nfma/agent-skills-evidence",
            "encrypted_key_path": "sync-traycer-notion-trigger/v1/key.json.gpg",
            "positive_case_ids": positive_ids,
            "near_miss_case_ids": sorted(case["id"] for case in cases if case["variant"] == "near_miss"),
            "check_counts": {case_id: 3 for case_id in positive_ids},
            "total_checks": 36,
            "thresholds": pack["thresholds"],
            "paired_confidence": pack["paired_confidence"],
            "execution": {
                "near_miss_arms": ["with_skill"],
                "positive_arms": ["baseline", "with_skill"],
                "session_count": 96,
                "trials_per_arm": 3,
            },
        }

        self.runner.validate_public_key_manifest(
            manifest,
            pack=pack,
            cases=cases,
            case_pack_path=CASE_PACK,
            skill_root=SKILL.parent,
        )
        manifest["skill_tree_sha256"] = "0" * 64
        with self.assertRaisesRegex(self.runner.EvalError, "does not bind"):
            self.runner.validate_public_key_manifest(
                manifest,
                pack=pack,
                cases=cases,
                case_pack_path=CASE_PACK,
                skill_root=SKILL.parent,
            )

    def test_committed_trigger_evidence_bindings_when_present(self) -> None:
        if not TRIGGER_KEY_MANIFEST.exists():
            self.assertFalse(TRIGGER_PROOF_REPORT.exists())
            return

        manifest = self.runner.read_json(TRIGGER_KEY_MANIFEST)
        pack, cases = self.runner.validated_case_pack(CASE_PACK)
        self.runner.validate_public_key_manifest(
            manifest,
            pack=pack,
            cases=cases,
            case_pack_path=CASE_PACK,
            skill_root=SKILL.parent,
        )
        bundle_commit = self.runner.require_commit_sha(manifest.get("bundle_commit"), "bundle_commit")
        for path in (SKILL, OPENAI_AGENT, ADAPTER, CASE_PACK, RUNNER):
            with self.subTest(path=path):
                self.assertEqual(git_show(bundle_commit, path), path.read_bytes())

        if not TRIGGER_PROOF_REPORT.exists():
            return
        proof = self.runner.read_json(TRIGGER_PROOF_REPORT)
        freeze_commit = self.runner.require_commit_sha(proof.get("freeze_commit"), "freeze_commit")
        self.assertEqual(
            git_show(freeze_commit, TRIGGER_KEY_MANIFEST),
            TRIGGER_KEY_MANIFEST.read_bytes(),
        )
        self.assertEqual(
            proof["sealed_inputs"]["key_manifest_sha256"],
            self.runner.canonical_json_sha256(manifest),
        )

    def test_run_defaults_enforce_the_per_session_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            arguments = self.runner.build_parser().parse_args(
                [
                    "run",
                    "--output-dir",
                    str(root / "evidence"),
                    "--workspace-root",
                    str(root / "workspaces"),
                    "--key-manifest",
                    str(root / "key-manifest.json"),
                    "--freeze-commit",
                    "a" * 40,
                ]
            )
        self.assertEqual(arguments.max_budget_usd, "1.00")
        self.assertEqual(self.runner.validated_max_budget("1.00"), "1.00")
        with self.assertRaisesRegex(self.runner.EvalError, "no more than 1.00"):
            self.runner.validated_max_budget("1.01")

    def test_public_tests_checkout_full_history_for_commit_bindings(self) -> None:
        workflow = TESTS_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("fetch-depth: 0", workflow)
        completed = subprocess.run(  # nosec B603 B607 - fixed git argv, no shell.
            ["git", "show", "HEAD:skills/sync-traycer-notion/SKILL.md"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            check=True,
        )
        self.assertTrue(completed.stdout.startswith(b"---\nname: sync-traycer-notion\n"))

    def test_private_archive_extractor_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape.txt", "escape")
            with self.assertRaisesRegex(self.private_verifier.VerificationError, "unsafe archive path"):
                self.private_verifier.safe_extract_zip(archive, root / "output")

    def test_downloaded_archive_regrades_after_original_run_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "original-run"
            original.mkdir()
            key_path = root / "key.json"
            output_path = root / "proof-report.json"
            raw_case_digest = "1" * 64
            canonical_case_digest = "2" * 64
            positive_ids = [f"case-{index:02d}" for index in range(1, 13)]
            near_miss_ids = [f"near-{index:02d}" for index in range(1, 9)]
            key = {
                "schema_version": 1,
                "suite": self.runner.SUITE_NAME,
                "case_pack_sha256": raw_case_digest,
                "case_pack_canonical_sha256": canonical_case_digest,
                "cases": {
                    case_id: {
                        "checks": [
                            {
                                "id": "task-identity",
                                "critical": True,
                                "kind": "contains",
                                "value": "TASK-13",
                            }
                        ]
                    }
                    for case_id in positive_ids
                },
            }
            key_path.write_text(json.dumps(key), encoding="utf-8")
            thresholds = {
                "positive_trigger_recall_bps": 9000,
                "near_miss_abstention_bps": 9500,
                "paired_delta_ci_lower_bps": 0,
                "critical_regressions": 0,
            }
            confidence = {
                "method": "case-cluster-bootstrap-v1",
                "confidence_bps": 9500,
                "resamples": 1000,
                "schedule_seed": "archive-test",
            }
            key_manifest = {
                "schema_version": 1,
                "suite": self.runner.SUITE_NAME,
                "bundle_commit": "a" * 40,
                "skill_sha256": "3" * 64,
                "skill_tree_sha256": "4" * 64,
                "case_pack_sha256": raw_case_digest,
                "case_pack_canonical_sha256": canonical_case_digest,
                "runner_sha256": "5" * 64,
                "key_sha256": self.runner.canonical_json_sha256(key),
                "ciphertext_sha256": "6" * 64,
                "recipient_fingerprint": "D" * 40,
                "positive_case_ids": positive_ids,
                "near_miss_case_ids": near_miss_ids,
                "check_counts": {case_id: 1 for case_id in positive_ids},
                "total_checks": 12,
                "thresholds": thresholds,
                "paired_confidence": confidence,
                "execution": {
                    "near_miss_arms": ["with_skill"],
                    "positive_arms": ["baseline", "with_skill"],
                    "session_count": 96,
                    "trials_per_arm": 3,
                },
            }
            key_manifest_path = original / "key-manifest.json"
            key_manifest_path.write_text(json.dumps(key_manifest), encoding="utf-8")

            records: list[dict[str, object]] = []
            variants = [
                *((case_id, "positive", ("baseline", "with_skill")) for case_id in positive_ids),
                *((case_id, "near_miss", ("with_skill",)) for case_id in near_miss_ids),
            ]
            for case_id, variant, arms in variants:
                for trial_number in range(1, 4):
                    for arm in arms:
                        run_id = f"{case_id}-{variant}-{trial_number}-{arm}"
                        response_path = original / "responses" / f"{run_id}.txt"
                        trace_path = original / "traces" / f"{run_id}.jsonl"
                        stderr_path = original / "traces" / f"{run_id}.stderr"
                        response_path.parent.mkdir(parents=True, exist_ok=True)
                        trace_path.parent.mkdir(parents=True, exist_ok=True)
                        response = "TASK-13" if arm == "with_skill" and variant == "positive" else "no change"
                        response_path.write_text(response, encoding="utf-8")
                        trace_path.write_text("trace", encoding="utf-8")
                        stderr_path.write_bytes(b"")
                        records.append(
                            {
                                "arm": arm,
                                "case_id": case_id,
                                "variant": variant,
                                "trial_number": trial_number,
                                "errors": [],
                                "discovered_target_skill": arm == "with_skill",
                                "invoked_skills": [self.runner.SKILL_NAME]
                                if arm == "with_skill" and variant == "positive"
                                else [],
                                "cost_microusd": 1000,
                                "input_tokens": 10,
                                "output_tokens": 5,
                                "latency_ms": 20,
                                "model": "test-model",
                                "prompt_sha256": "7" * 64,
                                "response_path": response_path.relative_to(original).as_posix(),
                                "response_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
                                "trace_path": trace_path.relative_to(original).as_posix(),
                                "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
                                "stderr_path": stderr_path.relative_to(original).as_posix(),
                                "stderr_sha256": hashlib.sha256(stderr_path.read_bytes()).hexdigest(),
                            }
                        )
            run_manifest = {
                "schema_version": 1,
                "suite": self.runner.SUITE_NAME,
                "case_pack_sha256": raw_case_digest,
                "case_pack_canonical_sha256": canonical_case_digest,
                "key_manifest_sha256": self.runner.canonical_json_sha256(key_manifest),
                "skill_sha256": key_manifest["skill_sha256"],
                "skill_tree_sha256": key_manifest["skill_tree_sha256"],
                "runner_sha256": key_manifest["runner_sha256"],
                "freeze_commit": "b" * 40,
                "execution_uuid": "archive-test-uuid",
                "execution_schedule_sha256": "8" * 64,
                "thresholds": thresholds,
                "paired_confidence": confidence,
                "profile": {"model": "test-model"},
                "claude_version": "test",
                "records": records,
            }
            (original / "run-manifest.json").write_text(json.dumps(run_manifest), encoding="utf-8")

            archive = root / "raw-evidence.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for path in sorted(original.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(original).as_posix())
            archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            archive_size = archive.stat().st_size
            shutil.rmtree(original)
            self.assertFalse(original.exists())

            result = self.private_verifier.verify_archive(
                argparse.Namespace(
                    archive=archive,
                    raw_evidence_sha256=archive_digest,
                    raw_evidence_size=archive_size,
                    key=key_path,
                    output=output_path,
                    private_release_tag="test-release",
                    private_asset_name=archive.name,
                )
            )

            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["status"], "passed")


if __name__ == "__main__":
    unittest.main()
