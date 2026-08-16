from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest import mock

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELAY = REPOSITORY_ROOT / "skills/orchestrate-risk-scaled-review/scripts/agy_review_relay.py"
CONFIGURATION_EXIT = 64
PROTOCOL_EXIT = 65
INVOCATION_EXIT = 69
MAX_PROMPT_FILE_BYTES = 16 * 1024
TEST_SENTINEL = "TRAYCER_PROMPT_SENTINEL_abcdefghijklmnopqrstuvwxyzABCDEF"


def filesystem_is_case_insensitive() -> bool:
    with tempfile.TemporaryDirectory() as temporary_directory:
        directory = Path(temporary_directory)
        (directory / "case-probe").write_text("probe", encoding="utf-8")
        return (directory / "CASE-PROBE").exists()


def event_line(value: dict[str, object]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def load_relay_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agy_review_relay_test_target", RELAY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load relay module from {RELAY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_fake_agy(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "agy"
    source = (
        f"#!{sys.executable}\n"
        + r"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")


mode = os.environ.get("FAKE_AGY_MODE", "valid")
include_args = os.environ.get("FAKE_AGY_INCLUDE_ARGS", "1") == "1"
conversation_id = os.environ.get("FAKE_AGY_CONVERSATION_ID", "conversation-123")
prompt_directory = Path(sys.argv[sys.argv.index("--add-dir") + 1])
prompt_path = Path(os.environ["FAKE_AGY_PROMPT_FILE"])
prompt_text = prompt_path.read_text(encoding="utf-8")
prompt_lines = prompt_text.splitlines()
prompt_sentinel = prompt_lines[0] if prompt_lines else ""
if mode == "missing-sentinel":
    result_response = "FAKE_RESPONSE"
elif mode == "polluted-sentinel":
    result_response = f"{prompt_sentinel} EXTRA\nFAKE_RESPONSE"
else:
    result_response = f"{prompt_sentinel}\nFAKE_RESPONSE"

if mode == "unterminated":
    sys.stdout.write("{broken")
elif mode == "malformed":
    sys.stdout.write("{broken}\n")
else:
    init: dict[str, object] = {"event": "init"}
    result: dict[str, object] = {
        "event": "result",
        "result": {
            "status": "SUCCESS",
            "usage": {"total_tokens": 42},
            "response": result_response,
        },
    }
    if mode != "missing":
        init["conversation_id"] = conversation_id
        result_value = result["result"]
        assert isinstance(result_value, dict)
        result_value["conversation_id"] = "different-conversation" if mode == "conflict" else conversation_id
    emit(init)
    if mode == "mutate-prompt":
        prompt_path.write_text(f"{prompt_sentinel}\nMUTATED PROMPT BODY", encoding="utf-8")
    if include_args:
        emit(
            {
                "event": "fake_args",
                "args": sys.argv[1:],
                "prompt_path": str(prompt_path),
                "prompt_text": prompt_text,
                "prompt_mode": prompt_path.stat().st_mode & 0o777,
            }
        )
    if mode != "init-only":
        emit(result)

sys.stderr.write(os.environ.get("FAKE_AGY_STDERR", ""))
raise SystemExit(int(os.environ.get("FAKE_AGY_EXIT", "0")))
"""
    )
    executable.write_text(source, encoding="utf-8")
    executable.chmod(0o755)
    return executable


def create_prompt_file(directory: Path, prompt: str = "Review this change", sentinel: str = TEST_SENTINEL) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    prompt_file = directory / "reviewer-prompt.md"
    prompt_file.write_text(f"{sentinel}\n{prompt}", encoding="utf-8")
    return prompt_file


def run_relay(
    fake_agy: Path,
    workspace: Path,
    prompt_file: Path | None = None,
    print_timeout: str = "30m",
    conversation: str | None = None,
    *,
    use_override: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if prompt_file is None:
        prompt_file = create_prompt_file(workspace / "prompt-artifacts")
    environment = os.environ.copy()
    if use_override:
        environment["AGY_BIN"] = str(fake_agy)
    else:
        environment.pop("AGY_BIN", None)
        environment["PATH"] = f"{fake_agy.parent}{os.pathsep}{environment.get('PATH', '')}"
    if extra_env:
        environment.update(extra_env)
    environment["FAKE_AGY_PROMPT_FILE"] = str(prompt_file.resolve())

    command = [
        sys.executable,
        str(RELAY),
        "--prompt-file",
        str(prompt_file),
        "--print-timeout",
        print_timeout,
    ]
    if conversation is not None:
        command.extend(["--conversation", conversation])
    return subprocess.run(
        command,
        capture_output=True,
        cwd=workspace,
        env=environment,
        check=False,
        shell=False,
    )


def parsed_events(stdout: bytes) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for raw_line in stdout.splitlines():
        value: object = json.loads(raw_line)
        if isinstance(value, dict):
            events.append(value)
    return events


class AgyReviewRelayTests(unittest.TestCase):
    def test_start_uses_durable_prompt_handle_and_required_flags_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()
            sentinel = workspace / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            marker = workspace / "must-not-exist"
            prompt = f"Review `code`; $(touch {marker}) && echo $HOME"
            prompt_file = create_prompt_file(root / "prompt-artifacts", prompt)

            result = run_relay(fake_agy, workspace, prompt_file=prompt_file)

            self.assertEqual(result.returncode, 0)
            events = parsed_events(result.stdout)
            fake_event = next(event for event in events if event.get("event") == "fake_args")
            fake_args = fake_event["args"]
            self.assertIsInstance(fake_args, list)
            fake_args = cast(list[str], fake_args)
            prompt_path_value = fake_event["prompt_path"]
            prompt_text_value = fake_event["prompt_text"]
            self.assertIsInstance(prompt_path_value, str)
            self.assertIsInstance(prompt_text_value, str)
            prompt_path = Path(cast(str, prompt_path_value))
            prompt_text = cast(str, prompt_text_value)
            expected_instruction = (
                f"Read and follow the complete review prompt at {prompt_path}. "
                "Begin your response with the exact sentinel from its first line. "
                f"If you cannot read the file, stop and respond exactly: PROMPT_FILE_UNREADABLE: {prompt_path}"
            )
            self.assertEqual(fake_args[fake_args.index("--prompt") + 1], expected_instruction)
            self.assertNotIn("--print", fake_args)
            self.assertTrue(all(prompt not in argument for argument in fake_args))
            self.assertTrue(all(TEST_SENTINEL not in argument for argument in fake_args))
            self.assertEqual(fake_args[fake_args.index("--print-timeout") + 1], "30m")
            self.assertEqual(fake_args[fake_args.index("--model") + 1], "gemini-3.7-flash-high")
            self.assertEqual(fake_args[fake_args.index("--mode") + 1], "plan")
            self.assertEqual(fake_args[fake_args.index("--output-format") + 1], "stream-json")
            self.assertEqual(fake_args[fake_args.index("--add-dir") + 1], str(prompt_path.parent))
            self.assertIn("--sandbox", fake_args)
            self.assertNotIn("--disable-slash-commands", fake_args)
            self.assertNotIn("--conversation", fake_args)
            self.assertNotIn("--continue", fake_args)
            self.assertNotIn("--dangerously-skip-permissions", fake_args)
            self.assertNotIn("--effort", fake_args)
            prompt_sentinel, separator, prompt_body = prompt_text.partition("\n")
            self.assertEqual(separator, "\n")
            self.assertTrue(prompt_sentinel.startswith("TRAYCER_PROMPT_SENTINEL_"))
            self.assertEqual(prompt_body, prompt)
            self.assertTrue(prompt_path.exists())
            self.assertFalse(marker.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(sorted(path.name for path in workspace.iterdir()), ["sentinel.txt"])

    def test_resume_uses_explicit_conversation_and_shorter_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(
                fake_agy,
                workspace,
                print_timeout="10m",
                conversation="conversation-123",
            )

            self.assertEqual(result.returncode, 0)
            events = parsed_events(result.stdout)
            fake_args = next(event["args"] for event in events if event.get("event") == "fake_args")
            self.assertIsInstance(fake_args, list)
            fake_args = cast(list[str], fake_args)
            self.assertEqual(fake_args[fake_args.index("--conversation") + 1], "conversation-123")
            self.assertEqual(fake_args[fake_args.index("--print-timeout") + 1], "10m")

    def test_path_fallback_resolves_fake_agy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            binary_directory = root / "bin"
            binary_directory.mkdir()
            fake_agy = create_fake_agy(binary_directory)
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(fake_agy, workspace, use_override=False)

            self.assertEqual(result.returncode, 0)
            self.assertEqual(parsed_events(result.stdout)[-1]["conversation_id"], "conversation-123")

    def test_output_shape_preserves_agy_stream_stderr_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()
            result = run_relay(
                fake_agy,
                workspace,
                extra_env={
                    "FAKE_AGY_INCLUDE_ARGS": "0",
                    "FAKE_AGY_STDERR": "agy stderr\n",
                    "FAKE_AGY_EXIT": "7",
                },
            )

            self.assertEqual(result.returncode, 7)
            events = parsed_events(result.stdout)
            original_stdout = b"".join(event_line(event) for event in events[:-1])
            self.assertTrue(result.stdout.startswith(original_stdout))
            self.assertEqual(result.stdout.count(b'"event":"relay_metadata"'), 1)
            self.assertEqual(result.stderr, b"agy stderr\n")
            metadata = events[-1]
            self.assertEqual(metadata["event"], "relay_metadata")
            self.assertEqual(metadata["conversation_id"], "conversation-123")
            self.assertEqual(metadata["status"], "SUCCESS")
            self.assertEqual(metadata["usage"], {"total_tokens": 42})
            self.assertEqual(metadata["agy_exit_code"], 7)
            self.assertEqual(metadata["protocol_errors"], [])

    def test_protocol_failure_takes_precedence_and_emits_metadata(self) -> None:
        for mode in ("missing", "conflict", "malformed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                fake_agy = create_fake_agy(root / "bin")
                workspace = root / "workspace"
                workspace.mkdir()

                result = run_relay(
                    fake_agy,
                    workspace,
                    extra_env={"FAKE_AGY_MODE": mode, "FAKE_AGY_EXIT": "9"},
                )

                self.assertEqual(result.returncode, PROTOCOL_EXIT)
                metadata: object = json.loads(result.stdout.splitlines()[-1])
                self.assertIsInstance(metadata, dict)
                metadata = cast(dict[str, object], metadata)
                self.assertEqual(metadata["event"], "relay_metadata")
                self.assertEqual(metadata["agy_exit_code"], 9)
                self.assertTrue(metadata["protocol_errors"])

    def test_unterminated_stream_emits_diagnostic_instead_of_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(
                fake_agy,
                workspace,
                extra_env={"FAKE_AGY_MODE": "unterminated", "FAKE_AGY_EXIT": "11"},
            )

            self.assertEqual(result.returncode, PROTOCOL_EXIT)
            self.assertEqual(result.stdout, b"{broken")
            self.assertNotIn(b"relay_metadata", result.stdout)
            self.assertIn(b"agy exit code 11", result.stderr)

    def test_init_only_stream_is_a_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(
                fake_agy,
                workspace,
                extra_env={"FAKE_AGY_MODE": "init-only", "FAKE_AGY_INCLUDE_ARGS": "0"},
            )

            self.assertEqual(result.returncode, PROTOCOL_EXIT)
            metadata = parsed_events(result.stdout)[-1]
            self.assertEqual(metadata["conversation_id"], "conversation-123")
            self.assertEqual(metadata["protocol_errors"], ["stream contains no result event"])

    def test_prompt_file_configuration_errors_use_exit_64(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_agy = create_fake_agy(root / "bin")
            missing_parent = root / "missing-case"
            missing_parent.mkdir()
            missing = missing_parent / "reviewer-prompt.md"
            directory_parent = root / "directory-case"
            directory_parent.mkdir()
            directory = directory_parent / "reviewer-prompt.md"
            directory.mkdir()
            oversized_parent = root / "oversized-case"
            oversized_parent.mkdir()
            oversized = oversized_parent / "reviewer-prompt.md"
            oversized.write_bytes(
                f"{TEST_SENTINEL}\n".encode("ascii") + b"x" * (MAX_PROMPT_FILE_BYTES + 1 - len(TEST_SENTINEL) - 1)
            )
            malformed_sentinel = create_prompt_file(
                root / "malformed-sentinel-case",
                sentinel="NOT_A_SENTINEL",
            )
            invalid_sentinel_parent = root / "invalid-sentinel-encoding-case"
            invalid_sentinel_parent.mkdir()
            invalid_sentinel_encoding = invalid_sentinel_parent / "reviewer-prompt.md"
            invalid_sentinel_encoding.write_bytes(b"\xff\nReview")
            unterminated_sentinel_parent = root / "unterminated-sentinel-case"
            unterminated_sentinel_parent.mkdir()
            unterminated_sentinel = unterminated_sentinel_parent / "reviewer-prompt.md"
            unterminated_sentinel.write_text(TEST_SENTINEL, encoding="ascii")

            cases: tuple[tuple[str, Path, bytes], ...] = (
                ("relative", Path("relative-prompt.md"), b"prompt file path must be absolute"),
                ("missing", missing, b"prompt file is unavailable:"),
                ("directory", directory, b"dedicated prompt directory entry is not a regular file:"),
                ("oversized", oversized, b"maximum is 16384 bytes"),
                (
                    "malformed sentinel",
                    malformed_sentinel,
                    b"prompt artifact first line is not a well-formed sentinel",
                ),
                (
                    "invalid sentinel encoding",
                    invalid_sentinel_encoding,
                    b"prompt artifact first line is not a well-formed sentinel",
                ),
                (
                    "unterminated sentinel",
                    unterminated_sentinel,
                    b"prompt artifact first line is not a well-formed sentinel",
                ),
            )

            for name, prompt_file, expected_diagnostic in cases:
                with self.subTest(name=name):
                    result = run_relay(
                        fake_agy,
                        workspace,
                        prompt_file=prompt_file,
                    )

                    self.assertEqual(result.returncode, CONFIGURATION_EXIT)
                    self.assertIn(b"agy relay configuration error:", result.stderr)
                    self.assertIn(expected_diagnostic, result.stderr)

            self.assertIn(
                str(MAX_PROMPT_FILE_BYTES + 1).encode("ascii"),
                run_relay(
                    fake_agy,
                    workspace,
                    prompt_file=oversized,
                ).stderr,
            )

    def test_dedicated_prompt_directory_rules_use_exit_64(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_agy = create_fake_agy(root / "bin")

            extra_entry_prompt = create_prompt_file(root / "extra-entry-case")
            (extra_entry_prompt.parent / ".DS_Store").write_text("metadata", encoding="utf-8")

            symlink_parent = root / "symlink-case"
            symlink_parent.mkdir()
            symlink_target = root / "symlink-target.md"
            symlink_target.write_text(f"{TEST_SENTINEL}\nReview", encoding="utf-8")
            symlink_prompt = symlink_parent / "reviewer-prompt.md"
            symlink_prompt.symlink_to(symlink_target)

            wrong_entry_parent = root / "wrong-entry-case"
            wrong_entry_parent.mkdir()
            (wrong_entry_parent / "other.md").write_text("other", encoding="utf-8")
            wrong_entry_prompt = wrong_entry_parent / "reviewer-prompt.md"

            cases = (
                (extra_entry_prompt, b"unexpected entries: .DS_Store"),
                (symlink_prompt, b"entry is not a regular file: reviewer-prompt.md"),
                (wrong_entry_prompt, b"wrong entry: other.md; expected reviewer-prompt.md"),
            )
            for prompt_file, expected_diagnostic in cases:
                with self.subTest(prompt_file=prompt_file):
                    result = run_relay(fake_agy, workspace, prompt_file=prompt_file)

                    self.assertEqual(result.returncode, CONFIGURATION_EXIT)
                    self.assertIn(expected_diagnostic, result.stderr)

    @unittest.skipUnless(
        filesystem_is_case_insensitive(),
        "requires a case-insensitive filesystem",
    )
    def test_case_variant_prompt_spelling_is_accepted_on_case_insensitive_filesystems(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompt_directory = Path(temporary_directory)
            on_disk_prompt = prompt_directory / "Reviewer-Prompt.md"
            on_disk_prompt.write_text(f"{TEST_SENTINEL}\nReview", encoding="utf-8")
            requested_prompt = prompt_directory / "reviewer-prompt.md"

            resolved, sentinel, _identity = relay_module.__dict__["read_prompt_sentinel"](requested_prompt)

            self.assertEqual(resolved, on_disk_prompt.resolve())
            self.assertEqual(sentinel, TEST_SENTINEL)

    def test_case_variant_prompt_spelling_follows_samefile_on_every_filesystem(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompt_directory = Path(temporary_directory)
            on_disk_prompt = prompt_directory / "Reviewer-Prompt.md"
            on_disk_prompt.write_text(f"{TEST_SENTINEL}\nReview", encoding="utf-8")
            requested_prompt = prompt_directory / "reviewer-prompt.md"
            resolved_directory = prompt_directory.resolve()
            on_disk_entry = resolved_directory / on_disk_prompt.name
            expected_entry = resolved_directory / requested_prompt.name
            path_exists = Path.exists

            def exists_with_case_alias(path: Path) -> bool:
                if path == expected_entry:
                    return True
                return path_exists(path)

            with (
                mock.patch.object(Path, "exists", autospec=True, side_effect=exists_with_case_alias),
                mock.patch.object(relay_module.os.path, "samefile", return_value=True) as samefile,
            ):
                resolved, sentinel, _identity = relay_module.__dict__["read_prompt_sentinel"](requested_prompt)

            samefile.assert_called_once_with(on_disk_entry, expected_entry)
            self.assertEqual(resolved, on_disk_prompt.resolve())
            self.assertEqual(sentinel, TEST_SENTINEL)

            stderr = io.StringIO()
            with (
                mock.patch.object(Path, "exists", autospec=True, side_effect=exists_with_case_alias),
                mock.patch.object(relay_module.os.path, "samefile", return_value=False) as samefile,
                mock.patch.object(
                    relay_module,
                    "parse_args",
                    return_value=argparse.Namespace(
                        conversation=None,
                        prompt_file=requested_prompt,
                        print_timeout="30m",
                    ),
                ),
                mock.patch.object(relay_module.sys, "stderr", stderr),
            ):
                return_code = relay_module.__dict__["main"]()

            samefile.assert_called_once_with(on_disk_entry, expected_entry)
            self.assertEqual(return_code, CONFIGURATION_EXIT)
            self.assertEqual(
                stderr.getvalue(),
                "agy relay configuration error: dedicated prompt directory contains wrong entry: "
                "Reviewer-Prompt.md; expected reviewer-prompt.md\n",
            )

    def test_different_prompt_filename_is_rejected(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompt_directory = Path(temporary_directory)
            (prompt_directory / "other.md").write_text("other", encoding="utf-8")
            requested_prompt = prompt_directory / "reviewer-prompt.md"

            with self.assertRaises(relay_module.__dict__["RelayConfigurationError"]) as raised:
                relay_module.__dict__["read_prompt_sentinel"](requested_prompt)

            self.assertEqual(
                str(raised.exception),
                "dedicated prompt directory contains wrong entry: other.md; expected reviewer-prompt.md",
            )

    def test_symlink_prompt_entry_is_rejected(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            prompt_directory = root / "prompt-artifact"
            prompt_directory.mkdir()
            target = root / "target.md"
            target.write_text(f"{TEST_SENTINEL}\nReview", encoding="utf-8")
            prompt_file = prompt_directory / "reviewer-prompt.md"
            prompt_file.symlink_to(target)

            with self.assertRaises(relay_module.__dict__["RelayConfigurationError"]) as raised:
                relay_module.__dict__["read_prompt_sentinel"](prompt_file)

            self.assertEqual(
                str(raised.exception),
                "dedicated prompt directory entry is not a regular file: reviewer-prompt.md",
            )

    def test_prompt_escape_guard_rejects_resolved_parent_mismatch(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaises(relay_module.__dict__["RelayConfigurationError"]) as raised:
                relay_module.__dict__["ensure_prompt_stays_in_directory"](
                    root / "outside/reviewer-prompt.md",
                    root / "dedicated",
                )

        self.assertIn("prompt file resolves outside its dedicated directory", str(raised.exception))

    def test_exact_prompt_size_ceiling_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()
            prompt_file = create_prompt_file(root / "exact-size-case")
            prefix_size = prompt_file.stat().st_size
            with prompt_file.open("a", encoding="utf-8") as handle:
                handle.write("x" * (MAX_PROMPT_FILE_BYTES - prefix_size))

            self.assertEqual(prompt_file.stat().st_size, MAX_PROMPT_FILE_BYTES)
            result = run_relay(fake_agy, workspace, prompt_file=prompt_file)

            self.assertEqual(result.returncode, 0)

    def test_helper_reads_only_the_sentinel_line(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompt_file = Path(temporary_directory) / "prompt-with-non-utf8-body.md"
            prompt_file.write_bytes(f"{TEST_SENTINEL}\n".encode("ascii") + b"\xff\x00")

            resolved, sentinel, _identity = relay_module.__dict__["read_prompt_sentinel"](prompt_file)

            self.assertEqual(resolved, prompt_file.resolve())
            self.assertEqual(sentinel, TEST_SENTINEL)

    def test_unreadable_prompt_file_is_a_configuration_error(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompt_file = create_prompt_file(Path(temporary_directory))
            with (
                mock.patch.object(Path, "open", side_effect=PermissionError("denied")),
                self.assertRaises(relay_module.__dict__["RelayConfigurationError"]) as raised,
            ):
                relay_module.__dict__["read_prompt_sentinel"](prompt_file)

            self.assertIn("prompt file is not readable", str(raised.exception))

    def test_prompt_change_before_spawn_uses_exit_64_without_invoking_agy(self) -> None:
        relay_module = load_relay_module()
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompt_file = create_prompt_file(Path(temporary_directory) / "prompt-artifact")
            stderr = io.StringIO()

            def mutate_prompt_before_returning_agy() -> Path:
                prompt_file.write_text(f"{TEST_SENTINEL}\nCHANGED BEFORE SPAWN", encoding="utf-8")
                return Path("/bin/echo")

            with (
                mock.patch.object(
                    relay_module,
                    "parse_args",
                    return_value=argparse.Namespace(
                        conversation=None,
                        prompt_file=prompt_file,
                        print_timeout="30m",
                    ),
                ),
                mock.patch.object(
                    relay_module,
                    "resolve_agy",
                    side_effect=mutate_prompt_before_returning_agy,
                ),
                mock.patch.object(relay_module.subprocess, "run") as run_agy,
                mock.patch.object(relay_module.sys, "stderr", stderr),
            ):
                return_code = relay_module.__dict__["main"]()

            self.assertEqual(return_code, CONFIGURATION_EXIT)
            self.assertIn("prompt file changed before reviewer invocation", stderr.getvalue())
            run_agy.assert_not_called()

    def test_prompt_change_during_execution_uses_exit_65(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(
                fake_agy,
                workspace,
                extra_env={"FAKE_AGY_MODE": "mutate-prompt"},
            )

            self.assertEqual(result.returncode, PROTOCOL_EXIT)
            metadata = parsed_events(result.stdout)[-1]
            protocol_errors = metadata["protocol_errors"]
            self.assertIsInstance(protocol_errors, list)
            protocol_errors = cast(list[str], protocol_errors)
            self.assertEqual(len(protocol_errors), 1)
            self.assertIn("prompt file changed during reviewer execution", protocol_errors[0])

    def test_unavailable_agy_errors_use_exit_69(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            workspace.mkdir()
            fake_agy = create_fake_agy(root / "bin")
            missing_agy = root / "missing-agy"
            non_executable_agy = root / "non-executable-agy"
            non_executable_agy.write_text("not executable", encoding="utf-8")

            cases: tuple[tuple[str, Path, bool, dict[str, str] | None], ...] = (
                ("missing PATH", fake_agy, False, {"PATH": ""}),
                ("missing override", missing_agy, True, None),
                ("non-executable override", non_executable_agy, True, None),
            )

            for name, agy, use_override, extra_env in cases:
                with self.subTest(name=name):
                    result = run_relay(
                        agy,
                        workspace,
                        use_override=use_override,
                        extra_env=extra_env,
                    )

                    self.assertEqual(result.returncode, INVOCATION_EXIT)
                    self.assertIn(b"agy relay unavailable:", result.stderr)

    def test_blank_conversation_ids_use_exit_64(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            for conversation in ("", "   "):
                with self.subTest(conversation=conversation):
                    result = run_relay(fake_agy, workspace, conversation=conversation)

                    self.assertEqual(result.returncode, CONFIGURATION_EXIT)
                    self.assertIn(b"conversation ID must not be blank", result.stderr)

    def test_spawn_oserror_uses_exit_69(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            invalid_executable = root / "invalid-executable"
            invalid_executable.write_bytes(b"not an executable format")
            invalid_executable.chmod(0o755)
            workspace = root / "workspace"
            workspace.mkdir()
            prompt_file = create_prompt_file(root / "prompt-artifacts")

            result = run_relay(invalid_executable, workspace, prompt_file=prompt_file)

            self.assertEqual(result.returncode, INVOCATION_EXIT)
            self.assertIn(b"agy relay invocation error:", result.stderr)
            self.assertTrue(prompt_file.exists())

    def test_missing_prompt_sentinel_is_a_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(
                fake_agy,
                workspace,
                extra_env={"FAKE_AGY_MODE": "missing-sentinel"},
            )

            self.assertEqual(result.returncode, PROTOCOL_EXIT)
            metadata = parsed_events(result.stdout)[-1]
            protocol_errors = metadata["protocol_errors"]
            self.assertIsInstance(protocol_errors, list)
            protocol_errors = cast(list[str], protocol_errors)
            self.assertIn("result response does not begin with the prompt sentinel", protocol_errors)

    def test_polluted_prompt_sentinel_line_is_a_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(
                fake_agy,
                workspace,
                extra_env={"FAKE_AGY_MODE": "polluted-sentinel"},
            )

            self.assertEqual(result.returncode, PROTOCOL_EXIT)
            metadata = parsed_events(result.stdout)[-1]
            self.assertEqual(metadata["conversation_id"], "conversation-123")
            self.assertEqual(
                metadata["protocol_errors"],
                ["result response does not begin with the prompt sentinel"],
            )

    def test_whitespace_stream_conversation_id_is_a_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            fake_agy = create_fake_agy(root / "bin")
            workspace = root / "workspace"
            workspace.mkdir()

            result = run_relay(
                fake_agy,
                workspace,
                extra_env={"FAKE_AGY_CONVERSATION_ID": "   "},
            )

            self.assertEqual(result.returncode, PROTOCOL_EXIT)
            metadata = parsed_events(result.stdout)[-1]
            self.assertIsNone(metadata["conversation_id"])
            protocol_errors = metadata["protocol_errors"]
            self.assertIsInstance(protocol_errors, list)
            protocol_errors = cast(list[str], protocol_errors)
            self.assertIn("init conversation_id is not a non-empty string", protocol_errors)
            self.assertIn("result conversation_id is not a non-empty string", protocol_errors)


if __name__ == "__main__":
    unittest.main()
