from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "create-agent-skill"
sys.path.insert(0, str(SKILL_ROOT))

import scripts.inventory_skills as inventory_skills  # noqa: E402
import scripts.isolation_trace as isolation_trace  # noqa: E402
import scripts.proof_protocol as proof_protocol  # noqa: E402
import scripts.skill_bundle as skill_bundle  # noqa: E402


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def execution_policy(tier: str) -> dict[str, Any]:
    contract = proof_protocol.TIER_CONTRACTS[tier]
    return {
        "tier": tier,
        "mode": tier,
        "runner_instructions": contract["instructions"],
        "allowed_capabilities": sorted(contract["allowed"]),
        "denied_capabilities": sorted(contract["denied"]),
        "artifact_capture": "coordinator-captured-final-response",
        "grading_scope": "response-content-only",
        "tool_events_must_be_absent": contract["tool_events_must_be_absent"],
        "network_must_be_disabled": contract["network_must_be_disabled"],
        "writable_workspace_must_be_absent": contract["writable_workspace_must_be_absent"],
        "fixture_root_required": contract["fixture_root_required"],
        "symlink_free_fixtures": contract["symlink_free_fixtures"],
        "pre_post_hash_required": contract["pre_post_hash_required"],
        "complete_tool_inventory_required": contract["complete_tool_inventory_required"],
    }


def synthetic_runner_pack(tier: str) -> dict[str, Any]:
    prefix = proof_protocol.TIER_CONTRACTS[tier]["case_prefix"]
    return {
        "schema_version": 2,
        "tier": tier,
        "suite": f"synthetic-{tier}-evals",
        "execution_policy": execution_policy(tier),
        "cases": [
            {
                "id": f"{prefix}01",
                "purpose": "Exercise a no-artifact route.",
                "positive_prompt": "Analyze a preview that ends next week.",
                "near_miss_prompt": "Summarize the preview steps.",
                "baseline": "no-skill",
                "setup": ["The preview end date is fixed."],
                "prohibited_effects": [
                    proof_protocol.TIER_CONTRACTS[tier]["core_prohibited_effect"],
                    "Do not publish a durable skill.",
                ],
            }
        ],
    }


def synthetic_key(tier: str) -> dict[str, Any]:
    prefix = proof_protocol.TIER_CONTRACTS[tier]["case_prefix"]
    return {
        "schema_version": 2,
        "tier": tier,
        "suite": f"synthetic-{tier}-evals",
        "cases": [
            {
                "case_id": f"{prefix}01",
                "expected": {"route": "defer", "longevity": "sunset/defer"},
                "checks": [
                    {
                        "id": "c1",
                        "text": "Defers the expiring workflow without claiming any effect.",
                        "kind": "semantic",
                    },
                    {
                        "id": "c2",
                        "text": "Explains why a response-only alternative is appropriate.",
                        "kind": "semantic",
                    },
                ],
            }
        ],
    }


def tool_boundary(host: str) -> dict[str, Any]:
    prefix = host.lower().replace(" ", "-")
    return {
        "tool_catalog_method": f"capture the complete {host} catalog",
        "tool_inventory_locator": {"record_match": {"type": "init"}, "field": "available_tools"},
        "host_tool_map": {
            "fixture-list": [f"{prefix}-list"],
            "fixture-stat": [f"{prefix}-stat"],
            "fixture-read": [f"{prefix}-read"],
            "fixture-search": [f"{prefix}-search"],
        },
        "fixture_root_enforcement": "resolve and reject every path outside the fixture root",
        "network_isolation": "no model-accessible network tools",
        "writable_workspace_isolation": "no writable model workspace",
        "trace_method": "capture the complete structured stream",
        "qualification_status": "verified",
        "qualification_evidence_path": f"coordinator-evidence://read-only-tools/{prefix}-qualification.json",
        "qualification_evidence_sha256": "8" * 64,
        "qualification_notes": [],
        "reliability": "reliable",
        "recheck_date": "2026-11-16",
    }


def safety_controls(host: str) -> dict[str, Any]:
    prefix = host.lower().replace(" ", "-")
    return {
        "host_mode": f"{prefix} strongest non-effectful mode",
        "sandbox": "host-enforced read-only sandbox where available",
        "trace_method": "capture the complete structured stream through terminal completion",
        "task_surface": "hash the immutable fixture root before and after each arm",
        "skill_install": "install only in the native isolated harness skill root",
        "allowed_read_surfaces": ["fixture-root", "skill-bundle"],
        "forbidden_event_kinds": ["write", "network", "external-service", "subagent"],
        "qualification_status": "verified",
        "qualification_evidence_path": f"coordinator-evidence://loaded-content-safe/{prefix}.jsonl",
        "qualification_evidence_sha256": "7" * 64,
        "qualification_notes": [],
        "reliability": "reliable",
        "recheck_date": "2026-11-16",
    }


def synthetic_profile(tier: str, *, reliable: bool = True) -> dict[str, Any]:
    lanes = []
    for lane_id, host, model in (("lane-a", "Host A", "model-a"), ("lane-b", "Host B", "model-b")):
        lane: dict[str, Any] = {
            "lane_id": lane_id,
            "host": host,
            "model": model,
            "reasoning": "high",
            "availability": "verified",
            "load_state_observation": {
                "method": f"trace exact skill injection on {host}",
                "positive_signal": "the positive trace records the injected skill",
                "negative_signal": "the near-miss trace omits the injected skill",
                "reliability": "reliable" if reliable else "unreliable",
            },
        }
        if tier == proof_protocol.READ_ONLY_TOOLS:
            lane["tool_boundary"] = tool_boundary(host)
        elif tier == proof_protocol.LOADED_CONTENT_SAFE:
            lane["safety_controls"] = safety_controls(host)
        else:
            lane.update(
                {
                    "qualification_evidence_path": f"coordinator-evidence://zero-tools/{lane_id}.jsonl",
                    "qualification_evidence_sha256": "0" * 64,
                    "qualification_notes": [],
                }
            )
        lanes.append(lane)
    return {
        "schema_version": 2,
        "tier": tier,
        "suite": f"synthetic-{tier}-evals",
        "snapshot_date": "2026-08-16",
        "runtime_verification_required": True,
        "notice": "Synthetic profile for validator tests.",
        "lanes": lanes,
    }


def synthetic_grader_profile(suite: str, tier: str = proof_protocol.READ_ONLY_TOOLS) -> dict[str, Any]:
    graders = []
    for grader_id, model in (("grader-a", "grader-model-a"), ("grader-b", "grader-model-b")):
        graders.append(
            {
                "grader_id": grader_id,
                "host": "Grader Host",
                "model": model,
                "api_surface": "structured print API with an explicit empty tool set",
                "trace_method": "capture init through terminal result",
                "availability": "verified",
                "execution_tier": "zero-tools",
                "exposed_tools": [],
                "explicit_empty_tool_set": True,
                "model_network_disabled": True,
                "writable_workspace_absent": True,
                "complete_trace": True,
                "verified_at": "2026-08-16T12:00:00Z",
                "evidence_path": f"coordinator-evidence://zero-tools/{grader_id}.jsonl",
                "evidence_sha256": "a" * 64,
                "recheck_date": "2026-11-16",
                "notes": [],
            }
        )
    return {
        "schema_version": 2,
        "tier": tier,
        "suite": suite,
        "profile_type": "independent-zero-tools-graders",
        "snapshot_date": "2026-08-16",
        "runtime_verification_required": True,
        "graders": graders,
    }


def create_synthetic_proof(root: Path, tier: str) -> dict[str, Any]:
    runner = synthetic_runner_pack(tier)
    key = synthetic_key(tier)
    profile = synthetic_profile(tier)
    grader_profile = (
        synthetic_grader_profile(runner["suite"], tier) if tier in proof_protocol.INDEPENDENT_GRADER_TIERS else None
    )
    evidence_root = root / "evidence"
    for lane in profile["lanes"]:
        if tier == proof_protocol.READ_ONLY_TOOLS:
            evidence_source = lane["tool_boundary"]
        elif tier == proof_protocol.LOADED_CONTENT_SAFE:
            evidence_source = lane["safety_controls"]
        else:
            evidence_source = lane
        locator = evidence_source["qualification_evidence_path"]
        relative = proof_protocol.evidence_relative_path(locator)
        if relative is None:
            raise ValueError(f"invalid synthetic evidence locator: {locator}")
        evidence_path = evidence_root.joinpath(*relative.parts)
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(f"qualification evidence for {lane['lane_id']}\n", encoding="utf-8")
        evidence_source["qualification_evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    if grader_profile is not None:
        for grader in grader_profile["graders"]:
            relative = proof_protocol.evidence_relative_path(grader["evidence_path"])
            if relative is None:
                raise ValueError(f"invalid synthetic grader evidence locator: {grader['evidence_path']}")
            evidence_path = evidence_root.joinpath(*relative.parts)
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(f"grader evidence for {grader['grader_id']}\n", encoding="utf-8")
            grader["evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    cases_path = root / "runner" / "cases.json"
    key_path = root / "external-key" / "grader-key.json"
    repository = root / "repository"
    (repository / ".git").mkdir(parents=True)
    manifest_path = repository / "evals" / "manifest.json"
    profile_path = root / "profile.json"
    write_json(cases_path, runner)
    write_json(key_path, key)
    write_json(profile_path, profile)
    manifest = {
        "schema_version": 2,
        "tier": tier,
        "suite": runner["suite"],
        "case_pack_sha256": proof_protocol.case_pack_digest(cases_path),
        "key_sha256": hashlib.sha256(key_path.read_bytes()).hexdigest(),
        "cases": [{"case_id": runner["cases"][0]["id"], "check_count": 2}],
    }
    write_json(manifest_path, manifest)
    report = proof_protocol.initialized_report(
        runner,
        profile,
        manifest,
        manifest["case_pack_sha256"],
        grader_profile,
    )
    return {
        "tier": tier,
        "runner": runner,
        "key": key,
        "profile": profile,
        "grader_profile": grader_profile,
        "manifest": manifest,
        "report": report,
        "cases_path": cases_path,
        "key_path": key_path,
        "manifest_path": manifest_path,
        "profile_path": profile_path,
        "repository": repository,
        "evidence_root": evidence_root,
    }


def normalized_events(profile_lane: dict[str, Any]) -> list[dict[str, str]]:
    boundary = profile_lane["tool_boundary"]["host_tool_map"]
    return [
        {
            "host_tool": names[0],
            "capability": capability,
            "resolved_path": "/fixtures/input.txt",
            "scope": "inside-fixture-root",
            "status": "allowed",
        }
        for capability, names in sorted(boundary.items())
    ]


def loaded_events() -> list[dict[str, str]]:
    return [
        {
            "arm": "baseline",
            "host_tool": "fixture-read",
            "capability": "fixture-read",
            "resolved_path": "/fixtures/input.txt",
            "scope": "inside-fixture-root",
            "status": "allowed",
        },
        {
            "arm": "with-skill",
            "host_tool": "native-skill-loader",
            "capability": "native-skill-load",
            "resolved_path": "/skills/create-agent-skill/SKILL.md",
            "scope": "inside-skill-bundle",
            "status": "allowed",
        },
        {
            "arm": "with-skill",
            "host_tool": "fixture-read",
            "capability": "fixture-read",
            "resolved_path": "/fixtures/input.txt",
            "scope": "inside-fixture-root",
            "status": "allowed",
        },
    ]


def complete_report(fixture: dict[str, Any]) -> dict[str, Any]:
    tier = fixture["tier"]
    report = copy.deepcopy(fixture["report"])
    report["claim"] = proof_protocol.TIER_CONTRACTS[tier]["claim"]
    report["evidence_namespace"] = f"{tier}/synthetic-run"
    report["longevity"] = {
        "verdict": "durable",
        "confidence": "high",
        "factors": {factor: "strong" for factor in proof_protocol.LONGEVITY_FACTORS},
        "rationale": ["The recurring workflow remains useful."],
        "death_modes": ["The host absorbs the workflow."],
        "drift_signals": ["The owning policy changes."],
        "owner": "owner",
        "recheck_date": "2027-02-15",
    }
    key_case = fixture["key"]["cases"][0]
    profile_by_id = {lane["lane_id"]: lane for lane in fixture["profile"]["lanes"]}
    for lane in report["lanes"]:
        profile_lane = profile_by_id[lane["lane_id"]]
        lane["profile"] = {
            "status": "verified",
            "verified_at": "2026-08-16T12:00:00Z",
            "evidence": ["runtime profile trace"],
        }
        if tier == proof_protocol.ZERO_TOOLS:
            lane["isolation"].update(
                {
                    "status": "verified",
                    "verified_at": "2026-08-16T12:00:00Z",
                    "raw_trace": "zero-tools/isolation.jsonl",
                    "raw_trace_sha256": "b" * 64,
                    "exposed_tools": [],
                    "tool_events": [],
                    "forbidden_events": [],
                    "notes": [],
                }
            )
        elif tier == proof_protocol.READ_ONLY_TOOLS:
            tools = sorted(tool for names in profile_lane["tool_boundary"]["host_tool_map"].values() for tool in names)
            lane["isolation"].update(
                {
                    "status": "verified",
                    "verified_at": "2026-08-16T12:00:00Z",
                    "raw_trace": "read-only-tools/isolation.jsonl",
                    "raw_trace_sha256": "b" * 64,
                    "exposed_tools": tools,
                    "tool_events": normalized_events(profile_lane),
                    "fixture_root_id": "fixtures-v1",
                    "pre_sha256": "c" * 64,
                    "post_sha256": "c" * 64,
                    "forbidden_events": [],
                    "notes": [],
                }
            )
        else:
            lane["isolation"].update(
                {
                    "status": "verified",
                    "verified_at": "2026-08-16T12:00:00Z",
                    "raw_trace": "loaded-content-safe/isolation.jsonl",
                    "raw_trace_sha256": "b" * 64,
                    "exposed_tools": ["Read", "Shell", "Write"],
                    "tool_events": loaded_events()[1:2],
                    "fixture_root_id": "fixtures-v1",
                    "pre_sha256": "c" * 64,
                    "post_sha256": "c" * 64,
                    "forbidden_events": [],
                    "notes": ["Broad unused tool exposure is recorded as a caveat."],
                }
            )
        method = profile_lane["load_state_observation"]["method"]
        for field in ("discovery", "positive_trigger", "near_miss"):
            lane[field] = {"status": "pass", "method": method, "evidence": [f"{field} trace"]}
        lane["behavior"]["status"] = "pass"
        lane["behavior"]["evidence"] = ["paired response comparison"]
        result = lane["behavior"]["case_results"][0]
        result.update(
            {
                "status": "pass",
                "observed_route": key_case["expected"]["route"],
                "observed_longevity": key_case["expected"]["longevity"],
                "baseline_artifact": f"{tier}/baseline.txt",
                "with_skill_artifact": f"{tier}/with-skill.txt",
            }
        )
        effects = {
            "mode": tier,
            "status": "clean",
            "baseline_trace": f"{tier}/baseline.jsonl",
            "with_skill_trace": f"{tier}/with-skill.jsonl",
            "forbidden_events": [],
            "notes": [],
        }
        if tier == proof_protocol.READ_ONLY_TOOLS:
            effects.update(
                {
                    "baseline_trace_sha256": "d" * 64,
                    "with_skill_trace_sha256": "e" * 64,
                    "fixture_root_id": "fixtures-v1",
                    "baseline_pre_sha256": "c" * 64,
                    "baseline_post_sha256": "c" * 64,
                    "with_skill_pre_sha256": "c" * 64,
                    "with_skill_post_sha256": "c" * 64,
                    "exposed_tools": lane["isolation"]["exposed_tools"],
                    "tool_events": normalized_events(profile_lane),
                }
            )
        elif tier == proof_protocol.LOADED_CONTENT_SAFE:
            effects.update(
                {
                    "baseline_trace_sha256": "d" * 64,
                    "with_skill_trace_sha256": "e" * 64,
                    "baseline_complete": True,
                    "with_skill_complete": True,
                    "fixture_root_id": "fixtures-v1",
                    "baseline_pre_sha256": "c" * 64,
                    "baseline_post_sha256": "c" * 64,
                    "with_skill_pre_sha256": "c" * 64,
                    "with_skill_post_sha256": "c" * 64,
                    "exposed_tools": lane["isolation"]["exposed_tools"],
                    "tool_events": loaded_events(),
                }
            )
        result["effect_observation"] = effects
        for actual, frozen in zip(result["checks"], key_case["checks"], strict=True):
            actual.update(
                {
                    "check_id": frozen["id"],
                    "check": frozen["text"],
                    "status": "pass",
                    "evidence": ["blind grade"],
                    "grader": "blind-llm",
                }
            )
        if tier == proof_protocol.ZERO_TOOLS:
            grader_lane_id = "lane-b" if lane["lane_id"] == "lane-a" else "lane-a"
            blind = {
                "grader_model": profile_by_id[grader_lane_id]["model"],
                "grader_lane_id": grader_lane_id,
                "grader_id": None,
            }
        else:
            blind = {
                "grader_model": fixture["grader_profile"]["graders"][0]["model"],
                "grader_lane_id": None,
                "grader_id": fixture["grader_profile"]["graders"][0]["grader_id"],
            }
        lane["blind_grading"] = {
            "status": "performed",
            "primary_outcome": "determinate",
            **blind,
            "grader_context": f"fresh-{lane['lane_id']}",
            "arm_labels_anonymized": True,
            "graded_after_both_arms": True,
            "key_custody": "external-coordinator",
            "key_sha256": fixture["manifest"]["key_sha256"],
            "evidence": ["blind grading artifact"],
            "secondary_grading": {
                "status": "not-required",
                "grader_model": None,
                "grader_lane_id": None,
                "grader_id": None,
                "grader_context": None,
                "arm_labels_anonymized": False,
                "graded_after_both_arms": False,
                "evidence": [],
            },
        }
        lane["efficiency"] = {
            "status": "unavailable",
            "baseline": None,
            "with_skill": None,
            "unit": None,
            "notes": ["No comparable usage metric."],
        }
    return report


def validate_complete(fixture: dict[str, Any], report: dict[str, Any]) -> list[str]:
    return proof_protocol.validate_report(
        report,
        fixture["runner"],
        fixture["profile"],
        fixture["manifest"],
        fixture["manifest"]["case_pack_sha256"],
        complete=True,
        key=fixture["key"],
        grader_profile=fixture["grader_profile"],
        evidence_root=fixture["evidence_root"],
    )


class InventorySkillsTests(unittest.TestCase):
    def test_preserves_discovery_path_and_deduplicates_physical_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            physical = root / "vendor" / "example"
            physical.mkdir(parents=True)
            (physical / "SKILL.md").write_text(
                "---\nname: example\ndescription: Publish reviewed changes.\n---\n", encoding="utf-8"
            )
            discovery = root / "skills"
            discovery.mkdir()
            (discovery / "example").symlink_to(physical, target_is_directory=True)
            skills, warnings = inventory_skills.inventory([discovery, physical.parent], ["publish"])
            self.assertEqual(warnings, [])
            self.assertEqual(len(skills), 1)
            self.assertEqual(Path(skills[0].path), discovery / "example")
            self.assertEqual(Path(skills[0].physical_path), physical.resolve())

    def test_does_not_count_frontmatter_as_body(self) -> None:
        text = "---\nname: alpha\ndescription: zebra\n---\nBody text.\n"
        self.assertNotIn("zebra", inventory_skills.skill_body(text))


class SkillBundleTests(unittest.TestCase):
    def test_scaffold_then_validate_and_refuse_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            target = destination / "new-skill"
            self.assertEqual(skill_bundle.scaffold_skill("new-skill", destination, "Use for test workflows.", []), [])
            skill = target / "SKILL.md"
            skill.write_text(
                skill.read_text().replace("TODO: Write concise imperative instructions.", "Follow the workflow."),
                encoding="utf-8",
            )
            self.assertEqual(skill_bundle.validate_skill(target), [])
            self.assertTrue(skill_bundle.scaffold_skill("new-skill", destination, "Use for tests.", []))

    def test_todo_marker_does_not_match_ordinary_words(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            target = destination / "word-test"
            skill_bundle.scaffold_skill("word-test", destination, "Use for tests.", [])
            skill = target / "SKILL.md"
            skill.write_text(
                skill.read_text().replace("TODO: Write concise imperative instructions.", "Follow the workflow."),
                encoding="utf-8",
            )
            skill.write_text(skill.read_text() + "\nMastodon and custodian are ordinary words.\n", encoding="utf-8")
            self.assertFalse(any("TODO" in error for error in skill_bundle.validate_skill(target)))
            skill.write_text(skill.read_text() + "\nTODO: finish.\n", encoding="utf-8")
            self.assertTrue(any("TODO" in error for error in skill_bundle.validate_skill(target)))

    def test_validates_titled_and_reference_style_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            target = destination / "links"
            skill_bundle.scaffold_skill("links", destination, "Use for link tests.", [])
            skill = target / "SKILL.md"
            skill.write_text(
                skill.read_text()
                + '\n[missing](references/missing.md "title")\n[other][missing-ref]\n\n[missing-ref]: references/other.md\n',
                encoding="utf-8",
            )
            errors = skill_bundle.validate_skill(target)
            self.assertTrue(any("references/missing.md" in error for error in errors))
            self.assertTrue(any("references/other.md" in error for error in errors))

    def test_rejects_secret_shaped_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            target = destination / "secret"
            skill_bundle.scaffold_skill("secret", destination, "Use for secret tests.", [])
            fake_access_key = "AKIA" + ("A" * 16)
            (target / "SKILL.md").write_text(
                (target / "SKILL.md").read_text() + f"\n{fake_access_key}\n", encoding="utf-8"
            )
            errors = skill_bundle.validate_skill(target)
            self.assertTrue(any("secret-shaped" in error for error in errors))
            self.assertNotIn(fake_access_key, "\n".join(errors))


class ProofProtocolTests(unittest.TestCase):
    def test_shipped_tiers_are_separate_and_structurally_valid(self) -> None:
        tiers = {
            proof_protocol.ZERO_TOOLS: (
                REPOSITORY_ROOT / "evals" / "create-agent-skill-zero-tools" / "runner-pack.json",
                SKILL_ROOT / "references" / "zero-tools-target-profile.json",
                REPOSITORY_ROOT / "evals" / "create-agent-skill-zero-tools" / "key-manifest.json",
            ),
            proof_protocol.READ_ONLY_TOOLS: (
                REPOSITORY_ROOT / "evals" / "create-agent-skill-read-only" / "runner-pack.json",
                SKILL_ROOT / "references" / "read-only-target-profile.json",
                REPOSITORY_ROOT / "evals" / "create-agent-skill-read-only" / "key-manifest.json",
            ),
            proof_protocol.LOADED_CONTENT_SAFE: (
                REPOSITORY_ROOT / "evals" / "create-agent-skill-loaded-content" / "runner-pack.json",
                SKILL_ROOT / "references" / "loaded-content-target-profile.json",
                REPOSITORY_ROOT / "evals" / "create-agent-skill-loaded-content" / "key-manifest.json",
            ),
        }
        packs = []
        manifests = []
        for tier, (pack_path, profile_path, manifest_path) in tiers.items():
            pack = json.loads(pack_path.read_text())
            profile = json.loads(profile_path.read_text())
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(proof_protocol.validate_cases(pack), [])
            self.assertEqual(proof_protocol.validate_profile(profile), [])
            self.assertEqual(
                proof_protocol.validate_manifest(
                    manifest,
                    pack,
                    proof_protocol.case_pack_digest(pack_path),
                    complete=manifest["key_sha256"] != proof_protocol.PENDING_KEY_DIGEST,
                ),
                [],
            )
            self.assertEqual(pack["tier"], tier)
            self.assertEqual(profile["tier"], tier)
            self.assertEqual(manifest["tier"], tier)
            self.assertFalse(any("expected" in case or "checks" in case for case in pack["cases"]))
            packs.append(pack)
            manifests.append(manifest)
        self.assertEqual(len({pack["suite"] for pack in packs}), 3)
        self.assertEqual(len({manifest["case_pack_sha256"] for manifest in manifests}), 3)
        sealed_key_digests = {
            manifest["key_sha256"]
            for manifest in manifests
            if manifest["key_sha256"] != proof_protocol.PENDING_KEY_DIGEST
        }
        self.assertEqual(len(sealed_key_digests), len(tiers))
        self.assertFalse((SKILL_ROOT / "assets" / "self-evals.json").exists())
        self.assertEqual(list((SKILL_ROOT / "assets").glob("*self-evals.json")), [])
        self.assertFalse((SKILL_ROOT / "references" / "target-profile.json").exists())
        repository_manifest = json.loads(
            (REPOSITORY_ROOT / "evals" / "create-agent-skill" / "key-manifest.json").read_text()
        )
        self.assertEqual(repository_manifest["key_sha256"], proof_protocol.PENDING_KEY_DIGEST)
        self.assertFalse(any("expected" in case or "checks" in case for case in repository_manifest["cases"]))
        self.assertFalse((SKILL_ROOT / "tests").exists())
        self.assertTrue(Path(__file__).is_relative_to(REPOSITORY_ROOT / "tests"))

    def test_shipped_loaded_content_fixtures_are_complete_and_symlink_free(self) -> None:
        fixture_root = REPOSITORY_ROOT / "evals" / "create-agent-skill-loaded-content" / "fixtures"
        expected_files = {
            "lds-01": {"skills-inventory.json", "publishing-workflow.md"},
            "lds-02": {"review-skill.md", "ownership.json", "unrelated-label-admin.md"},
            "lds-03": {"preview-facts.json", "one-time-steps.md"},
            "lds-04": {
                "access-review-sop.md",
                "evidence-ledger.json",
                "owner.json",
                "public-overview.md",
            },
        }
        self.assertEqual({path.name for path in fixture_root.iterdir()}, set(expected_files))
        for case_id, filenames in expected_files.items():
            case_root = fixture_root / case_id
            self.assertTrue(case_root.is_dir())
            self.assertEqual({path.name for path in case_root.iterdir()}, filenames)
            self.assertFalse(any(path.is_symlink() for path in case_root.rglob("*")))

    def test_shipped_independent_grader_profile_is_valid(self) -> None:
        profiles = (
            (
                "read-only-tools",
                "create-agent-skill-read-only-self-evals",
                SKILL_ROOT / "references" / "read-only-grader-profile.json",
            ),
            (
                "loaded-content-safe",
                "create-agent-skill-loaded-content-self-evals",
                SKILL_ROOT / "references" / "loaded-content-grader-profile.json",
            ),
        )
        for tier, suite, path in profiles:
            profile = json.loads(path.read_text())
            self.assertEqual(proof_protocol.validate_grader_profile(profile, suite, tier), [])
            self.assertGreaterEqual(len(profile["graders"]), 1)
            self.assertTrue(all(grader["exposed_tools"] == [] for grader in profile["graders"]))
            self.assertNotIn("claude-opus-5-zero-tools", {grader["grader_id"] for grader in profile["graders"]})

    def test_schema_v1_and_wrong_case_prefix_require_migration(self) -> None:
        pack = synthetic_runner_pack(proof_protocol.ZERO_TOOLS)
        pack["schema_version"] = 1
        pack["cases"][0]["id"] = "rdo-01"
        errors = proof_protocol.validate_cases(pack)
        self.assertTrue(any("requires migration" in error for error in errors))
        self.assertTrue(any("must start with tier prefix 'zro-'" in error for error in errors))

    def test_each_tier_complete_report_round_trips(self) -> None:
        for tier in (
            proof_protocol.ZERO_TOOLS,
            proof_protocol.READ_ONLY_TOOLS,
            proof_protocol.LOADED_CONTENT_SAFE,
        ):
            with self.subTest(tier=tier), tempfile.TemporaryDirectory() as temporary_directory:
                fixture = create_synthetic_proof(Path(temporary_directory), tier)
                self.assertEqual(proof_protocol.validate_cases(fixture["runner"]), [])
                self.assertEqual(proof_protocol.validate_profile(fixture["profile"]), [])
                self.assertEqual(validate_complete(fixture, complete_report(fixture)), [])

    def test_claims_are_tier_derived_and_never_convert(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.ZERO_TOOLS)
            report = complete_report(fixture)
            report["claim"] = "portable"
            errors = validate_complete(fixture, report)
            self.assertTrue(any("ambiguous" in error for error in errors))
            report["claim"] = "portable-read-only-tools"
            errors = validate_complete(fixture, report)
            self.assertTrue(any("claim must be one of" in error for error in errors))

    def test_cross_tier_pack_profile_manifest_key_and_report_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            zero = create_synthetic_proof(root / "zero", proof_protocol.ZERO_TOOLS)
            read = create_synthetic_proof(root / "read", proof_protocol.READ_ONLY_TOOLS)
            report = complete_report(zero)
            errors = proof_protocol.validate_report(
                report,
                zero["runner"],
                read["profile"],
                read["manifest"],
                zero["manifest"]["case_pack_sha256"],
                complete=True,
                key=read["key"],
                grader_profile=read["grader_profile"],
            )
            self.assertTrue(any("target profile tier differs" in error for error in errors))
            self.assertTrue(any("key manifest" in error for error in errors))
            self.assertTrue(any("grader key tier differs" in error for error in errors))

    def test_read_only_requires_independent_zero_tools_grader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.READ_ONLY_TOOLS)
            report = complete_report(fixture)
            errors = proof_protocol.validate_report(
                report,
                fixture["runner"],
                fixture["profile"],
                fixture["manifest"],
                fixture["manifest"]["case_pack_sha256"],
                complete=True,
                key=fixture["key"],
            )
            self.assertTrue(any("requires an independent grader profile" in error for error in errors))
            report["lanes"][0]["blind_grading"]["grader_model"] = "model-a"
            errors = validate_complete(fixture, report)
            self.assertTrue(any("must differ from lane model" in error for error in errors))

            stale = copy.deepcopy(fixture["grader_profile"])
            stale["graders"][0]["recheck_date"] = "2026-08-15"
            self.assertTrue(any("is stale" in error for error in proof_protocol.validate_grader_profile(stale)))

            tool_exposed = copy.deepcopy(fixture["grader_profile"])
            tool_exposed["graders"][0]["exposed_tools"] = ["Read"]
            self.assertTrue(
                any(
                    "exposed_tools must be empty" in error
                    for error in proof_protocol.validate_grader_profile(tool_exposed)
                )
            )

            alias_fixture = create_synthetic_proof(Path(temporary_directory) / "alias", proof_protocol.READ_ONLY_TOOLS)
            alias_fixture["profile"]["lanes"][0]["model"] = "claude-opus-5[1m]"
            alias_fixture["grader_profile"]["graders"][0]["model"] = "claude-opus-5"
            alias_report = complete_report(alias_fixture)
            alias_report["lanes"][0]["model"] = "claude-opus-5[1m]"
            alias_report["lanes"][0]["blind_grading"]["grader_model"] = "claude-opus-5"
            self.assertTrue(
                any("after alias normalization" in error for error in validate_complete(alias_fixture, alias_report))
            )

    def test_read_only_isolation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.READ_ONLY_TOOLS)
            mutations = [
                (
                    "unknown tool",
                    lambda report: report["lanes"][0]["isolation"]["exposed_tools"].append("shell"),
                    "exact profile allowlist",
                ),
                (
                    "path escape",
                    lambda report: report["lanes"][0]["isolation"]["tool_events"][0].update(
                        {"resolved_path": "/outside", "scope": "outside-fixture-root", "status": "allowed"}
                    ),
                    "cannot allow a tool event outside",
                ),
                (
                    "changed hash",
                    lambda report: report["lanes"][0]["isolation"].__setitem__("post_sha256", "9" * 64),
                    "differs from post_sha256",
                ),
                (
                    "case-level unknown exposed tool",
                    lambda report: report["lanes"][0]["behavior"]["case_results"][0]["effect_observation"][
                        "exposed_tools"
                    ].append("shell"),
                    "effect_observation.exposed_tools differs from the exact profile allowlist",
                ),
                (
                    "case fixture id differs from lane isolation",
                    lambda report: report["lanes"][0]["behavior"]["case_results"][0]["effect_observation"].__setitem__(
                        "fixture_root_id", "other-fixtures"
                    ),
                    "differs from lane isolation fixture_root_id",
                ),
                (
                    "case fixture digest differs from lane isolation",
                    lambda report: report["lanes"][0]["behavior"]["case_results"][0]["effect_observation"].__setitem__(
                        "with_skill_pre_sha256", "9" * 64
                    ),
                    "differs from lane isolation fixture digest",
                ),
                (
                    "case has no fixture reads",
                    lambda report: report["lanes"][0]["behavior"]["case_results"][0]["effect_observation"].__setitem__(
                        "tool_events", []
                    ),
                    "must include at least one allowed in-root fixture read event",
                ),
            ]
            for label, mutate, expected in mutations:
                with self.subTest(label=label):
                    report = complete_report(fixture)
                    mutate(report)
                    self.assertTrue(any(expected in error for error in validate_complete(fixture, report)))

    def test_loaded_content_allows_unused_tool_exposure_but_requires_native_load_and_clean_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.LOADED_CONTENT_SAFE)
            report = complete_report(fixture)
            self.assertEqual(validate_complete(fixture, report), [])
            self.assertIn("Write", report["lanes"][0]["isolation"]["exposed_tools"])

            no_load = complete_report(fixture)
            no_load["lanes"][0]["behavior"]["case_results"][0]["effect_observation"]["tool_events"] = [
                event for event in loaded_events() if event["capability"] != "native-skill-load"
            ]
            self.assertTrue(
                any("must prove native skill loading" in error for error in validate_complete(fixture, no_load))
            )

            baseline_load = complete_report(fixture)
            baseline_load["lanes"][0]["behavior"]["case_results"][0]["effect_observation"]["tool_events"].append(
                {
                    "arm": "baseline",
                    "host_tool": "native-skill-loader",
                    "capability": "native-skill-load",
                    "resolved_path": "/skills/create-agent-skill/SKILL.md",
                    "scope": "inside-skill-bundle",
                    "status": "allowed",
                }
            )
            self.assertTrue(
                any(
                    "must not load the skill in the baseline arm" in error
                    for error in validate_complete(fixture, baseline_load)
                )
            )

            changed = complete_report(fixture)
            changed["lanes"][0]["behavior"]["case_results"][0]["effect_observation"]["with_skill_post_sha256"] = (
                "9" * 64
            )
            self.assertTrue(
                any("differs from with_skill_post_sha256" in error for error in validate_complete(fixture, changed))
            )

            forbidden = complete_report(fixture)
            forbidden["lanes"][0]["behavior"]["case_results"][0]["effect_observation"]["tool_events"].append(
                {
                    "arm": "with-skill",
                    "host_tool": "Write",
                    "capability": "filesystem-write",
                    "resolved_path": "/fixtures/output.txt",
                    "scope": "inside-fixture-root",
                    "status": "allowed",
                }
            )
            self.assertTrue(
                any("capability must be one of" in error for error in validate_complete(fixture, forbidden))
            )

    def test_zero_tools_rejects_tool_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.ZERO_TOOLS)
            report = complete_report(fixture)
            report["lanes"][0]["isolation"]["exposed_tools"] = ["Read"]
            errors = validate_complete(fixture, report)
            self.assertTrue(any("must be empty for zero-tools" in error for error in errors))

    def test_route_longevity_and_semantic_checks_bind_to_external_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.ZERO_TOOLS)
            report = complete_report(fixture)
            result = report["lanes"][0]["behavior"]["case_results"][0]
            result["observed_route"] = "create"
            result["observed_longevity"] = "durable"
            result["checks"][0]["check"] = "changed criterion"
            errors = validate_complete(fixture, report)
            self.assertTrue(any("observed_route" in error and "defer" in error for error in errors))
            self.assertTrue(any("observed_longevity" in error and "sunset/defer" in error for error in errors))
            self.assertTrue(any("differs from disclosed key check" in error for error in errors))

    def test_external_key_guards_and_semantic_only_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture = create_synthetic_proof(root, proof_protocol.ZERO_TOOLS)
            resolved, errors = proof_protocol.validate_key_path(fixture["key_path"], fixture["manifest_path"])
            self.assertEqual(errors, [])
            self.assertEqual(resolved, fixture["key_path"].resolve())
            repository_key = fixture["repository"] / "key.json"
            write_json(repository_key, fixture["key"])
            _, errors = proof_protocol.validate_key_path(repository_key, fixture["manifest_path"])
            self.assertTrue(any("must be outside Git repository" in error for error in errors))
            symlinked_key = fixture["repository"] / "external-key-link.json"
            symlinked_key.symlink_to(fixture["key_path"])
            _, errors = proof_protocol.validate_key_path(symlinked_key, fixture["manifest_path"])
            self.assertTrue(any("must not be a symlink" in error for error in errors))
            symlinked_parent = fixture["repository"] / "evals" / "external-key-directory"
            symlinked_parent.symlink_to(fixture["key_path"].parent, target_is_directory=True)
            _, errors = proof_protocol.validate_key_path(
                symlinked_parent / fixture["key_path"].name,
                fixture["manifest_path"],
            )
            self.assertTrue(any("must not contain symlinks" in error for error in errors))
            bad_key = copy.deepcopy(fixture["key"])
            bad_key["cases"][0]["checks"][0]["command"] = "pytest"
            self.assertTrue(
                any(
                    "command is forbidden" in error
                    for error in proof_protocol.validate_key(bad_key, fixture["runner"], fixture["manifest"])
                )
            )

    def test_verified_zero_tools_profile_and_all_complete_reports_bind_evidence_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.ZERO_TOOLS)
            profile_without_evidence = copy.deepcopy(fixture["profile"])
            profile_without_evidence["lanes"][0]["qualification_evidence_path"] = None
            profile_without_evidence["lanes"][0]["qualification_evidence_sha256"] = None
            errors = proof_protocol.validate_profile(profile_without_evidence)
            self.assertTrue(any("qualification_evidence_path" in error for error in errors))

            report = complete_report(fixture)
            self.assertEqual(validate_complete(fixture, report), [])
            evidence_path = next(fixture["evidence_root"].rglob("lane-a.jsonl"))
            evidence_path.write_text("drifted qualification evidence\n", encoding="utf-8")
            errors = validate_complete(fixture, report)
            self.assertTrue(any("differs from recorded SHA-256" in error for error in errors))
            errors = proof_protocol.validate_report(
                report,
                fixture["runner"],
                fixture["profile"],
                fixture["manifest"],
                fixture["manifest"]["case_pack_sha256"],
                complete=True,
                key=fixture["key"],
                grader_profile=fixture["grader_profile"],
            )
            self.assertTrue(any("requires --evidence-root" in error for error in errors))

    def test_indeterminate_or_conflicting_primary_grade_requires_independent_secondary_grade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.READ_ONLY_TOOLS)
            report = complete_report(fixture)
            blind = report["lanes"][0]["blind_grading"]
            blind["primary_outcome"] = "indeterminate"
            errors = validate_complete(fixture, report)
            self.assertTrue(any("secondary_grading.status must be 'performed'" in error for error in errors))

            secondary = blind["secondary_grading"]
            secondary.update(
                {
                    "status": "performed",
                    "grader_model": fixture["grader_profile"]["graders"][1]["model"],
                    "grader_lane_id": None,
                    "grader_id": fixture["grader_profile"]["graders"][1]["grader_id"],
                    "grader_context": "fresh-secondary",
                    "arm_labels_anonymized": True,
                    "graded_after_both_arms": True,
                    "evidence": ["independent secondary blind grade"],
                }
            )
            self.assertEqual(validate_complete(fixture, report), [])
            secondary["grader_model"] = blind["grader_model"]
            secondary["grader_id"] = blind["grader_id"]
            self.assertTrue(
                any("must differ from primary grader model" in error for error in validate_complete(fixture, report))
            )

    def test_indeterminate_primary_grade_can_fail_closed_when_no_second_grader_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.READ_ONLY_TOOLS)
            fixture["grader_profile"]["graders"] = fixture["grader_profile"]["graders"][:1]
            report = complete_report(fixture)
            report["claim"] = "not-proven"
            blind = report["lanes"][0]["blind_grading"]
            blind["primary_outcome"] = "indeterminate"
            blind["secondary_grading"].update(
                {
                    "status": "unavailable",
                    "evidence": ["no different-model verified grader is qualified"],
                }
            )
            self.assertEqual(validate_complete(fixture, report), [])
            report["claim"] = "portable-read-only-tools"
            self.assertTrue(
                any("claim cannot be portable-read-only-tools" in error for error in validate_complete(fixture, report))
            )

    def test_secondary_unavailable_is_rejected_when_an_eligible_grader_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.READ_ONLY_TOOLS)
            report = complete_report(fixture)
            report["claim"] = "not-proven"
            blind = report["lanes"][0]["blind_grading"]
            blind["primary_outcome"] = "conflict"
            blind["secondary_grading"].update(
                {
                    "status": "unavailable",
                    "evidence": ["second grade was not requested"],
                }
            )
            self.assertTrue(any("must be 'performed'" in error for error in validate_complete(fixture, report)))

    def test_model_identity_normalizes_documented_aliases_only(self) -> None:
        expected = "claude-opus-5"
        aliases = (
            "claude-opus-5[1m]",
            "Claude_Opus_5",
            "claude-opus-5 (1m)",
            "claude-opus-5-1m",
            "anthropic/claude-opus-5",
            "claude-opus-5-20260101",
        )
        self.assertTrue(all(proof_protocol.canonical_model_identity(alias) == expected for alias in aliases))
        self.assertNotEqual(proof_protocol.canonical_model_identity("claude-sonnet-5"), expected)
        self.assertNotEqual(proof_protocol.canonical_model_identity("claude-opus-4"), expected)

    def test_unavailable_lane_blocks_only_its_tier_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture = create_synthetic_proof(Path(temporary_directory), proof_protocol.READ_ONLY_TOOLS)
            fixture["profile"]["lanes"][0]["availability"] = "unavailable"
            report = complete_report(fixture)
            lane = report["lanes"][0]
            lane["availability"] = "unavailable"
            lane["profile"].update({"status": "unavailable", "evidence": ["unsupported"]})
            lane["isolation"].update(
                {"status": "unavailable", "forbidden_events": ["unsupported"], "notes": ["unsupported"]}
            )
            report["claim"] = "not-proven"
            self.assertFalse(any("portable-zero-tools" in error for error in validate_complete(fixture, report)))
            report["claim"] = "portable-read-only-tools"
            self.assertTrue(
                any("claim cannot be portable-read-only-tools" in error for error in validate_complete(fixture, report))
            )


class IsolationTraceTests(unittest.TestCase):
    def make_profile(self, host: str, tool: str) -> dict[str, Any]:
        profile = synthetic_profile(proof_protocol.READ_ONLY_TOOLS)
        lane = profile["lanes"][0]
        lane["host"] = host
        lane["tool_boundary"]["host_tool_map"] = {
            "fixture-list": [f"{tool}-list"],
            "fixture-stat": [f"{tool}-stat"],
            "fixture-read": [tool],
            "fixture-search": [f"{tool}-search"],
        }
        lane["tool_boundary"]["tool_inventory_locator"] = {
            "claude": {"record_match": {"type": "system", "subtype": "init"}, "field": "tools"},
            "cursor": {"record_match": {"type": "init"}, "field": "available_tools"},
            "antigravity": {"record_match": {"type": "init"}, "field": "tool_catalog"},
            "codex": {"record_match": {"type": "thread.started"}, "field": "tools"},
        }[host]
        return profile

    def make_events(self, host: str, tool: str, fixture: Path) -> list[dict[str, Any]]:
        inventory = [f"{tool}-list", f"{tool}-stat", tool, f"{tool}-search"]
        if host == "claude":
            return [
                {"type": "system", "subtype": "init", "tools": inventory},
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": tool, "input": {"path": str(fixture)}}]},
                },
                {
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "immutable"}]},
                },
                {"type": "result", "subtype": "success"},
            ]
        if host == "cursor":
            return [
                {"type": "init", "available_tools": inventory},
                {"event": "tool_call", "tool_name": tool, "arguments": {"filePath": str(fixture)}},
                {"type": "success"},
            ]
        if host == "antigravity":
            return [
                {"type": "init", "tool_catalog": inventory},
                {"type": "tool_use", "name": tool, "input": {"AbsolutePath": str(fixture)}},
                {"status": "SUCCESS"},
            ]
        return [
            {"type": "thread.started", "tools": inventory},
            {"type": "item.completed", "item": {"type": tool, "path": str(fixture)}},
            {"type": "turn.completed"},
        ]

    def normalize(self, root: Path, host: str, *, post_hash: str | None = None) -> dict[str, Any]:
        fixture = root / "fixtures"
        fixture.mkdir(parents=True)
        input_file = fixture / "input.txt"
        input_file.write_text("immutable", encoding="utf-8")
        tool = f"{host}-read"
        trace = root / "trace.jsonl"
        trace.write_text(
            "\n".join(json.dumps(event) for event in self.make_events(host, tool, input_file)) + "\n",
            encoding="utf-8",
        )
        digest = "a" * 64
        return isolation_trace.normalize(
            host=host,
            events=isolation_trace.load_jsonl(trace),
            profile=self.make_profile(host, tool),
            lane_id="lane-a",
            fixture_root=fixture,
            fixture_root_id="fixtures-v1",
            pre_sha256=digest,
            post_sha256=post_hash or digest,
            purpose="behavior",
            raw_trace=trace,
        )

    def test_all_four_host_adapters_normalize_verified_read_events(self) -> None:
        for host in sorted(isolation_trace.HOSTS):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary_directory:
                result = self.normalize(Path(temporary_directory), host)
                self.assertEqual(result["status"], "verified")
                self.assertTrue(result["complete"])
                self.assertEqual(result["forbidden_events"], [])
                self.assertEqual(result["tool_events"][0]["scope"], "inside-fixture-root")

    def test_inventory_uses_only_declared_host_record_and_rejects_missing_or_conflicting_catalogs(self) -> None:
        locator = {"record_match": {"type": "system", "subtype": "init"}, "field": "tools"}
        events = [
            {
                "type": "system",
                "subtype": "init",
                "enabled_tools": ["Read", "Bash", "Write"],
                "metadata": {"tools": ["Read"]},
            }
        ]
        with self.assertRaisesRegex(ValueError, "field is absent or empty"):
            isolation_trace.extract_inventory(events, locator)

        events[0]["tools"] = ["Read"]
        self.assertEqual(isolation_trace.extract_inventory(events, locator), ["Read"])
        events.append({"type": "system", "subtype": "init", "tools": ["Read", "Bash"]})
        with self.assertRaisesRegex(ValueError, "records disagree"):
            isolation_trace.extract_inventory(events, locator)

    def test_status_uses_structured_fields_and_cannot_be_masked_by_message_text(self) -> None:
        self.assertEqual(
            isolation_trace.observed_status({"message": "read succeeded; no permission changes were made"}),
            "allowed",
        )
        self.assertEqual(isolation_trace.observed_status({"message": "errors: 0"}), "allowed")
        self.assertEqual(isolation_trace.observed_status({"status": "denied", "message": "ok"}), "denied")
        for status in ("mystery", "partial", "truncated", "cached", "warning"):
            with self.subTest(status=status):
                self.assertEqual(isolation_trace.observed_status({"status": status}), "allowed")

    def test_unknown_status_cannot_mask_an_outside_root_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture = root / "fixtures"
            fixture.mkdir()
            outside = root / "outside.txt"
            outside.write_text("safe sentinel", encoding="utf-8")
            tool = "claude-read"
            events = self.make_events("claude", tool, outside)
            events[1]["message"]["content"][0]["status"] = "partial"
            trace = root / "trace.jsonl"
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            result = isolation_trace.normalize(
                host="claude",
                events=isolation_trace.load_jsonl(trace),
                profile=self.make_profile("claude", tool),
                lane_id="lane-a",
                fixture_root=fixture,
                fixture_root_id="fixtures-v1",
                pre_sha256="a" * 64,
                post_sha256="a" * 64,
                purpose="behavior",
                raw_trace=trace,
            )
            self.assertEqual(result["status"], "unavailable")
            self.assertTrue(any("outside the fixture root" in event for event in result["forbidden_events"]))

    def test_bookkeeping_events_cannot_hide_outside_paths_or_forbidden_tools(self) -> None:
        for bookkeeping_type in sorted(isolation_trace.TOOL_BOOKKEEPING_EVENT_TYPES):
            with self.subTest(bookkeeping_type=bookkeeping_type), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                fixture = root / "fixtures"
                fixture.mkdir()
                inside = fixture / "input.txt"
                inside.write_text("immutable", encoding="utf-8")
                outside = root / "outside.txt"
                outside.write_text("safe sentinel", encoding="utf-8")
                tool = "claude-read"
                events = self.make_events("claude", tool, inside)
                events.insert(
                    -1,
                    {
                        "type": bookkeeping_type,
                        "name": "Bash",
                        "input": {"command": "forbidden shell command", "path": str(outside)},
                    },
                )
                trace = root / "trace.jsonl"
                trace.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
                result = isolation_trace.normalize(
                    host="claude",
                    events=isolation_trace.load_jsonl(trace),
                    profile=self.make_profile("claude", tool),
                    lane_id="lane-a",
                    fixture_root=fixture,
                    fixture_root_id="fixtures-v1",
                    pre_sha256="a" * 64,
                    post_sha256="a" * 64,
                    purpose="behavior",
                    raw_trace=trace,
                )
                self.assertEqual(result["status"], "unavailable")
                self.assertTrue(
                    any("unknown or forbidden tool event: Bash" in event for event in result["forbidden_events"])
                )
                self.assertTrue(any("outside the fixture root" in event for event in result["forbidden_events"]))

    def test_unnamed_bookkeeping_result_still_checks_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture = root / "fixtures"
            fixture.mkdir()
            inside = fixture / "input.txt"
            inside.write_text("immutable", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("safe sentinel", encoding="utf-8")
            tool = "claude-read"
            events = self.make_events("claude", tool, inside)
            events[2]["message"]["content"][0]["input"] = {"path": str(outside)}
            trace = root / "trace.jsonl"
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            result = isolation_trace.normalize(
                host="claude",
                events=isolation_trace.load_jsonl(trace),
                profile=self.make_profile("claude", tool),
                lane_id="lane-a",
                fixture_root=fixture,
                fixture_root_id="fixtures-v1",
                pre_sha256="a" * 64,
                post_sha256="a" * 64,
                purpose="behavior",
                raw_trace=trace,
            )
            self.assertEqual(result["status"], "unavailable")
            self.assertTrue(any("outside the fixture root" in event for event in result["forbidden_events"]))

    def test_codex_item_bookkeeping_cannot_hide_a_forbidden_tool_or_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture = root / "fixtures"
            fixture.mkdir()
            inside = fixture / "input.txt"
            inside.write_text("immutable", encoding="utf-8")
            outside = root / "outside.txt"
            outside.write_text("safe sentinel", encoding="utf-8")
            tool = "codex-read"
            events = self.make_events("codex", tool, inside)
            events.insert(
                -1,
                {
                    "type": "item.completed",
                    "item": {"type": "tool_output", "name": "shell", "path": str(outside)},
                },
            )
            trace = root / "trace.jsonl"
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            result = isolation_trace.normalize(
                host="codex",
                events=isolation_trace.load_jsonl(trace),
                profile=self.make_profile("codex", tool),
                lane_id="lane-a",
                fixture_root=fixture,
                fixture_root_id="fixtures-v1",
                pre_sha256="a" * 64,
                post_sha256="a" * 64,
                purpose="behavior",
                raw_trace=trace,
            )
            self.assertEqual(result["status"], "unavailable")
            self.assertTrue(
                any("unknown or forbidden tool event: shell" in event for event in result["forbidden_events"])
            )
            self.assertTrue(any("outside the fixture root" in event for event in result["forbidden_events"]))

    def test_unknown_tool_path_escape_incomplete_trace_and_hash_change_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            changed = self.normalize(root / "changed", "claude", post_hash="b" * 64)
            self.assertEqual(changed["status"], "unavailable")
            self.assertTrue(any("changed" in event for event in changed["forbidden_events"]))

            fixture = root / "escape" / "fixtures"
            fixture.mkdir(parents=True)
            outside = root / "outside.txt"
            outside.write_text("safe sentinel", encoding="utf-8")
            trace = root / "escape" / "trace.jsonl"
            tool = "claude-read"
            events = self.make_events("claude", tool, outside)
            events[0]["tools"].pop()
            events[0]["tools"].append("Bash")
            events.insert(2, {"type": "mystery_tool_call", "name": "Surprise", "input": {"path": str(outside)}})
            events.pop()
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            result = isolation_trace.normalize(
                host="claude",
                events=isolation_trace.load_jsonl(trace),
                profile=self.make_profile("claude", tool),
                lane_id="lane-a",
                fixture_root=fixture,
                fixture_root_id="fixtures-v1",
                pre_sha256="a" * 64,
                post_sha256="a" * 64,
                purpose="behavior",
                raw_trace=trace,
            )
            self.assertEqual(result["status"], "unavailable")
            combined = " ".join(result["forbidden_events"])
            self.assertIn("unknown exposed tool", combined)
            self.assertIn("required mapped tool", combined)
            self.assertIn("unknown or forbidden tool event", combined)
            self.assertIn("outside the fixture root", combined)
            self.assertIn("no recognized terminal", combined)

    def test_behavior_symlink_is_rejected_and_output_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture = root / "fixtures"
            fixture.mkdir()
            outside = root / "sentinel.txt"
            outside.write_text("safe", encoding="utf-8")
            (fixture / "escape").symlink_to(outside)
            tool = "claude-read"
            trace = root / "trace.jsonl"
            trace.write_text(
                "\n".join(json.dumps(event) for event in self.make_events("claude", tool, fixture / "escape")) + "\n",
                encoding="utf-8",
            )
            result = isolation_trace.normalize(
                host="claude",
                events=isolation_trace.load_jsonl(trace),
                profile=self.make_profile("claude", tool),
                lane_id="lane-a",
                fixture_root=fixture,
                fixture_root_id="fixtures-v1",
                pre_sha256="a" * 64,
                post_sha256="a" * 64,
                purpose="behavior",
                raw_trace=trace,
            )
            self.assertEqual(result["status"], "unavailable")
            self.assertTrue(any("contains symlink" in event for event in result["forbidden_events"]))
            output = root / "normalized.json"
            isolation_trace.write_new(output, result)
            with self.assertRaises(FileExistsError):
                isolation_trace.write_new(output, result)

    def test_containment_accepts_denied_traversal_absolute_and_symlink_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fixture = root / "fixtures"
            fixture.mkdir()
            outside = root / "safe-sentinel.txt"
            outside.write_text("safe", encoding="utf-8")
            (fixture / "escape-link").symlink_to(outside)
            tool = "claude-read"
            inventory = [f"{tool}-list", f"{tool}-stat", tool, f"{tool}-search"]
            events: list[dict[str, Any]] = [{"type": "system", "subtype": "init", "tools": inventory}]
            for requested in ("../safe-sentinel.txt", str(outside), str(fixture / "escape-link")):
                events.append(
                    {
                        "type": "tool_use",
                        "name": tool,
                        "input": {"path": requested},
                        "status": "denied",
                    }
                )
            events.append({"type": "result", "subtype": "success"})
            trace = root / "containment.jsonl"
            trace.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            result = isolation_trace.normalize(
                host="claude",
                events=isolation_trace.load_jsonl(trace),
                profile=self.make_profile("claude", tool),
                lane_id="lane-a",
                fixture_root=fixture,
                fixture_root_id="containment-v1",
                pre_sha256="a" * 64,
                post_sha256="a" * 64,
                purpose="containment",
                raw_trace=trace,
            )
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["forbidden_events"], [])
            self.assertEqual([event["status"] for event in result["tool_events"]], ["denied"] * 3)
            self.assertTrue(all(event["scope"] == "outside-fixture-root" for event in result["tool_events"]))


if __name__ == "__main__":
    unittest.main()
