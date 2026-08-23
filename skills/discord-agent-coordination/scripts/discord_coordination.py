#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DISCORD_MESSAGE_LIMIT = 2_000
DISCORD_THREAD_NAME_LIMIT = 100
THREAD_HASH_LENGTH = 12
ENVELOPE_MARKER = "[agent-coordination/v1]"
ENVELOPE_FIELDS = ("id", "kind", "from", "to", "task", "in-reply-to", "needs")
MESSAGE_KINDS = frozenset({"status", "request", "reply", "blocker", "handoff", "done"})
COMPONENT_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TARGET_ADDRESS_PATTERN = re.compile(
    r"epic/(?P<epic>[a-z0-9]+(?:-[a-z0-9]+)*)(?:/role/(?P<role>[a-z0-9]+(?:-[a-z0-9]+)*))?"
)
ROLE_ADDRESS_PATTERN = re.compile(r"epic/(?P<epic>[a-z0-9]+(?:-[a-z0-9]+)*)/role/(?P<role>[a-z0-9]+(?:-[a-z0-9]+)*)")
SENDER_ADDRESS_PATTERN = re.compile(
    r"epic/(?P<epic>[a-z0-9]+(?:-[a-z0-9]+)*)/role/(?P<role>[a-z0-9]+(?:-[a-z0-9]+)*)/"
    r"(?P<runtime>[a-z0-9]+(?:-[a-z0-9]+)*)"
)
TASK_PATTERN = re.compile(r"TASK-[A-Z0-9]+(?:-[A-Z0-9]+)*")
SNOWFLAKE_PATTERN = re.compile(r"[1-9][0-9]{0,19}")


class CoordinationError(ValueError):
    pass


def slug_component(value: str, label: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip()).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug or COMPONENT_PATTERN.fullmatch(slug) is None:
        raise CoordinationError(f"{label} does not contain a usable address component")
    return slug


def epic_address(epic_id: str) -> str:
    return f"epic/{slug_component(epic_id, 'epic ID')}"


def role_address(epic_id: str, role: str) -> str:
    return f"{epic_address(epic_id)}/role/{slug_component(role, 'role')}"


def sender_address(epic_id: str, role: str, runtime_agent_id: str) -> str:
    return f"{role_address(epic_id, role)}/{slug_component(runtime_agent_id, 'runtime agent ID')}"


def require_target_address(address: str) -> str:
    if TARGET_ADDRESS_PATTERN.fullmatch(address) is None:
        raise CoordinationError("target must be an epic activity or role inbox address")
    return address


def require_role_address(address: str) -> str:
    if ROLE_ADDRESS_PATTERN.fullmatch(address) is None:
        raise CoordinationError("cursor address must be a role inbox address")
    return address


def require_sender_address(address: str) -> str:
    if SENDER_ADDRESS_PATTERN.fullmatch(address) is None:
        raise CoordinationError("sender must include epic, role, and runtime agent components")
    return address


def thread_name(address: str, max_length: int = DISCORD_THREAD_NAME_LIMIT) -> str:
    require_target_address(address)
    minimum_length = THREAD_HASH_LENGTH + 2
    if not minimum_length <= max_length <= DISCORD_THREAD_NAME_LIMIT:
        raise CoordinationError(f"thread name limit must be between {minimum_length} and {DISCORD_THREAD_NAME_LIMIT}")
    readable = address.replace("/", "-")
    digest = hashlib.sha256(address.encode("utf-8")).hexdigest()[:THREAD_HASH_LENGTH]
    readable_limit = max_length - THREAD_HASH_LENGTH - 1
    return f"{readable[:readable_limit].rstrip('-')}-{digest}"


def require_uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise CoordinationError(f"{label} must be a UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise CoordinationError(f"{label} must use canonical lowercase UUID form")
    return value


def require_single_line(value: str, label: str) -> str:
    if not value or value != value.strip() or "\n" in value or "\r" in value or "\x00" in value:
        raise CoordinationError(f"{label} must be a non-empty, trimmed single line")
    return value


def require_task(value: str) -> str:
    require_single_line(value, "task")
    task_key, separator, reference = value.partition(" ")
    if TASK_PATTERN.fullmatch(task_key) is None:
        raise CoordinationError("task must begin with a TASK-* key")
    if separator:
        parsed = urlparse(reference)
        if parsed.scheme != "https" or not parsed.netloc or any(character.isspace() for character in reference):
            raise CoordinationError("task reference must be an HTTPS URL")
    return value


def validate_envelope_fields(fields: dict[str, str], body: str) -> None:
    require_uuid(fields["id"], "id")
    if fields["kind"] not in MESSAGE_KINDS:
        raise CoordinationError(f"kind must be one of: {', '.join(sorted(MESSAGE_KINDS))}")
    require_sender_address(fields["from"])
    require_target_address(fields["to"])
    require_task(fields["task"])
    if fields["in-reply-to"] != "none":
        require_uuid(fields["in-reply-to"], "in-reply-to")
    needs = require_single_line(fields["needs"], "needs")
    if len(needs) > 240:
        raise CoordinationError("needs must not exceed 240 characters")
    if not body.strip() or "\x00" in body or "\r" in body:
        raise CoordinationError("body must be non-empty UTF-8 text using LF line endings")


def parse_envelope(message: str) -> dict[str, str]:
    if len(message) > DISCORD_MESSAGE_LIMIT:
        raise CoordinationError(f"envelope exceeds Discord's {DISCORD_MESSAGE_LIMIT:,}-character limit")
    header, separator, body = message.partition("\n---\n")
    if not separator:
        raise CoordinationError("envelope is missing the header/body separator")
    lines = header.split("\n")
    if not lines or lines[0] != ENVELOPE_MARKER:
        raise CoordinationError("envelope marker is missing or unsupported")
    if len(lines) != len(ENVELOPE_FIELDS) + 1:
        raise CoordinationError("envelope has missing or extra header fields")

    fields: dict[str, str] = {}
    for line, expected_key in zip(lines[1:], ENVELOPE_FIELDS, strict=True):
        key, field_separator, value = line.partition(": ")
        if not field_separator or key != expected_key:
            raise CoordinationError(f"expected envelope field: {expected_key}")
        fields[key] = value
    validate_envelope_fields(fields, body)
    return {**fields, "body": body}


def render_envelope(
    *,
    kind: str,
    sender: str,
    target: str,
    task: str,
    needs: str,
    body: str,
    message_id: str | None = None,
    in_reply_to: str = "none",
) -> str:
    fields = {
        "id": message_id or str(uuid.uuid4()),
        "kind": kind,
        "from": sender,
        "to": target,
        "task": task,
        "in-reply-to": in_reply_to,
        "needs": needs,
    }
    message = "\n".join([ENVELOPE_MARKER, *(f"{key}: {fields[key]}" for key in ENVELOPE_FIELDS), "---", body])
    parse_envelope(message)
    return message


def require_snowflake(value: str, label: str) -> str:
    if SNOWFLAKE_PATTERN.fullmatch(value) is None:
        raise CoordinationError(f"{label} must be a positive Discord snowflake")
    return value


def default_state_directory() -> Path:
    override = os.environ.get("DISCORD_AGENT_COORDINATION_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    state_root = Path(xdg_state_home).expanduser() if xdg_state_home else Path.home() / ".local" / "state"
    return state_root / "discord-agent-coordination"


class CursorState:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_state_directory()
        self.path = self.directory / "state.json"

    def _ensure_directory(self) -> None:
        created = False
        try:
            self.directory.mkdir(mode=0o700, parents=True)
            created = True
        except FileExistsError:
            created = False
        directory_stat = self.directory.lstat()
        if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
            raise CoordinationError(f"state directory is not a real directory: {self.directory}")
        if created:
            self.directory.chmod(0o700)
        elif stat.S_IMODE(directory_stat.st_mode) & 0o077:
            raise CoordinationError(f"state directory permissions are broader than 0700: {self.directory}")

    def _load(self) -> dict[str, Any]:
        self._ensure_directory()
        if not self.path.exists():
            return {"version": 1, "inboxes": {}}
        path_stat = self.path.lstat()
        if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
            raise CoordinationError(f"state path is not a regular file: {self.path}")
        if stat.S_IMODE(path_stat.st_mode) & 0o077:
            raise CoordinationError(f"state file permissions are broader than 0600: {self.path}")
        try:
            state: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CoordinationError(f"state file is unreadable or malformed: {self.path}") from exc
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("inboxes"), dict):
            raise CoordinationError(f"state file has an unsupported schema: {self.path}")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        self._ensure_directory()
        descriptor, temporary_name = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            self.path.chmod(0o600)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

    def get(self, address: str) -> dict[str, str | None]:
        require_role_address(address)
        state = self._load()
        raw_entry = state["inboxes"].get(address)
        if raw_entry is None:
            return {"thread_id": None, "cursor": None}
        if not isinstance(raw_entry, dict):
            raise CoordinationError(f"state entry is malformed for {address}")
        thread_id = raw_entry.get("thread_id")
        cursor = raw_entry.get("cursor")
        if thread_id is not None and not isinstance(thread_id, str):
            raise CoordinationError(f"thread ID is malformed for {address}")
        if cursor is not None and not isinstance(cursor, str):
            raise CoordinationError(f"cursor is malformed for {address}")
        if thread_id is not None:
            require_snowflake(thread_id, "thread ID")
        if cursor is not None:
            require_snowflake(cursor, "cursor")
        return {"thread_id": thread_id, "cursor": cursor}

    def advance(self, address: str, cursor: str, thread_id: str | None = None) -> bool:
        require_role_address(address)
        require_snowflake(cursor, "cursor")
        if thread_id is not None:
            require_snowflake(thread_id, "thread ID")
        state = self._load()
        inboxes = state["inboxes"]
        raw_entry = inboxes.get(address, {})
        if not isinstance(raw_entry, dict):
            raise CoordinationError(f"state entry is malformed for {address}")
        previous_cursor = raw_entry.get("cursor")
        previous_thread_id = raw_entry.get("thread_id")
        if previous_cursor is not None:
            if not isinstance(previous_cursor, str):
                raise CoordinationError(f"cursor is malformed for {address}")
            require_snowflake(previous_cursor, "stored cursor")
            if int(cursor) < int(previous_cursor):
                raise CoordinationError(f"cursor regression for {address}: {cursor} < {previous_cursor}")
        if previous_thread_id is not None:
            if not isinstance(previous_thread_id, str):
                raise CoordinationError(f"thread ID is malformed for {address}")
            require_snowflake(previous_thread_id, "stored thread ID")
        if previous_thread_id is not None and previous_thread_id != thread_id and thread_id is not None:
            raise CoordinationError(f"address already maps to a different thread ID: {address}")
        resolved_thread_id = thread_id if thread_id is not None else previous_thread_id
        updated = previous_cursor != cursor or previous_thread_id != resolved_thread_id
        if not updated:
            return False
        inboxes[address] = {"thread_id": resolved_thread_id, "cursor": cursor}
        self._write(state)
        return True


def read_body(arguments: argparse.Namespace) -> str:
    if arguments.body_file is not None:
        try:
            return arguments.body_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CoordinationError(f"could not read body file: {arguments.body_file}") from exc
    return arguments.body


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and validate Discord agent-coordination protocol data")
    commands = parser.add_subparsers(dest="command", required=True)

    address_parser = commands.add_parser("address", help="Generate an epic, role, or sender address")
    address_parser.add_argument("--epic", required=True)
    address_parser.add_argument("--role")
    address_parser.add_argument("--runtime-agent")

    thread_parser = commands.add_parser("thread-name", help="Generate a deterministic Discord thread name")
    thread_parser.add_argument("address")
    thread_parser.add_argument("--max-length", type=int, default=DISCORD_THREAD_NAME_LIMIT)

    render_parser = commands.add_parser("render", help="Render and validate an envelope")
    render_parser.add_argument("--id", dest="message_id")
    render_parser.add_argument("--kind", required=True, choices=sorted(MESSAGE_KINDS))
    render_parser.add_argument("--from", dest="sender", required=True)
    render_parser.add_argument("--to", dest="target", required=True)
    render_parser.add_argument("--task", required=True)
    render_parser.add_argument("--in-reply-to", default="none")
    render_parser.add_argument("--needs", required=True)
    body_group = render_parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body")
    body_group.add_argument("--body-file", type=Path)

    validate_parser = commands.add_parser("validate", help="Validate an envelope from a file or stdin")
    validate_parser.add_argument("--file", type=Path)

    cursor_parser = commands.add_parser("cursor", help="Read or monotonically advance an inbox cursor")
    cursor_parser.add_argument("action", choices=("get", "advance"))
    cursor_parser.add_argument("--address", required=True)
    cursor_parser.add_argument("--cursor")
    cursor_parser.add_argument("--thread-id")
    cursor_parser.add_argument("--state-dir", type=Path)
    return parser


def run(arguments: argparse.Namespace) -> None:
    if arguments.command == "address":
        if arguments.runtime_agent and not arguments.role:
            raise CoordinationError("--runtime-agent requires --role")
        if arguments.runtime_agent:
            print(sender_address(arguments.epic, arguments.role, arguments.runtime_agent))
        elif arguments.role:
            print(role_address(arguments.epic, arguments.role))
        else:
            print(epic_address(arguments.epic))
    elif arguments.command == "thread-name":
        print(thread_name(arguments.address, arguments.max_length))
    elif arguments.command == "render":
        print(
            render_envelope(
                kind=arguments.kind,
                sender=arguments.sender,
                target=arguments.target,
                task=arguments.task,
                needs=arguments.needs,
                body=read_body(arguments),
                message_id=arguments.message_id,
                in_reply_to=arguments.in_reply_to,
            )
        )
    elif arguments.command == "validate":
        try:
            message = arguments.file.read_text(encoding="utf-8") if arguments.file else sys.stdin.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise CoordinationError("could not read envelope") from exc
        print(json.dumps(parse_envelope(message), sort_keys=True))
    elif arguments.command == "cursor":
        store = CursorState(arguments.state_dir)
        if arguments.action == "get":
            if arguments.cursor is not None or arguments.thread_id is not None:
                raise CoordinationError("cursor get does not accept --cursor or --thread-id")
            print(json.dumps(store.get(arguments.address), sort_keys=True))
        else:
            if arguments.cursor is None:
                raise CoordinationError("cursor advance requires --cursor")
            updated = store.advance(arguments.address, arguments.cursor, arguments.thread_id)
            print(json.dumps({"updated": updated, **store.get(arguments.address)}, sort_keys=True))


def main() -> int:
    parser = build_parser()
    try:
        run(parser.parse_args())
    except CoordinationError as exc:
        print(f"discord coordination error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
