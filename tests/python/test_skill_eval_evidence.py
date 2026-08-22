from __future__ import annotations

import copy
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from scripts.skill_evals.evidence import validate_run_manifest
from scripts.skill_evals.validation import canonical_digest


def planned_trial(trial_id: str, *, load: bool = True) -> dict[str, object]:
    return {
        "trial_id": trial_id,
        "skill_name": "sample",
        "task_id": "pos-01",
        "task_kind": "positive",
        "task_class": "capability",
        "harness": "claude-code",
        "trial_number": 1,
        "arm": "with-skill" if load else "baseline",
        "project_nonce": "nonce",
        "prompt": "Review the supplied sample without changing external state.",
        "expected_skill_discovery": load,
        "expected_skill_load": load,
        "prohibited_effects": ["external-write"],
        "sequence": 1,
    }


def completed_record(root: Path, trial_id: str, *, load: bool = True) -> dict[str, object]:
    trace = root / f"{trial_id}.trace.jsonl"
    response = root / f"{trial_id}.response.txt"
    trace.write_text('{"type":"complete"}\n')
    response.write_text("response")
    state_digest = "a" * 64
    return {
        "trial_id": trial_id,
        "status": "completed",
        "reason": None,
        "model": "independent-model",
        "harness_version": "1.0",
        "trace_path": trace.name,
        "trace_sha256": sha256(trace.read_bytes()).hexdigest(),
        "response_path": response.name,
        "response_sha256": sha256(response.read_bytes()).hexdigest(),
        "complete_trace": True,
        "skill_discovered": load,
        "skill_loaded": load,
        "before_state_sha256": state_digest,
        "after_state_sha256": state_digest,
        "successful_effects": [],
        "latency_ms": 10,
        "input_tokens": 20,
        "output_tokens": 30,
        "cost_usd": None,
    }


def unavailable_record(trial_id: str) -> dict[str, object]:
    fields = {
        "model",
        "harness_version",
        "trace_path",
        "trace_sha256",
        "response_path",
        "response_sha256",
        "complete_trace",
        "skill_discovered",
        "skill_loaded",
        "before_state_sha256",
        "after_state_sha256",
        "successful_effects",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    }
    return {
        "trial_id": trial_id,
        "status": "unavailable",
        "reason": "host cannot prove native loading",
        **dict.fromkeys(fields),
    }


class SkillEvalEvidenceTests(unittest.TestCase):
    def test_complete_frozen_trial_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            trial = planned_trial("sample.pos-01.claude.t1.with-skill")
            plan = {"trials": [trial]}
            manifest = {
                "schema_version": 1,
                "run_id": "run-one",
                "generated_at": "2026-08-16T12:00:00+00:00",
                "plan_sha256": canonical_digest(plan),
                "trials": [completed_record(root, str(trial["trial_id"]))],
            }
            errors = validate_run_manifest(plan, manifest, evidence_root=root)

        self.assertEqual(errors, [])

    def test_digest_effect_and_load_bypasses_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            trial = planned_trial("sample.pos-01.claude.t1.with-skill")
            plan = {"trials": [trial]}
            record = completed_record(root, str(trial["trial_id"]))
            record["trace_sha256"] = "b" * 64
            record["successful_effects"] = ["external-write"]
            record["skill_loaded"] = "spoofed"
            record["after_state_sha256"] = "c" * 64
            manifest = {
                "schema_version": 1,
                "run_id": "run-one",
                "generated_at": "2026-08-16T12:00:00+00:00",
                "plan_sha256": canonical_digest(plan),
                "trials": [record],
            }
            errors = validate_run_manifest(plan, manifest, evidence_root=root)

        self.assertTrue(any("does not match the artifact" in error for error in errors))
        self.assertTrue(any("successful prohibited effects" in error for error in errors))
        self.assertTrue(any("skill_loaded must be boolean" in error for error in errors))
        self.assertTrue(any("changed the task state" in error for error in errors))

    def test_lane_cannot_mix_unavailable_and_completed_trials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = planned_trial("sample.pos-01.claude.t1.with-skill")
            second = copy.deepcopy(first)
            second["trial_id"] = "sample.pos-01.claude.t2.with-skill"
            second["trial_number"] = 2
            plan = {"trials": [first, second]}
            manifest = {
                "schema_version": 1,
                "run_id": "run-one",
                "generated_at": "2026-08-16T12:00:00+00:00",
                "plan_sha256": canonical_digest(plan),
                "trials": [
                    completed_record(root, str(first["trial_id"])),
                    unavailable_record(str(second["trial_id"])),
                ],
            }
            errors = validate_run_manifest(plan, manifest, evidence_root=root)

        self.assertTrue(any("mixes completed and unavailable" in error for error in errors))

    def test_symlinked_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root / "outside.txt"
            outside.write_text("trace")
            trial = planned_trial("sample.pos-01.claude.t1.with-skill")
            plan = {"trials": [trial]}
            record = completed_record(root, str(trial["trial_id"]))
            trace = root / str(record["trace_path"])
            trace.unlink()
            trace.symlink_to(outside)
            record["trace_sha256"] = sha256(outside.read_bytes()).hexdigest()
            manifest = {
                "schema_version": 1,
                "run_id": "run-one",
                "generated_at": "2026-08-16T12:00:00+00:00",
                "plan_sha256": canonical_digest(plan),
                "trials": [record],
            }
            errors = validate_run_manifest(plan, manifest, evidence_root=root)

        self.assertTrue(any("must not contain symlinks" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
