from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .validation import (
    HARNESSES,
    MAX_TRIALS_PER_HARNESS,
    canonical_digest,
    is_within,
    read_object,
    validate_harness_profile,
    validate_registry,
)


def deployed_bundle_digest(skill_root: Path, repository_root: Path) -> str:
    physical_root = skill_root.resolve()
    if not is_within(physical_root, repository_root):
        raise ValueError(f"skill bundle escapes repository: {skill_root}")
    records: list[bytes] = []
    for path in sorted(physical_root.rglob("*")):
        relative = path.relative_to(physical_root)
        if any(part in {"tests", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"deployed skill bundle contains a symlink: {skill_root / relative}")
        if path.is_file():
            records.append(
                relative.as_posix().encode("utf-8")
                + b"\0"
                + sha256(path.read_bytes()).hexdigest().encode("ascii")
                + b"\n"
            )
    if not records:
        raise ValueError(f"deployed skill bundle is empty: {skill_root}")
    return sha256(b"".join(records)).hexdigest()


def build_plan(
    repository_root: Path,
    *,
    skill_names: list[str],
    harnesses: list[str],
) -> dict[str, Any]:
    registry_path = repository_root / "evals" / "registry.json"
    errors, _summary = validate_registry(repository_root, registry_path)
    if errors:
        raise ValueError(f"evaluation registry is invalid: {errors}")
    registry = read_object(registry_path)
    harness_profile = read_object(repository_root / "evals" / "harnesses.json")
    harness_errors = validate_harness_profile(harness_profile)
    if harness_errors:
        raise ValueError(f"harness profile is invalid: {harness_errors}")
    registered = {entry["name"]: entry for entry in registry["skills"]}
    selected_skills = sorted(set(skill_names)) if skill_names else sorted(registered)
    unknown_skills = sorted(set(selected_skills) - set(registered))
    if unknown_skills:
        raise ValueError(f"unknown skills: {unknown_skills}")
    selected_harnesses = sorted(set(harnesses)) if harnesses else [registry["defaults"]["canonical_harness"]]
    unknown_harnesses = sorted(set(selected_harnesses) - HARNESSES)
    if unknown_harnesses:
        raise ValueError(f"unknown harnesses: {unknown_harnesses}")

    plan_id = secrets.token_hex(16)
    lanes_by_harness = {lane["harness"]: lane for lane in harness_profile["lanes"]}
    trials: list[dict[str, Any]] = []
    suites: list[dict[str, Any]] = []
    for name in selected_skills:
        entry = registered[name]
        suite_path = repository_root / entry["suite"]
        suite = read_object(suite_path)
        trial_count = suite["execution_policy"]["trials_per_harness"]
        if not isinstance(trial_count, int) or not 3 <= trial_count <= MAX_TRIALS_PER_HARNESS:
            raise ValueError(f"{name} trials_per_harness must be between 3 and {MAX_TRIALS_PER_HARNESS}")
        skill_root = repository_root / "skills" / name
        suites.append(
            {
                "skill_name": name,
                "suite_version": suite["suite_version"],
                "suite_canonical_sha256": canonical_digest(suite),
                "deployed_bundle_sha256": deployed_bundle_digest(skill_root, repository_root),
            }
        )
        for harness in selected_harnesses:
            for task in suite["tasks"]:
                arms = ["with-skill"]
                if task["kind"] == "positive":
                    arms.append("baseline")
                bounded_trial_count = min(trial_count, MAX_TRIALS_PER_HARNESS)
                for trial_number in range(1, bounded_trial_count + 1):
                    for arm in arms:
                        expected_discovery = arm == "with-skill"
                        expected_load = arm == "with-skill" and task["kind"] == "positive"
                        trials.append(
                            {
                                "trial_id": (f"{name}.{task['id']}.{harness}.t{trial_number}.{arm}"),
                                "skill_name": name,
                                "task_id": task["id"],
                                "task_kind": task["kind"],
                                "task_class": task["class"],
                                "harness": harness,
                                "trial_number": trial_number,
                                "arm": arm,
                                "project_nonce": secrets.token_hex(12),
                                "prompt": task["prompt"],
                                "expected_skill_discovery": expected_discovery,
                                "expected_skill_load": expected_load,
                                "prohibited_effects": task["prohibited_effects"],
                            }
                        )
    secrets.SystemRandom().shuffle(trials)  # NOSONAR - SystemRandom is cryptographically secure.
    for sequence, trial in enumerate(trials, start=1):
        trial["sequence"] = sequence
    return {
        "schema_version": 1,
        "plan_id": plan_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "registry_canonical_sha256": canonical_digest(registry),
        "harness_profile_canonical_sha256": canonical_digest(harness_profile),
        "harnesses": selected_harnesses,
        "lanes": [lanes_by_harness[harness] for harness in selected_harnesses],
        "suites": suites,
        "trials": trials,
    }


def write_external_plan(plan: dict[str, Any], output_path: Path, repository_root: Path) -> None:
    if output_path.exists() or output_path.is_symlink():
        raise ValueError(f"refusing to overwrite plan output: {output_path}")
    parent = output_path.parent.resolve()
    if is_within(parent, repository_root):
        raise ValueError("plan output must be outside the repository")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # The caller intentionally chooses an exclusive-create path outside the repository.
    output_path.write_text(f"{json.dumps(plan, indent=2)}\n", encoding="utf-8")  # NOSONAR
