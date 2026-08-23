from __future__ import annotations

import importlib.util
import json
import stat
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPOSITORY_ROOT / "skills/discord-agent-coordination/scripts/discord_coordination.py"
SKILL_DIRECTORY = REPOSITORY_ROOT / "skills/discord-agent-coordination"
SKILL_PATH = SKILL_DIRECTORY / "SKILL.md"
OPENAI_PATH = SKILL_DIRECTORY / "agents/openai.yaml"
PROTOCOL_PATH = SKILL_DIRECTORY / "references/protocol.md"
WAKE_RELAY_PATH = SKILL_DIRECTORY / "references/wake-relay.md"
MESSAGE_ID = "123e4567-e89b-42d3-a456-426614174000"
REPLY_ID = "d9428888-122b-4c46-8f23-8b12dfe7c222"


def load_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("discord_coordination_test_target", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Discord coordination helper from {HELPER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DiscordCoordinationTests(unittest.TestCase):
    helper: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_helper()

    def test_addresses_and_thread_names_are_deterministic_and_collision_resistant(self) -> None:
        role = self.helper.role_address("Epic 759F", "Security Review")
        sender = self.helper.sender_address("Epic 759F", "Security Review", "Agent ABC")

        self.assertEqual(role, "epic/epic-759f/role/security-review")
        self.assertEqual(sender, "epic/epic-759f/role/security-review/agent-abc")
        self.assertEqual(self.helper.thread_name(role), "epic-epic-759f-role-security-review-cfed4d4e9dc4")
        self.assertLessEqual(len(self.helper.thread_name(role)), 100)

        shared_prefix = "x" * 160
        first = self.helper.role_address("epic", f"{shared_prefix}-first")
        second = self.helper.role_address("epic", f"{shared_prefix}-second")
        self.assertNotEqual(self.helper.thread_name(first), self.helper.thread_name(second))
        self.assertEqual(self.helper.thread_name(first)[:87], self.helper.thread_name(second)[:87])

    def test_envelope_round_trip_preserves_v1_contract(self) -> None:
        message = self.helper.render_envelope(
            message_id=MESSAGE_ID,
            kind="handoff",
            sender="epic/759f/role/primary/agent-abc",
            target="epic/759f/role/reviewer",
            task="TASK-60 https://notion.so/example",
            in_reply_to=REPLY_ID,
            needs="Review the linked patch",
            body="notion-sync: current\nEvidence: https://example.test/patch",
        )

        parsed = self.helper.parse_envelope(message)
        self.assertEqual(parsed["id"], MESSAGE_ID)
        self.assertEqual(parsed["kind"], "handoff")
        self.assertEqual(parsed["in-reply-to"], REPLY_ID)
        self.assertEqual(parsed["task"], "TASK-60 https://notion.so/example")
        self.assertEqual(parsed["body"], "notion-sync: current\nEvidence: https://example.test/patch")

    def test_envelope_rejects_malformed_and_oversized_messages(self) -> None:
        valid = self.helper.render_envelope(
            message_id=MESSAGE_ID,
            kind="status",
            sender="epic/759f/role/primary/agent-abc",
            target="epic/759f",
            task="TASK-60",
            needs="none",
            body="notion-sync: current",
        )

        malformed_cases = [
            valid.replace("kind: status", "kind: command"),
            valid.replace("task: TASK-60", "task: issue-60"),
            valid.replace("id: 123e4567-e89b-42d3-a456-426614174000", "id: not-a-uuid"),
            valid.replace("needs: none\n---", "---"),
        ]
        for malformed in malformed_cases:
            with self.subTest(message=malformed[:80]), self.assertRaises(self.helper.CoordinationError):
                self.helper.parse_envelope(malformed)

        with self.assertRaisesRegex(self.helper.CoordinationError, "2,000-character"):
            self.helper.render_envelope(
                message_id=MESSAGE_ID,
                kind="status",
                sender="epic/759f/role/primary/agent-abc",
                target="epic/759f",
                task="TASK-60",
                needs="none",
                body="x" * 2_000,
            )

    def test_cursor_is_monotonic_and_rejects_thread_rebinding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_directory = Path(temporary_directory) / "state"
            store = self.helper.CursorState(state_directory)
            address = "epic/759f/role/primary"

            self.assertTrue(store.advance(address, "100", "900"))
            self.assertFalse(store.advance(address, "100", "900"))
            self.assertTrue(store.advance(address, "101"))
            self.assertEqual(store.get(address), {"thread_id": "900", "cursor": "101"})
            with self.assertRaisesRegex(self.helper.CoordinationError, "cursor regression"):
                store.advance(address, "99")
            with self.assertRaisesRegex(self.helper.CoordinationError, "different thread ID"):
                store.advance(address, "102", "901")

    def test_cursor_state_uses_restrictive_permissions_and_no_message_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_directory = Path(temporary_directory) / "state"
            store = self.helper.CursorState(state_directory)
            store.advance("epic/759f/role/primary", "101", "900")

            self.assertEqual(stat.S_IMODE(state_directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store.path.stat().st_mode), 0o600)
            state = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(
                state,
                {
                    "version": 1,
                    "inboxes": {
                        "epic/759f/role/primary": {
                            "cursor": "101",
                            "thread_id": "900",
                        }
                    },
                },
            )

    def test_cursor_rejects_broad_existing_state_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_directory = Path(temporary_directory) / "state"
            state_directory.mkdir()
            state_directory.chmod(0o700)
            state_path = state_directory / "state.json"
            state_path.write_text('{"version": 1, "inboxes": {}}\n', encoding="utf-8")
            state_path.chmod(0o644)
            store = self.helper.CursorState(state_directory)

            with self.assertRaisesRegex(self.helper.CoordinationError, "broader than 0600"):
                store.get("epic/759f/role/primary")

    def test_cursor_rejects_broad_existing_directory_without_changing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state_directory = Path(temporary_directory) / "shared"
            state_directory.mkdir()
            state_directory.chmod(0o755)
            store = self.helper.CursorState(state_directory)

            with self.assertRaisesRegex(self.helper.CoordinationError, "broader than 0700"):
                store.get("epic/759f/role/primary")

            self.assertEqual(stat.S_IMODE(state_directory.stat().st_mode), 0o755)
            self.assertFalse((state_directory / "state.json").exists())


class DiscordSendWorkflowTests(unittest.TestCase):
    helper: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        cls.helper = load_helper()

    @staticmethod
    def render(
        *,
        body: str = "notion-sync: current",
        kind: str = "status",
        message_id: str = MESSAGE_ID,
        sender: str = "epic/759f/role/primary/agent-abc",
        task: str = "TASK-60",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # nosec B603
            [
                sys.executable,
                str(HELPER),
                "render",
                "--id",
                message_id,
                "--kind",
                kind,
                "--from",
                sender,
                "--to",
                "epic/759f",
                "--task",
                task,
                "--needs",
                "none",
                "--body",
                body,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_successful_render_emits_only_validated_stdout(self) -> None:
        rendered = self.render()

        self.assertEqual(rendered.returncode, 0)
        self.assertEqual(rendered.stderr, "")
        self.assertTrue(rendered.stdout.endswith("\n"))
        message = rendered.stdout.removesuffix("\n")
        validated = subprocess.run(  # nosec B603
            [sys.executable, str(HELPER), "validate"],
            input=message,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(validated.returncode, 0)
        self.assertEqual(validated.stderr, "")
        self.assertEqual(json.loads(validated.stdout)["task"], "TASK-60")

    def test_maximal_render_strips_only_the_renderer_newline_before_validation(self) -> None:
        baseline = self.render(body="x")
        self.assertEqual(baseline.returncode, 0)
        baseline_message = baseline.stdout.removesuffix("\n")
        maximal_body = "x" * (self.helper.DISCORD_MESSAGE_LIMIT - len(baseline_message) + 1)

        rendered = self.render(body=maximal_body)
        self.assertEqual(rendered.returncode, 0)
        self.assertEqual(len(rendered.stdout), self.helper.DISCORD_MESSAGE_LIMIT + 1)
        message = rendered.stdout.removesuffix("\n")
        validated = subprocess.run(  # nosec B603
            [sys.executable, str(HELPER), "validate"],
            input=message,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(len(message), self.helper.DISCORD_MESSAGE_LIMIT)
        self.assertEqual(validated.returncode, 0)
        self.assertEqual(validated.stderr, "")

    def test_failed_renders_emit_no_sendable_stdout(self) -> None:
        failure_cases = {
            "invalid task reference": {"task": "TASK-60 artifacts/local-only-reference"},
            "invalid kind": {"kind": "command"},
            "invalid sender": {"sender": "epic/759f"},
            "invalid UUID": {"message_id": "not-a-uuid"},
            "empty body": {"body": ""},
            "oversized body": {"body": "x" * 2_000},
        }

        for label, arguments in failure_cases.items():
            with self.subTest(label=label):
                rendered = self.render(**arguments)
                self.assertNotEqual(rendered.returncode, 0)
                self.assertEqual(rendered.stdout, "")
                self.assertTrue(rendered.stderr)


class WakeRelayDocumentationTests(unittest.TestCase):
    skill: str
    openai: str
    protocol: str
    wake_relay: str
    openai_prompt: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL_PATH.read_text(encoding="utf-8")
        cls.openai = OPENAI_PATH.read_text(encoding="utf-8")
        cls.protocol = PROTOCOL_PATH.read_text(encoding="utf-8")
        cls.wake_relay = WAKE_RELAY_PATH.read_text(encoding="utf-8")
        openai_config = yaml.safe_load(cls.openai)
        openai_prompt = openai_config["interface"]["default_prompt"]
        if not isinstance(openai_prompt, str):
            raise TypeError("Discord coordination default prompt must be a string")
        cls.openai_prompt = openai_prompt

    def test_skill_routes_optional_wake_relay_without_weakening_authority(self) -> None:
        skill = " ".join(self.skill.split())
        self.assertIn("[references/wake-relay.md](references/wake-relay.md)", self.skill)
        self.assertIn("Notion as the work queue and system of record", skill)
        self.assertIn("Registration failure is `relay: unavailable`", skill)
        self.assertIn("not authority to act", skill)
        self.assertIn('value: "discord"', self.openai)
        self.assertIn('value: "notion"', self.openai)

    def test_registration_requires_verified_exact_values_and_processing_cursor(self) -> None:
        wake_relay = " ".join(self.wake_relay.split())
        command_contract = [
            "skills/discord-agent-coordination/scripts/discord_wake_relay.py",
            "register",
            "--epic-id <exact-epic-uuid>",
            "--agent-id <current-traycer-agent-uuid>",
            "--address <deterministic-role-only-address>",
            "--thread-id <verified-discord-thread-snowflake>",
            "--bot-id <verified-coordination-bot-id>",
            "--cursor <existing-agent-processing-cursor>",
        ]
        command = wake_relay[wake_relay.index(command_contract[0]) :]
        offsets = [command.index(fragment) for fragment in command_contract]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn("Only after all values are verified", wake_relay)
        self.assertIn("Never use zero, a placeholder", wake_relay)
        self.assertIn("Never read, print, pass, or expose the Discord Keychain token", wake_relay)
        self.assertIn("It never creates an agent", wake_relay)
        self.assertIn("refuses identity rebinding", wake_relay)
        self.assertIn("cursor regression", wake_relay)

    def test_delivery_and_resume_contract_is_explicit(self) -> None:
        wake_relay = " ".join(self.wake_relay.split())
        protocol = " ".join(self.protocol.split())
        required_contract = [
            "every 15 seconds by default",
            "detection latency is up to about 15 seconds",
            "`kind` is `handoff`",
            "`needs` is not `none`",
            "`notion-sync: current`",
            "coalesced into one wake",
            "per-target cooldown",
            "metadata-only prompt",
            "Delivery is at least once",
            "processing cursor are separate",
            "Repeated wakes with no newer inbox message are safe no-ops",
        ]
        for requirement in required_contract:
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, wake_relay)
        self.assertIn("read and validate the inbox independently", protocol)
        self.assertIn("sync Notion before claiming a lifecycle transition", protocol)

    def test_recovery_preserves_manual_fallback_and_avoids_legacy_wording(self) -> None:
        for condition in (
            "Service unavailable",
            "Ineligible or retired agent",
            "Stale registration or changed identity/thread/bot",
            "Unsafe state ownership, permissions, type, or symlink",
            "Discord, Traycer Host, or A2A transient failure",
        ):
            with self.subTest(condition=condition):
                self.assertIn(condition, self.wake_relay)
        self.assertIn('launchctl print "gui/$(id -u)/com.nfma.discord-wake-relay"', self.wake_relay)
        self.assertIn("Never edit relay state files", self.wake_relay)
        self.assertIn("run destructive Discord operations", self.wake_relay)
        combined = "\n".join((self.skill, self.protocol, self.wake_relay)).lower()
        self.assertNotIn("without a wake relay", combined)
        self.assertNotIn("no wake relay", combined)

    def test_send_workflow_is_fail_closed_at_the_mcp_boundary(self) -> None:
        skill = " ".join(self.skill.split())
        protocol = " ".join(self.protocol.split())
        openai_prompt = " ".join(self.openai_prompt.split())

        self.assertIn("validated message text after a zero exit code", skill)
        self.assertIn("do not call any Discord operation that creates a message", protocol)
        self.assertIn("remove exactly one final newline only when the captured stdout ends with one", protocol)
        self.assertIn("otherwise use the captured stdout unchanged", protocol)
        self.assertIn("Run `discord_coordination.py validate` against that exact message text", protocol)
        self.assertIn("`messages_send`, `channels_forum_create_thread`", protocol)
        self.assertIn("Never send that field", protocol)
        self.assertIn("validated message text after a zero exit code", openai_prompt)


if __name__ == "__main__":
    unittest.main()
