#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from skill_evals.aggregation import aggregate_skill_report
from skill_evals.calibration import validate_calibration_report, validate_calibration_set
from skill_evals.evidence import validate_run_manifest
from skill_evals.grading import validate_external_key, validate_grade_report
from skill_evals.key_workflow import write_key_packet
from skill_evals.materialize import sync_suites
from skill_evals.planning import build_plan, write_external_plan
from skill_evals.validation import is_within, read_object, validate_registry

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPOSITORY_ROOT / "evals" / "registry.json"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Validate and report repository Agent Skill eval coverage")
    result.add_argument(
        "command",
        choices=(
            "aggregate",
            "init-key",
            "plan",
            "sync",
            "validate",
            "validate-calibration",
            "validate-grades",
            "validate-key",
            "validate-run",
            "status",
        ),
    )
    result.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    result.add_argument("--require-production", action="store_true")
    result.add_argument("--json", action="store_true")
    result.add_argument("--skill", action="append", default=[])
    result.add_argument("--harness", action="append", default=[])
    result.add_argument("--output", type=Path)
    result.add_argument("--plan", type=Path)
    result.add_argument("--run-manifest", type=Path)
    result.add_argument("--key", type=Path)
    result.add_argument("--grade-report", type=Path)
    result.add_argument("--calibration-set", type=Path)
    result.add_argument("--calibration-report", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "plan":
        if args.output is None:
            raise SystemExit("plan requires --output")
        plan = build_plan(
            REPOSITORY_ROOT,
            skill_names=args.skill,
            harnesses=args.harness,
        )
        write_external_plan(plan, args.output.resolve(), REPOSITORY_ROOT)
        print(f"wrote {len(plan['trials'])} trials for {len(plan['suites'])} skills to {args.output.resolve()}")
        return 0
    if args.command == "init-key":
        if len(args.skill) != 1 or args.output is None:
            raise SystemExit("init-key requires exactly one --skill and --output")
        suite = read_object(REPOSITORY_ROOT / "evals" / args.skill[0] / "suite.json")
        key_path, review_path = write_key_packet(
            suite,
            output_path=args.output.resolve(),
            repository_root=REPOSITORY_ROOT,
        )
        print(f"wrote unsealed key template to {key_path} and review packet to {review_path}")
        return 0
    if args.command == "validate-run":
        if args.plan is None or args.run_manifest is None:
            raise SystemExit("validate-run requires --plan and --run-manifest")
        plan_path = args.plan.resolve()
        run_manifest_path = args.run_manifest.resolve()
        if is_within(plan_path, REPOSITORY_ROOT) or is_within(run_manifest_path, REPOSITORY_ROOT):
            raise SystemExit("run evidence must remain outside the repository")
        errors = validate_run_manifest(
            read_object(plan_path),
            read_object(run_manifest_path),
            evidence_root=run_manifest_path.parent,
        )
        for error in errors:
            print(f"error: {error}")
        if not errors:
            print("run manifest is valid")
        return int(bool(errors))
    if args.command == "validate-calibration":
        if len(args.skill) != 1 or args.key is None:
            raise SystemExit("validate-calibration requires exactly one --skill and --key")
        if args.calibration_set is None or args.calibration_report is None:
            raise SystemExit("validate-calibration requires --calibration-set and --calibration-report")
        skill_name = args.skill[0]
        external_paths = [
            args.key.resolve(),
            args.calibration_set.resolve(),
            args.calibration_report.resolve(),
        ]
        if any(is_within(path, REPOSITORY_ROOT) for path in external_paths):
            raise SystemExit("keys and calibration evidence must remain outside the repository")
        suite_path = REPOSITORY_ROOT / "evals" / skill_name / "suite.json"
        suite = read_object(suite_path)
        key_manifest = read_object(suite_path.parent / "key-manifest.json")
        key_path, calibration_set_path, calibration_report_path = external_paths
        key = read_object(key_path)
        key_digest = sha256(key_path.read_bytes()).hexdigest()
        calibration_set = read_object(calibration_set_path)
        calibration_set_digest = sha256(calibration_set_path.read_bytes()).hexdigest()
        calibration_report = read_object(calibration_report_path)
        errors = validate_external_key(key, suite=suite, key_manifest=key_manifest)
        if key_manifest.get("key_sha256") != key_digest:
            errors.append("external key SHA-256 does not match the sealed key manifest")
        errors.extend(
            validate_calibration_set(
                calibration_set,
                suite=suite,
                key=key,
                key_sha256=key_digest,
            )
        )
        report_errors, metrics = validate_calibration_report(
            calibration_set,
            calibration_report,
            key=key,
            key_sha256=key_digest,
            calibration_set_sha256=calibration_set_digest,
        )
        errors.extend(report_errors)
        payload = {
            **metrics,
            "skill_name": skill_name,
            "reviewer": calibration_set.get("reviewer"),
            "reviewed_at": calibration_set.get("reviewed_at"),
            "key_sha256": key_digest,
            "calibration_set_sha256": calibration_set_digest,
            "calibration_report_sha256": sha256(calibration_report_path.read_bytes()).hexdigest(),
            "errors": errors,
        }
        if errors:
            payload["status"] = "failed"
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"calibration status: {payload.get('status', 'failed')}")
            for error in errors:
                print(f"error: {error}")
            for failure in payload.get("threshold_failures", []):
                print(f"threshold failure: {failure}")
        return int(payload.get("status") != "passed")
    if args.command in {"aggregate", "validate-key", "validate-grades"}:
        if len(args.skill) != 1 or args.key is None:
            raise SystemExit(f"{args.command} requires exactly one --skill and --key")
        skill_name = args.skill[0]
        key_path = args.key.resolve()
        if is_within(key_path, REPOSITORY_ROOT):
            raise SystemExit("plaintext grader keys must remain outside the repository")
        suite_path = REPOSITORY_ROOT / "evals" / skill_name / "suite.json"
        key_manifest_path = suite_path.parent / "key-manifest.json"
        suite = read_object(suite_path)
        key_manifest = read_object(key_manifest_path)
        key_digest = sha256(key_path.read_bytes()).hexdigest()
        errors = validate_external_key(
            read_object(key_path),
            suite=suite,
            key_manifest=key_manifest,
        )
        if key_manifest.get("key_sha256") != key_digest:
            errors.append("external key SHA-256 does not match the sealed key manifest")
        if args.command in {"aggregate", "validate-grades"}:
            if args.plan is None or args.run_manifest is None or args.grade_report is None:
                raise SystemExit(f"{args.command} requires --plan, --run-manifest, and --grade-report")
            external_paths = [
                args.plan.resolve(),
                args.run_manifest.resolve(),
                args.grade_report.resolve(),
            ]
            if any(is_within(path, REPOSITORY_ROOT) for path in external_paths):
                raise SystemExit("run and grading evidence must remain outside the repository")
            plan = read_object(external_paths[0])
            run_manifest = read_object(external_paths[1])
            report = read_object(external_paths[2])
            errors.extend(
                validate_run_manifest(
                    plan,
                    run_manifest,
                    evidence_root=args.run_manifest.resolve().parent,
                )
            )
            errors.extend(
                validate_grade_report(
                    plan,
                    run_manifest,
                    report,
                    key_sha256=key_digest,
                )
            )
            if args.command == "aggregate" and not errors:
                if args.output is None:
                    raise SystemExit("aggregate requires --output")
                output_path = args.output.resolve()
                if is_within(output_path, REPOSITORY_ROOT):
                    raise SystemExit("aggregate output must remain outside the repository")
                if output_path.exists() or output_path.is_symlink():
                    raise SystemExit(f"refusing to overwrite aggregate output: {output_path}")
                aggregate = aggregate_skill_report(suite, plan, run_manifest, report)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(f"{json.dumps(aggregate, indent=2)}\n", encoding="utf-8")
                print(f"wrote {aggregate['status']} aggregate report to {output_path}")
                return int(aggregate["status"] != "passed")
        for error in errors:
            print(f"error: {error}")
        if not errors:
            print(f"{args.command} inputs are valid")
        return int(bool(errors))
    if args.command == "sync":
        sync_suites(REPOSITORY_ROOT, write=True)
    errors, summary = validate_registry(
        REPOSITORY_ROOT,
        args.registry.resolve(),
        require_production=args.require_production,
    )
    if args.registry.resolve() == DEFAULT_REGISTRY.resolve():
        errors.extend(sync_suites(REPOSITORY_ROOT, write=False))
    payload = {
        "discovered": summary.discovered,
        "registered": summary.registered,
        "suites_present": summary.suites_present,
        "statuses": dict(sorted(summary.statuses.items())),
        "production_ready": not errors and summary.statuses.get("production", 0) == summary.discovered,
        "errors": errors,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"skills: {summary.registered}/{summary.discovered} registered; "
            f"suites: {summary.suites_present}; statuses: {dict(sorted(summary.statuses.items()))}"
        )
        for error in errors:
            print(f"error: {error}")
    if args.command == "status":
        return 0
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
