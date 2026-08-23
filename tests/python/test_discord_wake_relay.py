from __future__ import annotations

import importlib.util
import json
import os
import stat

# Tests invoke only temporary fake executables and the relay under test.
import subprocess  # nosec B404
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIRECTORY = REPOSITORY_ROOT / "skills/discord-agent-coordination/scripts"
RELAY_PATH = SCRIPT_DIRECTORY / "discord_wake_relay.py"
EPIC_ID = "759f71f3-248a-450d-9f23-9eee1750f25c"
AGENT_ID = "3e24b2e8-285b-4171-a780-40c5724eec56"
ADDRESS = f"epic/{EPIC_ID}/role/reviewer"
THREAD_ID = "123456789012345678"
BOT_ID = "223456789012345678"
INITIAL_CURSOR = "323456789012345670"
MESSAGE_IDS = [f"32345678901234567{index}" for index in range(1, 10)]
RAW_BODY_SENTINEL = "RAW-HANDOFF-BODY-MUST-NOT-LEAK"


def load_module(path: Path, name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load module from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT_DIRECTORY))


RELAY = load_module(RELAY_PATH, "discord_wake_relay_test_target")
COORDINATION = load_module(SCRIPT_DIRECTORY / "discord_coordination.py", "discord_coordination_for_wake_tests")


def write_executable(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o700)
    return path


def create_fake_traycer(directory: Path) -> Path:
    return write_executable(
        directory / "fake-traycer",
        r"""
        #!/usr/bin/env python3
        import json
        import os
        import sys
        import time
        from pathlib import Path

        arguments = sys.argv[1:]
        epic_id = os.environ.get("TRAYCER_EPIC_ID")
        agent_id = os.environ.get("TRAYCER_AGENT_ID")
        mode = os.environ.get("FAKE_TRAYCER_MODE", "ok")
        if arguments[:2] == ["agent", "list"]:
            if mode == "list-oversized":
                sys.stdout.write("x" * (600 * 1024))
                sys.stdout.flush()
                time.sleep(10)
                raise SystemExit(0)
            if mode == "list-fail":
                raise SystemExit(1)
            eligible = mode != "ineligible"
            data = {
                "caller": {"agentId": agent_id, "canSendMessages": True},
                "scope": "user",
                "agents": [
                    {
                        "id": agent_id,
                        "isLocal": eligible,
                        "isSelf": eligible,
                        "capabilities": {"sendMessage": eligible},
                    }
                ],
            }
            print(json.dumps({"type": "result", "status": "ok", "data": data}))
            raise SystemExit(0)
        if arguments[:2] == ["agent", "send"]:
            log_path = os.environ.get("FAKE_TRAYCER_LOG")
            if log_path:
                with Path(log_path).open("a", encoding="utf-8") as handle:
                    json.dump({"args": arguments, "epic_id": epic_id, "agent_id": agent_id}, handle)
                    handle.write("\n")
            fail_path = os.environ.get("FAKE_TRAYCER_FAIL_FILE")
            if mode == "send-fail" or (fail_path and Path(fail_path).exists()):
                raise SystemExit(1)
            print(json.dumps({"type": "result", "status": "ok", "data": {"delivered": True}}))
            raise SystemExit(0)
        raise SystemExit(64)
        """,
    )


def create_fake_mcp(directory: Path) -> Path:
    return write_executable(
        directory / "fake-mcp",
        r"""
        #!/usr/bin/env python3
        import json
        import os
        import sys
        from pathlib import Path

        fixture_path = Path(os.environ["FAKE_MCP_FIXTURE"])
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        mode = fixture.get("mode", "ok")
        log_path = os.environ.get("FAKE_MCP_LOG")

        def emit(request_id, result):
            if mode == "wrong-id":
                response_id = request_id + 1
            elif mode == "float-id":
                response_id = float(request_id)
            else:
                response_id = request_id
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": response_id, "result": result}) + "\n")
            sys.stdout.flush()

        for line in sys.stdin:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            request_id = request["id"]
            if method == "initialize":
                emit(request_id, {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {}})
            elif method == "tools/list":
                emit(
                    request_id,
                    {
                        "tools": [
                            {"name": "mcp_tools_search", "inputSchema": {"type": "object"}},
                            {"name": "mcp_tools_read", "inputSchema": {"type": "object"}},
                        ]
                    },
                )
            elif method == "tools/call":
                params = request["params"]
                if log_path:
                    with Path(log_path).open("a", encoding="utf-8") as handle:
                        json.dump(params, handle)
                        handle.write("\n")
                if params["name"] == "mcp_tools_search":
                    emit(
                        request_id,
                        {
                            "content": [],
                            "structuredContent": {
                                "matches": [
                                    {
                                        "name": "messages_read",
                                        "dispatcher": "mcp_tools_read",
                                        "inputSchema": {
                                            "type": "object",
                                            "properties": {
                                                "channel_id": {"type": "string"},
                                                "limit": {"type": "integer"},
                                                "after": {"type": "string"},
                                            },
                                            "required": ["channel_id"],
                                        },
                                    }
                                ]
                            },
                        },
                    )
                else:
                    arguments = params["arguments"]
                    channel_id = arguments["args"]["channel_id"]
                    messages = fixture.get("messages", [])
                    if mode == "oversized":
                        sys.stdout.write("{" + "x" * (1024 * 1024 + 1) + "\n")
                        sys.stdout.flush()
                        continue
                    emit(
                        request_id,
                        {
                            "content": [],
                            "structuredContent": {
                                "messages": messages,
                                "count": len(messages),
                                "channel_id": fixture.get("channel_id", channel_id),
                                "oldest_id": messages[-1]["id"] if messages else None,
                                "newest_id": messages[0]["id"] if messages else None,
                            },
                        },
                    )
        """,
    )


def timestamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


def envelope(
    *,
    kind: str = "handoff",
    target: str = ADDRESS,
    needs: str = "Review the waiting handoff",
    notion_sync: str = "current",
    task: str = "TASK-73 https://notion.so/example",
    raw_body: str = RAW_BODY_SENTINEL,
) -> str:
    return COORDINATION.render_envelope(
        message_id="123e4567-e89b-42d3-a456-426614174000",
        kind=kind,
        sender=f"epic/{EPIC_ID}/role/primary/agent-sender",
        target=target,
        task=task,
        in_reply_to="none",
        needs=needs,
        body=f"notion-sync: {notion_sync}\n{raw_body}",
    )


def discord_message(
    message_id: str,
    now: int,
    *,
    content: str | None = None,
    author_id: str = BOT_ID,
    created_at: str | None = None,
) -> dict[str, object]:
    return {
        "id": message_id,
        "author_id": author_id,
        "author_name": "coordination-bot",
        "content": envelope() if content is None else content,
        "timestamp": created_at or timestamp(now),
        "edited": False,
    }


class RelayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.state_directory = self.root / "state"
        self.store = RELAY.StateStore(self.state_directory)
        self.traycer_path = create_fake_traycer(self.root)
        self.mcp_path = create_fake_mcp(self.root)
        self.fixture_path = self.root / "mcp-fixture.json"
        self.traycer_log = self.root / "traycer.jsonl"
        self.mcp_log = self.root / "mcp.jsonl"
        self.fail_file = self.root / "send-fails"
        self.environment = {
            "FAKE_MCP_FIXTURE": str(self.fixture_path),
            "FAKE_MCP_LOG": str(self.mcp_log),
            "FAKE_TRAYCER_LOG": str(self.traycer_log),
            "FAKE_TRAYCER_FAIL_FILE": str(self.fail_file),
        }
        self.fixture_path.write_text('{"messages": []}\n', encoding="utf-8")
        self.traycer = RELAY.TraycerClient(self.traycer_path, 2)

    def register(self, *, cursor: str = INITIAL_CURSOR) -> Any:
        return RELAY.register_role(
            self.store,
            self.traycer,
            epic_id=EPIC_ID,
            agent_id=AGENT_ID,
            address=ADDRESS,
            thread_id=THREAD_ID,
            bot_id=BOT_ID,
            cursor=cursor,
        )

    def write_messages(self, messages: list[dict[str, object]], *, mode: str = "ok") -> None:
        self.fixture_path.write_text(json.dumps({"messages": messages, "mode": mode}) + "\n", encoding="utf-8")

    def run_cycle(self, now: int, *, cooldown: float = 60, max_age: float = 900) -> dict[str, int]:
        return RELAY.run_cycle(
            self.store,
            self.traycer,
            self.mcp_path,
            timeout=2,
            max_age=max_age,
            cooldown=cooldown,
            now=now,
        )

    def traycer_calls(self) -> list[dict[str, Any]]:
        if not self.traycer_log.exists():
            return []
        return [json.loads(line) for line in self.traycer_log.read_text(encoding="utf-8").splitlines()]


class SecureStateTests(RelayTestCase):
    def test_state_requires_an_absolute_path_and_enforces_registration_limit(self) -> None:
        with self.assertRaisesRegex(RELAY.RelayConfigurationError, "must be absolute"):
            RELAY.StateStore(Path("relative-state"))

        registrations = {}
        for index in range(RELAY.MAX_REGISTRATIONS + 1):
            address = f"epic/{EPIC_ID}/role/role-{index}"
            registrations[address] = RELAY.Registration(
                address=address,
                epic_id=EPIC_ID,
                agent_id=AGENT_ID,
                thread_id=THREAD_ID,
                bot_id=BOT_ID,
                cursor=INITIAL_CURSOR,
            )
        with self.assertRaisesRegex(RELAY.RelayConfigurationError, "exceeds configured limits"):
            self.store.save(RELAY.RelayState(registrations=registrations, audit=[]))
        self.assertFalse(self.store.path.exists())

    def test_state_and_lock_use_restrictive_permissions_and_atomic_files(self) -> None:
        with mock.patch.dict(os.environ, self.environment, clear=False):
            self.register()
        first_inode = self.store.path.stat().st_ino
        state = self.store.load()
        state.registrations[ADDRESS].cursor = MESSAGE_IDS[0]
        self.store.save(state)
        with self.store.instance_lock(), self.store.state_lock(1):
            pass

        self.assertEqual(stat.S_IMODE(self.state_directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.instance_lock_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.store.state_lock_path.stat().st_mode), 0o600)
        self.assertNotEqual(first_inode, self.store.path.stat().st_ino)
        self.assertEqual(list(self.state_directory.glob(".state-*")), [])

    def test_state_rejects_broad_directory_file_and_symlink_without_repair(self) -> None:
        broad_directory = self.root / "broad"
        broad_directory.mkdir(mode=0o755)
        with self.assertRaisesRegex(RELAY.RelayConfigurationError, "broader than 0700"):
            RELAY.StateStore(broad_directory).load()
        self.assertEqual(stat.S_IMODE(broad_directory.stat().st_mode), 0o755)

        secure_directory = self.root / "secure"
        secure_directory.mkdir(mode=0o700)
        state_path = secure_directory / "state.json"
        state_path.write_text("{}\n", encoding="utf-8")
        state_path.chmod(0o644)
        with self.assertRaisesRegex(RELAY.RelayConfigurationError, "broader than 0600"):
            RELAY.StateStore(secure_directory).load()
        self.assertEqual(stat.S_IMODE(state_path.stat().st_mode), 0o644)

        victim = self.root / "victim"
        victim.write_text("unchanged", encoding="utf-8")
        symlink_directory = self.root / "symlink-state"
        symlink_directory.mkdir(mode=0o700)
        (symlink_directory / "state.json").symlink_to(victim)
        with self.assertRaisesRegex(RELAY.RelayConfigurationError, "symlink"):
            RELAY.StateStore(symlink_directory).load()
        self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")

        for label in ("instance", "state"):
            with self.subTest(lock=label, unsafe="permissions"):
                lock_directory = self.root / f"broad-{label}-lock"
                lock_directory.mkdir(mode=0o700)
                store = RELAY.StateStore(lock_directory)
                lock_path = store.instance_lock_path if label == "instance" else store.state_lock_path
                lock_path.write_text("", encoding="utf-8")
                lock_path.chmod(0o644)
                lock = store.instance_lock() if label == "instance" else store.state_lock(0.1)
                with self.assertRaisesRegex(RELAY.RelayConfigurationError, "broader than 0600"), lock:
                    pass
            with self.subTest(lock=label, unsafe="symlink"):
                lock_directory = self.root / f"symlink-{label}-lock"
                lock_directory.mkdir(mode=0o700)
                store = RELAY.StateStore(lock_directory)
                lock_path = store.instance_lock_path if label == "instance" else store.state_lock_path
                lock_path.symlink_to(victim)
                lock = store.instance_lock() if label == "instance" else store.state_lock(0.1)
                with self.assertRaisesRegex(RELAY.RelayConfigurationError, "symlink"), lock:
                    pass

    def test_instance_and_state_locks_have_distinct_bounded_collision_behavior(self) -> None:
        with (
            self.store.instance_lock(),
            self.assertRaisesRegex(RELAY.RelayUnavailableError, "already running"),
            self.store.instance_lock(),
        ):
            pass

        started_at = time.monotonic()
        with (
            self.store.state_lock(1),
            self.assertRaisesRegex(RELAY.RelayUnavailableError, "state is busy"),
            self.store.state_lock(0.1),
        ):
            pass
        elapsed = time.monotonic() - started_at
        self.assertGreaterEqual(elapsed, 0.09)
        self.assertLess(elapsed, 0.5)
        with self.assertRaisesRegex(RELAY.RelayConfigurationError, "between 0.1"):
            self.store.state_lock(0)


class RegistrationTests(RelayTestCase):
    def test_registration_requires_an_eligible_local_self_agent(self) -> None:
        with (
            mock.patch.dict(os.environ, {**self.environment, "FAKE_TRAYCER_MODE": "ineligible"}, clear=False),
            self.assertRaisesRegex(RELAY.RelayConfigurationError, "eligible local self"),
        ):
            self.register()
        self.assertFalse(self.store.path.exists())

        started_at = time.monotonic()
        with (
            mock.patch.dict(os.environ, {**self.environment, "FAKE_TRAYCER_MODE": "list-oversized"}, clear=False),
            self.assertRaisesRegex(RELAY.RelayUnavailableError, "output exceeded"),
        ):
            self.traycer.validate_self(EPIC_ID, AGENT_ID)
        self.assertLess(time.monotonic() - started_at, self.traycer.timeout)

    def test_registration_is_strict_non_rebinding_and_non_regressing(self) -> None:
        with mock.patch.dict(os.environ, self.environment, clear=False):
            registration = self.register()
            self.assertEqual(registration.cursor, INITIAL_CURSOR)
            with self.assertRaisesRegex(RELAY.RelayConfigurationError, "cannot regress"):
                self.register(cursor="323456789012345669")
            with self.assertRaisesRegex(RELAY.RelayConfigurationError, "cannot be rebound"):
                RELAY.register_role(
                    self.store,
                    self.traycer,
                    epic_id=EPIC_ID,
                    agent_id=AGENT_ID,
                    address=ADDRESS,
                    thread_id="923456789012345678",
                    bot_id=BOT_ID,
                    cursor=INITIAL_CURSOR,
                )

    def test_registration_rejects_address_epic_mismatch_and_short_snowflakes(self) -> None:
        with mock.patch.dict(os.environ, self.environment, clear=False):
            with self.assertRaisesRegex(RELAY.RelayConfigurationError, "does not belong"):
                RELAY.register_role(
                    self.store,
                    self.traycer,
                    epic_id=EPIC_ID,
                    agent_id=AGENT_ID,
                    address="epic/other/role/reviewer",
                    thread_id=THREAD_ID,
                    bot_id=BOT_ID,
                    cursor=INITIAL_CURSOR,
                )
            with self.assertRaisesRegex(RELAY.RelayConfigurationError, "17-20 digit"):
                RELAY.register_role(
                    self.store,
                    self.traycer,
                    epic_id=EPIC_ID,
                    agent_id=AGENT_ID,
                    address=ADDRESS,
                    thread_id="123",
                    bot_id=BOT_ID,
                    cursor=INITIAL_CURSOR,
                )


class RelayDeliveryTests(RelayTestCase):
    def test_live_shaped_valid_author_wakes_and_wrong_author_advances_as_poison(self) -> None:
        now = 1_800_000_000
        valid_message = discord_message(MESSAGE_IDS[0], now)
        self.assertEqual(
            set(valid_message),
            {"id", "author_id", "author_name", "content", "timestamp", "edited"},
        )
        self.write_messages([valid_message])
        with mock.patch.dict(os.environ, self.environment, clear=False):
            self.register()
            summary = self.run_cycle(now, cooldown=0)
            wrong_author = discord_message(MESSAGE_IDS[1], now + 1, author_id="923456789012345678")
            self.write_messages([wrong_author])
            spoofed_summary = self.run_cycle(now + 1, cooldown=0)

        self.assertEqual(summary["wakes"], 1)
        self.assertEqual(summary["eligible"], 1)
        self.assertEqual(spoofed_summary["wakes"], 0)
        self.assertEqual(spoofed_summary["rejected"], 1)
        state = self.store.load()
        self.assertEqual(state.registrations[ADDRESS].cursor, MESSAGE_IDS[1])
        self.assertEqual([record["outcome"] for record in state.audit[-2:]], ["delivered", "author-mismatch"])

        calls = self.traycer_calls()
        self.assertEqual(len(calls), 1)
        arguments = calls[0]["args"]
        self.assertIsInstance(arguments, list)
        prompt = arguments[arguments.index("--message") + 1]
        self.assertIn(ADDRESS, prompt)
        self.assertIn("TASK-73", prompt)
        self.assertIn(MESSAGE_IDS[0], prompt)
        self.assertNotIn(RAW_BODY_SENTINEL, prompt)
        self.assertNotIn("Review the waiting handoff", prompt)
        self.assertNotIn(RAW_BODY_SENTINEL, self.store.path.read_text(encoding="utf-8"))
        self.assertNotIn(RAW_BODY_SENTINEL, self.mcp_log.read_text(encoding="utf-8"))

        mcp_calls = [json.loads(line) for line in self.mcp_log.read_text(encoding="utf-8").splitlines()]
        read_call = next(call for call in mcp_calls if call["name"] == "mcp_tools_read")
        self.assertEqual(
            read_call["arguments"],
            {
                "tool": "messages_read",
                "args": {"channel_id": THREAD_ID, "after": INITIAL_CURSOR, "limit": 100},
            },
        )

    def test_malformed_spoofed_stale_and_ineligible_messages_advance_as_poison(self) -> None:
        now = 1_800_000_000
        messages = [
            discord_message(MESSAGE_IDS[0], now, author_id="923456789012345678"),
            discord_message(MESSAGE_IDS[1], now, content="not an envelope"),
            discord_message(MESSAGE_IDS[2], now, content=envelope(target=f"epic/{EPIC_ID}/role/other")),
            discord_message(MESSAGE_IDS[3], now, content=envelope(kind="status")),
            discord_message(MESSAGE_IDS[4], now, content=envelope(needs="none")),
            discord_message(MESSAGE_IDS[5], now, content=envelope(notion_sync="pending")),
            discord_message(MESSAGE_IDS[6], now, created_at=timestamp(now - 901)),
            discord_message(MESSAGE_IDS[7], now, created_at=timestamp(now + 61)),
            discord_message(
                MESSAGE_IDS[8],
                now,
                content=envelope(raw_body=f"notion-sync: pending\n{RAW_BODY_SENTINEL}"),
            ),
        ]
        self.write_messages(list(reversed(messages)))
        with mock.patch.dict(os.environ, self.environment, clear=False):
            self.register()
            summary = self.run_cycle(now, max_age=900)

        self.assertEqual(summary["wakes"], 0)
        self.assertEqual(summary["rejected"], len(messages))
        self.assertEqual(self.store.load().registrations[ADDRESS].cursor, MESSAGE_IDS[8])
        self.assertEqual(self.traycer_calls(), [])
        outcomes = {record["outcome"] for record in self.store.load().audit}
        self.assertEqual(
            outcomes,
            {
                "author-mismatch",
                "envelope-invalid",
                "target-mismatch",
                "kind-ineligible",
                "needs-none",
                "notion-sync-not-current",
                "stale",
                "future",
            },
        )

    def test_transient_send_failure_advances_only_preceding_poison_then_retries(self) -> None:
        now = 1_800_000_000
        poison = discord_message(MESSAGE_IDS[0], now, content=envelope(kind="status"))
        eligible = discord_message(MESSAGE_IDS[1], now)
        self.write_messages([eligible, poison])
        self.fail_file.touch()
        with mock.patch.dict(os.environ, self.environment, clear=False):
            self.register()
            failed = self.run_cycle(now, cooldown=0)
            self.fail_file.unlink()
            retried = self.run_cycle(now + 1, cooldown=0)

        self.assertEqual(failed["failures"], 1)
        self.assertEqual(retried["wakes"], 1)
        self.assertEqual(self.store.load().registrations[ADDRESS].cursor, MESSAGE_IDS[1])
        self.assertEqual(len(self.traycer_calls()), 2)
        outcomes = [record["outcome"] for record in self.store.load().audit]
        self.assertEqual(outcomes, ["kind-ineligible", "delivered"])

    def test_coalescing_and_cooldown_preserve_new_handoff_until_next_wake(self) -> None:
        now = 1_800_000_000
        first = discord_message(MESSAGE_IDS[0], now, content=envelope(task="TASK-72"))
        second = discord_message(MESSAGE_IDS[1], now, content=envelope(task="TASK-73"))
        self.write_messages([second, first])
        with mock.patch.dict(os.environ, self.environment, clear=False):
            self.register()
            coalesced = self.run_cycle(now, cooldown=60)
            third = discord_message(MESSAGE_IDS[2], now + 30, content=envelope(task="TASK-74"))
            self.write_messages([third])
            cooling_down = self.run_cycle(now + 30, cooldown=60)
            delivered_later = self.run_cycle(now + 61, cooldown=60)

        self.assertEqual(coalesced["eligible"], 2)
        self.assertEqual(coalesced["wakes"], 1)
        self.assertEqual(cooling_down["cooldown"], 1)
        self.assertEqual(cooling_down["wakes"], 0)
        self.assertEqual(delivered_later["wakes"], 1)
        self.assertEqual(self.store.load().registrations[ADDRESS].cursor, MESSAGE_IDS[2])
        calls = self.traycer_calls()
        self.assertEqual(len(calls), 2)
        first_arguments = calls[0]["args"]
        first_prompt = first_arguments[first_arguments.index("--message") + 1]
        self.assertIn("Eligible handoffs coalesced: 2", first_prompt)
        self.assertIn("TASK-73", first_prompt)

    def test_mcp_protocol_failure_and_oversized_response_retain_cursor(self) -> None:
        now = 1_800_000_000
        with mock.patch.dict(os.environ, self.environment, clear=False):
            self.register()
            self.write_messages([discord_message(MESSAGE_IDS[0], now)], mode="wrong-id")
            wrong_id = self.run_cycle(now)
            self.write_messages([discord_message(MESSAGE_IDS[0], now)], mode="float-id")
            float_id = self.run_cycle(now)
            self.write_messages([discord_message(MESSAGE_IDS[0], now)], mode="oversized")
            oversized = self.run_cycle(now)

        self.assertGreaterEqual(wrong_id["failures"], 1)
        self.assertGreaterEqual(float_id["failures"], 1)
        self.assertGreaterEqual(oversized["failures"], 1)
        self.assertEqual(self.store.load().registrations[ADDRESS].cursor, INITIAL_CURSOR)
        self.assertEqual(self.traycer_calls(), [])

    def test_audit_is_metadata_only_and_bounded(self) -> None:
        registration = RELAY.Registration(
            address=ADDRESS,
            epic_id=EPIC_ID,
            agent_id=AGENT_ID,
            thread_id=THREAD_ID,
            bot_id=BOT_ID,
            cursor=INITIAL_CURSOR,
        )
        state = RELAY.RelayState(registrations={ADDRESS: registration}, audit=[])
        for index in range(150):
            message_id = str(400000000000000000 + index)
            RELAY.record_audit(state, registration, message_id, "envelope-invalid", 1_800_000_000)
        self.store.save(state)

        serialized = self.store.path.read_text(encoding="utf-8")
        self.assertEqual(len(self.store.load().audit), 100)
        self.assertNotIn(RAW_BODY_SENTINEL, serialized)
        self.assertNotIn("content", serialized)


class CommandLineTests(RelayTestCase):
    def test_register_and_once_commands_work_with_fake_subprocess_endpoints(self) -> None:
        now = int(datetime.now(tz=UTC).timestamp())
        self.write_messages([discord_message(MESSAGE_IDS[0], now)])
        environment = os.environ.copy()
        environment.update(self.environment)
        base = [
            sys.executable,
            str(RELAY_PATH),
            "--state-dir",
            str(self.state_directory),
            "--traycer",
            str(self.traycer_path),
            "--mcp-wrapper",
            str(self.mcp_path),
        ]
        register_command = [
            *base,
            "register",
            "--epic-id",
            EPIC_ID,
            "--agent-id",
            AGENT_ID,
            "--address",
            ADDRESS,
            "--thread-id",
            THREAD_ID,
            "--bot-id",
            BOT_ID,
            "--cursor",
            INITIAL_CURSOR,
        ]
        once_command = [*base, "once", "--cooldown", "0", "--timeout", "2"]
        # The interpreter and all invoked paths are fixed or temporary test paths.
        with mock.patch.dict(os.environ, self.environment, clear=False), self.store.instance_lock():
            registered = subprocess.run(  # nosec B603
                register_command,
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            overlapping_once = subprocess.run(  # nosec B603
                once_command,
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            daemon_cycle = self.run_cycle(now, cooldown=0)
        once = subprocess.run(  # nosec B603
            once_command,
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(registered.returncode, 0, registered.stderr)
        self.assertEqual(overlapping_once.returncode, 2)
        self.assertIn("another Discord wake relay instance is already running", overlapping_once.stderr)
        self.assertEqual(daemon_cycle["registrations"], 1)
        self.assertEqual(daemon_cycle["polled"], 1)
        self.assertEqual(daemon_cycle["wakes"], 1)
        self.assertEqual(once.returncode, 0, once.stderr)
        self.assertEqual(json.loads(once.stdout)["wakes"], 0)


if __name__ == "__main__":
    unittest.main()
