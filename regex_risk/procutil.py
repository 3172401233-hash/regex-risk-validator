"""Subprocess execution with parent-controlled timeouts.

The parent process owns the deadline. When it expires the child is asked
to terminate (SIGTERM / CTRL_BREAK equivalent via ``terminate``), given a
grace period, and then force-killed (``kill``) and reaped. Cancelling a
Future alone is never considered sufficient.

No shell is used anywhere: commands are always argv lists.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence


@dataclass
class ProcResult:
    exit_code: Optional[int]  # None if the process never started
    stdout: str
    stderr: str
    timed_out: bool
    wall_seconds: float
    launch_error: Optional[str] = None


def run_subprocess_with_timeout(
    argv: Sequence[str],
    input_text: str = "",
    timeout: float = 10.0,
    terminate_grace: float = 1.0,
    env: Optional[Mapping[str, str]] = None,
) -> ProcResult:
    """Run ``argv`` to completion, enforcing ``timeout`` from the parent.

    On timeout: terminate -> wait grace -> kill -> reap. The function
    always returns; the caller can never be blocked indefinitely by a
    stuck child, a child that exits early without output, or a broken
    pipe.
    """
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            env=dict(env) if env is not None else None,
        )
    except OSError as exc:
        return ProcResult(
            exit_code=None,
            stdout="",
            stderr="",
            timed_out=False,
            wall_seconds=time.monotonic() - start,
            launch_error=f"{type(exc).__name__}: {exc}",
        )

    timed_out = False
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=terminate_grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            # Reap unconditionally; communicate after kill cannot hang
            # because the child is dead and pipes are closed.
            stdout, stderr = proc.communicate()
    finally:
        if proc.poll() is None:  # defensive: never leave a zombie
            proc.kill()
            proc.wait()

    return ProcResult(
        exit_code=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
        wall_seconds=time.monotonic() - start,
    )
