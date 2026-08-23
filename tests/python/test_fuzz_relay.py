from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import ModuleType

from hypothesis import given, settings
from hypothesis import strategies as st

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELAY = REPOSITORY_ROOT / "skills/orchestrate-risk-scaled-review/scripts/agy_review_relay.py"
SENTINEL = "TRAYCER_PROMPT_SENTINEL_abcdefghijklmnopqrstuvwxyzABCDEF"


def load_relay_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agy_review_relay_fuzz_target", RELAY)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load relay module from {RELAY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RelayFuzzTests(unittest.TestCase):
    @settings(max_examples=200, deadline=None)
    @given(stdout=st.binary(max_size=4096), exit_code=st.integers(min_value=0, max_value=255))
    def test_arbitrary_stream_bytes_produce_bounded_json_metadata(self, stdout: bytes, exit_code: int) -> None:
        relay = load_relay_module()
        metadata, has_errors = relay.parse_stream_metadata(stdout, exit_code, SENTINEL)
        encoded = relay.encode_metadata(metadata)

        self.assertEqual(json.loads(encoded), metadata)
        self.assertEqual(has_errors, bool(metadata["protocol_errors"]))
        self.assertLessEqual(len(encoded), 64 * 1024)


if __name__ == "__main__":
    unittest.main()
