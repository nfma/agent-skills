#!/opt/homebrew/bin/python3
"""Per-user quota recovery supervisor for Traycer agent sessions."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import socket
import socketserver
import stat
import struct
import subprocess  # nosec B404
import sys
import tempfile
import termios
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID

STATE_VERSION = 2
SERVICE_LABEL = "com.nfma.traycer-quota-supervisor"
DEFAULT_ENDPOINT = "http://127.0.0.1:49440/mcp"
DEFAULT_POLL_SECONDS = 3_600.0
DEFAULT_SCAN_SECONDS = 60.0
DEFAULT_MISSING_GRACE_SECONDS = 120.0
DEFAULT_RESET_GRACE_SECONDS = 30.0
CURSOR_PROBE_TIMEOUT_SECONDS = 20.0
ANTIGRAVITY_PROBE_TIMEOUT_SECONDS = 30.0
MAX_SOCKET_REQUEST_BYTES = 65_536
RESUME_MESSAGE = (
    "Your provider quota is available again. Resume the work interrupted by the rate "
    "limit from the existing context, then report progress to your parent if one exists."
)


class SupervisorError(RuntimeError):
    """An expected supervisor failure."""


class TransportRejectedError(SupervisorError):
    """The local A2A service rejected a cached transport credential."""


@dataclass(frozen=True)
class QuotaStatus:
    state: str
    reason: str
    resets_at: int | None = None


@dataclass(frozen=True)
class Identity:
    agent_id: str
    harness: str
    parent_id: str | None
    archived: bool = False


@dataclass
class Credential:
    token: str
    endpoint: str
    pids: set[int] = field(default_factory=set)
    last_seen: float = field(default_factory=time.time)

    @property
    def identifier(self) -> str:
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RegistryAgent:
    agent_id: str
    surface: str
    harness: str
    parent_id: str | None
    archived: bool
    messageable: bool

    @property
    def open(self) -> bool:
        return not self.archived


@dataclass(frozen=True)
class RegistryView:
    agents: tuple[RegistryAgent, ...]
    authoritative_parent_ids: frozenset[str]


@dataclass
class Session:
    agent_id: str
    parent_id: str | None
    harness: str
    profile: str
    surface: str = "gui"
    registry_open: bool = True
    messageable: bool = True
    parent_messageable: bool = False
    status: str = "open"
    wake_sent: bool = False
    parent_sent: bool = False
    last_seen: float = field(default_factory=time.time)

    def persisted(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GroupState:
    state: str = "unknown"
    reason: str = "not yet polled"
    resets_at: int | None = None
    last_polled: float = 0.0
    next_poll_at: float = 0.0


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def state_root() -> Path:
    override = os.environ.get("TRAYCER_QUOTA_SUPERVISOR_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home).expanduser() if xdg_state_home else Path.home() / ".local" / "state"
    return base / "traycer-quota-supervisor"


def state_path() -> Path:
    return state_root() / "state.json"


def socket_path() -> Path:
    return state_root() / "supervisor.sock"


def lock_path() -> Path:
    return state_root() / "supervisor.lock"


def ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SupervisorError(f"state path is not a real directory: {path}")
    if metadata.st_mode & 0o077:
        # State can contain recovery metadata and must remain private to the user.
        # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions
        os.chmod(path, 0o700)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(data)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def validate_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as error:
        raise SupervisorError(f"invalid Traycer agent ID: {value!r}") from error


def validate_endpoint(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SupervisorError("the Traycer A2A endpoint must be local HTTP")
    try:
        port = parsed.port
    except ValueError as error:
        raise SupervisorError("the Traycer A2A endpoint has an invalid port") from error
    if not port or parsed.path != "/mcp":
        raise SupervisorError("the Traycer A2A endpoint must include a port and /mcp path")
    return value


def profile_identifier(profile: Any) -> str | None:
    if not isinstance(profile, dict):
        return None
    selection = profile.get("selection")
    if isinstance(selection, str):
        return selection
    if not isinstance(selection, dict):
        return None
    if selection.get("kind") == "ambient":
        return "ambient"
    for key in ("profileId", "profile_id", "id", "value"):
        value = selection.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def choose_profile(payload: Any) -> str:
    if not isinstance(payload, dict) or not isinstance(payload.get("profiles"), list):
        raise SupervisorError("Traycer returned malformed provider profiles")
    candidates: list[tuple[str, dict[str, Any]]] = []
    for profile in payload["profiles"]:
        identifier = profile_identifier(profile)
        if identifier and isinstance(profile, dict) and profile.get("authStatus") == "authenticated":
            candidates.append((identifier, profile))
    effective = [identifier for identifier, profile in candidates if profile.get("isEffectiveLastUsed") is True]
    if len(effective) == 1:
        return effective[0]
    if len(candidates) == 1:
        return candidates[0][0]
    raise SupervisorError("cannot identify one effective authenticated provider profile")


def quota_windows(value: Any, path: str = "") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        used_percent = value.get("usedPercent")
        if isinstance(used_percent, (int, float)) and not isinstance(used_percent, bool):
            yield path or "limit", value
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield from quota_windows(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from quota_windows(child, f"{path}[{index}]")


def classify_quota(limits: Any, now_ms: int | None = None) -> QuotaStatus:
    if not isinstance(limits, dict) or limits.get("available") is not True:
        return QuotaStatus("unknown", "provider usage is unavailable")
    reached_type = limits.get("rateLimitReachedType")
    if isinstance(reached_type, str) and reached_type:
        return QuotaStatus("blocked", f"Traycer reports limit reached: {reached_type}")

    credits = limits.get("credits")
    if isinstance(credits, dict):
        if credits.get("unlimited") is True:
            return QuotaStatus("available", "unlimited credits are available")
        balance = credits.get("balance")
        has_balance = False
        if (
            credits.get("hasCredits") is True
            and isinstance(balance, (int, float, str))
            and not isinstance(balance, bool)
        ):
            try:
                has_balance = float(balance) > 0
            except ValueError:
                has_balance = False
        if has_balance:
            return QuotaStatus("available", "usage credits are available")

    current_ms = int(time.time() * 1000) if now_ms is None else now_ms
    exhausted: list[tuple[str, int | None]] = []
    for path, window in quota_windows(limits):
        used_percent = window.get("usedPercent")
        if not isinstance(used_percent, (int, float)) or isinstance(used_percent, bool):
            continue
        if float(used_percent) < 100:
            continue
        resets_at = window.get("resetsAt")
        reset_ms = resets_at if isinstance(resets_at, int) and not isinstance(resets_at, bool) else None
        if reset_ms is not None and reset_ms <= current_ms:
            continue
        exhausted.append((path, reset_ms))
    if not exhausted:
        return QuotaStatus("available", "no exhausted quota windows")
    known_resets = [reset for _, reset in exhausted if reset is not None]
    names = ", ".join(path for path, _ in exhausted)
    return QuotaStatus(
        "blocked",
        f"exhausted quota window(s): {names}",
        max(known_resets) if known_resets else None,
    )


def parse_timestamp_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1_000)


def cursor_reset_timestamp(output: str) -> int | None:
    matches = re.findall(
        r"(?i)reset(?:s|_at)?(?:\s+at)?\s*[=:]?\s*"
        r"(\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?)",
        output,
    )
    return parse_timestamp_ms(matches[-1]) if matches else None


def strip_terminal_sequences(value: str) -> str:
    without_osc = re.sub(r"\x1b\].*?(?:\x07|\x1b\\)", "", value, flags=re.DOTALL)
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", without_osc)


def classify_cursor_status(output: Any) -> QuotaStatus:
    if not isinstance(output, str):
        return QuotaStatus("unknown", "Cursor status line is unavailable")
    plain = strip_terminal_sequences(output)
    cursor_matches = re.findall(r"(?i)\bcursor\s+([0-9]+(?:\.[0-9]+)?)%", plain)
    other_matches = re.findall(
        r"(?i)\bother\s+\$?([0-9]+(?:\.[0-9]+)?)/\$?([0-9]+(?:\.[0-9]+)?)",
        plain,
    )
    if not cursor_matches and not other_matches:
        return QuotaStatus("unknown", "Cursor status line has no quota values")
    exhausted: list[str] = []
    details: list[str] = []
    if cursor_matches:
        used_percent = float(cursor_matches[-1])
        details.append(f"cursor={used_percent:g}%")
        if used_percent >= 100:
            exhausted.append("cursor")
    if other_matches:
        used_value, limit_value = (float(value) for value in other_matches[-1])
        details.append(f"other=${used_value:g}/${limit_value:g}")
        if limit_value > 0 and used_value >= limit_value:
            exhausted.append("other")
    reset_at = cursor_reset_timestamp(plain)
    if exhausted:
        return QuotaStatus(
            "blocked",
            f"Cursor quota exhausted: {', '.join(exhausted)} ({', '.join(details)})",
            reset_at,
        )
    return QuotaStatus("available", f"Cursor quota available ({', '.join(details)})", reset_at)


def classify_antigravity_usage(payload: Any) -> QuotaStatus:
    data = payload.get("command", {}).get("data", {}) if isinstance(payload, dict) else {}
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return QuotaStatus("unknown", "Antigravity /usage returned no quota groups")
    exhausted: list[str] = []
    resets: list[int] = []
    bucket_count = 0
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
            continue
        for bucket in group["buckets"]:
            if not isinstance(bucket, dict):
                continue
            remaining = bucket.get("remaining_fraction")
            if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
                continue
            bucket_count += 1
            if float(remaining) > 0:
                continue
            label = bucket.get("id") or bucket.get("name") or f"bucket-{bucket_count}"
            exhausted.append(str(label))
            reset_at = parse_timestamp_ms(bucket.get("reset_time"))
            if reset_at is not None:
                resets.append(reset_at)
    if bucket_count == 0:
        return QuotaStatus("unknown", "Antigravity /usage returned no quota buckets")
    if exhausted:
        return QuotaStatus(
            "blocked",
            f"Antigravity quota exhausted: {', '.join(exhausted)}",
            max(resets) if resets else None,
        )
    return QuotaStatus("available", "Antigravity quota is available")


def resolve_executable(environment_name: str, name: str) -> str:
    override = os.environ.get(environment_name)
    if override:
        path = Path(override).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise SupervisorError(f"configured {name} executable is unavailable: {path}")
    local = Path.home() / ".local" / "bin" / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    discovered = shutil.which(name)
    if discovered:
        return discovered
    raise SupervisorError(f"{name} executable is unavailable")


def probe_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("TRAYCER_A2A_")}


def terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (PermissionError, ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired as error:
        raise SupervisorError("Cursor quota probe process did not terminate") from error


def capture_cursor_status_output(timeout: float = CURSOR_PROBE_TIMEOUT_SECONDS) -> str:
    executable = resolve_executable("TRAYCER_QUOTA_CURSOR_BIN", "cursor")
    ensure_private_directory(state_root())
    master, slave = pty.openpty()
    process: subprocess.Popen[bytes] | None = None
    output = bytearray()
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        # resolve_executable accepts only an executable file.
        process = subprocess.Popen(  # nosec B603
            [executable, "agent", "--trust", "--mode", "ask"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=state_root(),
            env=probe_environment(),
            start_new_session=True,
        )
        os.close(slave)
        slave = -1
        deadline = time.monotonic() + timeout
        skipped_mcp_approval = False
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], min(0.5, deadline - time.monotonic()))
            if not readable:
                if process.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(master, 65_536)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > 1_000_000:
                del output[:-500_000]
            decoded = output.decode("utf-8", errors="ignore")
            if not skipped_mcp_approval and "Continue without approval" in decoded:
                os.write(master, b"c")
                skipped_mcp_approval = True
            quota = classify_cursor_status(decoded)
            if quota.state != "unknown":
                break
        return output.decode("utf-8", errors="ignore")
    except OSError as error:
        raise SupervisorError(f"Cursor quota probe failed: {error}") from error
    finally:
        if slave >= 0:
            os.close(slave)
        os.close(master)
        if process is not None:
            terminate_process_group(process)


def probe_cursor_quota(timeout: float = CURSOR_PROBE_TIMEOUT_SECONDS) -> QuotaStatus:
    return classify_cursor_status(capture_cursor_status_output(timeout))


def probe_antigravity_quota(timeout: float = ANTIGRAVITY_PROBE_TIMEOUT_SECONDS) -> QuotaStatus:
    executable = resolve_executable("TRAYCER_QUOTA_ANTIGRAVITY_BIN", "agy")
    try:
        # resolve_executable accepts only an executable file.
        result = subprocess.run(  # nosec B603
            [executable, "-p", "/usage", "--output-format", "json"],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
            env=probe_environment(),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SupervisorError(f"Antigravity quota probe failed: {error}") from error
    if result.returncode != 0:
        raise SupervisorError(f"Antigravity /usage exited with status {result.returncode}")
    payload: Any = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    return classify_antigravity_usage(payload)


def parse_parent_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(
        r"(?:^|\n)Parent:\s*(?:\n\s*)?([0-9a-fA-F-]{36})\b",
        value,
        flags=re.IGNORECASE,
    )
    return validate_uuid(match.group(1)) if match else None


def parse_self(value: Any) -> tuple[str, str, bool]:
    if not isinstance(value, str):
        raise SupervisorError("Traycer returned malformed self identity")
    identifier = re.search(r"(?m)^([0-9a-fA-F-]{36})\b", value)
    harness = re.search(r"^Harness:\s*(\S+)\s*$", value, flags=re.IGNORECASE | re.MULTILINE)
    archived = re.search(r"^Archived:\s*(yes|no)\s*$", value, flags=re.IGNORECASE | re.MULTILINE)
    if not identifier or not harness:
        raise SupervisorError("Traycer self identity is missing an agent ID or harness")
    return validate_uuid(identifier.group(1)), harness.group(1), bool(archived and archived.group(1) == "yes")


REGISTRY_SECTIONS = {
    "You": "you",
    "Parent": "parent",
    "Siblings": "siblings",
    "Children (agents you spawned)": "children",
}
REGISTRY_MARKERS = {"[self]", "[archived]"}
REGISTRY_EXECUTION_PATTERN = re.compile(r"[a-z][a-z0-9_-]*/[a-z][a-z0-9_-]*")


def parse_registry_entry(value: str) -> tuple[str, str, str, bool, bool, bool]:
    prefix = re.match(r"^([0-9a-fA-F-]{36})\s+(.+)$", value)
    if prefix is None:
        raise SupervisorError("Traycer returned a malformed agent registry entry")
    agent_id = validate_uuid(prefix.group(1))
    remainder = prefix.group(2)
    markers: set[str] = set()
    while marker_match := re.match(r"^(\[[^\]\s]+\])(?:\s+|$)", remainder):
        marker = marker_match.group(1)
        if marker not in REGISTRY_MARKERS or marker in markers:
            raise SupervisorError("Traycer returned an unknown agent registry marker")
        markers.add(marker)
        remainder = remainder[marker_match.end() :]

    try:
        words = shlex.split(remainder)
    except ValueError as error:
        raise SupervisorError("Traycer returned a malformed agent registry entry") from error
    if not words:
        raise SupervisorError("Traycer returned an empty agent registry entry")
    execution_indexes = [index for index, word in enumerate(words[:2]) if REGISTRY_EXECUTION_PATTERN.fullmatch(word)]
    if not execution_indexes:
        raise SupervisorError("Traycer agent registry entry has no unambiguous surface/harness")
    execution_index = execution_indexes[-1]
    execution = words[execution_index]
    surface, harness = execution.split("/", 1)
    trailing = words[execution_index + 1 :]
    action = None
    if trailing and trailing[0] in {"R", "S", "R/S", "-"}:
        action = trailing.pop(0)
    if trailing and (trailing[0] not in {"dir:", "worktree:"} or len(trailing) < 2):
        raise SupervisorError("Traycer returned malformed agent registry metadata")
    messageable = action in {"S", "R/S"} or "[self]" in markers
    return agent_id, surface, harness, "[archived]" in markers, messageable, "[self]" in markers


def parse_agent_registry(value: Any) -> RegistryView:
    if not isinstance(value, str):
        raise SupervisorError("Traycer returned a malformed agent registry")
    lines = value.splitlines()
    if not lines or not lines[0].startswith("Agents in epic"):
        raise SupervisorError("Traycer agent registry is missing its header")

    sections: dict[str, list[tuple[str, str, str, bool, bool, bool]]] = {
        section: [] for section in REGISTRY_SECTIONS.values()
    }
    current_section: str | None = None
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line == "Legend:":
            break
        if line.endswith(":") and line[:-1] in REGISTRY_SECTIONS:
            current_section = REGISTRY_SECTIONS[line[:-1]]
            continue
        if line.casefold() in {"none", "(none)"} and current_section is not None:
            continue
        if current_section is None:
            raise SupervisorError("Traycer returned an entry outside an agent registry section")
        sections[current_section].append(parse_registry_entry(line))

    if len(sections["you"]) != 1 or len(sections["parent"]) > 1:
        raise SupervisorError("Traycer agent registry must contain one caller and at most one parent")
    you = sections["you"][0]
    if not you[5]:
        raise SupervisorError("Traycer agent registry caller is missing the self marker")
    if any(entry[5] for name, entries in sections.items() if name != "you" for entry in entries):
        raise SupervisorError("Traycer agent registry self marker appears outside the caller section")

    parent_id = sections["parent"][0][0] if sections["parent"] else None
    agents: list[RegistryAgent] = []
    seen_ids: set[str] = set()
    for section, entries in sections.items():
        for entry in entries:
            agent_id, surface, harness, archived, messageable, _is_self = entry
            if agent_id in seen_ids:
                raise SupervisorError("Traycer agent registry contains a duplicate agent")
            seen_ids.add(agent_id)
            direct_parent = None
            if section in {"you", "siblings"}:
                direct_parent = parent_id
            elif section == "children":
                direct_parent = you[0]
            agents.append(
                RegistryAgent(
                    agent_id=agent_id,
                    surface=surface,
                    harness=harness,
                    parent_id=direct_parent,
                    archived=archived,
                    messageable=messageable,
                )
            )

    authoritative_parent_ids = {you[0]}
    if parent_id:
        authoritative_parent_ids.add(parent_id)
    return RegistryView(tuple(agents), frozenset(authoritative_parent_ids))


class A2AClient:
    def __init__(self, endpoint: str, token: str, timeout: float = 10.0) -> None:
        self.endpoint = validate_endpoint(endpoint)
        if not token or len(token) > 8192 or any(character.isspace() for character in token):
            raise SupervisorError("invalid Traycer A2A bearer credential")
        self.token = token
        self.timeout = timeout
        self.request_id = 0

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.request_id += 1
        request_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self.request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=request_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        try:
            # validate_endpoint permits loopback HTTP endpoints only.
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with urlopen(request, timeout=self.timeout) as response:  # nosec B310
                body = response.read().decode("utf-8")
        except HTTPError as error:
            if error.code in {401, 403}:
                raise TransportRejectedError("Traycer A2A rejected the transport credential") from error
            raise SupervisorError(f"Traycer A2A request failed with HTTP status {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise SupervisorError(f"Traycer A2A request failed: {error}") from error
        payload = self._parse_response(body)
        if isinstance(payload.get("error"), dict):
            raise SupervisorError(f"Traycer A2A error: {payload['error'].get('message', 'unknown error')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise SupervisorError("Traycer A2A response has no result")
        content = result.get("content")
        if result.get("isError") is True:
            raise SupervisorError(self._content_text(content) or "Traycer tool returned an error")
        text_value = self._content_text(content)
        if text_value is None:
            return result.get("structuredContent", {})
        try:
            return json.loads(text_value)
        except json.JSONDecodeError:
            return text_value

    @staticmethod
    def _parse_response(body: str) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for line in body.splitlines():
            value = line[5:].strip() if line.startswith("data:") else line.strip()
            if not value or value == "[DONE]":
                continue
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                candidates.append(parsed)
        if not candidates:
            raise SupervisorError("Traycer A2A returned malformed JSON")
        return candidates[-1]

    @staticmethod
    def _content_text(content: Any) -> str | None:
        if not isinstance(content, list):
            return None
        values = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        texts = [value for value in values if isinstance(value, str)]
        return "\n".join(texts) if texts else None

    def identity(self) -> Identity:
        agent_id, harness, archived = parse_self(self.call_tool("traycer_get_self", {}))
        parent_id = parse_parent_id(self.call_tool("traycer_list_agents", {"scope": "user"}))
        return Identity(agent_id, harness, parent_id, archived)

    def registry(self) -> RegistryView:
        return parse_agent_registry(self.call_tool("traycer_list_agents", {"scope": "user"}))

    def profile(self, harness: str) -> str:
        payload = self.call_tool("traycer_list_provider_profiles", {"harnessId": harness})
        return choose_profile(payload)

    def quota(self, harness: str, profile: str) -> QuotaStatus:
        payload = self.call_tool(
            "traycer_get_provider_profile_rate_limits",
            {"harnessId": harness, "profile": profile},
        )
        limits = payload.get("rateLimits") if isinstance(payload, dict) else None
        return classify_quota(limits)

    def send(self, target_id: str, message: str) -> None:
        self.call_tool(
            "traycer_send_message",
            {"toAgentId": validate_uuid(target_id), "message": message, "expectReply": False},
        )


def credential_from_values(token: Any, endpoint: Any, pid: Any = None) -> Credential:
    if not isinstance(token, str):
        raise SupervisorError("process has no Traycer A2A credential")
    resolved_endpoint = endpoint if isinstance(endpoint, str) and endpoint else DEFAULT_ENDPOINT
    credential = Credential(token=token, endpoint=validate_endpoint(resolved_endpoint))
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1:
        credential.pids.add(pid)
    return credential


def scan_provider_processes() -> dict[str, Credential]:
    try:
        # The absolute macOS system process tool is invoked with fixed arguments.
        result = subprocess.run(  # nosec B603
            ["/bin/ps", "eww", "-axo", "pid=,command="],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SupervisorError(f"cannot scan Traycer provider processes: {error}") from error
    if result.returncode != 0:
        raise SupervisorError("cannot scan Traycer provider processes")

    discovered: dict[str, Credential] = {}
    token_pattern = re.compile(r"(?:^|\s)TRAYCER_A2A_MCP_TOKEN=([^\s]+)")
    endpoint_pattern = re.compile(
        r"(?:TRAYCER_A2A_MCP_URL=|mcp_servers\.traycer_a2a\.url=)"
        r"(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))"
    )
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        token_match = token_pattern.search(parts[1])
        if not token_match:
            continue
        endpoint_match = endpoint_pattern.search(parts[1])
        endpoint = (
            next(
                (value for value in endpoint_match.groups() if value),
                DEFAULT_ENDPOINT,
            )
            if endpoint_match
            else DEFAULT_ENDPOINT
        )
        try:
            credential = credential_from_values(token_match.group(1), endpoint, pid)
        except SupervisorError:
            continue
        existing = discovered.get(credential.identifier)
        if existing:
            existing.pids.add(pid)
        else:
            discovered[credential.identifier] = credential
    return discovered


def normalize_harness(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"agy", "google-antigravity"}:
        return "antigravity"
    return normalized


def group_key(harness: str, profile: str) -> str:
    return json.dumps([harness, profile], separators=(",", ":"))


def decode_group_key(value: str) -> tuple[str, str]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise SupervisorError("invalid persisted group key") from error
    if not isinstance(decoded, list) or len(decoded) != 2 or not all(isinstance(item, str) for item in decoded):
        raise SupervisorError("invalid persisted group key")
    return decoded[0], decoded[1]


def merge_registry_views(views: Iterable[RegistryView]) -> tuple[dict[str, RegistryAgent], set[str]]:
    merged: dict[str, RegistryAgent] = {}
    authoritative_parent_ids: set[str] = set()
    for view in views:
        authoritative_parent_ids.update(view.authoritative_parent_ids)
        for agent in view.agents:
            existing = merged.get(agent.agent_id)
            if existing is None:
                merged[agent.agent_id] = agent
                continue
            if (existing.surface, existing.harness) != (agent.surface, agent.harness):
                raise SupervisorError("Traycer agent registry views disagree on an agent harness")
            parent_ids = {value for value in (existing.parent_id, agent.parent_id) if value is not None}
            if len(parent_ids) > 1:
                raise SupervisorError("Traycer agent registry views disagree on an agent parent")
            merged[agent.agent_id] = RegistryAgent(
                agent_id=agent.agent_id,
                surface=agent.surface,
                harness=agent.harness,
                parent_id=next(iter(parent_ids), None),
                archived=existing.archived or agent.archived,
                messageable=(not (existing.archived or agent.archived) and (existing.messageable or agent.messageable)),
            )
    return merged, authoritative_parent_ids


class Supervisor:
    def __init__(
        self,
        *,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        scan_seconds: float = DEFAULT_SCAN_SECONDS,
        missing_grace_seconds: float = DEFAULT_MISSING_GRACE_SECONDS,
        client_factory: Callable[[Credential], Any] | None = None,
        process_scanner: Callable[[], dict[str, Credential]] = scan_provider_processes,
        quota_probe: Callable[[str, str, Credential | None], QuotaStatus] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.poll_seconds = poll_seconds
        self.scan_seconds = scan_seconds
        self.missing_grace_seconds = missing_grace_seconds
        self.client_factory = client_factory or (lambda credential: A2AClient(credential.endpoint, credential.token))
        self.process_scanner = process_scanner
        self.quota_probe = quota_probe or self._probe_quota
        self.clock = clock
        self.sessions: dict[str, Session] = {}
        self.credentials: dict[str, Credential] = {}
        self.groups: dict[str, GroupState] = {}
        self.stop_requested = threading.Event()
        self.state_lock = threading.RLock()
        self._load_state()

    def log(self, message: str) -> None:
        print(f"{utc_now()} {message}", file=sys.stderr, flush=True)

    def _load_state(self) -> None:
        path = state_path()
        try:
            with path.open(encoding="utf-8") as source:
                payload = json.load(source)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as error:
            self.log(f"ignoring unreadable state: {error}")
            return
        if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
            self.log("ignoring unsupported state version")
            return
        for agent_id, raw in payload.get("sessions", {}).items():
            if not isinstance(raw, dict):
                continue
            try:
                harness = raw["harness"]
                profile = raw["profile"]
                surface = raw.get("surface", "gui")
                if not all(isinstance(value, str) and value for value in (harness, profile, surface)):
                    raise TypeError("invalid persisted session identity")
                session = Session(
                    agent_id=validate_uuid(agent_id),
                    parent_id=validate_uuid(raw["parent_id"]) if raw.get("parent_id") else None,
                    harness=harness,
                    profile=profile,
                    surface=surface,
                    registry_open=raw.get("registry_open") is not False,
                    messageable=raw.get("messageable") is not False,
                    parent_messageable=raw.get("parent_messageable") is not False if raw.get("parent_id") else False,
                    status=raw.get("status", "open"),
                    wake_sent=raw.get("wake_sent") is True,
                    parent_sent=raw.get("parent_sent") is True,
                    last_seen=float(raw.get("last_seen", 0)),
                )
            except (KeyError, TypeError, ValueError, SupervisorError):
                continue
            if session.status in {"open", "candidate"}:
                self.sessions[session.agent_id] = session
        for key, raw in payload.get("groups", {}).items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                continue
            try:
                decode_group_key(key)
                self.groups[key] = GroupState(
                    state=raw.get("state", "unknown"),
                    reason=raw.get("reason", "restored state"),
                    resets_at=raw.get("resets_at"),
                    last_polled=float(raw.get("last_polled", 0)),
                    next_poll_at=float(raw.get("next_poll_at", 0)),
                )
            except (TypeError, ValueError, SupervisorError):
                continue

    def persist(self) -> None:
        ensure_private_directory(state_root())
        payload = {
            "version": STATE_VERSION,
            "updated_at": utc_now(),
            "sessions": {agent_id: session.persisted() for agent_id, session in self.sessions.items()},
            "groups": {key: asdict(group) for key, group in self.groups.items()},
        }
        atomic_write(state_path(), (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))

    def _call_with_any_transport(self, operation: Callable[[Credential], Any], label: str) -> Any:
        attempted = 0
        for credential_id, credential in list(self.credentials.items()):
            attempted += 1
            try:
                return operation(credential)
            except TransportRejectedError:
                self.credentials.pop(credential_id, None)
                self.log("discarded a rejected A2A transport")
            except SupervisorError:
                self.log(f"{label} failed through one A2A transport")
        if attempted:
            raise SupervisorError(f"{label} failed through every cached A2A transport")
        raise SupervisorError(f"{label} requires a cached A2A transport")

    def _refresh_transports(self, discovered: dict[str, Credential]) -> None:
        now = self.clock()
        for credential in self.credentials.values():
            credential.pids.clear()
        for credential_id, candidate in discovered.items():
            candidate.last_seen = now
            existing = self.credentials.get(credential_id)
            if existing is None:
                self.credentials[credential_id] = candidate
                continue
            existing.endpoint = candidate.endpoint
            existing.pids = set(candidate.pids)
            existing.last_seen = now

    def _registry_fresh(self, session: Session, now: float | None = None) -> bool:
        observed_at = self.clock() if now is None else now
        return session.registry_open and observed_at <= session.last_seen + self.missing_grace_seconds

    def _read_registry_views(self) -> list[RegistryView]:
        views: list[RegistryView] = []
        for credential_id, credential in list(self.credentials.items()):
            try:
                views.append(self.client_factory(credential).registry())
            except TransportRejectedError:
                self.credentials.pop(credential_id, None)
                self.log("discarded a rejected A2A transport")
            except SupervisorError:
                self.log("agent registry refresh failed through one A2A transport")
        return views

    def _profile_for_registry_agent(
        self,
        agent: RegistryAgent,
        resolved_profiles: dict[str, str],
    ) -> str | None:
        harness = normalize_harness(agent.harness)
        if harness in {"cursor", "antigravity"}:
            return "ambient"
        if harness in resolved_profiles:
            return resolved_profiles[harness]
        try:
            profile = self._call_with_any_transport(
                lambda credential: self.client_factory(credential).profile(harness),
                f"provider profile lookup for {harness}",
            )
        except SupervisorError as error:
            existing = self.sessions.get(agent.agent_id)
            if existing and existing.harness == harness:
                return existing.profile
            self.log(f"cannot register agent {agent.agent_id}: {error}")
            return None
        resolved_profiles[harness] = profile
        return profile

    def _reconcile_registry_views(self, views: list[RegistryView]) -> None:
        agents, authoritative_parent_ids = merge_registry_views(views)
        now = self.clock()
        resolved_profiles: dict[str, str] = {}
        open_ids = {agent_id for agent_id, agent in agents.items() if agent.open}

        for agent_id, agent in sorted(agents.items()):
            if agent.archived:
                continue
            harness = normalize_harness(agent.harness)
            profile = self._profile_for_registry_agent(agent, resolved_profiles)
            if profile is None:
                continue
            parent = agents.get(agent.parent_id) if agent.parent_id else None
            parent_id = agent.parent_id if parent and not parent.archived else None
            parent_messageable = bool(parent and parent.messageable and not parent.archived)
            session = self.sessions.get(agent_id)
            if session is None:
                session = Session(
                    agent_id=agent_id,
                    parent_id=parent_id,
                    harness=harness,
                    profile=profile,
                    surface=agent.surface,
                    registry_open=True,
                    messageable=agent.messageable,
                    parent_messageable=parent_messageable,
                    last_seen=now,
                )
                self.sessions[agent_id] = session
                self.log(f"registered agent {agent_id} on {harness}/{profile}")
            else:
                session.parent_id = parent_id
                session.harness = harness
                session.profile = profile
                session.surface = agent.surface
                session.registry_open = True
                session.messageable = agent.messageable
                session.parent_messageable = parent_messageable
                session.last_seen = now
            group = self.groups.get(group_key(harness, profile))
            if group and group.state == "blocked" and session.status != "candidate":
                session.status = "candidate"
                session.wake_sent = False
                session.parent_sent = False

        for agent_id, session in list(self.sessions.items()):
            explicitly_archived = agent_id in agents and agents[agent_id].archived
            authoritatively_absent = agent_id not in open_ids and session.parent_id in authoritative_parent_ids
            if explicitly_archived:
                self.sessions.pop(agent_id, None)
                self.log(f"removed closed agent {agent_id}")
            elif authoritatively_absent:
                session.registry_open = False
                session.messageable = False
                session.parent_messageable = False
                if now - session.last_seen > self.missing_grace_seconds:
                    self.sessions.pop(agent_id, None)
                    self.log(f"removed closed agent {agent_id}")

        active_group_keys = {
            group_key(session.harness, session.profile)
            for session in self.sessions.values()
            if self._registry_fresh(session, now)
        }
        self.groups = {key: group for key, group in self.groups.items() if key in active_group_keys}
        for key in active_group_keys:
            group = self.groups.get(key)
            if group is None:
                continue
            if group.state == "unknown":
                group.next_poll_at = 0.0
            elif group.state == "available":
                harness, profile = decode_group_key(key)
                self.deliver_recoveries(harness, profile)

    def reconcile(self) -> None:
        with self.state_lock:
            self._reconcile()

    def _reconcile(self) -> None:
        try:
            discovered = self.process_scanner()
        except SupervisorError as error:
            self.log(f"A2A transport process scan failed: {error}")
        else:
            self._refresh_transports(discovered)

        views = self._read_registry_views()
        if views:
            try:
                self._reconcile_registry_views(views)
            except SupervisorError as error:
                self.log(f"agent registry reconciliation failed: {error}")
        self.persist()

    def _probe_quota(self, harness: str, profile: str, credential: Credential | None) -> QuotaStatus:
        if harness == "cursor":
            return probe_cursor_quota()
        if harness == "antigravity":
            return probe_antigravity_quota()
        if credential is None:
            raise SupervisorError("provider quota lookup requires an A2A transport")
        return self.client_factory(credential).quota(harness, profile)

    def _next_poll_at(self, quota: QuotaStatus, now: float) -> float:
        regular = now + self.poll_seconds
        if quota.state != "blocked" or quota.resets_at is None:
            return regular
        after_reset = quota.resets_at / 1_000 + DEFAULT_RESET_GRACE_SECONDS
        return min(regular, after_reset) if after_reset > now else regular

    def poll_group(self, harness: str, profile: str) -> QuotaStatus:
        now = self.clock()
        key = group_key(harness, profile)
        group = self.groups.setdefault(key, GroupState())
        sessions = [
            session
            for session in self.sessions.values()
            if session.harness == harness and session.profile == profile and self._registry_fresh(session, now)
        ]
        if not sessions:
            return QuotaStatus("unknown", "no registry-open sessions")
        try:
            if harness in {"cursor", "antigravity"}:
                quota = self.quota_probe(harness, profile, None)
            else:
                quota = self._call_with_any_transport(
                    lambda credential: self.quota_probe(harness, profile, credential),
                    f"quota lookup for {harness}/{profile}",
                )
        except SupervisorError as error:
            quota = QuotaStatus("unknown", str(error))
        previous = group.state
        group.state = quota.state
        group.reason = quota.reason
        group.resets_at = quota.resets_at
        group.last_polled = now
        group.next_poll_at = self._next_poll_at(quota, now)
        if quota.state == "blocked":
            for session in sessions:
                if session.status != "candidate":
                    session.status = "candidate"
                    session.wake_sent = False
                    session.parent_sent = False
                    self.log(f"agent {session.agent_id} is eligible for quota recovery")
        if quota.state == "available":
            self.deliver_recoveries(harness, profile)
        if quota.state != previous:
            self.log(f"quota {harness}/{profile}: {previous} -> {quota.state}: {quota.reason}")
        return quota

    def poll_groups(self, *, force: bool = True) -> None:
        with self.state_lock:
            self._poll_groups(force=force)

    def _poll_groups(self, *, force: bool) -> None:
        now = self.clock()
        groups = {
            (session.harness, session.profile)
            for session in self.sessions.values()
            if self._registry_fresh(session, now)
        }
        for harness, profile in sorted(groups):
            group = self.groups.setdefault(group_key(harness, profile), GroupState())
            if not force and group.next_poll_at > now:
                continue
            self.poll_group(harness, profile)
        self.persist()

    def _send_message(self, target_id: str, message: str, label: str) -> None:
        def send(credential: Credential) -> None:
            self.client_factory(credential).send(target_id, message)

        self._call_with_any_transport(send, label)

    def deliver_recoveries(self, harness: str, profile: str) -> None:
        now = self.clock()
        for session in list(self.sessions.values()):
            if (
                session.harness != harness
                or session.profile != profile
                or session.status != "candidate"
                or not self._registry_fresh(session, now)
            ):
                continue
            if not session.messageable:
                session.status = "open"
                session.wake_sent = False
                session.parent_sent = False
                continue
            if not session.wake_sent:
                target_id = session.agent_id
                try:
                    self._send_message(target_id, RESUME_MESSAGE, f"wake delivery for agent {target_id}")
                except SupervisorError as error:
                    self.log(f"wake delivery failed for agent {target_id}: {error}")
                    continue
                session.wake_sent = True
                self.log(f"woke recovered agent {target_id}")
            parent_id = session.parent_id
            if parent_id and session.parent_messageable and not session.parent_sent:
                parent_message = (
                    f"Automated quota recovery: agent {session.agent_id} is no longer quota-blocked "
                    "and has been asked to resume its interrupted work."
                )
                try:
                    self._send_message(
                        parent_id,
                        parent_message,
                        f"parent notification for agent {session.agent_id}",
                    )
                except SupervisorError as error:
                    self.log(f"parent notification failed for agent {session.agent_id}: {error}")
                    continue
                session.parent_sent = True
            if session.wake_sent and (
                session.parent_id is None or not session.parent_messageable or session.parent_sent
            ):
                session.status = "open"
                session.wake_sent = False
                session.parent_sent = False

    def status_payload(self) -> dict[str, Any]:
        with self.state_lock:
            return self._status_payload()

    def _status_payload(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "service": SERVICE_LABEL,
            "transport": {
                "cached": len(self.credentials),
                "source_processes": len({pid for credential in self.credentials.values() for pid in credential.pids}),
            },
            "sessions": [
                {
                    "agent_id": session.agent_id,
                    "parent_id": session.parent_id,
                    "surface": session.surface,
                    "harness": session.harness,
                    "profile": session.profile,
                    "status": session.status,
                    "registry_open": session.registry_open,
                    "registry_fresh": self._registry_fresh(session, now),
                    "registry_last_seen": session.last_seen,
                    "messageable": session.messageable,
                    "parent_messageable": session.parent_messageable,
                }
                for session in sorted(self.sessions.values(), key=lambda item: item.agent_id)
            ],
            "groups": [
                {
                    "harness": decode_group_key(key)[0],
                    "profile": decode_group_key(key)[1],
                    **asdict(group),
                }
                for key, group in sorted(self.groups.items())
            ],
        }

    def run(self) -> None:
        next_scan = 0.0
        while not self.stop_requested.is_set():
            now = self.clock()
            if now >= next_scan:
                try:
                    self.reconcile()
                except SupervisorError as error:
                    self.log(str(error))
                next_scan = now + self.scan_seconds
            self.poll_groups(force=False)
            self.stop_requested.wait(1.0)


class SupervisorSocketServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 128

    def __init__(self, path: str, supervisor: Supervisor) -> None:
        self.supervisor = supervisor
        super().__init__(path, SupervisorRequestHandler)


class SupervisorRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_SOCKET_REQUEST_BYTES + 1)
        if len(raw) > MAX_SOCKET_REQUEST_BYTES:
            self._reply({"ok": False, "error": "request too large"})
            return
        try:
            request = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply({"ok": False, "error": "invalid JSON"})
            return
        if not isinstance(request, dict):
            self._reply({"ok": False, "error": "request must be an object"})
            return
        if request.get("action") == "status":
            server = self.server
            if not isinstance(server, SupervisorSocketServer):
                self._reply({"ok": False, "error": "invalid server state"})
                return
            self._reply({"ok": True, **server.supervisor.status_payload()})
            return
        self._reply({"ok": False, "error": "unsupported action"})

    def _reply(self, payload: dict[str, Any]) -> None:
        self.wfile.write((json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))


def acquire_singleton_lock() -> Any:
    ensure_private_directory(state_root())
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path(), flags, 0o600)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise SupervisorError("supervisor lock is not a regular file")
    os.fchmod(descriptor, 0o600)
    lock = os.fdopen(descriptor, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise SupervisorError("the quota supervisor is already running") from None
    return lock


def serve(arguments: argparse.Namespace) -> int:
    lock = acquire_singleton_lock()
    path = socket_path()
    path.unlink(missing_ok=True)
    supervisor = Supervisor(
        poll_seconds=arguments.poll_seconds,
        scan_seconds=arguments.scan_seconds,
        missing_grace_seconds=arguments.missing_grace_seconds,
    )
    server = SupervisorSocketServer(str(path), supervisor)
    os.chmod(path, 0o600)

    def stop(_signum: int, _frame: Any) -> None:
        supervisor.stop_requested.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server_thread = threading.Thread(target=server.serve_forever, name="quota-supervisor-socket", daemon=True)
    server_thread.start()
    supervisor.log(f"started {SERVICE_LABEL}")
    try:
        supervisor.run()
    finally:
        server.shutdown()
        server.server_close()
        path.unlink(missing_ok=True)
        lock.close()
    return 0


def send_socket_request(payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path()))
        client.sendall(data)
        response = client.makefile("rb").readline(MAX_SOCKET_REQUEST_BYTES + 1)
    finally:
        client.close()
    if len(response) > MAX_SOCKET_REQUEST_BYTES:
        raise SupervisorError("supervisor response is too large")
    try:
        parsed = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SupervisorError("supervisor returned malformed JSON") from error
    if not isinstance(parsed, dict) or parsed.get("ok") is not True:
        error_message = parsed.get("error") if isinstance(parsed, dict) else "unknown error"
        raise SupervisorError(f"supervisor rejected request: {error_message}")
    return parsed


def command_status(arguments: argparse.Namespace) -> int:
    try:
        payload = send_socket_request({"action": "status"})
    except (OSError, SupervisorError) as error:
        raise SupervisorError(f"quota supervisor is not reachable: {error}") from error
    if arguments.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        sessions = payload.get("sessions", [])
        groups = payload.get("groups", [])
        transport = payload.get("transport", {})
        cached = transport.get("cached", 0) if isinstance(transport, dict) else 0
        source_processes = transport.get("source_processes", 0) if isinstance(transport, dict) else 0
        print(
            f"{SERVICE_LABEL}: running; {len(sessions)} session(s), {len(groups)} quota group(s); "
            f"{cached} cached transport(s), {source_processes} source process(es)"
        )
        for session in sessions:
            registry_state = "registry-open" if session.get("registry_open") is True else "registry-missing"
            freshness_state = "registry-fresh" if session.get("registry_fresh") is True else "registry-stale"
            message_state = "messageable" if session.get("messageable") is True else "not-messageable"
            print(
                f"  {session['agent_id']} {session['surface']}/{session['harness']} "
                f"{session['profile']} {session['status']} {registry_state} {freshness_state} {message_state}"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help=argparse.SUPPRESS)
    run_parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    run_parser.add_argument("--scan-seconds", type=float, default=DEFAULT_SCAN_SECONDS)
    run_parser.add_argument("--missing-grace-seconds", type=float, default=DEFAULT_MISSING_GRACE_SECONDS)
    run_parser.set_defaults(handler=serve)

    status_parser = subparsers.add_parser("status", help="show sanitized supervisor status")
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=command_status)
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        return arguments.handler(arguments)
    except (OSError, subprocess.SubprocessError, SupervisorError) as error:
        print(f"traycer-quota-supervisor: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
