#!/usr/bin/env python3
# ruff: noqa: UP045  # Keep the verifier executable on Python 3.9.
"""Snapshot Git-authored content and verify writes stayed in package tests/ roots."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess  # nosec B404  # Git uses a fixed argv with shell=False.
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = "test-rust-boundary-snapshot-v3"
PACKAGE_HEADER = re.compile(r"^\s*\[\s*package\s*\]\s*(?:#.*)?$")
INVALID_CHANGED_KINDS = {"other", "symlink", "unreadable", "unreadable-directory"}
EXCLUDED_SCRATCH_DIRECTORIES = {".venv", "node_modules", "target"}
EXCLUDED_SCRATCH_FILES = {".ds_store"}


class BoundaryError(Exception):
    """Raised for invalid inputs or unsafe repository layouts."""


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _resolve_root(raw: str) -> Path:
    root = Path(raw).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise BoundaryError(f"workspace is not a directory: {root}")
    return root


def _assert_no_symlink_components(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BoundaryError(f"symlinked package/test path is not allowed: {cursor}")


def _manifest_has_package(manifest: Path) -> bool:
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BoundaryError(f"cannot read {manifest}: {exc}") from exc

    try:
        tomllib = importlib.import_module("tomllib")
    except ModuleNotFoundError:
        for line in text.splitlines():
            if '"""' in line or "'''" in line:
                raise BoundaryError(
                    f"Python 3.9/3.10 cannot safely inspect a multiline TOML manifest; "
                    f"use an already-installed Python 3.11+ for {manifest}"
                ) from None
            if PACKAGE_HEADER.fullmatch(line):
                return True
        return False

    try:
        parsed = tomllib.loads(text)
    except (TypeError, ValueError) as exc:
        raise BoundaryError(f"cannot parse {manifest}: {exc}") from exc
    return isinstance(parsed.get("package"), dict)


def _git_bytes(root: Path, arguments: Iterable[str]) -> bytes:
    command = ["git", "-C", str(root), *arguments]
    try:
        # The executable and argument forms are fixed; no shell is involved.
        result = subprocess.run(  # nosec B603
            command,
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise BoundaryError(f"cannot run Git: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise BoundaryError(f"Git command failed ({' '.join(command[3:])}): {detail}")
    return result.stdout


def _git_paths(root: Path, *arguments: str) -> set[str]:
    output = _git_bytes(root, arguments)
    return {os.fsdecode(item) for item in output.split(b"\0") if item}


def _assert_git_root(root: Path) -> None:
    output = _git_bytes(root, ["rev-parse", "--show-toplevel"])
    top_level = Path(os.fsdecode(output).strip()).resolve(strict=True)
    if top_level != root:
        raise BoundaryError(f"workspace must be the Git worktree root: {top_level}")


def _join_repo_path(prefix: str, relative: str) -> str:
    if not prefix:
        return Path(relative).as_posix()
    return (Path(prefix) / relative).as_posix()


def _local_gitlinks(root: Path) -> dict[str, str]:
    output = _git_bytes(root, ["ls-files", "--stage", "-z"])
    gitlinks: dict[str, str] = {}
    for item in output.split(b"\0"):
        if not item.startswith(b"160000 ") or b"\t" not in item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        fields = metadata.split()
        if len(fields) >= 2:
            gitlinks[os.fsdecode(raw_path)] = os.fsdecode(fields[1])
    return gitlinks


def _resolve_gitlink_repository(
    root: Path,
    repository: Path,
    relative: str,
    logical: str,
    visited: set[Path],
) -> Optional[Path]:
    candidate = repository / relative
    if not candidate.is_dir():
        return None
    resolved = candidate.resolve(strict=True)
    if not _inside(resolved, root):
        raise BoundaryError(f"submodule escapes the workspace: {logical}")
    try:
        top_level = Path(os.fsdecode(_git_bytes(resolved, ["rev-parse", "--show-toplevel"])).strip()).resolve(
            strict=True
        )
    except BoundaryError:
        return None
    if top_level != resolved or resolved in visited:
        return None
    return resolved


def _git_repositories(root: Path) -> tuple[list[tuple[str, Path]], dict[str, str], list[str]]:
    repositories: list[tuple[str, Path]] = [("", root)]
    gitlinks: dict[str, str] = {}
    unavailable: list[str] = []
    visited = {root}
    index = 0
    while index < len(repositories):
        prefix, repository = repositories[index]
        index += 1
        for relative, object_id in _local_gitlinks(repository).items():
            logical = _join_repo_path(prefix, relative)
            gitlinks[logical] = object_id
            resolved = _resolve_gitlink_repository(root, repository, relative, logical, visited)
            if resolved is None:
                unavailable.append(logical)
                continue
            visited.add(resolved)
            repositories.append((logical, resolved))
    return repositories, dict(sorted(gitlinks.items())), sorted(unavailable)


def _repository_state(repositories: list[tuple[str, Path]]) -> dict[str, list[str]]:
    merged: dict[str, set[str]] = {"tracked": set(), "modified": set(), "staged": set(), "untracked": set()}
    for prefix, root in repositories:
        local = {
            "tracked": _git_paths(root, "ls-files", "-z"),
            "modified": _git_paths(root, "diff", "--name-only", "-z"),
            "staged": _git_paths(root, "diff", "--cached", "--name-only", "-z"),
            "untracked": _git_paths(root, "ls-files", "--others", "--exclude-standard", "-z"),
        }
        for key, paths in local.items():
            merged[key].update(_join_repo_path(prefix, path) for path in paths)
    return {key: sorted(paths) for key, paths in merged.items()}


def _ignored_roots(repositories: list[tuple[str, Path]]) -> list[str]:
    ignored: list[str] = []
    for prefix, root in repositories:
        output = _git_bytes(
            root,
            ["status", "--ignored", "--porcelain=v1", "--untracked-files=normal", "-z"],
        )
        for item in output.split(b"\0"):
            if item.startswith(b"!! "):
                ignored.append(_join_repo_path(prefix, os.fsdecode(item[3:])))
    return sorted(ignored)


def _is_builtin_scratch(relative: str) -> bool:
    path = Path(relative)
    lowered = [part.casefold() for part in path.parts]
    if path.name.casefold() in EXCLUDED_SCRATCH_FILES:
        return True
    return any(part in EXCLUDED_SCRATCH_DIRECTORIES for part in lowered)


def _normalise_relative_path(raw: str, *, label: str) -> str:
    candidate = Path(raw)
    if candidate.is_absolute():
        raise BoundaryError(f"{label} must be repository-relative: {raw}")
    normalized = Path(os.path.normpath(raw.rstrip("/")))
    if normalized == Path(".") or ".." in normalized.parts:
        raise BoundaryError(f"{label} escapes the workspace: {raw}")
    return normalized.as_posix()


def _path_at_or_below(relative: str, raw_root: str) -> bool:
    path = Path(relative)
    root = Path(raw_root)
    return path == root or _inside(path, root)


def _matches_scratch_exclusion(relative: str, exclusions: Iterable[str]) -> bool:
    normalized = Path(relative.rstrip("/")).as_posix()
    return any(_path_at_or_below(normalized, raw_root) for raw_root in exclusions)


def _git_ignores_path(root: Path, relative: str) -> bool:
    for candidate in (relative, f"{relative.rstrip('/')}/"):
        command = ["git", "-C", str(root), "check-ignore", "--no-index", "-q", "--", candidate]
        try:
            result = subprocess.run(  # nosec B603
                command,
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise BoundaryError(f"cannot run Git: {exc}") from exc
        if result.returncode not in {0, 1}:
            detail = result.stderr.decode(errors="replace").strip()
            raise BoundaryError(f"Git check-ignore failed for {relative}: {detail}")
        if result.returncode == 0:
            return True
    return False


def _scratch_exclusions(root: Path, raw_paths: Iterable[str], test_roots: list[str]) -> list[str]:
    exclusions: set[str] = set()
    for raw in raw_paths:
        relative = _normalise_relative_path(raw, label="scratch exclusion")
        path = Path(relative)
        lowered = {part.casefold() for part in path.parts}
        protected_name = path.name.casefold()
        if (
            ".git" in lowered
            or ".cargo" in lowered
            or protected_name in {"cargo.lock", "cargo.toml"}
            or protected_name.startswith("rust-toolchain")
        ):
            raise BoundaryError(f"scratch exclusion targets protected repository content: {raw}")
        if any(
            _path_at_or_below(relative, test_root) or _path_at_or_below(test_root, relative) for test_root in test_roots
        ):
            raise BoundaryError(f"scratch exclusion overlaps an allowed tests root: {raw}")
        if not _git_ignores_path(root, relative):
            raise BoundaryError(f"scratch exclusion is not covered by Git ignore rules: {raw}")
        exclusions.add(relative)
    return sorted(exclusions)


def _package_test_roots(root: Path, lexical_root: Path, raw_roots: Iterable[str]) -> list[str]:
    resolved: set[Path] = set()
    for raw in raw_roots:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = lexical_root / candidate
        lexical = Path(os.path.abspath(candidate))
        package = candidate.resolve(strict=True)
        if not package.is_dir() or not _inside(package, root):
            raise BoundaryError(f"package root escapes workspace: {raw}")
        if _inside(lexical, lexical_root):
            _assert_no_symlink_components(lexical, lexical_root)
        else:
            _assert_no_symlink_components(package, root)
        manifest = package / "Cargo.toml"
        if not manifest.is_file() or manifest.is_symlink():
            raise BoundaryError(f"package root lacks a regular Cargo.toml: {package}")
        if not _manifest_has_package(manifest):
            raise BoundaryError(f"Cargo.toml is not a concrete package: {manifest}")
        tests = package / "tests"
        _assert_no_symlink_components(tests, root)
        resolved.add(tests)
    return sorted(path.relative_to(root).as_posix() for path in resolved)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _entry(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        return {"kind": "missing", "errno": exc.errno}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            return {"kind": "unreadable", "mode": mode, "errno": exc.errno}
        return {"kind": "symlink", "mode": mode, "target": target}
    if stat.S_ISDIR(info.st_mode):
        return {"kind": "directory", "mode": mode}
    if stat.S_ISREG(info.st_mode):
        try:
            digest = _sha256(path)
        except OSError as exc:
            return {
                "kind": "unreadable",
                "mode": mode,
                "size": info.st_size,
                "errno": exc.errno,
            }
        return {
            "kind": "file",
            "mode": mode,
            "size": info.st_size,
            "sha256": digest,
        }
    return {"kind": "other", "mode": mode, "file_type": stat.S_IFMT(info.st_mode)}


def _record_tree(root: Path, raw_root: str, entries: dict[str, dict[str, Any]]) -> None:
    tree_root = root / raw_root
    if not tree_root.exists() and not tree_root.is_symlink():
        return
    entries[raw_root] = _entry(tree_root)

    def on_error(error: OSError) -> None:
        if error.filename is None:
            return
        path = Path(error.filename)
        if _inside(path, root):
            entries[path.relative_to(root).as_posix()] = {
                "kind": "unreadable-directory",
                "errno": error.errno,
            }

    for current_raw, dirnames, filenames in os.walk(
        tree_root,
        topdown=True,
        followlinks=False,
        onerror=on_error,
    ):
        current = Path(current_raw)
        dirnames[:] = sorted(dirnames)
        for name in dirnames:
            path = current / name
            entries[path.relative_to(root).as_posix()] = _entry(path)
        for name in sorted(filenames):
            path = current / name
            entries[path.relative_to(root).as_posix()] = _entry(path)


def _snapshot_entries(
    root: Path,
    roots: list[str],
    state: dict[str, list[str]],
    ignored: list[str],
    scratch_exclusions: list[str],
    gitlinks: dict[str, str],
    unavailable_gitlinks: list[str],
) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    authored_paths = set(state["tracked"]) | set(state["untracked"])
    for relative in sorted(authored_paths):
        entries[relative] = _entry(root / relative)
    for relative, object_id in gitlinks.items():
        entry = entries.setdefault(relative, _entry(root / relative))
        entry["gitlink_oid"] = object_id
    for raw_root in ignored:
        if not _matches_scratch_exclusion(raw_root, scratch_exclusions):
            _record_tree(root, raw_root.rstrip("/"), entries)
    for raw_root in unavailable_gitlinks:
        _record_tree(root, raw_root, entries)
    for raw_root in roots:
        _record_tree(root, raw_root, entries)
    return dict(sorted(entries.items()))


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except FileExistsError as exc:
        raise BoundaryError(f"refusing to overwrite baseline: {path}") from exc


def _load_baseline(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BoundaryError(f"cannot read baseline {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise BoundaryError(f"unsupported baseline schema: {path}")
    if not isinstance(payload.get("entries"), dict):
        raise BoundaryError(f"baseline entries are invalid: {path}")
    roots = payload.get("allowed_test_roots")
    if not isinstance(roots, list) or not roots or not all(isinstance(item, str) for item in roots):
        raise BoundaryError(f"baseline test roots are invalid: {path}")
    clean_files = payload.get("clean_tracked_test_files")
    if not isinstance(clean_files, list) or not all(isinstance(item, str) for item in clean_files):
        raise BoundaryError(f"baseline clean-test inventory is invalid: {path}")
    scratch_exclusions = payload.get("scratch_exclusions")
    if not isinstance(scratch_exclusions, list) or not all(isinstance(item, str) for item in scratch_exclusions):
        raise BoundaryError(f"baseline scratch exclusions are invalid: {path}")
    return payload


def _is_allowed(relative: str, roots: list[str]) -> bool:
    path = Path(relative)
    for raw_root in roots:
        root = Path(raw_root)
        if path == root or _inside(path, root):
            lowered_parts = {part.casefold() for part in path.parts}
            return path.name.casefold() != "cargo.toml" and ".git" not in lowered_parts
    return False


def _normalise_approved_deletions(raw_paths: Iterable[str], roots: list[str]) -> set[str]:
    approved: set[str] = set()
    for raw in raw_paths:
        relative = _normalise_relative_path(raw, label="approved deletion")
        if not _is_allowed(relative, roots):
            raise BoundaryError(f"approved deletion is outside an allowed tests root: {raw}")
        approved.add(relative)
    return approved


def _new_move_identity(
    relative: str,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    roots: list[str],
) -> Optional[tuple[str, int]]:
    previous = before.get(relative)
    current = after.get(relative)
    if previous and previous.get("kind") != "missing":
        return None
    if not current or current.get("kind") != "file" or not _is_allowed(relative, roots):
        return None
    size = int(current.get("size", -1))
    if size <= 0:
        return None
    return str(current.get("sha256")), size


def _clean_source_identity(
    source: str,
    before: dict[str, dict[str, Any]],
    clean_tracked: set[str],
    roots: list[str],
) -> Optional[tuple[str, int]]:
    if source not in clean_tracked:
        return None
    previous = before.get(source, {})
    if previous.get("kind") != "file" or not _is_allowed(source, roots):
        return None
    size = int(previous.get("size", -1))
    if size <= 0:
        return None
    return str(previous.get("sha256")), size


def _take_matching_destination(
    source: str,
    identity: tuple[str, int],
    destinations: dict[tuple[str, int], list[str]],
) -> Optional[str]:
    candidates = [
        candidate
        for candidate in destinations.get(identity, [])
        if Path(candidate).suffix.casefold() == Path(source).suffix.casefold()
    ]
    if not candidates:
        return None
    destination = candidates[0]
    destinations[identity].remove(destination)
    return destination


def _detect_clean_moves(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    changed: list[str],
    deleted_files: set[str],
    clean_tracked: set[str],
    roots: list[str],
) -> dict[str, str]:
    destinations: dict[tuple[str, int], list[str]] = {}
    for relative in changed:
        identity = _new_move_identity(relative, before, after, roots)
        if identity is not None:
            destinations.setdefault(identity, []).append(relative)

    moves: dict[str, str] = {}
    for source in sorted(deleted_files):
        identity = _clean_source_identity(source, before, clean_tracked, roots)
        if identity is not None:
            destination = _take_matching_destination(source, identity, destinations)
            if destination is None:
                continue
            moves[source] = destination
    return moves


def _print_ignored_notice(ignored: list[str]) -> None:
    if not ignored:
        return
    preview = ", ".join(ignored[:8])
    suffix = "" if len(ignored) <= 8 else f", ... (+{len(ignored) - 8})"
    print(f"ignored scratch excluded from enforcement: {preview}{suffix}")


def _print_move_notice(moves: dict[str, str]) -> None:
    for source, destination in sorted(moves.items()):
        print(f"clean move: {source} -> {destination}")


def _print_gitlink_notice(gitlinks: dict[str, str], unavailable: list[str]) -> None:
    if gitlinks:
        print(f"submodule gitlinks inspected: {', '.join(gitlinks)}")
    if unavailable:
        print(f"submodule worktrees unavailable; existing content hashed directly: {', '.join(unavailable)}")


def _snapshot_command(args: argparse.Namespace) -> int:
    lexical_root = Path(os.path.abspath(Path(args.workspace).expanduser()))
    root = _resolve_root(args.workspace)
    _assert_git_root(root)
    output = Path(args.output).expanduser().resolve()
    if _inside(output, root):
        raise BoundaryError("baseline output must be outside the workspace")
    roots = _package_test_roots(root, lexical_root, args.package_root)
    repositories, gitlinks, unavailable_gitlinks = _git_repositories(root)
    state = _repository_state(repositories)
    ignored = _ignored_roots(repositories)
    configured_exclusions = _scratch_exclusions(root, args.exclude_scratch, roots)
    automatic_exclusions = {Path(path.rstrip("/")).as_posix() for path in ignored if _is_builtin_scratch(path)}
    scratch_exclusions = sorted(set(configured_exclusions) | automatic_exclusions)
    entries = _snapshot_entries(
        root,
        roots,
        state,
        ignored,
        scratch_exclusions,
        gitlinks,
        unavailable_gitlinks,
    )
    symlinked_tests = sorted(
        relative
        for relative, entry in entries.items()
        if entry.get("kind") == "symlink" and _is_allowed(relative, roots)
    )
    if symlinked_tests:
        raise BoundaryError(f"symlink below an allowed tests root is not allowed: {symlinked_tests[0]}")
    dirty = set(state["modified"]) | set(state["staged"]) | set(state["untracked"])
    clean_tracked = sorted(
        relative for relative in state["tracked"] if relative not in dirty and _is_allowed(relative, roots)
    )
    excluded_scratch = sorted(path for path in ignored if _matches_scratch_exclusion(path, scratch_exclusions))
    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 -- Python 3.9 support.
        "workspace": str(root),
        "allowed_test_roots": roots,
        "clean_tracked_test_files": clean_tracked,
        "repository_state": state,
        "scratch_exclusions": scratch_exclusions,
        "ignored_scratch_roots": excluded_scratch,
        "gitlinks": gitlinks,
        "unavailable_gitlinks": unavailable_gitlinks,
        "entries": entries,
    }
    _write_json_exclusive(output, payload)
    print(f"snapshot: {len(payload['entries'])} entries; allowed={','.join(roots)}")
    _print_ignored_notice(excluded_scratch)
    _print_gitlink_notice(gitlinks, unavailable_gitlinks)
    return 0


def _deleted_files(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    changed: list[str],
) -> set[str]:
    return {
        relative
        for relative in changed
        if before.get(relative, {}).get("kind") == "file" and after.get(relative, {}).get("kind") != "file"
    }


def _changed_path_violation(
    relative: str,
    current: Optional[dict[str, Any]],
    roots: list[str],
    deletions: set[str],
    clean_tracked: set[str],
    approved: set[str],
) -> Optional[str]:
    if not _is_allowed(relative, roots):
        return relative
    if relative in deletions:
        if relative not in clean_tracked:
            return f"{relative} (baseline-dirty or untracked test deletion)"
        if relative not in approved:
            return f"{relative} (unapproved clean test deletion)"
    if current and current.get("kind") in INVALID_CHANGED_KINDS:
        return f"{relative} ({current.get('kind')})"
    return None


def _print_verification_notices(
    excluded_scratch: list[str],
    moves: dict[str, str],
    gitlinks: dict[str, str],
    unavailable_gitlinks: list[str],
) -> None:
    _print_ignored_notice(excluded_scratch)
    _print_move_notice(moves)
    _print_gitlink_notice(gitlinks, unavailable_gitlinks)


def _verify_command(args: argparse.Namespace) -> int:
    root = _resolve_root(args.workspace)
    _assert_git_root(root)
    baseline_path = Path(args.baseline).expanduser().resolve(strict=True)
    payload = _load_baseline(baseline_path)
    if payload.get("workspace") != str(root):
        raise BoundaryError("baseline belongs to a different workspace")
    roots = payload["allowed_test_roots"]
    scratch_exclusions = payload["scratch_exclusions"]
    for raw_root in roots:
        _assert_no_symlink_components(root / raw_root, root)

    approved = _normalise_approved_deletions(args.allow_test_deletion, roots)
    before = payload["entries"]
    repositories, gitlinks, unavailable_gitlinks = _git_repositories(root)
    state = _repository_state(repositories)
    ignored = _ignored_roots(repositories)
    after = _snapshot_entries(
        root,
        roots,
        state,
        ignored,
        scratch_exclusions,
        gitlinks,
        unavailable_gitlinks,
    )
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    clean_tracked = set(payload["clean_tracked_test_files"])
    deleted_files = _deleted_files(before, after, changed)
    moves = _detect_clean_moves(before, after, changed, deleted_files, clean_tracked, roots)
    deletions = deleted_files

    violations: list[str] = []
    new_builtin_scratch = sorted(
        Path(path.rstrip("/")).as_posix()
        for path in ignored
        if _is_builtin_scratch(path) and not _matches_scratch_exclusion(path, scratch_exclusions)
    )
    violations.extend(f"{path} (new scratch root appeared during the run)" for path in new_builtin_scratch)
    for relative in changed:
        violation = _changed_path_violation(
            relative,
            after.get(relative),
            roots,
            deletions,
            clean_tracked,
            approved,
        )
        if violation is not None:
            violations.append(violation)

    unused_approvals = sorted(approved - deletions)
    violations.extend(f"{path} (approved deletion was not observed)" for path in unused_approvals)

    excluded_scratch = sorted(path for path in ignored if _matches_scratch_exclusion(path, scratch_exclusions))
    if violations:
        print("boundary violation:", file=sys.stderr)
        for relative in violations:
            print(f"  {relative}", file=sys.stderr)
        print(f"clean moves={len(moves)}; approved deletions={len(approved)}")
        _print_verification_notices(excluded_scratch, moves, gitlinks, unavailable_gitlinks)
        return 1
    print(
        f"boundary ok: {len(changed)} changed entries within package tests/ roots; "
        f"clean moves={len(moves)}; approved deletions={len(approved)}"
    )
    _print_verification_notices(excluded_scratch, moves, gitlinks, unavailable_gitlinks)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="capture pre-task Git-authored content")
    snapshot.add_argument("workspace")
    snapshot.add_argument("--output", required=True)
    snapshot.add_argument("--package-root", action="append", default=[], required=True)
    snapshot.add_argument(
        "--exclude-scratch",
        action="append",
        default=[],
        metavar="REPO_RELATIVE_PATH",
        help="exact Git-ignored ambient scratch root to exclude; repeat as needed",
    )
    snapshot.set_defaults(handler=_snapshot_command)

    verify = subparsers.add_parser("verify", help="verify writes against a prior snapshot")
    verify.add_argument("workspace")
    verify.add_argument("--baseline", required=True)
    verify.add_argument(
        "--allow-test-deletion",
        action="append",
        default=[],
        metavar="REPO_RELATIVE_PATH",
        help="exact clean tracked test file whose deletion the user approved; repeat as needed",
    )
    verify.set_defaults(handler=_verify_command)
    return parser


def main(argv: Optional[list[str]] = None) -> int:  # noqa: UP045 -- Python 3.9 support.
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (BoundaryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
