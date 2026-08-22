from __future__ import annotations

import copy
import unittest

from scripts.skill_evals.aggregation import aggregate_skill_report, bootstrap_lower_bound


def fixtures() -> tuple[dict, dict, dict, dict]:
    suite = {
        "skill_name": "sample",
        "execution_policy": {"trials_per_harness": 3},
        "thresholds": {
            "positive_trigger_recall": 0.9,
            "near_miss_abstention": 0.95,
            "paired_delta_ci_lower": 0,
            "critical_regressions": 0,
        },
    }
    plan_trials = []
    run_trials = []
    grades = []
    for number in range(1, 4):
        for arm in ("baseline", "with-skill"):
            trial_id = f"sample.pos-01.claude-code.t{number}.{arm}"
            plan_trials.append(
                {
                    "trial_id": trial_id,
                    "skill_name": "sample",
                    "harness": "claude-code",
                    "task_id": "pos-01",
                    "task_kind": "positive",
                    "trial_number": number,
                    "arm": arm,
                }
            )
            run_trials.append(
                {
                    "trial_id": trial_id,
                    "status": "completed",
                    "reason": None,
                    "skill_loaded": arm == "with-skill",
                    "latency_ms": 10,
                    "input_tokens": 20,
                    "output_tokens": 30,
                    "cost_usd": 0.01,
                }
            )
            grades.append(
                {
                    "trial_id": trial_id,
                    "final_outcome": "pass",
                    "final_score": 0.8 if arm == "with-skill" else 0.5,
                    "critical_failure": False,
                }
            )
        near_id = f"sample.near-01.claude-code.t{number}.with-skill"
        plan_trials.append(
            {
                "trial_id": near_id,
                "skill_name": "sample",
                "harness": "claude-code",
                "task_id": "near-01",
                "task_kind": "near-miss",
                "trial_number": number,
                "arm": "with-skill",
            }
        )
        run_trials.append(
            {
                "trial_id": near_id,
                "status": "completed",
                "reason": None,
                "skill_loaded": False,
                "latency_ms": 10,
                "input_tokens": 20,
                "output_tokens": 30,
                "cost_usd": None,
            }
        )
    return suite, {"trials": plan_trials}, {"trials": run_trials}, {"grades": grades}


class SkillEvalAggregationTests(unittest.TestCase):
    def test_passing_metrics_include_consistency_and_positive_delta(self) -> None:
        suite, plan, run, grades = fixtures()

        report = aggregate_skill_report(suite, plan, run, grades)

        self.assertEqual(report["status"], "passed")
        harness = report["harnesses"][0]
        self.assertEqual(harness["metrics"]["positive_trigger_recall"], 1)
        self.assertEqual(harness["metrics"]["near_miss_abstention"], 1)
        self.assertAlmostEqual(harness["metrics"]["paired_delta_mean"], 0.3)
        self.assertEqual(harness["metrics"]["pass_at_1"], 1)
        self.assertEqual(harness["metrics"]["pass_power_3"], 1)

    def test_trigger_failure_is_reported_as_failed_not_invalid(self) -> None:
        suite, plan, run, grades = fixtures()
        treatment = next(
            trial
            for trial in run["trials"]
            if ".pos-01." in trial["trial_id"] and trial["trial_id"].endswith("with-skill")
        )
        treatment["skill_loaded"] = False

        report = aggregate_skill_report(suite, plan, run, grades)

        self.assertEqual(report["status"], "failed")
        self.assertIn("positive-trigger-recall", report["harnesses"][0]["threshold_failures"])

    def test_whole_unavailable_lane_remains_unavailable(self) -> None:
        suite, plan, run, grades = fixtures()
        unavailable = copy.deepcopy(run)
        for trial in unavailable["trials"]:
            trial["status"] = "unavailable"
            trial["reason"] = "native load state cannot be observed"

        report = aggregate_skill_report(suite, plan, unavailable, grades)

        self.assertEqual(report["status"], "unavailable")
        self.assertIsNone(report["harnesses"][0]["metrics"])

    def test_mixed_passed_and_unavailable_harnesses_are_not_proven(self) -> None:
        suite, plan, run, grades = fixtures()
        extra_plan = []
        extra_run = []
        for planned in plan["trials"]:
            planned_copy = copy.deepcopy(planned)
            planned_copy["trial_id"] = planned_copy["trial_id"].replace(".claude-code.", ".cursor.")
            planned_copy["harness"] = "cursor"
            extra_plan.append(planned_copy)
            extra_run.append(
                {
                    "trial_id": planned_copy["trial_id"],
                    "status": "unavailable",
                    "reason": "native load state cannot be observed",
                }
            )
        plan["trials"].extend(extra_plan)
        run["trials"].extend(extra_run)

        report = aggregate_skill_report(suite, plan, run, grades)

        self.assertEqual(report["status"], "not-proven")

    def test_bootstrap_lower_bound_is_deterministic(self) -> None:
        values = [0.1, 0.2, 0.3, 0.4]

        self.assertEqual(bootstrap_lower_bound(values), bootstrap_lower_bound(values))
        self.assertIsNone(bootstrap_lower_bound([]))


if __name__ == "__main__":
    unittest.main()
