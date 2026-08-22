#!/usr/bin/env python3
"""Run private recovery checks for sync-traycer-notion evaluation evidence."""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import stat
import subprocess  # nosec B404 - fixed GnuPG argv and the local grader are required for recovery.
import sys
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import cast

RUNNER_PATH = Path(__file__).with_name("run-trigger-evals.py")


class VerificationError(RuntimeError):
    """Raised when private evidence recovery fails."""


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_traycer_notion_private_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise VerificationError(f"could not load runner from {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
                raise VerificationError(f"unsafe archive path: {member.filename}")
            file_type = (member.external_attr >> 16) & 0o170000
            if file_type not in {0, stat.S_IFDIR, stat.S_IFREG}:
                raise VerificationError(f"archive contains a non-regular entry: {member.filename}")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.is_dir():
                target.mkdir(exist_ok=True)
                continue
            with bundle.open(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def verify_record_paths(runner: ModuleType, run_manifest_path: Path) -> None:
    run_manifest = runner.read_json(run_manifest_path)
    for index, record in enumerate(runner.require_list(run_manifest.get("records"), "records")):
        if not isinstance(record, dict):
            raise VerificationError(f"record {index} is not an object")
        for field in ("response", "trace", "stderr"):
            path_field = f"{field}_path"
            digest_field = f"{field}_sha256"
            path = runner.resolve_evidence_path(run_manifest_path.parent, record.get(path_field), path_field)
            if sha256_file(path) != record.get(digest_field):
                raise VerificationError(f"{digest_field} mismatch for record {index}")


def verify_archive(arguments: argparse.Namespace) -> int:
    archive = arguments.archive.expanduser().resolve(strict=True)
    expected_size = arguments.raw_evidence_size
    if archive.stat().st_size != expected_size:
        raise VerificationError("raw evidence archive size mismatch")
    if sha256_file(archive) != arguments.raw_evidence_sha256:
        raise VerificationError("raw evidence archive digest mismatch")
    key = arguments.key.expanduser().resolve(strict=True)
    output = arguments.output.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="sync-notion-evidence-") as temporary_directory:
        extracted = Path(temporary_directory) / "archive"
        extracted.mkdir()
        safe_extract_zip(archive, extracted)
        run_manifest = extracted / "run-manifest.json"
        key_manifest = extracted / "key-manifest.json"
        if not run_manifest.is_file() or not key_manifest.is_file():
            raise VerificationError("archive must contain run-manifest.json and key-manifest.json at its root")
        runner = load_runner()
        verify_record_paths(runner, run_manifest)
        namespace = argparse.Namespace(
            key=key,
            key_manifest=key_manifest,
            output=output,
            private_asset_name=arguments.private_asset_name,
            private_release_tag=arguments.private_release_tag,
            raw_evidence_sha256=arguments.raw_evidence_sha256,
            raw_evidence_size=expected_size,
            run_manifest=run_manifest,
        )
        return cast(Callable[[argparse.Namespace], int], runner.grade_suite)(namespace)


def run_checked(command: list[str], label: str) -> bytes:
    completed = subprocess.run(command, capture_output=True, check=False)  # nosec B603 - fixed argv only.
    if completed.returncode != 0:
        raise VerificationError(f"{label} failed with exit {completed.returncode}")
    return completed.stdout


def recover_key(arguments: argparse.Namespace) -> int:
    ciphertext = arguments.ciphertext.expanduser().resolve(strict=True)
    secret_key = arguments.secret_key.expanduser().resolve(strict=True)
    passphrase_file = arguments.passphrase_file.expanduser().resolve(strict=True)
    runner = load_runner()
    key_manifest = runner.read_canonical_json(arguments.key_manifest.expanduser().resolve(strict=True))
    if not isinstance(key_manifest, dict):
        raise VerificationError("key manifest must be an object")
    if sha256_file(ciphertext) != key_manifest.get("ciphertext_sha256"):
        raise VerificationError("encrypted key digest mismatch")
    with tempfile.TemporaryDirectory(prefix="sync-notion-gnupg-") as temporary_directory:
        gnupg_home = Path(temporary_directory) / "gnupg"
        gnupg_home.mkdir(mode=0o700)
        plaintext = Path(temporary_directory) / "key.json"
        try:
            run_checked(
                [arguments.gpg_bin, "--homedir", str(gnupg_home), "--batch", "--import", str(secret_key)],
                "secret-key import",
            )
            listing = run_checked(
                [
                    arguments.gpg_bin,
                    "--homedir",
                    str(gnupg_home),
                    "--batch",
                    "--with-colons",
                    "--fingerprint",
                    "--list-secret-keys",
                ],
                "secret-key fingerprint listing",
            ).decode("utf-8", errors="strict")
            fingerprints = {
                fields[9]
                for line in listing.splitlines()
                if (fields := line.split(":"))[0] == "fpr" and len(fields) > 9
            }
            if key_manifest.get("recipient_fingerprint") not in fingerprints:
                raise VerificationError("recovery identity does not match the sealed recipient fingerprint")
            run_checked(
                [
                    arguments.gpg_bin,
                    "--homedir",
                    str(gnupg_home),
                    "--batch",
                    "--yes",
                    "--pinentry-mode",
                    "loopback",
                    "--passphrase-file",
                    str(passphrase_file),
                    "--output",
                    str(plaintext),
                    "--decrypt",
                    str(ciphertext),
                ],
                "key decryption",
            )
            recovered = runner.read_canonical_json(plaintext)
            if runner.canonical_json_sha256(recovered) != key_manifest.get("key_sha256"):
                raise VerificationError("recovered plaintext key digest mismatch")
        finally:
            subprocess.run(  # nosec B603 - fixed local teardown command.
                [arguments.gpgconf_bin, "--homedir", str(gnupg_home), "--kill", "all"],
                capture_output=True,
                check=False,
            )
    print("recovered key matches the sealed canonical digest")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    archive_parser = subparsers.add_parser("archive", help="verify and re-grade a downloaded archive")
    archive_parser.add_argument("--archive", type=Path, required=True)
    archive_parser.add_argument("--raw-evidence-sha256", required=True)
    archive_parser.add_argument("--raw-evidence-size", type=int, required=True)
    archive_parser.add_argument("--key", type=Path, required=True)
    archive_parser.add_argument("--output", type=Path, required=True)
    archive_parser.add_argument("--private-release-tag", required=True)
    archive_parser.add_argument("--private-asset-name", required=True)
    archive_parser.set_defaults(handler=verify_archive)

    key_parser = subparsers.add_parser("recover-key", help="verify GPG recovery in a temporary home")
    key_parser.add_argument("--ciphertext", type=Path, required=True)
    key_parser.add_argument("--key-manifest", type=Path, required=True)
    key_parser.add_argument("--secret-key", type=Path, required=True)
    key_parser.add_argument("--passphrase-file", type=Path, required=True)
    key_parser.add_argument("--gpg-bin", default="gpg")
    key_parser.add_argument("--gpgconf-bin", default="gpgconf")
    key_parser.set_defaults(handler=recover_key)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        handler = arguments.handler
        return cast(Callable[[argparse.Namespace], int], handler)(arguments)
    except (OSError, ValueError, VerificationError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
