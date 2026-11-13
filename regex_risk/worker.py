"""Worker entry point executed as ``python -m regex_risk.worker``.

Protocol: read one JSON object from stdin, write one JSON object to
stdout, exit 0. Any abnormal exit (non-zero code, no output, garbage
output) is treated by the parent as a worker error.

This module is spawn-safe: it does not rely on fork semantics, so it
works with the Windows ``spawn`` start method as well as POSIX.
Only the stdlib ``re`` module is used; no eval/exec anywhere.
"""

from __future__ import annotations

import json
import re
import sys
import time


def run_job(job: dict) -> dict:
    pattern = job["pattern"]
    flags = int(job.get("flags", 0))
    mode = job.get("mode", "search")
    string = job["string"]

    compile_start = time.perf_counter()
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return {
            "status": "invalid_pattern",
            "error": f"{type(exc).__name__}: {exc}",
        }
    except (TypeError, ValueError, OverflowError) as exc:
        return {
            "status": "invalid_pattern",
            "error": f"{type(exc).__name__}: {exc}",
        }
    compile_seconds = time.perf_counter() - compile_start

    match_func = getattr(compiled, mode, None)
    if match_func is None or mode not in ("search", "match", "fullmatch"):
        return {"status": "invalid_pattern", "error": f"unknown mode: {mode!r}"}

    match_start = time.perf_counter()
    m = match_func(string)
    match_seconds = time.perf_counter() - match_start

    return {
        "status": "match" if m is not None else "no_match",
        "matched": m is not None,
        "match_span": list(m.span()) if m is not None else None,
        "compile_seconds": compile_seconds,
        "match_seconds": match_seconds,
    }


def main() -> int:
    raw = sys.stdin.readline()
    try:
        job = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        sys.stdout.write(json.dumps({"status": "bad_request"}) + "\n")
        sys.stdout.flush()
        return 2
    try:
        result = run_job(job)
    except Exception as exc:  # last-resort guard; parent still gets JSON
        result = {"status": "worker_exception", "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
