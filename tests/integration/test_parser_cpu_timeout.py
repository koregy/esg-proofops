"""A kernel CPU timeout must not be reported as an opaque parser failure."""

import sys
from types import SimpleNamespace

import pytest
from proofops.adapters.parsing.opendataloader import OpenDataLoaderParser, ParseFailure


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX CPU limit signal")
def test_cpu_limit_reports_timeout_before_wall_deadline(tmp_path):
    profile = SimpleNamespace(
        timeout_seconds=10, memory_bytes=512 * 1024 * 1024, max_output_bytes=1024
    )
    child = "import resource; resource.setrlimit(resource.RLIMIT_CPU, (1, 2))\nwhile True: pass"
    with pytest.raises(ParseFailure, match="^PARSER_TIMEOUT$"):
        OpenDataLoaderParser._execute([sys.executable, "-I", "-c", child], tmp_path, {}, profile)
