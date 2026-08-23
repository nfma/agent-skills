from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import stat
import subprocess  # nosec B404
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .validation import is_within

DEFAULT_MODEL = "gemini-3.7-flash-high"
# This deny applies to the entire subprocess tree. Reassess before staging MCP
# configuration, because an MCP helper may have legitimate Keychain needs.
# sandbox-exec is deprecated, but currently fails closed when unavailable,
# malformed, or nested inside an existing macOS sandbox.
MACOS_KEYCHAIN_BLOCK_PROFILE = '(version 1)(allow default)(deny process-exec (literal "/usr/bin/security"))'
PREFLIGHT_RECEIPT = "AGY_STRUCTURED_PREFLIGHT_OK"
PREFLIGHT_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"preflight": {"const": PREFLIGHT_RECEIPT}},
        "required": ["preflight"],
        "additionalProperties": False,
    },
    separators=(",", ":"),
)


@dataclass(frozen=True)
class AntigravityResult:
    exit_code: int
    conversation_id: str | None
    response: str | None
    structured_output: dict[str, Any] | None
    trace_errors: tuple[str, ...]
    credential_removed_after_init: bool
    ambient_brain_unchanged: bool
    ambient_conversation_state_absent: bool
    process_cleanup: str
    expanded_skill_names: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.trace_errors and self.ambient_conversation_state_absent


def _refuse_existing(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing to overwrite {label}: {path}")


def _validate_external_root(path: Path, forbidden_roots: tuple[Path, ...]) -> Path:
    absolute = path.absolute()
    if absolute.is_symlink():
        raise ValueError("Antigravity state root must not be a symlink")
    absolute = absolute.resolve(strict=False)
    if absolute == Path.home() or absolute == Path("/"):
        raise ValueError("Antigravity state root must be a narrow disposable directory")
    for forbidden in forbidden_roots:
        resolved_forbidden = forbidden.resolve()
        if is_within(absolute, resolved_forbidden):
            raise ValueError(f"Antigravity state root must remain outside {resolved_forbidden}")
    current = absolute
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("Antigravity state root must not contain symlinks")
        current = current.parent
    _refuse_existing(absolute, "Antigravity state root")
    absolute.mkdir(parents=True, mode=0o700)
    absolute.chmod(0o700)
    return absolute


def _tree_metadata(root: Path) -> str:
    if not root.exists():
        return sha256(b"absent\n").hexdigest()
    records: list[bytes] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        details = path.lstat()
        kind = "symlink" if path.is_symlink() else "dir" if path.is_dir() else "file"
        records.append(
            f"{relative}\0{kind}\0{stat.S_IMODE(details.st_mode):o}\0{details.st_size}\0{details.st_mtime_ns}\n".encode()
        )
    return sha256(b"".join(records)).hexdigest()


def _bundle_digest(root: Path) -> str:
    records: list[bytes] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"Antigravity skill bundle contains a symlink: {relative}")
        if path.is_file():
            records.append(
                relative.as_posix().encode("utf-8")
                + b"\0"
                + sha256(path.read_bytes()).hexdigest().encode("ascii")
                + b"\n"
            )
    return sha256(b"".join(records)).hexdigest()


def _build_command(
    runner: str,
    *,
    prompt: str,
    model: str,
    effort: str,
    log_path: Path,
    timeout_seconds: int,
    json_schema: str | None,
) -> list[str]:
    command = [
        runner,
        "--mode",
        "plan",
        "--sandbox",
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--effort",
        effort,
        "--print-timeout",
        f"{timeout_seconds}s",
        "--log-file",
        str(log_path),
    ]
    if json_schema is not None:
        command.extend(["--json-schema", json_schema])
    # --prompt is prompt-valued. Keep it last so the prompt can never consume a later option.
    command.extend(["--prompt", prompt])
    return command


def _configure_keychain_containment(command: list[str]) -> tuple[list[str], str]:
    if sys.platform != "darwin":
        return command, "not-applicable"
    probe = ["/usr/bin/sandbox-exec", "-p", MACOS_KEYCHAIN_BLOCK_PROFILE, "/usr/bin/true"]
    try:
        completed = subprocess.run(  # nosec B603
            probe,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except OSError as error:
        raise RuntimeError("macOS Keychain containment probe could not start") from error
    if completed.returncode != 0:
        raise RuntimeError(
            f"macOS Keychain containment probe exited with status {completed.returncode}; "
            "the runner may already be inside a macOS sandbox"
        )
    return ["/usr/bin/sandbox-exec", "-p", MACOS_KEYCHAIN_BLOCK_PROFILE, *command], "macos-sandbox-exec"


def _validate_trace(
    records: list[dict[str, Any]],
    *,
    model: str,
    exit_code: int,
    require_tool_free: bool,
    terminated_after_terminal_result: bool,
    expected_expanded_skill: str | None,
) -> tuple[list[str], str | None, str | None, dict[str, Any] | None, tuple[str, ...]]:
    errors: list[str] = []
    init_records = [record for record in records if record.get("event") == "init"]
    result_records = [record for record in records if record.get("event") == "result"]
    if len(init_records) != 1:
        errors.append("structured trace must contain exactly one init event")
    if len(result_records) != 1:
        errors.append("structured trace must contain exactly one result event")
    if records and records[-1].get("event") != "result":
        errors.append("structured trace must end with a result event")
    if exit_code != 0 and not terminated_after_terminal_result:
        errors.append(f"Antigravity exited with status {exit_code}")

    conversation_id: str | None = None
    response: str | None = None
    structured_output: dict[str, Any] | None = None
    expanded_skill_names: tuple[str, ...] = ()
    if len(init_records) == 1:
        init = init_records[0]
        conversation_id = init.get("conversation_id") if isinstance(init.get("conversation_id"), str) else None
        init_payload = init.get("init")
        if not conversation_id:
            errors.append("init event must contain a conversation_id")
        if not isinstance(init_payload, dict) or init_payload.get("model") != model:
            errors.append("init event model does not match the requested model")
        elif isinstance(init_payload.get("expanded_commands"), list):
            expanded_skill_names = tuple(
                command["name"]
                for command in init_payload["expanded_commands"]
                if isinstance(command, dict) and command.get("type") == "skill" and isinstance(command.get("name"), str)
            )
    if expected_expanded_skill is not None and expected_expanded_skill not in expanded_skill_names:
        errors.append(f"init event did not expand the required skill: {expected_expanded_skill}")
    if len(result_records) == 1:
        result_payload = result_records[0].get("result")
        if not isinstance(result_payload, dict):
            errors.append("result event payload must be an object")
        else:
            if result_payload.get("status") != "SUCCESS":
                errors.append("result event status must be SUCCESS")
            raw_response = result_payload.get("response")
            if not isinstance(raw_response, str) or not raw_response:
                errors.append("result event response must be a non-empty string")
            else:
                response = raw_response
            raw_structured_output = result_payload.get("structured_output")
            if isinstance(raw_structured_output, dict):
                structured_output = raw_structured_output
            result_conversation = result_payload.get("conversation_id")
            if conversation_id and result_conversation != conversation_id:
                errors.append("result conversation_id does not match init")

    if require_tool_free:
        tool_events = [
            record
            for record in records
            if record.get("event") == "step_update"
            and isinstance(record.get("step_update"), dict)
            and record["step_update"].get("step_type") == "tool"
        ]
        if tool_events:
            errors.append("structured-output preflight must not contain tool events")
    return errors, conversation_id, response, structured_output, expanded_skill_names


def _stop_process_group(process: subprocess.Popen[bytes]) -> tuple[int, str]:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return process.wait(), "already-exited"
    try:
        return process.wait(timeout=2), "terminated"
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        return process.wait(), "killed"


def _delete_disposable_credential(credential_path: Path) -> bool:
    with suppress(FileNotFoundError):
        credential_path.unlink()
    return not credential_path.exists() and not credential_path.is_symlink()


def run_antigravity(
    *,
    project_root: Path,
    state_root: Path,
    credential_file: Path,
    prompt: str,
    stdout_path: Path,
    stderr_path: Path,
    final_response_path: Path,
    metadata_path: Path,
    repository_root: Path,
    runner: str = "agy",
    model: str = DEFAULT_MODEL,
    effort: str = "high",
    timeout_seconds: int = 300,
    json_schema: str | None = None,
    require_tool_free: bool = False,
    skill_bundle: Path | None = None,
    expected_expanded_skill: str | None = None,
) -> AntigravityResult:
    project_root = project_root.resolve()
    repository_root = repository_root.resolve()
    if not project_root.is_dir():
        raise ValueError("Antigravity project root must be an existing directory")
    credential_file = credential_file.resolve()
    if not credential_file.is_file():
        raise ValueError("Antigravity credential file is missing")
    for path, label in (
        (stdout_path, "Antigravity stdout"),
        (stderr_path, "Antigravity stderr"),
        (final_response_path, "Antigravity final response"),
        (metadata_path, "Antigravity run metadata"),
    ):
        _refuse_existing(path, label)
        path.parent.mkdir(parents=True, exist_ok=True)

    state_root = _validate_external_root(state_root, (repository_root, project_root))
    state_cli_root = state_root / ".gemini" / "antigravity-cli"
    state_cli_root.mkdir(parents=True, mode=0o700)
    staged_skill: dict[str, str] | None = None
    if skill_bundle is not None:
        source_skill = skill_bundle.resolve()
        skill_md = source_skill / "SKILL.md"
        if not source_skill.is_dir() or not skill_md.is_file():
            raise ValueError("Antigravity skill bundle must contain SKILL.md")
        bundle_digest = _bundle_digest(source_skill)
        installed_skill = state_cli_root / "skills" / source_skill.name
        installed_skill.parent.mkdir(parents=True, mode=0o700)
        shutil.copytree(source_skill, installed_skill)
        staged_skill = {
            "name": source_skill.name,
            "install_root": str(installed_skill),
            "bundle_sha256": bundle_digest,
            "skill_md_sha256": sha256(skill_md.read_bytes()).hexdigest(),
        }
    ambient_brain = credential_file.parent / "brain"
    ambient_before = _tree_metadata(ambient_brain)
    command = _build_command(
        runner,
        prompt=prompt,
        model=model,
        effort=effort,
        log_path=Path(os.devnull),
        timeout_seconds=timeout_seconds,
        json_schema=json_schema,
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(state_root),
            "XDG_CACHE_HOME": str(state_root / ".cache"),
            "XDG_CONFIG_HOME": str(state_root / ".config"),
            "XDG_DATA_HOME": str(state_root / ".local" / "share"),
        }
    )
    command, keychain_containment_mode = _configure_keychain_containment(command)
    staged_credential = state_cli_root / "antigravity-oauth-token"
    records: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    credential_removed_after_init = False
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    process_cleanup = "clean-exit"
    terminated_after_terminal_result = False
    credential_disposed_after_run = False
    try:
        shutil.copyfile(credential_file, staged_credential)
        staged_credential.chmod(0o600)
        with (
            stdout_path.open("xb") as stdout_file,
            stderr_path.open("x", encoding="utf-8") as stderr_file,
        ):
            # The caller selects a local harness executable; no shell is involved.
            process = subprocess.Popen(  # nosec B603
                command,
                cwd=project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                start_new_session=True,
            )
            if process.stdout is None:
                raise RuntimeError("Antigravity stdout pipe is unavailable")
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            pending = b""
            line_number = 0
            stdout_eof = False
            terminal_seen_at: float | None = None
            process_exited_at: float | None = None
            started_at = time.monotonic()

            def consume_line(raw_line: bytes) -> None:
                nonlocal credential_removed_after_init, line_number, terminal_seen_at
                line_number += 1
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    parse_errors.append(f"stdout line {line_number} is not UTF-8")
                    return
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors.append(f"stdout line {line_number} is not JSON")
                    return
                if not isinstance(record, dict):
                    parse_errors.append(f"stdout line {line_number} is not a JSON object")
                    return
                records.append(record)
                if record.get("event") == "init" and staged_credential.exists():
                    staged_credential.unlink()
                    credential_removed_after_init = True
                if record.get("event") == "result":
                    terminal_seen_at = time.monotonic()

            while True:
                now = time.monotonic()
                if terminal_seen_at is not None and process.poll() is None and now - terminal_seen_at >= 2:
                    _, stop_method = _stop_process_group(process)
                    process_cleanup = f"{stop_method}-after-terminal-result"
                    terminated_after_terminal_result = True
                elif terminal_seen_at is None and process.poll() is None and now - started_at >= timeout_seconds:
                    _, stop_method = _stop_process_group(process)
                    process_cleanup = f"{stop_method}-after-hard-timeout"
                    parse_errors.append("Antigravity process exceeded the hard wall-clock timeout")

                if process.poll() is not None:
                    if process_exited_at is None:
                        process_exited_at = now
                    if stdout_eof or now - process_exited_at >= 2:
                        exit_code = process.returncode
                        if not stdout_eof and process_cleanup == "clean-exit":
                            process_cleanup = "bounded-drain-after-process-exit"
                        break

                next_deadline = started_at + timeout_seconds
                if terminal_seen_at is not None:
                    next_deadline = min(next_deadline, terminal_seen_at + 2)
                if process_exited_at is not None:
                    next_deadline = min(next_deadline, process_exited_at + 2)
                wait_seconds = max(0.0, min(0.25, next_deadline - time.monotonic()))
                for key, _mask in selector.select(wait_seconds):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        stdout_eof = True
                        continue
                    stdout_file.write(chunk)
                    stdout_file.flush()
                    pending += chunk
                    while b"\n" in pending:
                        raw_line, pending = pending.split(b"\n", 1)
                        consume_line(raw_line)

            if pending:
                consume_line(pending)
            selector.close()
            process.stdout.close()
    finally:
        try:
            if process is not None and process.poll() is None:
                _stop_process_group(process)
        finally:
            try:
                if selector is not None:
                    selector.close()
                if process is not None and process.stdout is not None:
                    process.stdout.close()
            finally:
                credential_disposed_after_run = _delete_disposable_credential(staged_credential)

    trace_errors, conversation_id, response, structured_output, expanded_skill_names = _validate_trace(
        records,
        model=model,
        exit_code=exit_code,
        require_tool_free=require_tool_free,
        terminated_after_terminal_result=terminated_after_terminal_result,
        expected_expanded_skill=expected_expanded_skill,
    )
    trace_errors = [*parse_errors, *trace_errors]
    if not credential_disposed_after_run:
        trace_errors.append("the disposable Antigravity credential file was not removed after the run")
    if response is not None:
        final_response_path.write_text(response, encoding="utf-8")
    else:
        final_response_path.write_text("", encoding="utf-8")
    ambient_after = _tree_metadata(ambient_brain)
    ambient_brain_unchanged = ambient_before == ambient_after
    ambient_conversation_state_absent = conversation_id is not None and not (ambient_brain / conversation_id).exists()
    if not ambient_conversation_state_absent:
        trace_errors.append("the current conversation wrote ambient Antigravity brain state")
    metadata = {
        "schema_version": 1,
        "runner": runner,
        "model": model,
        "effort": effort,
        "command": ["<PROMPT>" if index == len(command) - 1 else value for index, value in enumerate(command)],
        "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
        "state_root": str(state_root),
        "state_brain_root": str(state_cli_root / "brain"),
        "keychain_containment_mode": keychain_containment_mode,
        "staged_skill": staged_skill,
        "credential_staged_with_mode": "0600",
        "credential_removed_after_init": credential_removed_after_init,
        "credential_file_disposed_after_run": credential_disposed_after_run,
        "credential_disposed_after_run": credential_disposed_after_run,
        "ambient_brain_before_sha256": ambient_before,
        "ambient_brain_after_sha256": ambient_after,
        "ambient_brain_unchanged": ambient_brain_unchanged,
        "ambient_conversation_state_absent": ambient_conversation_state_absent,
        "exit_code": exit_code,
        "process_cleanup": process_cleanup,
        "conversation_id": conversation_id,
        "structured_output": structured_output,
        "expanded_skill_names": list(expanded_skill_names),
        "trace_errors": trace_errors,
        "stdout_sha256": sha256(stdout_path.read_bytes()).hexdigest(),
        "stderr_sha256": sha256(stderr_path.read_bytes()).hexdigest(),
        "final_response_sha256": sha256(final_response_path.read_bytes()).hexdigest(),
    }
    metadata_path.write_text(f"{json.dumps(metadata, indent=2)}\n", encoding="utf-8")
    return AntigravityResult(
        exit_code=exit_code,
        conversation_id=conversation_id,
        response=response,
        structured_output=structured_output,
        trace_errors=tuple(trace_errors),
        credential_removed_after_init=credential_removed_after_init,
        ambient_brain_unchanged=ambient_brain_unchanged,
        ambient_conversation_state_absent=ambient_conversation_state_absent,
        process_cleanup=process_cleanup,
        expanded_skill_names=expanded_skill_names,
    )


def run_structured_output_preflight(**kwargs: Any) -> AntigravityResult:
    prompt = (
        "Complete the request using Antigravity's mandatory finish operation. "
        f'Set the finish argument named preflight directly to the string "{PREFLIGHT_RECEIPT}". '
        "Do not wrap it in another object, do not emit a separate response before finishing, "
        "and do not use any other tools."
    )
    result = run_antigravity(
        **kwargs,
        prompt=prompt,
        json_schema=PREFLIGHT_SCHEMA,
        require_tool_free=True,
    )
    errors = list(result.trace_errors)
    if result.structured_output != {"preflight": PREFLIGHT_RECEIPT}:
        errors.append("preflight structured_output does not match the required receipt object")
    updated = AntigravityResult(
        exit_code=result.exit_code,
        conversation_id=result.conversation_id,
        response=result.response,
        structured_output=result.structured_output,
        trace_errors=tuple(errors),
        credential_removed_after_init=result.credential_removed_after_init,
        ambient_brain_unchanged=result.ambient_brain_unchanged,
        ambient_conversation_state_absent=result.ambient_conversation_state_absent,
        process_cleanup=result.process_cleanup,
        expanded_skill_names=result.expanded_skill_names,
    )
    metadata_path = kwargs.get("metadata_path")
    if isinstance(metadata_path, Path) and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["trace_errors"] = list(updated.trace_errors)
        metadata["structured_output_preflight_valid"] = updated.valid
        metadata_path.write_text(f"{json.dumps(metadata, indent=2)}\n", encoding="utf-8")
    return updated
