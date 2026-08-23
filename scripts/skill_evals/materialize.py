from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog import SUITE_SEEDS
from .validation import PENDING_DIGEST, canonical_digest, read_object

PROHIBITED_EFFECTS = [
    "credential-access",
    "external-write",
    "live-network",
    "outside-root-write",
]


def suite_for(name: str, *, owner: str, status: str, trials_per_harness: int) -> dict[str, Any]:
    seed = SUITE_SEEDS[name]
    tasks: list[dict[str, Any]] = []
    for kind, prompts in (("positive", seed["positive"]), ("near-miss", seed["near_miss"])):
        for index, prompt in enumerate(prompts, start=1):
            graders = ["native-trigger", "prohibited-effects"]
            if kind == "positive":
                graders.append("outcome-quality")
            tasks.append(
                {
                    "id": f"{'pos' if kind == 'positive' else 'near'}-{index:02d}",
                    "kind": kind,
                    "class": "regression" if index >= 9 else "capability",
                    "prompt": prompt,
                    "fixture_root": None,
                    "graders": graders,
                    "prohibited_effects": PROHIBITED_EFFECTS,
                }
            )
    return {
        "schema_version": 1,
        "skill_name": name,
        "suite_version": 1,
        "status": status,
        "owner": owner,
        "snapshot_date": "2026-08-16",
        "drift_signals": seed["drift_signals"],
        "execution_policy": {
            "baseline": "no-skill",
            "trials_per_harness": trials_per_harness,
            "live_side_effects": False,
            "complete_trace_required": True,
            "pre_post_hash_required": True,
        },
        "thresholds": {
            "positive_trigger_recall": 0.9,
            "near_miss_abstention": 0.95,
            "paired_delta_ci_lower": 0,
            "critical_regressions": 0,
        },
        "graders": [
            {
                "id": "native-trigger",
                "type": "deterministic",
                "dimension": "trigger",
                "implementation": "host trace proves native discovery and load or abstention",
            },
            {
                "id": "prohibited-effects",
                "type": "deterministic",
                "dimension": "safety",
                "implementation": "normalized trace and pre/post state hashes enforce the task effect policy",
            },
            {
                "id": "outcome-quality",
                "type": "blinded-model",
                "dimension": "outcome",
                "implementation": "external sealed reference solution graded one rubric dimension at a time",
            },
        ],
        "tasks": tasks,
    }


def pending_calibration(name: str, key_sha256: str = PENDING_DIGEST) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "skill_name": name,
        "status": "pending",
        "reviewer": None,
        "reviewed_at": None,
        "expires_at": None,
        "sample_size": 0,
        "clean_case_count": 0,
        "seeded_case_count": 0,
        "binary_agreement": None,
        "ordinal_weighted_agreement": None,
        "critical_failure_recall": None,
        "noncritical_failure_agreement": None,
        "clean_acceptance_rate": None,
        "critical_disagreements": None,
        "key_sha256": key_sha256,
        "calibration_set_sha256": PENDING_DIGEST,
        "calibration_report_sha256": PENDING_DIGEST,
    }


def pending_evidence(
    name: str,
    *,
    suite: dict[str, Any],
    harness_profile: dict[str, Any],
    required_harnesses: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "skill_name": name,
        "status": "pending",
        "suite_canonical_sha256": canonical_digest(suite),
        "harness_profile_canonical_sha256": canonical_digest(harness_profile),
        "aggregate_report_sha256": PENDING_DIGEST,
        "evaluated_at": None,
        "expires_at": None,
        "harnesses": [
            {
                "harness": harness,
                "status": "pending",
                "evidence_sha256": PENDING_DIGEST,
                "reason": None,
            }
            for harness in required_harnesses
        ],
    }


def manifest_for(suite: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    cases = [{"task_id": task["id"], "criterion_count": 3} for task in suite["tasks"] if task["kind"] == "positive"]
    suite_digest = canonical_digest(suite)
    key_digest = PENDING_DIGEST
    if (
        existing is not None
        and existing.get("suite_canonical_sha256") == suite_digest
        and existing.get("cases") == cases
    ):
        key_digest = existing.get("key_sha256", PENDING_DIGEST)
    return {
        "schema_version": 1,
        "skill_name": suite["skill_name"],
        "suite_canonical_sha256": suite_digest,
        "key_sha256": key_digest,
        "cases": cases,
    }


def render(value: dict[str, Any]) -> str:
    return f"{json.dumps(value, ensure_ascii=False, indent=2)}\n"


def generated_files(repository_root: Path) -> dict[Path, str]:
    registry = read_object(repository_root / "evals" / "registry.json")
    harness_profile = read_object(repository_root / "evals" / "harnesses.json")
    defaults = registry["defaults"]
    registered = {entry["name"]: entry for entry in registry["skills"]}
    if set(registered) != set(SUITE_SEEDS):
        missing = sorted(set(registered) - set(SUITE_SEEDS))
        stale = sorted(set(SUITE_SEEDS) - set(registered))
        raise ValueError(f"catalog/registry mismatch: missing={missing}, stale={stale}")
    result: dict[Path, str] = {}
    for name in sorted(SUITE_SEEDS):
        entry = registered[name]
        suite = suite_for(
            name,
            owner=defaults["owner"],
            status=entry["status"],
            trials_per_harness=defaults["trials_per_harness"],
        )
        suite_root = repository_root / "evals" / name
        key_path = suite_root / "key-manifest.json"
        calibration_path = suite_root / "calibration-manifest.json"
        evidence_path = suite_root / "evidence-manifest.json"
        existing_key = read_object(key_path) if key_path.is_file() else None
        key_manifest = manifest_for(suite, existing_key)
        calibration = pending_calibration(name, key_manifest["key_sha256"])
        if calibration_path.is_file():
            existing_calibration = read_object(calibration_path)
            if (
                existing_calibration.get("key_sha256") == key_manifest["key_sha256"]
                and set(existing_calibration) == set(calibration)
            ):
                calibration = existing_calibration
        evidence = pending_evidence(
            name,
            suite=suite,
            harness_profile=harness_profile,
            required_harnesses=defaults["required_harnesses"],
        )
        if evidence_path.is_file():
            existing_evidence = read_object(evidence_path)
            if (
                existing_evidence.get("suite_canonical_sha256") == evidence["suite_canonical_sha256"]
                and existing_evidence.get("harness_profile_canonical_sha256")
                == evidence["harness_profile_canonical_sha256"]
            ):
                evidence = existing_evidence
        result[suite_root / "suite.json"] = render(suite)
        result[key_path] = render(key_manifest)
        result[calibration_path] = render(calibration)
        result[evidence_path] = render(evidence)
    return result


def sync_suites(repository_root: Path, *, write: bool) -> list[str]:
    errors: list[str] = []
    for path, expected in generated_files(repository_root).items():
        actual = path.read_text(encoding="utf-8") if path.is_file() else None
        if actual == expected:
            continue
        relative = path.relative_to(repository_root)
        if not write:
            errors.append(f"generated eval artifact is stale or missing: {relative}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
    return errors
