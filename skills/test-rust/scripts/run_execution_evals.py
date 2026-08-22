#!/usr/bin/env python3
# ruff: noqa: UP006, UP017, UP035, UP045  # Keep the runner executable on Python 3.9.
"""Run agent-in-the-loop execution evaluations for the test-rust skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil

# The runner invokes fixed local tools without a shell.
import subprocess  # nosec B404
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
SUITE_NAME = "test-rust-production-execution"
SKILL_NAME = "test-rust"
SKILL_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SUITE = REPOSITORY_ROOT / "evals/test-rust-execution/suite.json"
BOUNDARY_SCRIPT = SKILL_ROOT / "scripts/check_tests_boundaries.py"
AGENT_TOOLS = ("Read", "Skill", "Edit", "Write")
EXPECTED_TLA_FILES = (
    "Settlement.tla",
    "Settlement.cfg",
    "SettlementSafetyBug.tla",
    "SettlementSafetyBug.cfg",
    "SettlementLivenessBug.tla",
    "SettlementLivenessBug.cfg",
)
REQUIRED_CASE_IDS = {"lean-business-logic", "pbt-mutation", "tla-protocol"}
SOURCE_LIBRARY_PATH = Path("src/lib.rs")
LEAN_SOURCE_PATH = Path("tests/formal/lean/CreditAllocation.lean")
LEAN_MODEL_SCOPE_MARKER = "-- proof-scope: model-only; does-not-prove-rust"
PROTEST_SIGNATURE_LIMIT = 1000


class EvalError(RuntimeError):
    """Raised when the execution proof contract is invalid."""


def read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvalError(f"expected a JSON object in {path}")
    return value


def format_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


TREE_METADATA_DIRECTORIES = frozenset({".git", "__pycache__"})
TREE_GENERATED_FILENAMES = frozenset({".DS_Store"})
TREE_GENERATED_SUFFIXES = frozenset({".pyc"})


def git_ignored_tree_paths(root: Path, relative_paths: Sequence[str]) -> frozenset[str]:
    if not relative_paths:
        return frozenset()
    completed = subprocess.run(  # nosec B603
        ("git", "-C", str(root), "check-ignore", "--stdin", "-z"),
        input=("\0".join(relative_paths) + "\0").encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if completed.returncode in {0, 1}:
        return frozenset(path for path in completed.stdout.decode("utf-8").split("\0") if path)
    if completed.returncode == 128 and b"not a git repository" in completed.stderr:
        return frozenset()
    detail = completed.stderr.decode(errors="replace").strip()
    raise EvalError(f"cannot classify ignored files below {root}: {detail}")


def sha256_tree(root: Path) -> str:
    candidates: List[Tuple[str, Path]] = []
    for path in root.rglob("*"):
        relative_path = path.relative_to(root)
        if any(part in TREE_METADATA_DIRECTORIES for part in relative_path.parts):
            continue
        if not path.is_file():
            continue
        if path.name in TREE_GENERATED_FILENAMES or path.suffix in TREE_GENERATED_SUFFIXES:
            continue
        candidates.append((relative_path.as_posix(), path))
    ignored_paths = git_ignored_tree_paths(root, [relative for relative, _ in candidates])
    digest = hashlib.sha256()
    for relative, path in sorted(candidates):
        if relative in ignored_paths:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def require_external_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if path_is_within(resolved, REPOSITORY_ROOT):
        raise EvalError("execution evidence must be written outside the repository")
    resolved.mkdir(parents=True, exist_ok=False)
    return resolved


def create_external_directory(parent: Optional[Path] = None) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="test-rust-execution-", dir=str(parent) if parent else None)).resolve()
    if path_is_within(directory, REPOSITORY_ROOT):
        shutil.rmtree(directory)
        raise EvalError("the system temporary directory must be outside the repository")
    return directory


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    output_directory: Path,
    label: str,
    timeout_seconds: int,
    extra_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    environment = os.environ.copy()
    if extra_env:
        environment.update(extra_env)
    started = time.monotonic()
    try:
        completed = subprocess.run(  # nosec B603
            list(argv),
            cwd=cwd,
            env=environment,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        completed = subprocess.CompletedProcess(
            argv,
            124,
            stdout=error.stdout or b"",
            stderr=error.stderr or b"",
        )
    duration_ms = round((time.monotonic() - started) * 1000)
    output_directory.mkdir(parents=True, exist_ok=True)
    stdout_path = output_directory / f"{label}.stdout"
    stderr_path = output_directory / f"{label}.stderr"
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    return {
        "argv": list(argv),
        "duration_ms": duration_ms,
        "exit_code": completed.returncode,
        "stderr_path": str(stderr_path),
        "stderr_sha256": sha256_file(stderr_path),
        "stdout_path": str(stdout_path),
        "stdout_sha256": sha256_file(stdout_path),
    }


def command_text(result: Mapping[str, Any]) -> str:
    stdout = Path(str(result["stdout_path"])).read_text(encoding="utf-8", errors="replace")
    stderr = Path(str(result["stderr_path"])).read_text(encoding="utf-8", errors="replace")
    return stdout + "\n" + stderr


def _validated_case(raw_case: Any, suite_path: Path, seen: set[str]) -> Dict[str, Any]:
    if not isinstance(raw_case, dict):
        raise EvalError("every execution case must be an object")
    case_id = raw_case.get("id")
    fixture = raw_case.get("fixture")
    prompt = raw_case.get("prompt")
    if not isinstance(case_id, str) or not case_id:
        raise EvalError("every execution case needs non-empty id, fixture, and prompt")
    if not all(isinstance(item, str) and item for item in (fixture, prompt)):
        raise EvalError("every execution case needs non-empty id, fixture, and prompt")
    if case_id in seen:
        raise EvalError(f"duplicate execution case id: {case_id}")
    seen.add(case_id)
    fixture_root = (suite_path.parent / str(fixture)).resolve(strict=True)
    if not path_is_within(fixture_root, suite_path.parent / "fixtures"):
        raise EvalError(f"fixture escapes the suite: {fixture}")
    if SKILL_NAME.casefold() in str(prompt).casefold():
        raise EvalError(f"{case_id} prompt explicitly names the skill")
    return {**raw_case, "fixture_root": fixture_root}


def validated_suite(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    suite = read_json(path)
    identity = (suite.get("schema_version"), suite.get("suite"), suite.get("skill_name"))
    if identity != (SCHEMA_VERSION, SUITE_NAME, SKILL_NAME):
        raise EvalError("suite schema, name, or skill does not match the runner")
    raw_cases = suite.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise EvalError("suite cases must be a non-empty list")
    seen: set[str] = set()
    cases = [_validated_case(raw_case, path, seen) for raw_case in raw_cases]
    if {case["id"] for case in cases} != REQUIRED_CASE_IDS:
        raise EvalError(f"the production suite must contain exactly {sorted(REQUIRED_CASE_IDS)}")
    return suite, cases


def initialize_workspace(fixture: Path, workspace: Path, with_skill: bool) -> None:
    shutil.copytree(fixture, workspace)
    if with_skill:
        destination = workspace / ".claude/skills" / SKILL_NAME
        destination.parent.mkdir(parents=True)
        shutil.copytree(SKILL_ROOT, destination)
    commands = (
        ("git", "init", "--quiet"),
        ("git", "add", "--all"),
        (
            "git",
            "-c",
            "user.name=Execution Eval",
            "-c",
            "user.email=execution-eval.invalid",
            "commit",
            "--quiet",
            "-m",
            "Seed execution fixture",
        ),
    )
    for argv in commands:
        completed = subprocess.run(  # nosec B603
            argv,
            cwd=workspace,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise EvalError(f"{' '.join(argv)} failed: {completed.stderr.decode(errors='replace')}")


def create_boundary_snapshot(workspace: Path, baseline: Path, evidence: Path) -> Dict[str, Any]:
    return run_command(
        (
            sys.executable,
            str(BOUNDARY_SCRIPT),
            "snapshot",
            str(workspace),
            "--output",
            str(baseline),
            "--package-root",
            ".",
        ),
        cwd=workspace,
        output_directory=evidence,
        label="boundary-snapshot",
        timeout_seconds=60,
    )


def verify_boundary(workspace: Path, baseline: Path, evidence: Path, label: str) -> Dict[str, Any]:
    return run_command(
        (
            sys.executable,
            str(BOUNDARY_SCRIPT),
            "verify",
            str(workspace),
            "--baseline",
            str(baseline),
        ),
        cwd=workspace,
        output_directory=evidence,
        label=label,
        timeout_seconds=60,
    )


def claude_command(arguments: argparse.Namespace, prompt: str) -> List[str]:
    tools = ",".join(AGENT_TOOLS)
    return [
        arguments.claude_bin,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        arguments.model,
        "--effort",
        arguments.effort,
        "--setting-sources",
        "project",
        "--tools",
        tools,
        "--allowedTools",
        tools,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--permission-mode",
        "acceptEdits",
        "--max-budget-usd",
        arguments.max_budget_usd,
        "--no-session-persistence",
        prompt,
    ]


def parse_stream_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise EvalError(f"invalid Claude stream JSON on line {line_number}") from error
        if not isinstance(value, dict):
            raise EvalError(f"Claude stream event {line_number} is not an object")
        events.append(value)
    if not events:
        raise EvalError("Claude emitted no stream events")
    return events


def tool_uses(events: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    uses: List[Dict[str, Any]] = []
    for event in events:
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        uses.extend(item for item in content if isinstance(item, dict) and item.get("type") == "tool_use")
    return uses


def _tool_input(use: Mapping[str, Any]) -> Mapping[str, Any]:
    inputs = use.get("input")
    return inputs if isinstance(inputs, dict) else {}


def _invoked_skill(use: Mapping[str, Any]) -> Optional[str]:
    skill = _tool_input(use).get("skill")
    if use.get("name") == "Skill" and isinstance(skill, str):
        return skill
    return None


def _edited_path(use: Mapping[str, Any]) -> Optional[str]:
    if use.get("name") not in {"Edit", "Write"}:
        return None
    inputs = _tool_input(use)
    path = inputs.get("file_path") or inputs.get("path")
    return path if isinstance(path, str) else None


def claude_summary(result: Mapping[str, Any]) -> Dict[str, Any]:
    events = parse_stream_events(Path(str(result["stdout_path"])))
    init_events = [event for event in events if event.get("type") == "system" and event.get("subtype") == "init"]
    result_events = [event for event in events if event.get("type") == "result"]
    if len(init_events) != 1 or len(result_events) != 1:
        raise EvalError("Claude trace must contain exactly one init and one result event")
    uses = tool_uses(events)
    initialization = init_events[0]
    final = result_events[0]
    invoked_skills = [skill for use in uses if (skill := _invoked_skill(use)) is not None]
    edited_paths = [path for use in uses if (path := _edited_path(use)) is not None]
    return {
        "advertised_skills": initialization.get("skills", []),
        "advertised_tools": initialization.get("tools", []),
        "edited_paths": edited_paths,
        "invoked_skills": invoked_skills,
        "model": initialization.get("model"),
        "success": final.get("subtype") == "success" and final.get("is_error") is False,
        "tools_used": [str(use.get("name", "")) for use in uses],
    }


def render_agent_prompt(case: Mapping[str, Any]) -> str:
    return (
        "Work directly in this disposable evaluation repository. Inspect the existing public API and "
        "make the requested repository edits now; do not merely describe them. The coordinator has "
        "already captured an immutable boundary baseline and will run all commands after you finish. "
        "You have no shell tool and must not contact services or delegate.\n\n"
        f"Task:\n{case['prompt']}\n\n"
        "End with a concise list of files changed and residual risks."
    )


def run_agent(
    arguments: argparse.Namespace,
    case: Mapping[str, Any],
    workspace: Path,
    evidence: Path,
) -> Tuple[Dict[str, Any], List[str]]:
    prompt = render_agent_prompt(case)
    result = run_command(
        claude_command(arguments, prompt),
        cwd=workspace,
        output_directory=evidence,
        label="agent",
        timeout_seconds=arguments.agent_timeout_seconds,
    )
    errors: List[str] = []
    try:
        summary = claude_summary(result)
    except EvalError as error:
        summary = {}
        errors.append(str(error))
    if result["exit_code"] != 0:
        errors.append(f"agent exited with status {result['exit_code']}")
    if summary.get("success") is not True:
        errors.append("agent trace did not finish successfully")
    if any(tool not in AGENT_TOOLS for tool in summary.get("tools_used", [])):
        errors.append("agent used an unexpected tool")
    return {
        **result,
        **summary,
        "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
    }, errors


def cargo_environment(target: Path) -> Dict[str, str]:
    return {
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TARGET_DIR": str(target),
        "PROPTEST_DISABLE_FAILURE_PERSISTENCE": "0",
    }


def run_cargo_test(
    workspace: Path,
    evidence: Path,
    target: Path,
    label: str,
    test_filter: Optional[str] = None,
) -> Dict[str, Any]:
    argv = ["cargo", "test", "--locked", "--offline"]
    if test_filter:
        argv.extend((test_filter, "--", "--nocapture"))
    return run_command(
        argv,
        cwd=workspace,
        output_directory=evidence,
        label=label,
        timeout_seconds=180,
        extra_env=cargo_environment(target),
    )


def apply_unique_replacement(path: Path, before: str, after: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(before) != 1:
        raise EvalError(f"seed replacement must occur exactly once in {path}")
    path.write_text(source.replace(before, after), encoding="utf-8")


def copy_workspace(workspace: Path, destination: Path) -> None:
    shutil.copytree(
        workspace,
        destination,
        ignore=shutil.ignore_patterns(".git", "target", "mutants.out", "mutants.out.old"),
    )


def proptest_failure_signature(result: Mapping[str, Any]) -> Optional[str]:
    text = command_text(result)
    section: List[str] = []
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("minimal failing input:"):
            collecting = True
        elif collecting and stripped.startswith(("successes:", "local rejects:", "global rejects:")):
            break
        if collecting:
            section.append(line)
    if section:
        return " ".join("\n".join(section).split())[:PROTEST_SIGNATURE_LIMIT]
    if "Test failed:" in text:
        return " ".join(text[text.index("Test failed:") :].split())[:PROTEST_SIGNATURE_LIMIT]
    return None


def mutant_lines(output_root: Path, filename: str) -> List[str]:
    candidates = list(output_root.rglob(filename))
    if len(candidates) != 1:
        raise EvalError(f"expected one {filename} below {output_root}, found {len(candidates)}")
    return [line.strip() for line in candidates[0].read_text(encoding="utf-8").splitlines() if line.strip()]


def classify_mutants(
    caught: Sequence[str],
    missed: Sequence[str],
    unviable: Sequence[str],
    timeout: Sequence[str],
) -> Dict[str, Any]:
    equivalent = [line for line in missed if "replace + with - in normalize_checksum" in line]
    boundary_unreachable = [line for line in missed if "diagnostic_bucket" in line]
    explained = set(equivalent + boundary_unreachable)
    unexplained = [line for line in missed if line not in explained]
    return {
        "boundary_unreachable": boundary_unreachable,
        "caught": list(caught),
        "equivalent": equivalent,
        "timeout": list(timeout),
        "unexplained_survivors": unexplained,
        "unviable": list(unviable),
    }


def _property_artifact_errors(workspace: Path, property_file: Path) -> List[str]:
    errors: List[str] = []
    persistence_file = workspace / "tests/proptest-regressions/property.txt"
    if not persistence_file.is_file():
        errors.append("tests/proptest-regressions/property.txt was not authored")
    property_source = property_file.read_text(encoding="utf-8")
    for required in (
        "corrupted_checksum_is_rejected",
        "RngSeed::Fixed",
        "FileFailurePersistence::Direct",
        "proptest-regressions",
    ):
        if required not in property_source:
            errors.append(f"tests/property.rs is missing {required}")
    return errors


def _run_checksum_replays(
    workspace: Path,
    evidence: Path,
) -> Tuple[List[Dict[str, Any]], List[str], List[str], List[str]]:
    errors: List[str] = []
    replay_results: List[Dict[str, Any]] = []
    signatures: List[str] = []
    regression_hashes: List[str] = []
    for trial in (1, 2):
        defect = evidence / "defects" / f"checksum-{trial}"
        copy_workspace(workspace, defect)
        apply_unique_replacement(
            defect / SOURCE_LIBRARY_PATH,
            "if actual != declared {",
            "if actual != declared && actual.wrapping_add(1) != declared {",
        )
        replay = run_cargo_test(
            defect,
            evidence,
            evidence / f"targets/checksum-defect-{trial}",
            f"cargo-test-checksum-defect-{trial}",
            "corrupted_checksum_is_rejected",
        )
        replay_results.append(replay)
        signature = proptest_failure_signature(replay)
        if replay["exit_code"] == 0 or signature is None:
            errors.append(f"Proptest trial {trial} did not detect the checksum defect")
        else:
            signatures.append(signature)
        persisted = list(defect.glob("tests/proptest-regressions/*"))
        regression_hashes.extend(sha256_file(path) for path in persisted if path.is_file())
    if len(signatures) == 2 and signatures[0] != signatures[1]:
        errors.append("fixed-seed Proptest defect replays produced different minimal failures")
    if not regression_hashes:
        errors.append("the failing Proptest run did not persist a concrete regression")
    return replay_results, signatures, regression_hashes, errors


def _run_mutation_evaluation(
    workspace: Path,
    evidence: Path,
    arguments: argparse.Namespace,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    errors: List[str] = []
    mutation_output = evidence / "mutation-output"
    mutation = run_command(
        (
            "cargo",
            "mutants",
            "--dir",
            str(workspace),
            "--no-config",
            "--file",
            SOURCE_LIBRARY_PATH.as_posix(),
            "--re",
            "replace parse| in parse$|normalize_checksum|diagnostic_bucket",
            "--output",
            str(mutation_output),
            "--timeout",
            str(arguments.mutant_timeout_seconds),
            "--jobs",
            "1",
            "--no-times",
            "--colors",
            "never",
            "--caught",
            "--unviable",
        ),
        cwd=workspace,
        output_directory=evidence,
        label="cargo-mutants",
        timeout_seconds=arguments.mutation_timeout_seconds,
        extra_env={"CARGO_NET_OFFLINE": "true"},
    )
    try:
        classification = classify_mutants(
            mutant_lines(mutation_output, "caught.txt"),
            mutant_lines(mutation_output, "missed.txt"),
            mutant_lines(mutation_output, "unviable.txt"),
            mutant_lines(mutation_output, "timeout.txt"),
        )
    except EvalError as error:
        classification = {}
        errors.append(str(error))
    if not classification.get("caught"):
        errors.append("cargo-mutants caught no public parse mutant")
    if not classification.get("equivalent"):
        errors.append("the known equivalent normalize_checksum mutant was not classified")
    if not classification.get("boundary_unreachable"):
        errors.append("the known boundary-unreachable diagnostic mutant was not classified")
    if not classification.get("unviable"):
        errors.append("cargo-mutants produced no unviable generic-return mutant")
    if classification.get("unexplained_survivors"):
        errors.append("cargo-mutants left unexplained viable survivors")
    return mutation, classification, errors


def evaluate_pbt_mutation(
    workspace: Path,
    evidence: Path,
    arguments: argparse.Namespace,
) -> Tuple[Dict[str, Any], List[str]]:
    property_file = workspace / "tests/property.rs"
    if not property_file.is_file():
        return {}, ["tests/property.rs was not authored"]
    errors = _property_artifact_errors(workspace, property_file)
    correct = run_cargo_test(
        workspace,
        evidence,
        evidence / "targets/correct",
        "cargo-test-correct",
    )
    if correct["exit_code"] != 0:
        errors.append("the unmodified codec test suite did not pass")
    replay_results, signatures, regression_hashes, replay_errors = _run_checksum_replays(
        workspace,
        evidence,
    )
    errors.extend(replay_errors)
    mutation, classification, mutation_errors = _run_mutation_evaluation(workspace, evidence, arguments)
    errors.extend(mutation_errors)

    return {
        "cargo_test_correct": correct,
        "cargo_mutants": mutation,
        "mutation_classification": classification,
        "proptest_failure_signatures": signatures,
        "proptest_regression_sha256": sorted(set(regression_hashes)),
        "seeded_defect_replays": replay_results,
    }, errors


def parse_distinct_states(text: str) -> Optional[int]:
    marker = " distinct states found"
    values: List[int] = []
    for line in text.splitlines():
        prefix, separator, _ = line.partition(marker)
        if not separator:
            continue
        tokens = prefix.rsplit(maxsplit=1)
        if not tokens:
            continue
        token = tokens[-1]
        try:
            values.append(int(token.replace(",", "")))
        except ValueError:
            continue
    return max(values) if values else None


def run_tlc(
    workspace: Path,
    evidence: Path,
    jar: Path,
    module: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    model_root = workspace / "tests/formal/tla"
    metadir = evidence / "tlc-state" / module
    metadir.mkdir(parents=True)
    return run_command(
        (
            "java",
            "-XX:+UseParallelGC",
            "-jar",
            str(jar),
            "-workers",
            "1",
            "-metadir",
            str(metadir),
            "-config",
            str(model_root / f"{module}.cfg"),
            str(model_root / f"{module}.tla"),
        ),
        cwd=model_root,
        output_directory=evidence,
        label=f"tlc-{module}",
        timeout_seconds=timeout_seconds,
    )


def evaluate_tla_protocol(
    workspace: Path,
    evidence: Path,
    arguments: argparse.Namespace,
) -> Tuple[Dict[str, Any], List[str]]:
    errors: List[str] = []
    rust_test = workspace / "tests/protocol.rs"
    if not rust_test.is_file():
        errors.append("tests/protocol.rs was not authored")
    model_root = workspace / "tests/formal/tla"
    for filename in EXPECTED_TLA_FILES:
        if not (model_root / filename).is_file():
            errors.append(f"tests/formal/tla/{filename} was not authored")
    if errors:
        return {}, errors

    correct_rust = run_cargo_test(
        workspace,
        evidence,
        evidence / "targets/protocol-correct",
        "cargo-test-protocol-correct",
    )
    if correct_rust["exit_code"] != 0:
        errors.append("the unmodified protocol test suite did not pass")

    defect = evidence / "defects/protocol-duplicate-ack"
    copy_workspace(workspace, defect)
    apply_unique_replacement(
        defect / SOURCE_LIBRARY_PATH,
        "Event::Acknowledge if self.delivered && !self.terminal => {",
        "Event::Acknowledge if self.delivered => {",
    )
    defect_rust = run_cargo_test(
        defect,
        evidence,
        evidence / "targets/protocol-defect",
        "cargo-test-protocol-defect",
    )
    if defect_rust["exit_code"] == 0:
        errors.append("the Rust conformance tests missed the duplicate-ack defect")

    jar = Path(arguments.tla2tools_jar).expanduser().resolve(strict=True)
    tlc_results = {
        module: run_tlc(
            workspace,
            evidence,
            jar,
            module,
            arguments.tlc_timeout_seconds,
        )
        for module in ("Settlement", "SettlementSafetyBug", "SettlementLivenessBug")
    }
    correct_text = command_text(tlc_results["Settlement"])
    safety_text = command_text(tlc_results["SettlementSafetyBug"])
    liveness_text = command_text(tlc_results["SettlementLivenessBug"])
    distinct_states = parse_distinct_states(correct_text)
    if tlc_results["Settlement"]["exit_code"] != 0:
        errors.append("TLC rejected the correct settlement model")
    if distinct_states is None or distinct_states < 4:
        errors.append("the correct settlement model explored fewer than four distinct states")
    if (
        tlc_results["SettlementSafetyBug"]["exit_code"] == 0
        or "Invariant" not in safety_text
        or "violated" not in safety_text
    ):
        errors.append("TLC did not report the seeded safety invariant violation")
    if (
        tlc_results["SettlementLivenessBug"]["exit_code"] == 0
        or "Temporal properties were violated" not in liveness_text
    ):
        errors.append("TLC did not report the seeded liveness violation")

    return {
        "cargo_test_correct": correct_rust,
        "cargo_test_seeded_defect": defect_rust,
        "correct_model_distinct_states": distinct_states,
        "tla2tools_jar_sha256": sha256_file(jar),
        "tlc": tlc_results,
    }, errors


def run_lean(
    lean_bin: Path,
    source: Path,
    evidence: Path,
    label: str,
    timeout_seconds: int,
) -> Dict[str, Any]:
    output = evidence / "lean-output" / f"{label}.olean"
    output.parent.mkdir(parents=True, exist_ok=True)
    return run_command(
        (str(lean_bin), "--json", "-o", str(output), str(source)),
        cwd=source.parent,
        output_directory=evidence,
        label=label,
        timeout_seconds=timeout_seconds,
    )


def _lean_code_without_comments_or_strings(source: str) -> str:
    masked: List[str] = []
    index = 0
    block_depth = 0
    in_line_comment = False
    in_string = False
    escaped = False
    while index < len(source):
        character = source[index]
        pair = source[index : index + 2]
        if in_line_comment:
            if character == "\n":
                in_line_comment = False
                masked.append(character)
            else:
                masked.append(" ")
            index += 1
            continue
        if block_depth:
            if pair == "/-":
                block_depth += 1
                masked.extend((" ", " "))
                index += 2
            elif pair == "-/":
                block_depth -= 1
                masked.extend((" ", " "))
                index += 2
            else:
                masked.append("\n" if character == "\n" else " ")
                index += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            masked.append("\n" if character == "\n" else " ")
            index += 1
            continue
        if pair == "--":
            in_line_comment = True
            masked.extend((" ", " "))
            index += 2
        elif pair == "/-":
            block_depth = 1
            masked.extend((" ", " "))
            index += 2
        elif character == '"':
            in_string = True
            masked.append(" ")
            index += 1
        else:
            masked.append(character)
            index += 1
    return "".join(masked)


def _lean_flexible_fragment_pattern(fragment: str) -> re.Pattern[str]:
    tokens = re.findall(r"\w+|[^\w\s]", fragment)
    if not tokens:
        raise ValueError("Lean fragment must contain at least one token")
    pattern = r"[ \t\r\n]*".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<!\w){pattern}(?!\w)")


def _lean_definition_block(code: str, declaration: str, path: Path) -> Tuple[int, int]:
    declaration_pattern = _lean_flexible_fragment_pattern(declaration)
    header_pattern = re.compile(
        rf"^[ \t]*{declaration_pattern.pattern}[ \t]*(?:\r?\n|$)",
        re.MULTILINE,
    )
    headers = list(header_pattern.finditer(code))
    if len(headers) != 1:
        raise EvalError(f"Lean definition {declaration} must occur exactly once in {path}")
    header = headers[0]
    block_end = len(code)
    offset = header.end()
    previous_line_blank = False
    for line in code[header.end() :].splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        is_declaration = re.match(r"(?:def|theorem|end)\b", stripped) is not None
        is_top_level = len(stripped) == len(line)
        if is_declaration and (is_top_level or previous_line_blank):
            block_end = offset
            break
        previous_line_blank = not line.strip()
        offset += len(line)
    return header.start(), block_end


def apply_unique_lean_definition_replacement(path: Path, before: str, after: str) -> None:
    source = path.read_text(encoding="utf-8")
    code = _lean_code_without_comments_or_strings(source)
    if len(code) != len(source):
        raise EvalError(f"Lean source mask must preserve offsets in {path}")
    block_start, block_end = _lean_definition_block(
        code,
        "def applyCredits : Nat → List Nat → Nat",
        path,
    )
    matches = list(_lean_flexible_fragment_pattern(before).finditer(code, block_start, block_end))
    if len(matches) != 1:
        raise EvalError(f"Lean definition seed replacement must occur exactly once in {path}")
    match = matches[0]
    updated = source[: match.start()] + after + source[match.end() :]
    path.write_text(updated, encoding="utf-8")


def _lean_declaration_errors(source: str) -> List[str]:
    code = _lean_code_without_comments_or_strings(source)
    normalized = "".join(character if character.isalnum() or character == "_" else " " for character in code)
    tokens = set(normalized.casefold().split())
    errors = []
    if "axiom" in tokens:
        errors.append(f"{LEAN_SOURCE_PATH.as_posix()} contains forbidden axiom declaration")
    # `constant` is not a Lean 4.33 command, but keep the conservative guard explicit.
    if "constant" in tokens:
        errors.append(f"{LEAN_SOURCE_PATH.as_posix()} contains forbidden constant declaration")
    return errors


def _lean_json_messages(result: Mapping[str, Any]) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    for path_key in ("stdout_path", "stderr_path"):
        text = Path(str(result[path_key])).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                messages.append(payload)
    return messages


def _lean_proof_errors(result: Mapping[str, Any]) -> List[str]:
    for message in _lean_json_messages(result):
        kind = str(message.get("kind", "")).casefold()
        data = str(message.get("data", ""))
        normalized_data = " ".join(
            "".join(character if character.isalnum() else " " for character in data.casefold()).split()
        )
        if kind == "hassorry" or normalized_data == "declaration uses sorry":
            return ["Lean proof contains sorry/admit"]
    return []


def _lean_artifact_errors(workspace: Path) -> List[str]:
    errors: List[str] = []
    rust_test = workspace / "tests/credit_allocation.rs"
    lean_source = workspace / LEAN_SOURCE_PATH
    if not rust_test.is_file():
        errors.append("tests/credit_allocation.rs was not authored")
    if not lean_source.is_file():
        errors.append(f"{LEAN_SOURCE_PATH.as_posix()} was not authored")
        return errors
    source = lean_source.read_text(encoding="utf-8")
    code = _lean_code_without_comments_or_strings(source)
    for required in (
        "def applyCredits : Nat → List Nat → Nat",
        "applyCredits (subtotal - credit) rest",
        "theorem applyCredits_le_subtotal",
    ):
        if required not in code:
            errors.append(f"{LEAN_SOURCE_PATH.as_posix()} is missing {required}")
    if LEAN_MODEL_SCOPE_MARKER not in {line.strip() for line in source.splitlines()}:
        errors.append(f"{LEAN_SOURCE_PATH.as_posix()} omits the model-to-Rust proof limitation")
    errors.extend(_lean_declaration_errors(source))
    return errors


def evaluate_lean_business_logic(
    workspace: Path,
    evidence: Path,
    arguments: argparse.Namespace,
) -> Tuple[Dict[str, Any], List[str]]:
    errors = _lean_artifact_errors(workspace)
    if errors:
        return {}, errors
    correct_rust = run_cargo_test(
        workspace,
        evidence,
        evidence / "targets/lean-rust-correct",
        "cargo-test-lean-rust-correct",
    )
    if correct_rust["exit_code"] != 0:
        errors.append("the unmodified credit-allocation tests did not pass")

    rust_defect = evidence / "defects/lean-rust-credit-increase"
    copy_workspace(workspace, rust_defect)
    apply_unique_replacement(
        rust_defect / SOURCE_LIBRARY_PATH,
        "remaining.saturating_sub(*credit)",
        "remaining.saturating_add(*credit)",
    )
    defect_rust = run_cargo_test(
        rust_defect,
        evidence,
        evidence / "targets/lean-rust-defect",
        "cargo-test-lean-rust-defect",
    )
    if defect_rust["exit_code"] == 0:
        errors.append("the Rust conformance tests missed the credit-increase defect")

    lean_bin = Path(arguments.lean_bin).expanduser().resolve(strict=True)
    correct_lean = run_lean(
        lean_bin,
        workspace / LEAN_SOURCE_PATH,
        evidence,
        "lean-correct",
        arguments.lean_timeout_seconds,
    )
    errors.extend(_lean_proof_errors(correct_lean))
    if correct_lean["exit_code"] != 0:
        errors.append("Lean rejected the correct credit-allocation proof")

    lean_defect = evidence / "defects/lean-model-credit-increase"
    copy_workspace(workspace, lean_defect)
    apply_unique_lean_definition_replacement(
        lean_defect / LEAN_SOURCE_PATH,
        "applyCredits (subtotal - credit) rest",
        "applyCredits (subtotal + credit) rest",
    )
    defect_lean = run_lean(
        lean_bin,
        lean_defect / LEAN_SOURCE_PATH,
        evidence,
        "lean-seeded-defect",
        arguments.lean_timeout_seconds,
    )
    if defect_lean["exit_code"] == 0:
        errors.append("Lean accepted the seeded credit-increase model defect")

    return {
        "cargo_test_correct": correct_rust,
        "cargo_test_seeded_defect": defect_rust,
        "lean_bin_sha256": sha256_file(lean_bin),
        "lean_correct": correct_lean,
        "lean_seeded_defect": defect_lean,
    }, errors


def version_result(argv: Sequence[str], cwd: Path, evidence: Path, label: str) -> Dict[str, Any]:
    return run_command(
        argv,
        cwd=cwd,
        output_directory=evidence,
        label=label,
        timeout_seconds=30,
    )


def _required_tool_path(
    raw_path: Optional[str],
    *,
    flag: str,
    case_id: str,
    executable: bool,
) -> Path:
    if not raw_path:
        raise EvalError(f"{flag} is required for case {case_id}")
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except OSError as error:
        raise EvalError(f"{flag} for case {case_id} is unavailable: {raw_path}") from error
    if not path.is_file() or (executable and not os.access(path, os.X_OK)):
        raise EvalError(f"{flag} for case {case_id} is unavailable: {raw_path}")
    return path


def _resolve_selected_tool_paths(
    arguments: argparse.Namespace,
    selected_case_ids: set[str],
) -> Dict[str, Path]:
    paths: Dict[str, Path] = {}
    if "lean-business-logic" in selected_case_ids:
        paths["lean"] = _required_tool_path(
            arguments.lean_bin,
            flag="--lean-bin",
            case_id="lean-business-logic",
            executable=True,
        )
        arguments.lean_bin = str(paths["lean"])
    if "tla-protocol" in selected_case_ids:
        paths["tla2tools"] = _required_tool_path(
            arguments.tla2tools_jar,
            flag="--tla2tools-jar",
            case_id="tla-protocol",
            executable=False,
        )
        arguments.tla2tools_jar = str(paths["tla2tools"])
    return paths


def _selected_tool_versions(
    arguments: argparse.Namespace,
    selected_case_ids: set[str],
    versions_root: Path,
    tool_paths: Mapping[str, Path],
) -> Dict[str, Dict[str, Any]]:
    versions = {
        "cargo": version_result(("cargo", "--version"), REPOSITORY_ROOT, versions_root, "cargo"),
        "claude": version_result(
            (arguments.claude_bin, "--version"),
            REPOSITORY_ROOT,
            versions_root,
            "claude",
        ),
    }
    if "pbt-mutation" in selected_case_ids:
        versions["cargo_mutants"] = version_result(
            ("cargo", "mutants", "--version"),
            REPOSITORY_ROOT,
            versions_root,
            "cargo-mutants",
        )
    if "tla-protocol" in selected_case_ids:
        versions["java"] = version_result(("java", "-version"), REPOSITORY_ROOT, versions_root, "java")
    if "lean-business-logic" in selected_case_ids:
        versions["lean"] = version_result(
            (str(tool_paths["lean"]), "--version"),
            REPOSITORY_ROOT,
            versions_root,
            "lean",
        )
    return versions


def warm_dependency_cache(
    cases: Sequence[Mapping[str, Any]],
    run_root: Path,
) -> Dict[str, Dict[str, Any]]:
    results = {}
    for case in cases:
        case_id = str(case["id"])
        workspace = run_root / "dependency-preflight/workspaces" / case_id
        shutil.copytree(Path(str(case["fixture_root"])), workspace)
        result = run_command(
            ("cargo", "fetch", "--locked"),
            cwd=workspace,
            output_directory=run_root / "dependency-preflight/evidence",
            label=f"cargo-fetch-{case_id}",
            timeout_seconds=300,
            extra_env={"CARGO_TARGET_DIR": str(run_root / "dependency-preflight/targets" / case_id)},
        )
        if result["exit_code"] != 0:
            raise EvalError(f"dependency preflight failed for {case_id}")
        results[case_id] = result
    return results


def evaluate_case(
    case_id: str,
    workspace: Path,
    evidence: Path,
    arguments: argparse.Namespace,
) -> Tuple[Dict[str, Any], List[str]]:
    if case_id == "pbt-mutation":
        return evaluate_pbt_mutation(workspace, evidence, arguments)
    if case_id == "tla-protocol":
        return evaluate_tla_protocol(workspace, evidence, arguments)
    if case_id == "lean-business-logic":
        return evaluate_lean_business_logic(workspace, evidence, arguments)
    raise EvalError(f"unsupported execution case: {case_id}")


def run_case(
    arguments: argparse.Namespace,
    case: Mapping[str, Any],
    arm: str,
    run_root: Path,
) -> Dict[str, Any]:
    case_id = str(case["id"])
    record_root = run_root / "records" / f"{case_id}--{arm}"
    workspace = run_root / "workspaces" / f"{case_id}--{arm}"
    record_root.mkdir(parents=True)
    initialize_workspace(Path(str(case["fixture_root"])), workspace, arm == "with_skill")
    baseline = record_root / "boundary-baseline.json"
    snapshot = create_boundary_snapshot(workspace, baseline, record_root)
    errors = []
    if snapshot["exit_code"] != 0:
        errors.append("boundary snapshot failed")

    agent, agent_errors = run_agent(arguments, case, workspace, record_root)
    errors.extend(agent_errors)
    if arm == "with_skill" and SKILL_NAME not in agent.get("invoked_skills", []):
        errors.append("the treatment agent did not load test-rust")
    if arm == "baseline" and SKILL_NAME in agent.get("invoked_skills", []):
        errors.append("the baseline agent unexpectedly loaded test-rust")
    boundary_after_agent = verify_boundary(
        workspace,
        baseline,
        record_root,
        "boundary-after-agent",
    )
    if boundary_after_agent["exit_code"] != 0:
        errors.append("agent writes violated the tests-only boundary")

    evaluation: Dict[str, Any] = {}
    if boundary_after_agent["exit_code"] == 0 and agent.get("success") is True:
        evaluation, evaluation_errors = evaluate_case(case_id, workspace, record_root, arguments)
        errors.extend(evaluation_errors)
    boundary_after_tools = verify_boundary(
        workspace,
        baseline,
        record_root,
        "boundary-after-tools",
    )
    if boundary_after_tools["exit_code"] != 0:
        errors.append("coordinator tool execution changed content outside tests")
    return {
        "agent": agent,
        "arm": arm,
        "boundary_after_agent": boundary_after_agent,
        "boundary_after_tools": boundary_after_tools,
        "boundary_snapshot": snapshot,
        "case_id": case_id,
        "errors": errors,
        "evaluation": evaluation,
        "passed": not errors,
        "workspace": str(workspace),
        "workspace_sha256": sha256_tree(workspace),
    }


def run_suite(arguments: argparse.Namespace) -> int:
    suite_path = Path(arguments.suite).expanduser().resolve(strict=True)
    suite, cases = validated_suite(suite_path)
    selected_case_ids = set(arguments.case or [case["id"] for case in cases])
    cases = [case for case in cases if case["id"] in selected_case_ids]
    tool_paths = _resolve_selected_tool_paths(arguments, selected_case_ids)
    arms = arguments.arm or ["baseline", "with_skill"]
    partial = len(cases) != len(suite["cases"]) or len(arms) != 2
    if arguments.output:
        run_root = require_external_directory(Path(arguments.output))
    else:
        run_root = create_external_directory(
            Path(arguments.output_parent).expanduser().resolve() if arguments.output_parent else None
        )
    fixture_hashes_before = {str(case["id"]): sha256_tree(Path(str(case["fixture_root"]))) for case in cases}
    versions_root = run_root / "versions"
    versions = _selected_tool_versions(arguments, selected_case_ids, versions_root, tool_paths)
    dependency_preflight = warm_dependency_cache(cases, run_root)
    records = []
    for case in cases:
        for arm in arms:
            records.append(run_case(arguments, case, arm, run_root))
    fixture_hashes_after = {str(case["id"]): sha256_tree(Path(str(case["fixture_root"]))) for case in cases}
    fixture_immutable = fixture_hashes_before == fixture_hashes_after
    treatment = [record for record in records if record["arm"] == "with_skill"]
    baseline = [record for record in records if record["arm"] == "baseline"]
    treatment_boundary_violations = sum(
        1
        for record in treatment
        if record["boundary_after_agent"]["exit_code"] != 0 or record["boundary_after_tools"]["exit_code"] != 0
    )
    baseline_boundary_violations = sum(
        1
        for record in baseline
        if record["boundary_after_agent"]["exit_code"] != 0 or record["boundary_after_tools"]["exit_code"] != 0
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite": SUITE_NAME,
        "suite_sha256": sha256_file(suite_path),
        "skill_sha256": sha256_tree(SKILL_ROOT),
        "started_at": utc_now(),
        "execution_policy": suite["execution_policy"],
        "dependency_preflight": dependency_preflight,
        "fixture_hashes": fixture_hashes_after,
        "fixture_source_immutable": fixture_immutable,
        "records": records,
        "partial": partial,
        "summary": {
            "baseline_boundary_violations": baseline_boundary_violations,
            "baseline_passed": sum(1 for record in baseline if record["passed"]),
            "baseline_total": len(baseline),
            "critical_boundary_violations": treatment_boundary_violations,
            "treatment_passed": sum(1 for record in treatment if record["passed"]),
            "treatment_total": len(treatment),
        },
        "tool_versions": versions,
    }
    selected_records_pass = all(record["passed"] for record in (treatment or baseline))
    report["passed"] = fixture_immutable and selected_records_pass and treatment_boundary_violations == 0
    report_path = run_root / "execution-report.json"
    report_path.write_text(format_json(report), encoding="utf-8")
    print(
        format_json(
            {
                "passed": report["passed"],
                "report": str(report_path),
                "run_root": str(run_root),
                "summary": report["summary"],
            }
        ),
        end="",
    )
    return 0 if report["passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--output")
    parser.add_argument("--output-parent")
    parser.add_argument(
        "--case",
        action="append",
        choices=tuple(sorted(REQUIRED_CASE_IDS)),
        help="run only one case; repeat to select more than one",
    )
    parser.add_argument(
        "--arm",
        action="append",
        choices=("baseline", "with_skill"),
        help="run only one arm; repeat to select both",
    )
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--effort", default="xhigh")
    parser.add_argument("--max-budget-usd", default="3.00")
    parser.add_argument("--agent-timeout-seconds", type=int, default=900)
    parser.add_argument("--mutation-timeout-seconds", type=int, default=900)
    parser.add_argument("--mutant-timeout-seconds", type=int, default=30)
    parser.add_argument("--tlc-timeout-seconds", type=int, default=180)
    parser.add_argument("--lean-timeout-seconds", type=int, default=180)
    parser.add_argument("--lean-bin", default=None)
    parser.add_argument("--tla2tools-jar", default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        arguments = build_parser().parse_args(argv)
        return run_suite(arguments)
    except (EvalError, OSError, ValueError) as error:
        print(f"execution eval error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
