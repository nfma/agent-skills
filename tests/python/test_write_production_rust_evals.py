from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPOSITORY_ROOT / "skills/write-production-rust/scripts/run_evals.py"
CASE_PACK = REPOSITORY_ROOT / "skills/write-production-rust/assets/trigger-behavior-evals.json"
SKILL_DOCUMENT = REPOSITORY_ROOT / "skills/write-production-rust/SKILL.md"
SOURCE_BOUNDARY = REPOSITORY_ROOT / "skills/write-production-rust/references/source-boundary.md"
AUDIT_BASELINE = REPOSITORY_ROOT / ".skill-audit-baseline.json"
KEY_MANIFEST = REPOSITORY_ROOT / "evals/write-production-rust/semantic-key-manifest.json"
PROOF_REPORT = REPOSITORY_ROOT / "evals/write-production-rust/proof-report.json"


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("write_production_rust_eval_runner", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load evaluation runner from {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event_line(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


def valid_trace(
    *, advertised_skills: list[str], tools: list[str], invoked_skill: bool, response: str = "final response"
) -> bytes:
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
            "result": response,
        }
    )
    return b"".join(event_line(event) for event in events)


def frozen_trigger_records(
    runner: ModuleType,
    root: Path,
    trigger_cases: list[dict[str, object]],
) -> dict[tuple[str, str, str, str], dict[str, object]]:
    records: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for case in trigger_cases:
        case_id = str(case["id"])
        for variant in runner.VARIANTS:
            for arm in runner.ARMS:
                trace = valid_trace(
                    advertised_skills=[runner.SKILL_NAME] if arm == "with_skill" else [],
                    tools=list(runner.TRIGGER_TOOLS),
                    invoked_skill=arm == "with_skill" and variant == "positive",
                )
                trace_path = root / f"{case_id}--{variant}--{arm}.jsonl"
                trace_path.write_bytes(trace)
                records[("trigger", case_id, variant, arm)] = {
                    "trace_path": str(trace_path),
                    "trace_sha256": runner.sha256_bytes(trace),
                }
    return records


def frozen_behavior_record(
    runner: ModuleType,
    root: Path,
    *,
    arm: str,
    tools: list[str],
    response: str = "final response",
) -> tuple[tuple[str, str, str, str], dict[str, object]]:
    trace = valid_trace(advertised_skills=[], tools=tools, invoked_skill=False, response=response)
    trace_path = root / f"behavior--{arm}.jsonl"
    response_path = root / f"behavior--{arm}.txt"
    trace_path.write_bytes(trace)
    response_path.write_text(response, encoding="utf-8")
    return (
        ("behavior", "case", "none", arm),
        {
            "errors": [],
            "trace_path": str(trace_path),
            "trace_sha256": runner.sha256_bytes(trace),
            "response_path": str(response_path),
            "response_sha256": runner.sha256_file(response_path),
        },
    )


class WriteProductionRustEvalTests(unittest.TestCase):
    runner: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def test_case_pack_has_unnamed_positive_near_miss_and_behavior_cases(self) -> None:
        pack, trigger_cases, behavior_cases = self.runner.validated_case_pack(CASE_PACK)

        self.assertGreaterEqual(len(trigger_cases), 3)
        self.assertGreaterEqual(len(behavior_cases), 3)
        self.assertEqual(pack["guidance_bundle"], self.runner.discovered_guidance_files())
        for case in trigger_cases:
            self.assertNotIn("write-production-rust", case["positive"].casefold())
            self.assertNotIn("write-production-rust", case["near_miss"].casefold())
        for case in behavior_cases:
            self.assertNotIn("write-production-rust", case["prompt"].casefold())

    def test_skill_declares_compatibility_and_precise_test_rust_handoffs(self) -> None:
        skill = SKILL_DOCUMENT.read_text(encoding="utf-8")
        source_boundary = SOURCE_BOUNDARY.read_text(encoding="utf-8")
        audit_baseline = json.loads(AUDIT_BASELINE.read_text(encoding="utf-8"))

        self.assertIn("\ncompatibility:", skill)
        self.assertNotIn("Rust testing skill", skill)
        self.assertNotIn("Rust testing skill", source_boundary)
        self.assertIn("`test-rust`", skill)
        self.assertIn("`test-rust`", source_boundary)
        self.assertNotIn(
            "write-production-rust",
            {entry["skill"] for entry in audit_baseline["compatibilityOmissions"]},
        )

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

    def test_semantic_grade_rejects_boolean_scores(self) -> None:
        response = json.dumps(
            {
                "scores": {"A": {"criterion": True}, "B": {"criterion": 0}},
                "winner": "A",
                "rationale": "Invalid boolean score.",
            }
        )

        with self.assertRaisesRegex(self.runner.EvalError, "scores must be integers"):
            self.runner.parse_grade(response, [{"id": "criterion", "text": "Criterion"}])

    def test_candidate_mapping_is_seeded_by_sorted_behavior_trace_digests(self) -> None:
        case_id = "behavior-case"
        trace_sha256s = ["f" * 64, "0" * 64]
        material = {"behavior_trace_sha256s": sorted(trace_sha256s), "case_id": case_id}
        seed = bytes.fromhex(self.runner.sha256_bytes(self.runner.canonical_json_bytes(material)))
        expected = {"A": "baseline", "B": "with_skill"} if seed[0] % 2 == 0 else {"A": "with_skill", "B": "baseline"}

        self.assertEqual(self.runner.candidate_mapping(case_id, trace_sha256s), expected)
        self.assertEqual(self.runner.candidate_mapping(case_id, list(reversed(trace_sha256s))), expected)

    def test_proof_identifier_binds_guidance_and_grader_traces(self) -> None:
        sealed_inputs = {"guidance_bundle": {"files": [], "sha256": "a" * 64}}
        grades = [{"case_id": "case", "grader_trace_sha256": "b" * 64}]

        proof_id = self.runner.proof_identifier(
            run_manifest_sha256="c" * 64,
            sealed_inputs=sealed_inputs,
            grades=grades,
        )

        self.assertTrue(proof_id.startswith("sha256:"))
        self.assertNotEqual(
            proof_id,
            self.runner.proof_identifier(
                run_manifest_sha256="c" * 64,
                sealed_inputs={"guidance_bundle": {"files": [], "sha256": "d" * 64}},
                grades=grades,
            ),
        )
        self.assertNotEqual(
            proof_id,
            self.runner.proof_identifier(
                run_manifest_sha256="c" * 64,
                sealed_inputs=sealed_inputs,
                grades=[{"case_id": "case", "grader_trace_sha256": "e" * 64}],
            ),
        )

    def test_run_manifest_digests_are_verified_against_committed_inputs(self) -> None:
        pack = self.runner.read_json(self.runner.DEFAULT_CASE_PACK)
        guidance_report = self.runner.guidance_bundle_report(self.runner.validated_guidance_bundle(pack))
        manifest = {
            "proof_contract_version": self.runner.PROOF_CONTRACT_VERSION,
            "case_pack_sha256": self.runner.sha256_file(self.runner.DEFAULT_CASE_PACK),
            "guidance_bundle": guidance_report,
            "skill_sha256": self.runner.sha256_file(self.runner.SKILL_ROOT / "SKILL.md"),
        }

        self.assertEqual(
            self.runner.validated_run_manifest_digests(manifest),
            (manifest["case_pack_sha256"], manifest["skill_sha256"], guidance_report),
        )
        for field in ("case_pack_sha256", "skill_sha256"):
            with self.subTest(field=field):
                tampered = {**manifest, field: "0" * 64}
                with self.assertRaisesRegex(self.runner.EvalError, "does not match"):
                    self.runner.validated_run_manifest_digests(tampered)

    def test_committed_proof_report_is_fresh_for_current_sealed_inputs(self) -> None:
        proof_report = self.runner.read_json(PROOF_REPORT)
        key_manifest = self.runner.read_json(KEY_MANIFEST)
        pack = self.runner.read_json(CASE_PACK)
        expected = {
            "schema_version": self.runner.SCHEMA_VERSION,
            "proof_contract_version": self.runner.PROOF_CONTRACT_VERSION,
            "suite": self.runner.SUITE_NAME,
            "passed": True,
            "sealed_inputs": {
                "case_pack_sha256": self.runner.sha256_file(CASE_PACK),
                "guidance_bundle": self.runner.guidance_bundle_report(self.runner.validated_guidance_bundle(pack)),
                "key_manifest_sha256": self.runner.sha256_file(KEY_MANIFEST),
                "key_sha256": key_manifest["key_sha256"],
                "skill_sha256": self.runner.sha256_file(SKILL_DOCUMENT),
            },
        }

        self.assertEqual(
            {field: proof_report.get(field) for field in expected},
            expected,
        )

    def test_guidance_bundle_rejects_missing_extra_reordered_and_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            skill_root = Path(temporary_directory)
            references = skill_root / "references"
            references.mkdir()
            (skill_root / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (references / "a.md").write_text("a\n", encoding="utf-8")
            expected_paths = ["SKILL.md", "references/a.md"]
            expected = self.runner.guidance_bundle_report(expected_paths, skill_root)

            missing = {**expected, "files": expected["files"][:-1]}
            with self.assertRaisesRegex(self.runner.EvalError, "missing="):
                self.runner.validate_reported_guidance_bundle(missing, expected)

            extra_file = {"path": "references/extra.md", "sha256": "0" * 64}
            extra = {**expected, "files": [*expected["files"], extra_file]}
            with self.assertRaisesRegex(self.runner.EvalError, "extra="):
                self.runner.validate_reported_guidance_bundle(extra, expected)

            reordered = {**expected, "files": list(reversed(expected["files"]))}
            with self.assertRaisesRegex(self.runner.EvalError, "reordered"):
                self.runner.validate_reported_guidance_bundle(reordered, expected)

            (references / "a.md").write_text("changed\n", encoding="utf-8")
            changed_expected = self.runner.guidance_bundle_report(expected_paths, skill_root)
            with self.assertRaisesRegex(self.runner.EvalError, "does not match committed references/a.md"):
                self.runner.validate_reported_guidance_bundle(expected, changed_expected)

    def test_guidance_inventory_rejects_unsealed_or_reordered_committed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            skill_root = Path(temporary_directory)
            references = skill_root / "references"
            references.mkdir()
            (skill_root / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (references / "a.md").write_text("a\n", encoding="utf-8")

            self.assertEqual(
                self.runner.validated_guidance_bundle({"guidance_bundle": ["SKILL.md", "references/a.md"]}, skill_root),
                ["SKILL.md", "references/a.md"],
            )
            with self.assertRaisesRegex(self.runner.EvalError, "reordered"):
                self.runner.validated_guidance_bundle({"guidance_bundle": ["references/a.md", "SKILL.md"]}, skill_root)
            (references / "new.md").write_text("new\n", encoding="utf-8")
            with self.assertRaisesRegex(self.runner.EvalError, "missing=.*references/new.md"):
                self.runner.validated_guidance_bundle({"guidance_bundle": ["SKILL.md", "references/a.md"]}, skill_root)

    def test_trigger_records_must_exactly_match_the_sealed_case_pack(self) -> None:
        _pack, trigger_cases, _behavior_cases = self.runner.validated_case_pack(CASE_PACK)
        records: dict[tuple[str, str, str, str], dict[str, object]] = {
            ("trigger", case["id"], variant, arm): {}
            for case in trigger_cases
            for variant in self.runner.VARIANTS
            for arm in self.runner.ARMS
        }

        expected_keys = sorted(
            (
                {"case_id": case["id"], "variant": variant, "arm": arm}
                for case in trigger_cases
                for variant in self.runner.VARIANTS
                for arm in self.runner.ARMS
            ),
            key=lambda item: (item["case_id"], item["variant"], item["arm"]),
        )
        self.assertEqual(
            self.runner.validate_trigger_record_set(records, trigger_cases), {"count": 12, "keys": expected_keys}
        )
        missing = records.copy()
        missing.pop(next(iter(missing)))
        with self.assertRaisesRegex(self.runner.EvalError, "missing="):
            self.runner.validate_trigger_record_set(missing, trigger_cases)
        extra = {**records, ("trigger", "unexpected", "positive", "baseline"): {}}
        with self.assertRaisesRegex(self.runner.EvalError, "extra="):
            self.runner.validate_trigger_record_set(extra, trigger_cases)

    def test_indexed_records_rejects_unknown_stages(self) -> None:
        manifest = {
            "records": [
                {
                    "stage": "other",
                    "case_id": "case",
                    "variant": "none",
                    "arm": "baseline",
                }
            ]
        }

        with self.assertRaisesRegex(self.runner.EvalError, "record.stage must be trigger or behavior"):
            self.runner.indexed_records(manifest)

    def test_grade_suite_rejects_total_record_cardinality_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_manifest = root / "run-manifest.json"
            semantic_key = root / "semantic-key.json"
            run_manifest.write_text(
                json.dumps({"schema_version": self.runner.SCHEMA_VERSION, "suite": self.runner.SUITE_NAME}),
                encoding="utf-8",
            )
            arguments = self.runner.build_parser().parse_args(
                ["grade", "--run-manifest", str(run_manifest), "--key", str(semantic_key)]
            )
            patches = {
                "validated_run_manifest_digests": mock.Mock(return_value=("a" * 64, "b" * 64, {})),
                "validated_case_pack": mock.Mock(
                    return_value=({}, [{"id": "trigger-case"}], [{"id": "behavior-case"}])
                ),
                "indexed_records": mock.Mock(return_value={}),
            }

            with ExitStack() as stack:
                stack.enter_context(mock.patch.multiple(self.runner, **patches))
                stack.enter_context(self.assertRaisesRegex(self.runner.EvalError, "run record count must be 6, got 0"))
                self.runner.grade_suite(arguments)

    def test_grade_progress_is_written_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_manifest = root / "run-manifest.json"
            semantic_key = root / "semantic-key.json"
            run_manifest.write_text(
                json.dumps(
                    {
                        "schema_version": self.runner.SCHEMA_VERSION,
                        "suite": self.runner.SUITE_NAME,
                        "profile": {"model_alias": "behavior-model"},
                    }
                ),
                encoding="utf-8",
            )
            semantic_key.write_text("{}", encoding="utf-8")
            arguments = self.runner.build_parser().parse_args(
                ["grade", "--run-manifest", str(run_manifest), "--key", str(semantic_key)]
            )
            behavior_records = {
                ("behavior", "case", "none", arm): {"trace_sha256": arm[0] * 64} for arm in self.runner.ARMS
            }
            patches = {
                "validated_run_manifest_digests": mock.Mock(return_value=("a" * 64, "b" * 64, {})),
                "validated_case_pack": mock.Mock(return_value=({}, [], [{"id": "case"}])),
                "indexed_records": mock.Mock(return_value=behavior_records),
                "validate_behavior_guidance": mock.Mock(),
                "recompute_trigger_records": mock.Mock(return_value={}),
                "recompute_behavior_records": mock.Mock(return_value=behavior_records),
                "validate_key": mock.Mock(
                    return_value=({"cases": {"case": {"criteria": []}}}, {"key_sha256": "c" * 64})
                ),
                "grade_case": mock.Mock(side_effect=RuntimeError("stop after progress")),
            }

            with ExitStack() as stack:
                stack.enter_context(mock.patch.multiple(self.runner, **patches))
                print_mock = stack.enter_context(mock.patch("builtins.print"))
                stack.enter_context(self.assertRaisesRegex(RuntimeError, "stop after progress"))
                self.runner.grade_suite(arguments)

            print_mock.assert_any_call("grading case", file=self.runner.sys.stderr, flush=True)

    def test_behavior_records_and_guidance_must_match_the_sealed_case_pack(self) -> None:
        _pack, _trigger_cases, behavior_cases = self.runner.validated_case_pack(CASE_PACK)
        records: dict[tuple[str, str, str, str], dict[str, object]] = {}
        for case in behavior_cases:
            for arm in self.runner.ARMS:
                _prompt, injection_sha256, guidance_digests = self.runner.render_behavior_prompt(case, arm)
                records[("behavior", case["id"], "none", arm)] = {
                    "task_prompt_sha256": self.runner.sha256_bytes(case["prompt"].encode()),
                    "injection_sha256": injection_sha256,
                    "guidance_digests": guidance_digests,
                }

        self.assertEqual(self.runner.validate_behavior_record_set(records, behavior_cases)["count"], 6)
        self.runner.validate_behavior_guidance(records, behavior_cases)

        missing = records.copy()
        missing.pop(next(iter(missing)))
        with self.assertRaisesRegex(self.runner.EvalError, "missing="):
            self.runner.validate_behavior_record_set(missing, behavior_cases)

        treatment_key = next(key for key in records if key[3] == "with_skill")
        tampered = {key: dict(value) for key, value in records.items()}
        tampered[treatment_key]["guidance_digests"] = {}
        with self.assertRaisesRegex(self.runner.EvalError, "guidance file digests mismatch"):
            self.runner.validate_behavior_guidance(tampered, behavior_cases)

    def test_trigger_proof_is_recomputed_from_frozen_host_traces(self) -> None:
        _pack, trigger_cases, _behavior_cases = self.runner.validated_case_pack(CASE_PACK)
        with tempfile.TemporaryDirectory() as temporary_directory:
            records = frozen_trigger_records(self.runner, Path(temporary_directory), trigger_cases)
            for record in records.values():
                record.update(
                    {
                        "advertised_skills": 123,
                        "invoked_skills": 123,
                        "tools_used": 123,
                        "mcp_server_count": "forged",
                        "errors": ["forged"],
                    }
                )

            recomputed = self.runner.recompute_trigger_records(records)

            self.assertTrue(all(self.runner.trigger_proof(recomputed).values()))

    def test_trigger_records_without_trace_evidence_are_rejected(self) -> None:
        _pack, trigger_cases, _behavior_cases = self.runner.validated_case_pack(CASE_PACK)
        records: dict[tuple[str, str, str, str], dict[str, object]] = {
            ("trigger", case["id"], variant, arm): {}
            for case in trigger_cases
            for variant in self.runner.VARIANTS
            for arm in self.runner.ARMS
        }

        self.runner.validate_trigger_record_set(records, trigger_cases)
        with self.assertRaisesRegex(self.runner.EvalError, "trigger trace path"):
            self.runner.recompute_trigger_records(records)

    def test_trigger_trace_digest_must_match_frozen_bytes(self) -> None:
        _pack, trigger_cases, _behavior_cases = self.runner.validated_case_pack(CASE_PACK)
        with tempfile.TemporaryDirectory() as temporary_directory:
            records = frozen_trigger_records(self.runner, Path(temporary_directory), trigger_cases)
            first_record = next(iter(records.values()))
            first_record["trace_sha256"] = "0" * 64

            with self.assertRaisesRegex(self.runner.EvalError, "trace digest mismatch"):
                self.runner.recompute_trigger_records(records)

    def test_trigger_trace_contract_is_revalidated(self) -> None:
        _pack, trigger_cases, _behavior_cases = self.runner.validated_case_pack(CASE_PACK)
        with tempfile.TemporaryDirectory() as temporary_directory:
            records = frozen_trigger_records(self.runner, Path(temporary_directory), trigger_cases)
            baseline_key = next(key for key in records if key[3] == "baseline")
            baseline_record = records[baseline_key]
            invalid_trace = valid_trace(
                advertised_skills=[self.runner.SKILL_NAME],
                tools=list(self.runner.TRIGGER_TOOLS),
                invoked_skill=False,
            )
            Path(str(baseline_record["trace_path"])).write_bytes(invalid_trace)
            baseline_record["trace_sha256"] = self.runner.sha256_bytes(invalid_trace)

            with self.assertRaisesRegex(self.runner.EvalError, "trace contract failed"):
                self.runner.recompute_trigger_records(records)

    def test_behavior_trace_contract_is_revalidated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            key, record = frozen_behavior_record(
                self.runner,
                Path(temporary_directory),
                arm="baseline",
                tools=["Read"],
            )

            with self.assertRaisesRegex(self.runner.EvalError, "behavior trace contract failed"):
                self.runner.recompute_behavior_records({key: record})

    def test_invalid_utf8_behavior_trace_raises_eval_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            key, record = frozen_behavior_record(
                self.runner,
                Path(temporary_directory),
                arm="baseline",
                tools=[],
            )
            trace_path = Path(str(record["trace_path"]))
            trace_path.write_bytes(b"\xff")
            record["trace_sha256"] = self.runner.sha256_file(trace_path)

            with self.assertRaisesRegex(self.runner.EvalError, "invalid behavior trace for case/baseline"):
                self.runner.recompute_behavior_records({key: record})

    def test_honest_zero_tool_behavior_trace_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            baseline_key, baseline = frozen_behavior_record(self.runner, root, arm="baseline", tools=[])
            treatment_key, treatment = frozen_behavior_record(self.runner, root, arm="with_skill", tools=[])

            recomputed = self.runner.recompute_behavior_records({baseline_key: baseline, treatment_key: treatment})

            for key in (baseline_key, treatment_key):
                self.assertEqual(
                    recomputed[key],
                    {
                        "advertised_tools": [],
                        "tools_used": [],
                        "mcp_server_count": 0,
                        "success": True,
                        "errors": [],
                        "trace_sha256": baseline["trace_sha256"] if key == baseline_key else treatment["trace_sha256"],
                    },
                )

    def test_behavior_trace_must_match_the_frozen_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            key, record = frozen_behavior_record(
                self.runner,
                Path(temporary_directory),
                arm="baseline",
                tools=[],
                response="response from trace A",
            )
            response_path = Path(str(record["response_path"]))
            response_path.write_text("unrelated response B", encoding="utf-8")
            record["response_sha256"] = self.runner.sha256_file(response_path)

            with self.assertRaisesRegex(self.runner.EvalError, "trace response mismatch"):
                self.runner.recompute_behavior_records({key: record})

    def test_malformed_trigger_proof_record_raises_eval_error(self) -> None:
        malformed = {
            ("trigger", "case", "positive", "with_skill"): {
                "advertised_skills": 123,
                "invoked_skills": [self.runner.SKILL_NAME],
                "tools_used": [],
                "mcp_server_count": 0,
                "errors": [],
            }
        }

        with self.assertRaisesRegex(self.runner.EvalError, "advertised_skills"):
            self.runner.trigger_proof(malformed)

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
