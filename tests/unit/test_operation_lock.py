"""Exercise actual OS process exclusion and release, without model calls."""

import subprocess
import sys

import pytest
from proofops_agent.operation_lock import operation_lock

CHILD = """
import sys
from pathlib import Path
from proofops_agent.operation_lock import operation_lock
with operation_lock(Path(sys.argv[1])) as acquired:
    print('acquired' if acquired else 'busy')
"""


def child_result(path):
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    return result.stdout.strip()


def test_process_exclusion_and_release_after_error(tmp_path):
    path = tmp_path / ".operation.lock"
    with pytest.raises(RuntimeError, match="interrupted"):
        with operation_lock(path) as acquired:
            assert acquired
            assert child_result(path) == "busy"
            raise RuntimeError("interrupted")
    assert path.exists()
    assert child_result(path) == "acquired"


def test_reopen_locks_same_byte_even_if_file_is_not_empty(tmp_path):
    path = tmp_path / ".operation.lock"
    path.write_bytes(b"retained lock file")
    with operation_lock(path) as acquired:
        assert acquired
        assert child_result(path) == "busy"
    assert child_result(path) == "acquired"
    assert path.read_bytes() == b"retained lock file"
