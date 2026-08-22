#!/usr/bin/env python3
"""Validate sync-traycer-notion evidence with the digest-bound runner logic."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

RUNNER_PATH = Path(__file__).with_name("run-trigger-evals.py")


def load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_traycer_notion_contract_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load runner from {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RUNNER = load_runner()
EvidenceContractError = _RUNNER.EvidenceContractError
MAX_SAFE_INTEGER = _RUNNER.MAX_SAFE_INTEGER
canonical_json_bytes = _RUNNER.canonical_json_bytes
canonical_json_sha256 = _RUNNER.canonical_json_sha256
parse_canonical_json = _RUNNER.parse_canonical_json
read_canonical_json = _RUNNER.read_canonical_json
validate_canonical_value = _RUNNER.validate_canonical_value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        digests = {str(path): canonical_json_sha256(read_canonical_json(path)) for path in arguments.paths}
    except (EvidenceContractError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for path, digest in digests.items():
        print(f"{digest}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
