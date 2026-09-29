"""Bounded diagnosis of one contained OpenDataLoader parse (Windows job object).

Runs the unchanged parser child (``opendataloader.py <work>``) under the same
``WindowsJobIsolation`` limits the adapter uses, with the same environment and
request shape, and records what the adapter itself cannot report:

* the launcher exit code and the last 4 KiB of its stderr,
* peak committed job memory and total job CPU time (user + kernel, all processes),
* the work directory path length and the longest produced path,
* produced file names and sizes.

It is a diagnostic only. The work directory is a throwaway scratch directory under
``--work-root``; nothing it produces is written into a run, a parse manifest or
an artifact root, and it is deleted afterwards. Limits are the ones passed on the
command line; nothing is widened implicitly, and the process tree is always killed
through the job before the scratch directory is removed.

    PYTHONUTF8=1 .venv/Scripts/python.exe scripts/diagnose_parser_containment.py \\
        --pdf <report.pdf> --pages 2,23,26 --java "C:/Program Files/Java/jdk-21/bin/java.exe" \\
        --memory-bytes 805306368 --timeout-seconds 120 --work-root C:/pd --out diag.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

STDERR_TAIL_BYTES = 4096


def _accounting(isolation) -> dict:
    """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: CPU is in 100 ns units."""
    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("total_user", ctypes.c_int64),
            ("total_kernel", ctypes.c_int64),
            ("period_user", ctypes.c_int64),
            ("period_kernel", ctypes.c_int64),
            ("page_faults", wintypes.DWORD),
            ("total_processes", wintypes.DWORD),
            ("active_processes", wintypes.DWORD),
            ("terminated_processes", wintypes.DWORD),
        ]

    info = Accounting()
    ok = isolation._kernel.QueryInformationJobObject(
        isolation._job, 1, ctypes.byref(info), ctypes.sizeof(info), None
    )
    if not ok:
        return {}
    return dict(
        cpu_user_seconds=info.total_user / 1e7,
        cpu_kernel_seconds=info.total_kernel / 1e7,
        total_processes=info.total_processes,
        terminated_processes=info.terminated_processes,
    )


def diagnose(args) -> dict:
    from proofops.adapters.parsing import opendataloader as odl
    from proofops.adapters.parsing.windows_isolation import (
        WindowsJobIsolation,
        kill_and_reap,
    )
    from proofops.application.ingest.graph_fusion import ParserProfile

    pages = tuple(int(p) for p in args.pages.split(","))
    profile = ParserProfile(
        "00000000-0000-4000-8000-00000000d1a9",
        java_executable=args.java,
        timeout_seconds=args.timeout_seconds,
        max_output_bytes=args.max_output_bytes,
        memory_bytes=args.memory_bytes,
        physical_pages=pages,
    )
    pdf = Path(args.pdf).read_bytes()
    java = str(Path(args.java))
    runtime = subprocess.run([java, "-version"], capture_output=True, timeout=30).stderr.decode(
        errors="replace"
    )
    root = Path(args.work_root)
    root.mkdir(parents=True, exist_ok=True)
    result = dict(
        profile=profile.invocation_snapshot(),
        memory_bytes=args.memory_bytes,
        timeout_seconds=args.timeout_seconds,
        java_version=runtime.splitlines()[0] if runtime else None,
        java_21=odl.is_java_21(runtime),
    )
    with TemporaryDirectory(prefix=".diag-", dir=root) as scratch:
        work = Path(scratch) / "w"
        work.mkdir()
        (work / "source.pdf").write_bytes(pdf)
        (work / "request.json").write_bytes(
            odl._json(dict(profile=profile.invocation_snapshot(), selected=pages))
        )
        env = odl._child_environment(java, work, profile)
        stderr_path = Path(scratch) / "stderr.txt"
        isolation = WindowsJobIsolation(
            memory_bytes=args.memory_bytes, cpu_seconds=max(1, int(args.timeout_seconds))
        )
        started = time.monotonic()
        peak_output = 0
        outcome = "exited"
        try:
            with stderr_path.open("wb") as stderr:
                process = _spawn_with_stderr(isolation, odl, work, env, stderr)
                try:
                    while process.poll() is None:
                        if time.monotonic() - started > args.timeout_seconds:
                            outcome = "wall_timeout"
                            break
                        peak_output = max(peak_output, odl._output_bytes(work))
                        if peak_output > args.max_output_bytes:
                            outcome = "output_limit"
                            break
                        time.sleep(0.05)
                    result.update(
                        outcome=outcome,
                        exit_code=process.poll(),
                        wall_seconds=round(time.monotonic() - started, 2),
                        peak_job_memory_bytes=isolation.peak_memory_bytes(process),
                        **_accounting(isolation),
                    )
                finally:
                    isolation.terminate_tree(process)
                    kill_and_reap(process)
        finally:
            isolation.close()
        tail = stderr_path.read_bytes()[-STDERR_TAIL_BYTES:]
        files = {p.name: p.stat().st_size for p in work.rglob("*") if p.is_file()}
        longest = max((len(str(p)) for p in work.rglob("*")), default=len(str(work)))
        result.update(
            work_path_length=len(str(work)),
            longest_path_length=longest,
            peak_output_bytes=peak_output,
            files=files,
            stderr_tail=tail.decode(errors="replace"),
            memory_headroom_bytes=args.memory_bytes - result.get("peak_job_memory_bytes", 0),
        )
    return result


def _spawn_with_stderr(isolation, odl, work, env, stderr):
    """``WindowsJobIsolation.spawn`` with stderr kept: suspended, assigned, resumed."""
    from proofops.adapters.parsing import windows_isolation as wi

    process = subprocess.Popen(
        [sys.executable, "-I", odl.__file__, str(work)],
        cwd=str(work),
        env=dict(env),
        stdout=subprocess.DEVNULL,
        stderr=stderr,
        stdin=subprocess.DEVNULL,
        creationflags=wi._CREATE_SUSPENDED | wi._CREATE_NO_WINDOW | wi._CREATE_NEW_PROCESS_GROUP,
    )
    try:
        if not isolation._kernel.AssignProcessToJobObject(isolation._job, int(process._handle)):
            raise wi.IsolationUnavailable("assign_to_job_failed")
        isolation._resume(process.pid)
    except BaseException:
        wi.kill_and_reap(process)
        raise
    return process


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--java", required=True)
    parser.add_argument("--memory-bytes", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--max-output-bytes", type=int, default=20_000_000)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print(json.dumps(dict(error="windows_only")))
        return 2
    out = Path(args.out)
    if out.exists():
        print(json.dumps(dict(error="output exists; refusing to overwrite")))
        return 2
    result = diagnose(args)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({k: v for k, v in result.items() if k != "stderr_tail"}, indent=2))
    print("stderr tail:", result["stderr_tail"][-1500:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
