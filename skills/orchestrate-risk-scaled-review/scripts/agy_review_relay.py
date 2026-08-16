#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

MODEL = "gemini-3.7-flash-high"
CONFIGURATION_EXIT = 64
PROTOCOL_EXIT = 65
INVOCATION_EXIT = 69
MAX_PROMPT_FILE_BYTES = 16 * 1024
PROMPT_SENTINEL_PREFIX = "TRAYCER_PROMPT_SENTINEL_"
PROMPT_SENTINEL_PATTERN = re.compile(rf"{PROMPT_SENTINEL_PREFIX}[A-Za-z0-9_-]{{32}}")
MAX_SENTINEL_LINE_BYTES = 128
MALFORMED_SENTINEL_ERROR = "prompt artifact first line is not a well-formed sentinel"
PRINT_TIMEOUT_ARGUMENTS = {"10m": "10m", "30m": "30m"}
PromptIdentity = tuple[int, int, int, int, int]


class RelayConfigurationError(Exception):
    pass


class RelayUnavailableError(Exception):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely relay one prompt to Antigravity CLI")
    parser.add_argument("--conversation", help="Explicit conversation ID to resume")
    parser.add_argument("--prompt-file", required=True, type=Path, help="Absolute path to the durable review prompt")
    parser.add_argument(
        "--print-timeout",
        required=True,
        choices=tuple(PRINT_TIMEOUT_ARGUMENTS),
        help="Explicit agy print timeout: 30m for reviews or 10m for reconciliation",
    )
    return parser.parse_args()


def resolve_agy() -> Path:
    override = os.environ.get("AGY_BIN")
    candidate = Path(override).expanduser() if override else None

    if candidate is None:
        discovered = shutil.which("agy")
        if discovered is None:
            raise RelayUnavailableError("agy is unavailable: set AGY_BIN or add agy to PATH")
        candidate = Path(discovered)

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RelayUnavailableError(f"agy executable does not exist: {candidate}") from exc

    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RelayUnavailableError(f"agy path is not an executable file: {resolved}")
    return resolved


def prompt_handle_instruction(prompt_path: Path) -> str:
    return (
        f"Read and follow the complete review prompt at {prompt_path}. "
        "Begin your response with the exact sentinel from its first line. "
        f"If you cannot read the file, stop and respond exactly: PROMPT_FILE_UNREADABLE: {prompt_path}"
    )


def build_command(agy: Path, prompt_path: Path, print_timeout: str, conversation: str | None) -> list[str]:
    if print_timeout == "10m":
        timeout_argument = "10m"
    elif print_timeout == "30m":
        timeout_argument = "30m"
    else:
        raise RelayConfigurationError(f"unsupported print timeout: {print_timeout}")
    command = [
        str(agy),
        "--model",
        MODEL,
        "--mode",
        "plan",
        "--sandbox",
        "--output-format",
        "stream-json",
        "--print-timeout",
        timeout_argument,
        "--add-dir",
        str(prompt_path.parent),
    ]
    if conversation:
        command.extend(["--conversation", conversation])
    command.extend(["--prompt", prompt_handle_instruction(prompt_path)])
    return command


def ensure_prompt_stays_in_directory(resolved: Path, dedicated_directory: Path) -> None:
    if resolved.parent != dedicated_directory:
        raise RelayConfigurationError(
            f"prompt file resolves outside its dedicated directory: {resolved} (directory: {dedicated_directory})"
        )


def prompt_identity(prompt_stat: os.stat_result) -> PromptIdentity:
    return (
        prompt_stat.st_dev,
        prompt_stat.st_ino,
        prompt_stat.st_size,
        prompt_stat.st_mtime_ns,
        prompt_stat.st_ctime_ns,
    )


def require_prompt_identity(prompt_path: Path, expected: PromptIdentity, stage: str) -> None:
    try:
        current = prompt_identity(prompt_path.stat())
    except OSError as exc:
        raise RelayConfigurationError(f"prompt file changed {stage}: {prompt_path}: {exc}") from exc
    if current != expected:
        raise RelayConfigurationError(f"prompt file changed {stage}: {prompt_path}")


def dedicated_prompt_entry(prompt_path: Path) -> tuple[Path, Path]:
    if not prompt_path.is_absolute():
        raise RelayConfigurationError("prompt file path must be absolute")

    try:
        dedicated_directory = prompt_path.parent.resolve(strict=True)
        entries = list(dedicated_directory.iterdir())
    except OSError as exc:
        raise RelayConfigurationError(f"prompt directory is unavailable: {prompt_path.parent}: {exc}") from exc

    expected_entry = dedicated_directory / prompt_path.name
    if not entries:
        raise RelayConfigurationError(f"prompt file is unavailable: {expected_entry}")
    if len(entries) != 1:
        offending = sorted(entry.name for entry in entries if entry != expected_entry)
        if not offending:
            offending = sorted(entry.name for entry in entries)
        raise RelayConfigurationError("dedicated prompt directory contains unexpected entries: " + ", ".join(offending))

    sole_entry = entries[0]
    try:
        entry_is_named_prompt = expected_entry.exists() and os.path.samefile(sole_entry, expected_entry)
    except OSError:
        entry_is_named_prompt = False
    if not entry_is_named_prompt:
        raise RelayConfigurationError(
            f"dedicated prompt directory contains wrong entry: {sole_entry.name}; expected {prompt_path.name}"
        )

    try:
        entry_stat = sole_entry.lstat()
    except OSError as exc:
        raise RelayConfigurationError(f"prompt file is unavailable: {sole_entry}: {exc}") from exc
    if not stat.S_ISREG(entry_stat.st_mode):
        raise RelayConfigurationError(f"dedicated prompt directory entry is not a regular file: {sole_entry.name}")
    return dedicated_directory, sole_entry


def resolve_prompt_entry(sole_entry: Path, dedicated_directory: Path) -> tuple[Path, os.stat_result]:
    try:
        resolved = sole_entry.resolve(strict=True)
        prompt_stat = resolved.stat()
    except OSError as exc:
        raise RelayConfigurationError(f"prompt file is unavailable: {sole_entry}: {exc}") from exc
    ensure_prompt_stays_in_directory(resolved, dedicated_directory)

    if not stat.S_ISREG(prompt_stat.st_mode):
        raise RelayConfigurationError(f"prompt file is not a regular file: {resolved}")
    if prompt_stat.st_size > MAX_PROMPT_FILE_BYTES:
        raise RelayConfigurationError(
            f"prompt file is {prompt_stat.st_size} bytes; maximum is {MAX_PROMPT_FILE_BYTES} bytes "
            "because prompt files must contain handles, not inlined review content"
        )
    return resolved, prompt_stat


def read_sentinel_line(resolved: Path) -> str:
    try:
        with resolved.open("rb") as handle:
            first_line = handle.readline(MAX_SENTINEL_LINE_BYTES + 1)
    except OSError as exc:
        raise RelayConfigurationError(f"prompt file is not readable: {resolved}: {exc}") from exc

    if len(first_line) > MAX_SENTINEL_LINE_BYTES or not first_line.endswith(b"\n"):
        raise RelayConfigurationError(MALFORMED_SENTINEL_ERROR)
    try:
        sentinel = first_line.removesuffix(b"\n").removesuffix(b"\r").decode("ascii")
    except UnicodeDecodeError as exc:
        raise RelayConfigurationError(MALFORMED_SENTINEL_ERROR) from exc
    if PROMPT_SENTINEL_PATTERN.fullmatch(sentinel) is None:
        raise RelayConfigurationError(MALFORMED_SENTINEL_ERROR)
    return sentinel


def read_prompt_sentinel(prompt_path: Path) -> tuple[Path, str, PromptIdentity]:
    dedicated_directory, sole_entry = dedicated_prompt_entry(prompt_path)
    resolved, prompt_stat = resolve_prompt_entry(sole_entry, dedicated_directory)
    sentinel = read_sentinel_line(resolved)
    return resolved, sentinel, prompt_identity(prompt_stat)


def parse_stream_event(raw_line: bytes, line_number: int, protocol_errors: list[str]) -> dict[str, object] | None:
    if not raw_line.strip():
        return None
    try:
        event: object = json.loads(raw_line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        protocol_errors.append(f"line {line_number} is not valid JSON")
        return None
    if not isinstance(event, dict):
        protocol_errors.append(f"line {line_number} is not a JSON object")
        return None
    return event


def record_conversation_id(
    value: object,
    source: str,
    conversation_ids: set[str],
    protocol_errors: list[str],
) -> None:
    if isinstance(value, str) and value.strip():
        conversation_ids.add(value)
    elif value is not None:
        protocol_errors.append(f"{source} conversation_id is not a non-empty string")


def parse_result_event(
    event: dict[str, object],
    conversation_ids: set[str],
    protocol_errors: list[str],
    sentinel: str,
) -> tuple[object, object]:
    result = event.get("result")
    if not isinstance(result, dict):
        protocol_errors.append("result event does not contain a JSON object")
        return None, None

    record_conversation_id(result.get("conversation_id"), "result", conversation_ids, protocol_errors)
    result_status = result.get("status")
    if result_status is not None and not isinstance(result_status, str):
        protocol_errors.append("result status is not a string")
    result_usage = result.get("usage")
    if result_usage is not None and not isinstance(result_usage, dict):
        protocol_errors.append("result usage is not a JSON object")
    result_response = result.get("response")
    if not isinstance(result_response, str):
        protocol_errors.append("result response is not a string")
    elif not result_response.splitlines() or result_response.splitlines()[0] != sentinel:
        protocol_errors.append("result response does not begin with the prompt sentinel")
    return result_status, result_usage


def parse_stream_metadata(stdout: bytes, agy_exit_code: int, sentinel: str) -> tuple[dict[str, object], bool]:
    conversation_ids: set[str] = set()
    protocol_errors: list[str] = []
    result_seen = False
    result_status: object = None
    result_usage: object = None

    for line_number, raw_line in enumerate(stdout.splitlines(), start=1):
        event = parse_stream_event(raw_line, line_number, protocol_errors)
        if event is None:
            continue
        event_name = event.get("event")
        if event_name == "init":
            record_conversation_id(event.get("conversation_id"), "init", conversation_ids, protocol_errors)
            continue
        if event_name != "result":
            continue
        result_seen = True
        result_status, result_usage = parse_result_event(
            event,
            conversation_ids,
            protocol_errors,
            sentinel,
        )

    if not result_seen:
        protocol_errors.append("stream contains no result event")
    if not conversation_ids:
        protocol_errors.append("stream contains no conversation_id")
    elif len(conversation_ids) > 1:
        protocol_errors.append("stream contains conflicting conversation_id values")

    conversation_id = next(iter(conversation_ids)) if len(conversation_ids) == 1 else None
    metadata: dict[str, object] = {
        "event": "relay_metadata",
        "conversation_id": conversation_id,
        "status": result_status,
        "usage": result_usage,
        "agy_exit_code": agy_exit_code,
        "protocol_errors": protocol_errors,
    }
    return metadata, bool(protocol_errors)


def encode_metadata(metadata: dict[str, object]) -> bytes:
    return json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"


def main() -> int:
    args = parse_args()
    try:
        if args.conversation is not None and not args.conversation.strip():
            raise RelayConfigurationError("conversation ID must not be blank")
        prompt_path, sentinel, expected_prompt_identity = read_prompt_sentinel(args.prompt_file)
    except RelayConfigurationError as exc:
        print(f"agy relay configuration error: {exc}", file=sys.stderr)
        return CONFIGURATION_EXIT

    try:
        agy = resolve_agy()
    except RelayUnavailableError as exc:
        print(f"agy relay unavailable: {exc}", file=sys.stderr)
        return INVOCATION_EXIT

    command = build_command(agy, prompt_path, args.print_timeout, args.conversation)
    try:
        require_prompt_identity(prompt_path, expected_prompt_identity, "before reviewer invocation")
    except RelayConfigurationError as exc:
        print(f"agy relay configuration error: {exc}", file=sys.stderr)
        return CONFIGURATION_EXIT

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            shell=False,
        )
    except OSError as exc:
        print(f"agy relay invocation error: {exc}", file=sys.stderr)
        return INVOCATION_EXIT

    prompt_change_error: str | None = None
    try:
        require_prompt_identity(prompt_path, expected_prompt_identity, "during reviewer execution")
    except RelayConfigurationError as exc:
        prompt_change_error = str(exc)

    metadata, protocol_failed = parse_stream_metadata(completed.stdout, completed.returncode, sentinel)
    if prompt_change_error is not None:
        protocol_errors = metadata["protocol_errors"]
        if isinstance(protocol_errors, list):
            protocol_errors.append(prompt_change_error)
        protocol_failed = True
    can_append_metadata = not completed.stdout or completed.stdout.endswith(b"\n")

    sys.stdout.buffer.write(completed.stdout)
    if can_append_metadata:
        sys.stdout.buffer.write(encode_metadata(metadata))
    sys.stdout.buffer.flush()

    sys.stderr.buffer.write(completed.stderr)
    if not can_append_metadata:
        diagnostic = (
            "agy relay protocol error: stdout does not end with a newline; "
            f"cannot append relay_metadata safely (agy exit code {completed.returncode})\n"
        )
        sys.stderr.buffer.write(diagnostic.encode("utf-8"))
    sys.stderr.buffer.flush()

    if protocol_failed or not can_append_metadata:
        return PROTOCOL_EXIT
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
