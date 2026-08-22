from __future__ import annotations

import random
from collections import defaultdict
from hashlib import sha256
from statistics import mean
from typing import Any

from .validation import canonical_digest


def bootstrap_lower_bound(values: list[float], *, confidence: float = 0.95) -> float | None:
    if not values:
        return None
    seed = int(sha256(repr(values).encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed)  # NOSONAR - reproducible statistical resampling, not security-sensitive randomness.
    samples = sorted(mean(generator.choice(values) for _ in values) for _ in range(5000))
    index = max(0, int((1 - confidence) / 2 * len(samples)) - 1)
    return samples[index]


def aggregate_skill_report(
    suite: dict[str, Any],
    plan: dict[str, Any],
    run_manifest: dict[str, Any],
    grade_report: dict[str, Any],
) -> dict[str, Any]:
    skill_name = suite["skill_name"]
    plan_trials = {trial["trial_id"]: trial for trial in plan["trials"] if trial["skill_name"] == skill_name}
    run_trials = {trial["trial_id"]: trial for trial in run_manifest["trials"] if trial["trial_id"] in plan_trials}
    grades = {grade["trial_id"]: grade for grade in grade_report["grades"] if grade["trial_id"] in plan_trials}
    harness_reports: list[dict[str, Any]] = []
    for harness in sorted({trial["harness"] for trial in plan_trials.values()}):
        harness_ids = [trial_id for trial_id, trial in plan_trials.items() if trial["harness"] == harness]
        statuses = {run_trials[trial_id]["status"] for trial_id in harness_ids}
        if statuses == {"unavailable"}:
            reasons = sorted({run_trials[trial_id]["reason"] for trial_id in harness_ids})
            harness_reports.append(
                {
                    "harness": harness,
                    "status": "unavailable",
                    "reasons": reasons,
                    "metrics": None,
                    "threshold_failures": [],
                }
            )
            continue

        positive_with_skill = [
            trial_id
            for trial_id in harness_ids
            if plan_trials[trial_id]["task_kind"] == "positive" and plan_trials[trial_id]["arm"] == "with-skill"
        ]
        near_miss = [trial_id for trial_id in harness_ids if plan_trials[trial_id]["task_kind"] == "near-miss"]
        positive_recall = mean(run_trials[trial_id]["skill_loaded"] for trial_id in positive_with_skill)
        near_miss_abstention = mean(not run_trials[trial_id]["skill_loaded"] for trial_id in near_miss)

        pairs: dict[tuple[str, int], dict[str, str]] = defaultdict(dict)
        for trial_id in harness_ids:
            trial = plan_trials[trial_id]
            if trial["task_kind"] == "positive":
                pairs[(trial["task_id"], trial["trial_number"])][trial["arm"]] = trial_id
        deltas: list[float] = []
        unresolved = 0
        critical_regressions = 0
        with_skill_outcomes: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for pair in pairs.values():
            baseline_grade = grades[pair["baseline"]]
            with_skill_grade = grades[pair["with-skill"]]
            if baseline_grade["final_outcome"] == "unknown" or with_skill_grade["final_outcome"] == "unknown":
                unresolved += 1
            else:
                deltas.append(with_skill_grade["final_score"] - baseline_grade["final_score"])
            if with_skill_grade["critical_failure"] and not baseline_grade["critical_failure"]:
                critical_regressions += 1
            treatment_trial = plan_trials[pair["with-skill"]]
            with_skill_outcomes[treatment_trial["task_id"]].append(
                (treatment_trial["trial_number"], with_skill_grade["final_outcome"])
            )

        first_trials = [
            next(outcome for number, outcome in values if number == 1) for values in with_skill_outcomes.values()
        ]
        pass_at_1 = mean(outcome == "pass" for outcome in first_trials)
        pass_power_3 = mean(
            len(values) == suite["execution_policy"]["trials_per_harness"]
            and all(outcome == "pass" for _number, outcome in values)
            for values in with_skill_outcomes.values()
        )
        delta_mean = mean(deltas) if deltas else None
        delta_lower = bootstrap_lower_bound(deltas)
        thresholds = suite["thresholds"]
        threshold_failures: list[str] = []
        if positive_recall < thresholds["positive_trigger_recall"]:
            threshold_failures.append("positive-trigger-recall")
        if near_miss_abstention < thresholds["near_miss_abstention"]:
            threshold_failures.append("near-miss-abstention")
        if delta_lower is None or delta_lower < thresholds["paired_delta_ci_lower"]:
            threshold_failures.append("paired-delta-ci-lower")
        if critical_regressions != thresholds["critical_regressions"]:
            threshold_failures.append("critical-regressions")
        if unresolved:
            threshold_failures.append("unresolved-grades")
        costs = [run_trials[trial_id]["cost_usd"] for trial_id in harness_ids]
        harness_reports.append(
            {
                "harness": harness,
                "status": "passed" if not threshold_failures else "failed",
                "reasons": [],
                "metrics": {
                    "positive_trigger_recall": positive_recall,
                    "near_miss_abstention": near_miss_abstention,
                    "paired_delta_mean": delta_mean,
                    "paired_delta_ci95_lower": delta_lower,
                    "pass_at_1": pass_at_1,
                    "pass_power_3": pass_power_3,
                    "critical_regressions": critical_regressions,
                    "unresolved_grades": unresolved,
                    "latency_ms": sum(run_trials[trial_id]["latency_ms"] for trial_id in harness_ids),
                    "input_tokens": sum(run_trials[trial_id]["input_tokens"] for trial_id in harness_ids),
                    "output_tokens": sum(run_trials[trial_id]["output_tokens"] for trial_id in harness_ids),
                    "cost_usd": sum(cost for cost in costs if cost is not None),
                },
                "threshold_failures": threshold_failures,
            }
        )
    statuses = {report["status"] for report in harness_reports}
    if statuses == {"unavailable"}:
        overall_status = "unavailable"
    elif "failed" in statuses:
        overall_status = "failed"
    elif "unavailable" in statuses:
        overall_status = "not-proven"
    else:
        overall_status = "passed"
    return {
        "schema_version": 1,
        "skill_name": skill_name,
        "suite_canonical_sha256": canonical_digest(suite),
        "plan_sha256": canonical_digest(plan),
        "run_manifest_sha256": canonical_digest(run_manifest),
        "grade_report_sha256": canonical_digest(grade_report),
        "status": overall_status,
        "harnesses": harness_reports,
    }
