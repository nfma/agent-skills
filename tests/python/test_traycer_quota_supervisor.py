from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[2] / "services" / "traycer-quota-supervisor" / "traycer_quota_supervisor.py"
LOADER = importlib.machinery.SourceFileLoader("traycer_quota_supervisor", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
if SPEC is None:
    raise RuntimeError("cannot load the quota supervisor test module")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
LOADER.exec_module(MODULE)


AGENT_A = "11111111-1111-4111-8111-111111111111"
AGENT_B = "22222222-2222-4222-8222-222222222222"
PARENT = "33333333-3333-4333-8333-333333333333"


class FakeClock:
    def __init__(self, value=1_000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Backend:
    def __init__(self):
        self.profiles = {}
        self.profile_calls = []
        self.quotas = {}
        self.quota_calls = []
        self.registry_results = {}
        self.sent = []
        self.failures = {}
        self.process_scans = []

    def client(self, credential):
        return FakeClient(self, credential.token)

    def probe(self, harness, profile, credential):
        token = credential.token if credential else None
        self.quota_calls.append((harness, profile, token))
        values = self.quotas[(harness, profile)]
        if isinstance(values, list):
            return values.pop(0)
        return values

    def scan(self):
        if not self.process_scans:
            return {}
        return self.process_scans.pop(0)


class FakeClient:
    def __init__(self, backend, token):
        self.backend = backend
        self.token = token

    def registry(self):
        result = self.backend.registry_results[self.token]
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def profile(self, harness):
        self.backend.profile_calls.append((self.token, harness))
        return self.backend.profiles[harness]

    def send(self, target_id, message):
        failures = self.backend.failures.get(target_id, 0)
        if failures:
            self.backend.failures[target_id] = failures - 1
            raise MODULE.SupervisorError("temporary delivery failure")
        self.backend.sent.append((self.token, target_id, message))


class SupervisorTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(
            os.environ,
            {"TRAYCER_QUOTA_SUPERVISOR_STATE_DIR": self.temporary.name},
        )
        self.environment.start()
        self.clock = FakeClock()
        self.backend = Backend()

    def tearDown(self):
        self.environment.stop()
        self.temporary.cleanup()

    def _make_supervisor(self):
        return MODULE.Supervisor(
            client_factory=self.backend.client,
            process_scanner=self.backend.scan,
            quota_probe=self.backend.probe,
            clock=self.clock,
        )

    def add_transport(self, supervisor, token=None, pid=10):
        token = token or f"transport-{AGENT_A}"
        credential = MODULE.Credential(token, MODULE.DEFAULT_ENDPOINT, pids={pid})
        supervisor.credentials[credential.identifier] = credential
        return credential

    def registry_agent(
        self,
        agent_id,
        *,
        parent_id=None,
        harness="codex",
        surface="gui",
        archived=False,
        messageable=True,
    ):
        return MODULE.RegistryAgent(
            agent_id,
            surface,
            harness,
            parent_id,
            archived,
            messageable,
        )

    def register(self, supervisor, token, agent_id, parent_id=None, harness="codex", pid=10):
        self.add_transport(supervisor, token, pid)
        if harness not in {"cursor", "antigravity"}:
            self.backend.profiles[harness] = "ambient"
        agent = self.registry_agent(agent_id, parent_id=parent_id, harness=harness)
        supervisor._reconcile_registry_views([MODULE.RegistryView((agent,), frozenset())])
        session = supervisor.sessions[agent_id]
        session.parent_id = parent_id
        session.parent_messageable = parent_id is not None
        return session


class QuotaClassificationTests(unittest.TestCase):
    def test_traycer_explicit_and_active_exhausted_limits_are_blocked(self):
        explicit = MODULE.classify_quota(
            {"available": True, "rateLimitReachedType": "primary"},
            now_ms=1_000,
        )
        window = MODULE.classify_quota(
            {"available": True, "fiveHour": {"usedPercent": 100, "resetsAt": 2_000}},
            now_ms=1_000,
        )
        self.assertEqual(explicit.state, "blocked")
        self.assertEqual(window.state, "blocked")
        self.assertEqual(window.resets_at, 2_000)

    def test_traycer_expired_window_and_positive_credits_are_available(self):
        expired = MODULE.classify_quota(
            {"available": True, "primary": {"usedPercent": 100, "resetsAt": 999}},
            now_ms=1_000,
        )
        credits = MODULE.classify_quota(
            {
                "available": True,
                "primary": {"usedPercent": 100, "resetsAt": 2_000},
                "credits": {"hasCredits": True, "balance": "1.5"},
            },
            now_ms=1_000,
        )
        self.assertEqual(expired.state, "available")
        self.assertEqual(credits.state, "available")

    def test_cursor_status_uses_used_percentage_and_other_spend(self):
        blocked = MODULE.classify_cursor_status(
            "plan \x1b[38;5;196mcursor 100%\x1b[39m · \x1b[38;5;196mother $20.00/$20.00\x1b[39m · month"
        )
        available = MODULE.classify_cursor_status("plan cursor 42% · other $3.50/$20.00 · month")
        self.assertEqual(blocked.state, "blocked")
        self.assertEqual(available.state, "available")

    def test_cursor_status_accepts_optional_iso_reset_time(self):
        status = MODULE.classify_cursor_status("cursor 100% · other $20/$20 · reset at 2026-08-24T10:00:00Z")
        self.assertEqual(status.resets_at, 1_787_565_600_000)

    def test_cursor_malformed_status_is_unknown(self):
        self.assertEqual(MODULE.classify_cursor_status("ready").state, "unknown")

    def test_antigravity_remaining_fraction_controls_quota(self):
        available = {"command": {"data": {"groups": [{"buckets": [{"id": "weekly", "remaining_fraction": 0.2}]}]}}}
        blocked = {
            "command": {
                "data": {
                    "groups": [
                        {
                            "buckets": [
                                {
                                    "id": "five-hour",
                                    "remaining_fraction": 0,
                                    "reset_time": "2026-08-24T10:00:00Z",
                                }
                            ]
                        }
                    ]
                }
            }
        }
        self.assertEqual(MODULE.classify_antigravity_usage(available).state, "available")
        exhausted = MODULE.classify_antigravity_usage(blocked)
        self.assertEqual(exhausted.state, "blocked")
        self.assertEqual(exhausted.resets_at, 1_787_565_600_000)


class ParsingTests(unittest.TestCase):
    def test_profile_selection_prefers_effective_authenticated_profile(self):
        payload = {
            "profiles": [
                {
                    "selection": {"kind": "managed", "profileId": "other"},
                    "authStatus": "authenticated",
                },
                {
                    "selection": {"kind": "ambient"},
                    "authStatus": "authenticated",
                    "isEffectiveLastUsed": True,
                },
            ]
        }
        self.assertEqual(MODULE.choose_profile(payload), "ambient")

    def test_self_and_parent_text_are_parsed(self):
        self_text = f"{AGENT_A}\narchived: no\nharness: codex\n"
        agents_text = f'Agents in epic:\nParent:\n{PARENT} "Parent" gui/codex\n'
        self.assertEqual(MODULE.parse_self(self_text), (AGENT_A, "codex", False))
        self.assertEqual(MODULE.parse_parent_id(agents_text), PARENT)

    def test_registry_you_and_children_capture_harness_state_and_parent(self):
        registry_text = f"""Agents in epic (relative to you):
You:
{PARENT} [self] "Parent" gui/codex worktree: /tmp/parent

Children (agents you spawned):
{AGENT_A} "Claude child" gui/claude R/S dir: /tmp/child
{AGENT_B} [archived] "Archived child" gui/cursor R/S dir: /tmp/archived

Legend:
[self]: caller
"""

        view = MODULE.parse_agent_registry(registry_text)
        agents = {agent.agent_id: agent for agent in view.agents}

        self.assertEqual(view.authoritative_parent_ids, {PARENT})
        self.assertEqual((agents[AGENT_A].surface, agents[AGENT_A].harness), ("gui", "claude"))
        self.assertEqual(agents[AGENT_A].parent_id, PARENT)
        self.assertTrue(agents[AGENT_A].messageable)
        self.assertTrue(agents[AGENT_A].open)
        self.assertFalse(agents[AGENT_A].archived)
        self.assertFalse(agents[AGENT_B].open)
        self.assertTrue(agents[AGENT_B].archived)

    def test_registry_titles_that_look_like_markers_or_execution_tokens_parse(self):
        entries = (
            f'{AGENT_A} "fix/retry" gui/claude R/S dir: /tmp/child',
            f'{AGENT_A} "[WIP] review" gui/claude R/S dir: /tmp/child',
            f"{AGENT_A} gui/claude R/S dir: /tmp/child",
        )
        for entry in entries:
            with self.subTest(entry=entry):
                registry_text = f"""Agents in epic (relative to you):
You:
{PARENT} [self] "Parent" gui/codex worktree: /tmp/parent

Children (agents you spawned):
{entry}

Legend:
"""

                agents = {agent.agent_id: agent for agent in MODULE.parse_agent_registry(registry_text).agents}

                self.assertEqual((agents[AGENT_A].surface, agents[AGENT_A].harness), ("gui", "claude"))

    def test_registry_unknown_or_duplicate_unquoted_markers_fail_closed(self):
        for entry in (
            f'{AGENT_A} [future] "Child" gui/claude R/S dir: /tmp/child',
            f'{AGENT_A} [archived] [archived] "Child" gui/claude R/S dir: /tmp/child',
        ):
            with self.subTest(entry=entry), self.assertRaises(MODULE.SupervisorError):
                MODULE.parse_registry_entry(entry)

    def test_registry_parent_and_siblings_capture_direct_parent(self):
        registry_text = f"""Agents in epic (relative to you):
You:
{AGENT_A} [self] "Child" gui/claude worktree: /tmp/child

Parent:
{PARENT} "Parent" gui/codex R/S worktree: /tmp/parent

Siblings:
{AGENT_B} "Sibling" gui/cursor S dir: /tmp/sibling

Legend:
"""

        agents = {agent.agent_id: agent for agent in MODULE.parse_agent_registry(registry_text).agents}

        self.assertEqual(agents[AGENT_A].parent_id, PARENT)
        self.assertEqual(agents[AGENT_B].parent_id, PARENT)
        self.assertIsNone(agents[PARENT].parent_id)

    def test_registry_parser_fails_closed_on_malformed_entries(self):
        malformed = f"""Agents in epic (relative to you):
You:
{PARENT} [self] "Parent" gui/codex worktree: /tmp/parent

Children (agents you spawned):
{AGENT_A} Broken child title gui/claude R/S dir: /tmp/child
"""

        with self.assertRaises(MODULE.SupervisorError):
            MODULE.parse_agent_registry(malformed)

    def test_registry_views_union_compatible_partial_relationships(self):
        first = MODULE.RegistryView(
            (MODULE.RegistryAgent(AGENT_A, "gui", "claude", PARENT, False, True),),
            frozenset({PARENT}),
        )
        second = MODULE.RegistryView(
            (MODULE.RegistryAgent(AGENT_B, "gui", "cursor", PARENT, False, True),),
            frozenset({PARENT}),
        )

        agents, authoritative_parent_ids = MODULE.merge_registry_views([first, second])

        self.assertEqual(set(agents), {AGENT_A, AGENT_B})
        self.assertEqual(authoritative_parent_ids, {PARENT})

    def test_registry_archive_marker_dominates_open_messageable_views(self):
        open_agent = MODULE.RegistryAgent(AGENT_A, "gui", "claude", PARENT, False, True)
        archived_agent = MODULE.RegistryAgent(AGENT_A, "gui", "claude", PARENT, True, False)
        for first, second in ((open_agent, archived_agent), (archived_agent, open_agent)):
            with self.subTest(first_archived=first.archived):
                agents, _ = MODULE.merge_registry_views(
                    [
                        MODULE.RegistryView((first,), frozenset({PARENT})),
                        MODULE.RegistryView((second,), frozenset({PARENT})),
                    ]
                )

                self.assertTrue(agents[AGENT_A].archived)
                self.assertFalse(agents[AGENT_A].messageable)

    def test_group_key_round_trips_unusual_profile(self):
        key = MODULE.group_key("claude", "managed/profile")
        self.assertEqual(MODULE.decode_group_key(key), ("claude", "managed/profile"))


class CommandSurfaceTests(unittest.TestCase):
    def test_runtime_exposes_run_and_status_without_an_install_command(self):
        parser = MODULE.build_parser()

        self.assertEqual(parser.parse_args(["run"]).command, "run")
        self.assertEqual(parser.parse_args(["status"]).command, "status")
        with self.assertRaises(SystemExit):
            parser.parse_args(["install"])

    def test_status_payload_contains_no_discovered_credential(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(
                os.environ,
                {"TRAYCER_QUOTA_SUPERVISOR_STATE_DIR": directory},
            ),
        ):
            supervisor = MODULE.Supervisor(
                process_scanner=lambda: {},
                quota_probe=lambda *_arguments: MODULE.QuotaStatus(
                    "available",
                    "capacity",
                ),
            )
            credential = MODULE.Credential(
                "do-not-report-this-token",
                MODULE.DEFAULT_ENDPOINT,
                pids={42},
            )
            supervisor.credentials[credential.identifier] = credential
            supervisor.sessions[AGENT_A] = MODULE.Session(
                AGENT_A,
                None,
                "codex",
                "ambient",
                messageable=True,
            )

            payload = json.dumps(supervisor.status_payload())

        self.assertNotIn("do-not-report-this-token", payload)
        self.assertNotIn(MODULE.DEFAULT_ENDPOINT, payload)
        self.assertNotIn("credential", payload)
        self.assertIn(AGENT_A, payload)
        self.assertIn('"registry_open": true', payload)
        self.assertIn('"registry_fresh": true', payload)
        self.assertIn('"source_processes": 1', payload)


class RecoveryStateTests(SupervisorTestCase):
    def test_blocked_group_marks_every_registry_open_session_as_candidate(self):
        supervisor = self._make_supervisor()
        first = self.register(supervisor, "token-a", AGENT_A)
        second = self.register(supervisor, "token-b", AGENT_B, PARENT)
        self.backend.quotas[("codex", "ambient")] = MODULE.QuotaStatus("blocked", "full")

        supervisor.poll_groups()

        self.assertEqual(first.status, "candidate")
        self.assertEqual(second.status, "candidate")
        self.assertEqual(self.backend.quota_calls, [("codex", "ambient", "token-a")])

    def test_available_transition_wakes_candidates_and_notifies_parent(self):
        supervisor = self._make_supervisor()
        first = self.register(supervisor, "token-a", AGENT_A)
        second = self.register(supervisor, "token-b", AGENT_B, PARENT)
        self.backend.quotas[("codex", "ambient")] = [
            MODULE.QuotaStatus("blocked", "full"),
            MODULE.QuotaStatus("available", "capacity"),
        ]

        supervisor.poll_groups()
        supervisor.poll_groups()

        targets = [target for _, target, _ in self.backend.sent]
        self.assertEqual(targets.count(AGENT_A), 1)
        self.assertEqual(targets.count(AGENT_B), 1)
        self.assertEqual(targets.count(PARENT), 1)
        self.assertEqual(first.status, "open")
        self.assertEqual(second.status, "open")

    def test_new_session_discovered_while_group_blocked_is_candidate(self):
        supervisor = self._make_supervisor()
        self.register(supervisor, "token-a", AGENT_A)
        self.backend.quotas[("codex", "ambient")] = MODULE.QuotaStatus("blocked", "full")
        supervisor.poll_groups()

        second = self.register(supervisor, "token-b", AGENT_B)

        self.assertEqual(second.status, "candidate")

    def test_archived_candidate_is_not_woken(self):
        supervisor = self._make_supervisor()
        session = self.register(supervisor, "token-a", AGENT_A)
        self.backend.quotas[("codex", "ambient")] = MODULE.QuotaStatus("blocked", "full")
        supervisor.poll_groups()
        archived = self.registry_agent(AGENT_A, archived=True)
        supervisor._reconcile_registry_views([MODULE.RegistryView((archived,), frozenset())])
        self.backend.quotas[("codex", "ambient")] = MODULE.QuotaStatus("available", "capacity")

        supervisor.deliver_recoveries("codex", "ambient")

        self.assertNotIn(session.agent_id, supervisor.sessions)
        self.assertEqual(self.backend.sent, [])

    def test_parent_retry_does_not_resend_agent_wake(self):
        supervisor = self._make_supervisor()
        session = self.register(supervisor, "token-a", AGENT_A, PARENT)
        session.status = "candidate"
        self.backend.failures[PARENT] = 1

        supervisor.deliver_recoveries("codex", "ambient")
        supervisor.deliver_recoveries("codex", "ambient")

        targets = [target for _, target, _ in self.backend.sent]
        self.assertEqual(targets.count(AGENT_A), 1)
        self.assertEqual(targets.count(PARENT), 1)
        self.assertEqual(session.status, "open")

    def test_fresh_non_messageable_candidate_ends_without_a_late_wake(self):
        supervisor = self._make_supervisor()
        session = self.register(supervisor, "token-a", AGENT_A)
        session.status = "candidate"
        session.messageable = False

        supervisor.deliver_recoveries("codex", "ambient")

        self.assertEqual(session.status, "open")
        self.assertEqual(self.backend.sent, [])

        session.messageable = True
        supervisor.deliver_recoveries("codex", "ambient")
        self.assertEqual(self.backend.sent, [])

        self.backend.quotas[("codex", "ambient")] = [
            MODULE.QuotaStatus("blocked", "full"),
            MODULE.QuotaStatus("available", "capacity"),
        ]
        supervisor.poll_group("codex", "ambient")
        supervisor.poll_group("codex", "ambient")

        self.assertEqual([target for _, target, _ in self.backend.sent], [AGENT_A])

    def test_cursor_and_antigravity_use_ambient_without_a2a_profile_lookup(self):
        supervisor = self._make_supervisor()

        cursor = self.register(supervisor, "cursor-token", AGENT_A, harness="cursor")
        antigravity = self.register(
            supervisor,
            "agy-token",
            AGENT_B,
            harness="google-antigravity",
        )

        self.assertEqual((cursor.harness, cursor.profile), ("cursor", "ambient"))
        self.assertEqual((antigravity.harness, antigravity.profile), ("antigravity", "ambient"))
        self.assertEqual(self.backend.profile_calls, [])

    def test_known_reset_schedules_an_earlier_probe(self):
        supervisor = self._make_supervisor()
        reset_ms = int((self.clock() + 300) * 1_000)
        next_poll = supervisor._next_poll_at(
            MODULE.QuotaStatus("blocked", "full", reset_ms),
            self.clock(),
        )
        self.assertEqual(next_poll, self.clock() + 300 + MODULE.DEFAULT_RESET_GRACE_SECONDS)

    def test_persisted_state_contains_no_credentials(self):
        supervisor = self._make_supervisor()
        session = self.register(supervisor, "do-not-persist-this-token", AGENT_A)
        session.status = "candidate"
        supervisor.persist()

        text = MODULE.state_path().read_text()

        self.assertNotIn("do-not-persist-this-token", text)
        self.assertNotIn(MODULE.DEFAULT_ENDPOINT, text)
        self.assertNotIn("credential_id", text)
        self.assertEqual(json.loads(text)["sessions"][AGENT_A]["status"], "candidate")


class RegistryRecoveryTests(SupervisorTestCase):
    def registry_view(self, *, child_archived=False, include_child=True):
        parent = self.registry_agent(PARENT, harness="codex")
        agents = [parent]
        if include_child:
            agents.append(
                self.registry_agent(
                    AGENT_A,
                    parent_id=PARENT,
                    harness="claude",
                    archived=child_archived,
                )
            )
        return MODULE.RegistryView(tuple(agents), frozenset({PARENT}))

    def configure_registry_transport(self, supervisor, *registry_results):
        credential = MODULE.Credential("parent-codex-transport", MODULE.DEFAULT_ENDPOINT, pids={10})
        self.backend.process_scans = [
            {credential.identifier: credential},
            {},
        ]
        self.backend.registry_results[credential.token] = list(registry_results)
        self.backend.profiles.update({"codex": "codex-profile", "claude": "claude-profile"})
        return credential

    def test_provider_process_exit_does_not_hide_or_block_registry_open_claude_recovery(self):
        supervisor = self._make_supervisor()
        view = self.registry_view()
        credential = self.configure_registry_transport(supervisor, view, view)
        self.backend.quotas[("claude", "claude-profile")] = [
            MODULE.QuotaStatus("blocked", "full"),
            MODULE.QuotaStatus("available", "capacity"),
            MODULE.QuotaStatus("available", "capacity"),
        ]

        supervisor.reconcile()
        supervisor.poll_group("claude", "claude-profile")
        supervisor.reconcile()

        child = supervisor.sessions[AGENT_A]
        payload = supervisor.status_payload()
        child_status = next(item for item in payload["sessions"] if item["agent_id"] == AGENT_A)
        self.assertEqual(child.status, "candidate")
        self.assertEqual((child.harness, child.profile), ("claude", "claude-profile"))
        self.assertEqual(child.parent_id, PARENT)
        self.assertTrue(child.parent_messageable)
        self.assertTrue(child_status["registry_open"])
        self.assertTrue(child_status["messageable"])
        self.assertEqual(payload["transport"], {"cached": 1, "source_processes": 0})
        self.assertEqual(supervisor.credentials[credential.identifier].pids, set())
        self.assertIn((credential.token, "claude"), self.backend.profile_calls)

        supervisor.poll_group("claude", "claude-profile")
        supervisor.poll_group("claude", "claude-profile")

        targets = [target for token, target, _message in self.backend.sent if token == credential.token]
        self.assertEqual(len(self.backend.sent), 2)
        self.assertEqual(targets.count(AGENT_A), 1)
        self.assertEqual(targets.count(PARENT), 1)
        self.assertEqual(
            self.backend.quota_calls,
            [
                ("claude", "claude-profile", credential.token),
                ("claude", "claude-profile", credential.token),
                ("claude", "claude-profile", credential.token),
            ],
        )
        self.assertEqual(child.status, "open")

    def test_successful_registry_refresh_removes_archived_child(self):
        supervisor = self._make_supervisor()
        self.configure_registry_transport(
            supervisor,
            self.registry_view(),
            self.registry_view(child_archived=True),
        )

        supervisor.reconcile()
        supervisor.sessions[AGENT_A].status = "candidate"
        supervisor.reconcile()

        self.assertNotIn(AGENT_A, supervisor.sessions)
        self.assertEqual(self.backend.sent, [])

    def test_stale_parentless_candidate_is_not_polled_or_woken_until_confirmed(self):
        supervisor = self._make_supervisor()
        session = self.register(supervisor, "token-a", AGENT_A)
        session.status = "candidate"
        key = MODULE.group_key("codex", "ambient")
        supervisor.groups[key] = MODULE.GroupState(state="available", reason="capacity")
        self.clock.advance(supervisor.missing_grace_seconds + 1)

        supervisor.poll_groups()
        supervisor.deliver_recoveries("codex", "ambient")

        status = next(item for item in supervisor.status_payload()["sessions"] if item["agent_id"] == AGENT_A)
        self.assertTrue(status["registry_open"])
        self.assertFalse(status["registry_fresh"])
        self.assertEqual(session.status, "candidate")
        self.assertEqual(self.backend.quota_calls, [])
        self.assertEqual(self.backend.sent, [])

        confirmed = self.registry_agent(AGENT_A)
        supervisor._reconcile_registry_views([MODULE.RegistryView((confirmed,), frozenset())])

        self.assertEqual([target for _, target, _ in self.backend.sent], [AGENT_A])
        self.assertEqual(session.status, "open")

    def test_successful_authoritative_registry_refresh_removes_absent_child(self):
        supervisor = self._make_supervisor()
        self.configure_registry_transport(
            supervisor,
            self.registry_view(),
            self.registry_view(include_child=False),
            self.registry_view(include_child=False),
        )

        supervisor.reconcile()
        supervisor.sessions[AGENT_A].status = "candidate"
        supervisor.reconcile()
        self.assertFalse(supervisor.sessions[AGENT_A].registry_open)
        supervisor.deliver_recoveries("claude", "claude-profile")
        self.assertEqual(self.backend.sent, [])

        self.clock.advance(supervisor.missing_grace_seconds + 1)
        supervisor.reconcile()

        self.assertNotIn(AGENT_A, supervisor.sessions)
        self.assertEqual(self.backend.sent, [])

    def test_failed_registry_refresh_preserves_candidate_and_cached_transport(self):
        supervisor = self._make_supervisor()
        credential = self.configure_registry_transport(
            supervisor,
            self.registry_view(),
            MODULE.SupervisorError("temporary registry failure parent-codex-transport"),
        )

        supervisor.reconcile()
        supervisor.sessions[AGENT_A].status = "candidate"
        errors = io.StringIO()
        with redirect_stderr(errors):
            supervisor.reconcile()

        self.assertEqual(supervisor.sessions[AGENT_A].status, "candidate")
        self.assertTrue(supervisor.sessions[AGENT_A].registry_open)
        self.assertIn(credential.identifier, supervisor.credentials)
        self.assertEqual(supervisor.credentials[credential.identifier].pids, set())
        self.assertNotIn("parent-codex-transport", errors.getvalue())

        self.clock.advance(supervisor.missing_grace_seconds + 1)
        supervisor.groups[MODULE.group_key("claude", "claude-profile")] = MODULE.GroupState(
            state="available",
            reason="capacity",
        )
        supervisor.deliver_recoveries("claude", "claude-profile")
        child_status = next(item for item in supervisor.status_payload()["sessions"] if item["agent_id"] == AGENT_A)
        self.assertFalse(child_status["registry_fresh"])
        self.assertEqual(self.backend.sent, [])

    def test_rejected_transport_is_evicted_without_removing_sessions(self):
        supervisor = self._make_supervisor()
        credential = self.configure_registry_transport(
            supervisor,
            self.registry_view(),
            MODULE.TransportRejectedError("rejected"),
        )

        supervisor.reconcile()
        supervisor.sessions[AGENT_A].status = "candidate"
        supervisor.reconcile()

        self.assertEqual(supervisor.sessions[AGENT_A].status, "candidate")
        self.assertTrue(supervisor.sessions[AGENT_A].registry_open)
        self.assertNotIn(credential.identifier, supervisor.credentials)


class TransportDiscoveryTests(unittest.TestCase):
    def test_any_traycer_managed_process_is_discovered_and_deduplicated(self):
        output = "\n".join(
            [
                (
                    "10 /Users/nfma/.local/bin/cursor-agent agent "
                    + "TRAYCER_A2A_MCP_TOKEN=secret-token "
                    + "TRAYCER_A2A_MCP_URL=http://127.0.0.1:49440/mcp"
                ),
                "11 /Users/nfma/.local/bin/cursor-agent worker TRAYCER_A2A_MCP_TOKEN=secret-token",
                "12 /Users/nfma/.local/bin/agy TRAYCER_A2A_MCP_TOKEN=other-token",
                "13 /usr/bin/other --without-credential",
            ]
        )
        completed = type("Completed", (), {"returncode": 0, "stdout": output})()

        with patch.object(MODULE.subprocess, "run", return_value=completed):
            credentials = MODULE.scan_provider_processes()

        self.assertEqual(len(credentials), 2)
        cursor = credentials[MODULE.Credential("secret-token", MODULE.DEFAULT_ENDPOINT).identifier]
        self.assertEqual(cursor.pids, {10, 11})
        self.assertEqual(cursor.endpoint, MODULE.DEFAULT_ENDPOINT)

    def test_probe_environment_drops_a2a_credentials(self):
        credential_value = f"ephemeral-{AGENT_A}"
        with patch.dict(
            os.environ,
            {
                "TRAYCER_A2A_MCP_TOKEN": credential_value,
                "TRAYCER_A2A_MCP_URL": MODULE.DEFAULT_ENDPOINT,
                "PRESERVE_ME": "yes",
            },
        ):
            environment = MODULE.probe_environment()
        self.assertNotIn("TRAYCER_A2A_MCP_TOKEN", environment)
        self.assertNotIn("TRAYCER_A2A_MCP_URL", environment)
        self.assertEqual(environment["PRESERVE_ME"], "yes")


if __name__ == "__main__":
    unittest.main()
