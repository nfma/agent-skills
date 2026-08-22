from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from scripts.skill_evals.antigravity_runner import (
    MACOS_KEYCHAIN_BLOCK_PROFILE,
    _stop_process_group,
    run_antigravity,
    run_structured_output_preflight,
)


class AntigravityRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "repository"
        self.project = self.root / "project"
        self.repository.mkdir()
        self.project.mkdir()
        self.credential = self.root / "ambient" / "antigravity-oauth-token"
        self.credential.parent.mkdir()
        self.credential.write_text('{"auth_method":"test","token":"secret"}\n', encoding="utf-8")
        self.credential.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _runner(self, body: str) -> Path:
        runner = self.root / f"fake-agy-{len(list(self.root.glob('fake-agy-*')))}"
        runner.write_text(f"#!/usr/bin/env python3\n{textwrap.dedent(body)}", encoding="utf-8")
        runner.chmod(0o755)
        return runner

    def _paths(self, name: str) -> dict[str, Path]:
        evidence = self.root / "evidence" / name
        return {
            "state_root": self.root / "state" / name,
            "stdout_path": evidence / "stdout.jsonl",
            "stderr_path": evidence / "stderr.txt",
            "final_response_path": evidence / "final.txt",
            "metadata_path": evidence / "metadata.json",
        }

    def test_preflight_uses_prompt_argument_and_disposable_home(self) -> None:
        runner = self._runner(
            r"""
            import json
            import os
            import sys
            import time
            from pathlib import Path

            arguments = sys.argv[1:]
            home = Path(os.environ["HOME"])
            token = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
            if not token.is_file() or sys.stdin.read():
                raise SystemExit(9)
            prompt_index = arguments.index("--prompt")
            if prompt_index != len(arguments) - 2 or "--output-format" not in arguments:
                raise SystemExit(10)
            prompt = arguments[prompt_index + 1]
            if (
                "mandatory finish operation" not in prompt
                or 'argument named preflight directly to the string "AGY_STRUCTURED_PREFLIGHT_OK"' not in prompt
                or "do not use any other tools" not in prompt
            ):
                raise SystemExit(13)
            model = arguments[arguments.index("--model") + 1]
            conversation = "11111111-1111-1111-1111-111111111111"
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            time.sleep(0.2)
            (home / "credential-after-init.txt").write_text(str(token.exists()))
            brain = home / ".gemini" / "antigravity-cli" / "brain" / conversation
            brain.mkdir(parents=True)
            (brain / "output_format_plan.md").write_text("disposable plan")
            response = json.dumps({"preflight": "AGY_STRUCTURED_PREFLIGHT_OK"}, separators=(",", ":"))
            print(json.dumps({"event": "step_update", "step_update": {"conversation_id": conversation, "step_index": 2, "state": "DONE", "step_type": "finish"}}))
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": response + "\n" + response, "structured_output": {"preflight": "AGY_STRUCTURED_PREFLIGHT_OK"}}}))
            """
        )
        paths = self._paths("valid")

        result = run_structured_output_preflight(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            **paths,
        )

        self.assertTrue(result.valid, result.trace_errors)
        self.assertTrue(result.credential_removed_after_init)
        self.assertEqual((paths["state_root"] / "credential-after-init.txt").read_text(), "False")
        self.assertTrue(
            (
                paths["state_root"]
                / ".gemini"
                / "antigravity-cli"
                / "brain"
                / "11111111-1111-1111-1111-111111111111"
                / "output_format_plan.md"
            ).is_file()
        )
        self.assertFalse((paths["state_root"] / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").exists())
        metadata = json.loads(paths["metadata_path"].read_text())
        self.assertEqual(metadata["command"][-2:], ["--prompt", "<PROMPT>"])
        self.assertTrue(metadata["ambient_brain_unchanged"])
        self.assertTrue(metadata["ambient_conversation_state_absent"])
        self.assertTrue(metadata["credential_file_disposed_after_run"])
        self.assertTrue(metadata["credential_disposed_after_run"])
        self.assertIn("/dev/null", metadata["command"])

    @unittest.skipUnless(sys.platform == "darwin", "macOS Keychain regression")
    def test_disposable_home_blocks_keychain_access(self) -> None:
        runner = self._runner(
            r"""
            import json
            import subprocess
            import sys

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            blocked_by_sandbox = False
            try:
                subprocess.run(
                    ["/usr/bin/security", "-i"],
                    input=b"add-generic-password -U -s antigravity-runner-test -a antigravity -w temporary\n",
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
            except PermissionError:
                blocked_by_sandbox = True
            if not blocked_by_sandbox:
                raise SystemExit(12)
            conversation = "77777777-7777-7777-7777-777777777777"
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": "ok"}}))
            """
        )
        paths = self._paths("disposable-keychain")

        result = run_antigravity(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            prompt="test disposable keychain",
            state_root=paths["state_root"],
            stdout_path=paths["stdout_path"],
            stderr_path=paths["stderr_path"],
            final_response_path=paths["final_response_path"],
            metadata_path=paths["metadata_path"],
        )

        self.assertTrue(result.valid, result.trace_errors)
        metadata = json.loads(paths["metadata_path"].read_text())
        self.assertEqual(metadata["keychain_containment_mode"], "macos-sandbox-exec")
        self.assertEqual(
            metadata["command"][:3],
            ["/usr/bin/sandbox-exec", "-p", MACOS_KEYCHAIN_BLOCK_PROFILE],
        )

    def test_nested_macos_sandbox_fails_before_staging_credential(self) -> None:
        paths = self._paths("nested-sandbox")

        with (
            patch("scripts.skill_evals.antigravity_runner.sys.platform", "darwin"),
            patch(
                "scripts.skill_evals.antigravity_runner.subprocess.run",
                return_value=subprocess.CompletedProcess([], 71),
            ),
            self.assertRaisesRegex(RuntimeError, "already be inside a macOS sandbox"),
        ):
            run_antigravity(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner="agy",
                prompt="test nested sandbox",
                state_root=paths["state_root"],
                stdout_path=paths["stdout_path"],
                stderr_path=paths["stderr_path"],
                final_response_path=paths["final_response_path"],
                metadata_path=paths["metadata_path"],
            )

        staged_credential = paths["state_root"] / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        self.assertFalse(staged_credential.exists())

    def test_credential_is_removed_when_process_start_fails(self) -> None:
        paths = self._paths("process-start-failure")

        with (
            patch("scripts.skill_evals.antigravity_runner.sys.platform", "linux"),
            patch("scripts.skill_evals.antigravity_runner.subprocess.Popen", side_effect=OSError("boom")),
            self.assertRaisesRegex(OSError, "boom"),
        ):
            run_antigravity(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner="agy",
                prompt="test cleanup",
                state_root=paths["state_root"],
                stdout_path=paths["stdout_path"],
                stderr_path=paths["stderr_path"],
                final_response_path=paths["final_response_path"],
                metadata_path=paths["metadata_path"],
            )

        staged_credential = paths["state_root"] / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        self.assertFalse(staged_credential.exists())

    def test_fallback_credential_is_removed_after_failed_run(self) -> None:
        runner = self._runner(
            r"""
            import json
            import os
            import sys
            import time
            from pathlib import Path

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            credential = Path(os.environ["HOME"]) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
            conversation = "12121212-1212-1212-1212-121212121212"
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            deadline = time.monotonic() + 2
            while credential.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            if credential.exists():
                raise SystemExit(24)
            credential.write_text('{"auth_method":"test","token":"fallback"}\n')
            raise SystemExit(23)
            """
        )
        paths = self._paths("failed-fallback")

        with patch("scripts.skill_evals.antigravity_runner.sys.platform", "linux"):
            result = run_antigravity(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner=str(runner),
                prompt="fail after creating fallback credential",
                state_root=paths["state_root"],
                stdout_path=paths["stdout_path"],
                stderr_path=paths["stderr_path"],
                final_response_path=paths["final_response_path"],
                metadata_path=paths["metadata_path"],
            )

        fallback = paths["state_root"] / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        self.assertEqual(result.exit_code, 23)
        self.assertFalse(result.valid)
        self.assertFalse(fallback.exists())
        metadata = json.loads(paths["metadata_path"].read_text())
        self.assertTrue(metadata["credential_file_disposed_after_run"])
        self.assertTrue(metadata["credential_disposed_after_run"])

    def test_fallback_credential_is_removed_when_cleanup_is_interrupted(self) -> None:
        paths = self._paths("interrupted-cleanup")
        fallback = paths["state_root"] / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        runner = self._runner(
            r"""
            import time

            time.sleep(60)
            """
        )

        def interrupt_run(_timeout: float) -> None:
            fallback.write_text('{"auth_method":"test","token":"fallback"}\n', encoding="utf-8")
            fallback.chmod(0o600)
            raise KeyboardInterrupt

        def interrupt_cleanup(process: subprocess.Popen[bytes]) -> None:
            _stop_process_group(process)
            raise KeyboardInterrupt

        with (
            patch("scripts.skill_evals.antigravity_runner.sys.platform", "linux"),
            patch(
                "scripts.skill_evals.antigravity_runner.selectors.DefaultSelector.select",
                side_effect=interrupt_run,
            ),
            patch(
                "scripts.skill_evals.antigravity_runner._stop_process_group",
                side_effect=interrupt_cleanup,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_antigravity(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner=str(runner),
                prompt="interrupt during cleanup",
                state_root=paths["state_root"],
                stdout_path=paths["stdout_path"],
                stderr_path=paths["stderr_path"],
                final_response_path=paths["final_response_path"],
                metadata_path=paths["metadata_path"],
            )

        self.assertFalse(fallback.exists())

    def test_preflight_rejects_plain_text_and_refuses_overwrite(self) -> None:
        runner = self._runner("print('plain text')\n")
        paths = self._paths("plain")
        result = run_structured_output_preflight(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            **paths,
        )
        self.assertFalse(result.valid)
        self.assertIn("stdout line 1 is not JSON", result.trace_errors)

        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            run_structured_output_preflight(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner=str(runner),
                **paths,
            )

    def test_run_stages_cli_global_skill_and_requires_native_expansion(self) -> None:
        skill = self.root / "external" / "sample-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: sample-skill\ndescription: Sample.\n---\n\n# Sample\n",
            encoding="utf-8",
        )
        runner = self._runner(
            r"""
            import json
            import os
            import sys
            from pathlib import Path

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            home = Path(os.environ["HOME"])
            skill = home / ".gemini" / "antigravity-cli" / "skills" / "sample-skill" / "SKILL.md"
            if not skill.is_file():
                raise SystemExit(11)
            conversation = "55555555-5555-5555-5555-555555555555"
            init = {
                "model": model,
                "expanded_commands": [{"name": "sample-skill", "type": "skill"}],
            }
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": init}), flush=True)
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": "ok"}}))
            """
        )
        paths = self._paths("skill")

        result = run_antigravity(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            prompt="/sample-skill",
            skill_bundle=skill,
            expected_expanded_skill="sample-skill",
            require_tool_free=True,
            state_root=paths["state_root"],
            stdout_path=paths["stdout_path"],
            stderr_path=paths["stderr_path"],
            final_response_path=paths["final_response_path"],
            metadata_path=paths["metadata_path"],
        )

        self.assertTrue(result.valid, result.trace_errors)
        self.assertEqual(result.expanded_skill_names, ("sample-skill",))
        metadata = json.loads(paths["metadata_path"].read_text())
        self.assertEqual(metadata["staged_skill"]["name"], "sample-skill")
        self.assertEqual(
            metadata["staged_skill"]["skill_md_sha256"],
            sha256((skill / "SKILL.md").read_bytes()).hexdigest(),
        )

    def test_tool_free_policy_rejects_finish_tool_events(self) -> None:
        runner = self._runner(
            r"""
            import json
            import sys

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            conversation = "99999999-9999-9999-9999-999999999999"
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            tool_info = {"name": "finish", "parameters": {"preflight": "AGY_STRUCTURED_PREFLIGHT_OK"}}
            print(json.dumps({"event": "step_update", "step_update": {"conversation_id": conversation, "step_index": 1, "state": "DONE", "step_type": "tool", "tool_name": "finish", "tool_info": tool_info}}))
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": "ok"}}))
            """
        )
        paths = self._paths("finish-tool-event")

        result = run_antigravity(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            prompt="test tool-free policy",
            require_tool_free=True,
            state_root=paths["state_root"],
            stdout_path=paths["stdout_path"],
            stderr_path=paths["stderr_path"],
            final_response_path=paths["final_response_path"],
            metadata_path=paths["metadata_path"],
        )

        self.assertFalse(result.valid)
        self.assertIn("structured-output preflight must not contain tool events", result.trace_errors)

    def test_run_rejects_missing_required_skill_expansion(self) -> None:
        runner = self._runner(
            r"""
            import json
            import sys

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            conversation = "66666666-6666-6666-6666-666666666666"
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": "ok"}}))
            """
        )
        paths = self._paths("missing-expansion")

        result = run_antigravity(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            prompt="/sample-skill",
            expected_expanded_skill="sample-skill",
            state_root=paths["state_root"],
            stdout_path=paths["stdout_path"],
            stderr_path=paths["stderr_path"],
            final_response_path=paths["final_response_path"],
            metadata_path=paths["metadata_path"],
        )

        self.assertFalse(result.valid)
        self.assertIn("init event did not expand the required skill: sample-skill", result.trace_errors)

    def test_state_root_must_be_outside_repository_and_project(self) -> None:
        runner = self._runner("raise SystemExit(1)\n")
        paths = self._paths("inside")
        paths["state_root"] = self.project / "state"
        with self.assertRaisesRegex(ValueError, "must remain outside"):
            run_structured_output_preflight(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner=str(runner),
                **paths,
            )

    def test_only_current_conversation_ambient_brain_state_is_attributed(self) -> None:
        runner = self._runner(
            r"""
            import json
            import os
            import sys
            from pathlib import Path

            arguments = sys.argv[1:]
            conversation = "22222222-2222-2222-2222-222222222222"
            model = arguments[arguments.index("--model") + 1]
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            ambient = Path(os.environ["FAKE_AMBIENT_BRAIN"])
            target = conversation if os.environ["FAKE_AMBIENT_TARGET"] == "current" else "unrelated-conversation"
            (ambient / target).mkdir(parents=True, exist_ok=True)
            payload = {"preflight": "AGY_STRUCTURED_PREFLIGHT_OK"}
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": json.dumps(payload), "structured_output": payload}}))
            """
        )
        ambient_brain = self.credential.parent / "brain"
        with patch.dict(
            "os.environ",
            {"FAKE_AMBIENT_BRAIN": str(ambient_brain), "FAKE_AMBIENT_TARGET": "unrelated"},
        ):
            unrelated = run_structured_output_preflight(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner=str(runner),
                **self._paths("unrelated"),
            )
        self.assertTrue(unrelated.valid, unrelated.trace_errors)
        self.assertFalse(unrelated.ambient_brain_unchanged)
        self.assertTrue(unrelated.ambient_conversation_state_absent)

        with patch.dict(
            "os.environ",
            {"FAKE_AMBIENT_BRAIN": str(ambient_brain), "FAKE_AMBIENT_TARGET": "current"},
        ):
            current = run_structured_output_preflight(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner=str(runner),
                **self._paths("current"),
            )
        self.assertFalse(current.valid)
        self.assertIn("the current conversation wrote ambient Antigravity brain state", current.trace_errors)

    def test_terminal_result_ends_a_lingering_process_group(self) -> None:
        runner = self._runner(
            r"""
            import json
            import sys
            import time

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            conversation = "33333333-3333-3333-3333-333333333333"
            payload = {"preflight": "AGY_STRUCTURED_PREFLIGHT_OK"}
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": json.dumps(payload), "structured_output": payload}}), flush=True)
            time.sleep(60)
            """
        )
        started_at = time.monotonic()
        result = run_structured_output_preflight(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            timeout_seconds=10,
            **self._paths("terminal-hang"),
        )
        self.assertLess(time.monotonic() - started_at, 5)
        self.assertTrue(result.valid, result.trace_errors)
        self.assertIn("after-terminal-result", result.process_cleanup)

    def test_hard_timeout_works_without_stdout_eof(self) -> None:
        runner = self._runner(
            r"""
            import json
            import sys
            import time

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            print(json.dumps({"event": "init", "conversation_id": "44444444-4444-4444-4444-444444444444", "init": {"model": model}}), flush=True)
            time.sleep(60)
            """
        )
        started_at = time.monotonic()
        result = run_structured_output_preflight(
            project_root=self.project,
            credential_file=self.credential,
            repository_root=self.repository,
            runner=str(runner),
            timeout_seconds=1,
            **self._paths("hard-timeout"),
        )
        self.assertLess(time.monotonic() - started_at, 4)
        self.assertFalse(result.valid)
        self.assertIn("Antigravity process exceeded the hard wall-clock timeout", result.trace_errors)
        self.assertIn("after-hard-timeout", result.process_cleanup)

    def test_exited_runner_does_not_wait_forever_for_detached_stdout(self) -> None:
        runner = self._runner(
            r"""
            import json
            import os
            import sys
            import time
            from pathlib import Path

            arguments = sys.argv[1:]
            model = arguments[arguments.index("--model") + 1]
            child_pid = os.fork()
            if child_pid == 0:
                os.setsid()
                time.sleep(60)
                os._exit(0)
            Path(os.environ["HOME"], "detached.pid").write_text(str(child_pid))
            conversation = "88888888-8888-8888-8888-888888888888"
            print(json.dumps({"event": "init", "conversation_id": conversation, "init": {"model": model}}), flush=True)
            print(json.dumps({"event": "result", "result": {"conversation_id": conversation, "status": "SUCCESS", "response": "ok"}}), flush=True)
            """
        )
        paths = self._paths("detached-stdout")
        started_at = time.monotonic()

        try:
            result = run_antigravity(
                project_root=self.project,
                credential_file=self.credential,
                repository_root=self.repository,
                runner=str(runner),
                prompt="test detached stdout",
                timeout_seconds=10,
                state_root=paths["state_root"],
                stdout_path=paths["stdout_path"],
                stderr_path=paths["stderr_path"],
                final_response_path=paths["final_response_path"],
                metadata_path=paths["metadata_path"],
            )
        finally:
            detached_pid_path = paths["state_root"] / "detached.pid"
            if detached_pid_path.is_file():
                with suppress(ProcessLookupError):
                    os.kill(int(detached_pid_path.read_text()), signal.SIGKILL)

        self.assertLess(time.monotonic() - started_at, 5)
        self.assertTrue(result.valid, result.trace_errors)
        self.assertEqual(result.process_cleanup, "bounded-drain-after-process-exit")


if __name__ == "__main__":
    unittest.main()
