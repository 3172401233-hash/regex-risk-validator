"""Orchestration: run regex samples in subprocesses under a budget."""

from __future__ import annotations

import json
import os
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .candidates import CandidateRule, generate_candidates, minimize_timeout_sample
from .models import (
    AnalysisReport,
    Assessment,
    Limits,
    MatchMode,
    MinimizedSample,
    Observation,
    OutcomeKind,
)
from .procutil import run_subprocess_with_timeout

_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _worker_argv() -> list[str]:
    return [sys.executable, "-m", "regex_risk.worker"]


def _worker_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        _PACKAGE_PARENT if not existing else _PACKAGE_PARENT + os.pathsep + existing
    )
    return env


class Budget:
    """Mutable per-analysis budget tracker (deadline + launch counter)."""

    def __init__(self, limits: Limits):
        self.limits = limits
        self.deadline = time.monotonic() + limits.total_time_seconds
        self.launches_left = limits.max_subprocess_launches
        self._lock = threading.Lock()

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def exhausted(self) -> bool:
        return self.remaining_seconds() <= 0.0

    def try_claim_launch(self) -> bool:
        with self._lock:
            if self.launches_left <= 0 or self.exhausted():
                return False
            self.launches_left -= 1
            return True

    def per_task_timeout(self) -> float:
        """A task's deadline never exceeds the remaining analysis time."""
        return min(self.limits.per_match_timeout_seconds, self.remaining_seconds())


class RegexRiskAnalyzer:
    """Reusable analyzer instance. Safe to keep using after timeouts."""

    def __init__(self, limits: Optional[Limits] = None):
        self.limits = limits or Limits()
        self.limits.validate()

    # ------------------------------------------------------------------
    # Low-level: one sample, one subprocess.
    # ------------------------------------------------------------------
    def evaluate_one(
        self,
        pattern: str,
        flags: int,
        mode: str,
        string: str,
        timeout: Optional[float] = None,
        source: str = "provided",
    ) -> Observation:
        """Evaluate a single input in a fresh subprocess.

        The parent enforces ``timeout``; the child is terminated, then
        killed if needed, and always reaped.
        """
        effective_timeout = (
            timeout if timeout is not None else self.limits.per_match_timeout_seconds
        )
        job = json.dumps(
            {"pattern": pattern, "flags": int(flags), "mode": mode, "string": string}
        )
        result = run_subprocess_with_timeout(
            _worker_argv(),
            input_text=job + "\n",
            timeout=effective_timeout,
            terminate_grace=self.limits.terminate_grace_seconds,
            env=_worker_env(),
        )
        base = dict(input=string, input_length=len(string), source=source)

        if result.launch_error is not None:
            return Observation(
                outcome=OutcomeKind.LAUNCH_FAILURE.value,
                error=result.launch_error,
                wall_seconds=result.wall_seconds,
                **base,
            )
        if result.timed_out:
            return Observation(
                outcome=OutcomeKind.TIMEOUT.value,
                error=f"exceeded {effective_timeout:.3f}s; child terminated and reaped",
                wall_seconds=result.wall_seconds,
                **base,
            )
        if result.exit_code != 0:
            return Observation(
                outcome=OutcomeKind.WORKER_ERROR.value,
                error=f"worker exited with code {result.exit_code}: "
                f"{result.stderr.strip()[:300]}",
                wall_seconds=result.wall_seconds,
                **base,
            )
        line = result.stdout.strip().splitlines()
        try:
            payload = json.loads(line[-1]) if line else {}
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict) or "status" not in payload:
            return Observation(
                outcome=OutcomeKind.WORKER_ERROR.value,
                error="worker returned no usable result",
                wall_seconds=result.wall_seconds,
                **base,
            )

        status = payload["status"]
        if status == "invalid_pattern":
            return Observation(
                outcome=OutcomeKind.INVALID_PATTERN.value,
                error=payload.get("error", "invalid pattern"),
                wall_seconds=result.wall_seconds,
                **base,
            )
        if status in ("worker_exception", "bad_request"):
            return Observation(
                outcome=OutcomeKind.WORKER_ERROR.value,
                error=payload.get("error", status),
                wall_seconds=result.wall_seconds,
                **base,
            )

        compile_s = payload.get("compile_seconds")
        match_s = payload.get("match_seconds")
        overhead = None
        if compile_s is not None and match_s is not None:
            overhead = max(0.0, result.wall_seconds - compile_s - match_s)
        return Observation(
            outcome=OutcomeKind.MATCH.value if payload.get("matched") else OutcomeKind.NO_MATCH.value,
            matched=bool(payload.get("matched")),
            match_span=payload.get("match_span"),
            compile_seconds=compile_s,
            match_seconds=match_s,
            wall_seconds=result.wall_seconds,
            process_overhead_seconds=overhead,
            **base,
        )

    # ------------------------------------------------------------------
    # Full analysis.
    # ------------------------------------------------------------------
    def analyze(
        self,
        pattern: str,
        flags: int = 0,
        mode: str = "search",
        string: Optional[str] = None,
        rules: Optional[list[CandidateRule]] = None,
    ) -> AnalysisReport:
        limits = self.limits
        limits.validate()
        if mode not in {m.value for m in MatchMode}:
            raise ValueError(f"unsupported mode: {mode!r}")
        if len(pattern) > limits.max_pattern_length:
            raise ValueError(
                f"pattern length {len(pattern)} exceeds limit "
                f"{limits.max_pattern_length}"
            )
        if string is not None and len(string) > limits.max_input_length:
            raise ValueError(
                f"input length {len(string)} exceeds limit {limits.max_input_length}"
            )

        report = AnalysisReport(
            pattern=pattern,
            flags=int(flags),
            mode=mode,
            python_version=platform.python_version(),
            limits=limits,
        )
        budget = Budget(limits)

        # 1) The caller-provided string first.
        jobs: list[tuple[str, str]] = []
        if string is not None:
            jobs.append(("provided", string))

        # 2) Generated candidates (length-checked before construction).
        jobs.extend(
            generate_candidates(
                pattern,
                rules,
                max_input_length=limits.max_input_length,
                max_candidates=limits.max_candidates,
                seed=limits.seed,
            )
        )

        observations = self._run_jobs(pattern, flags, mode, jobs, budget)
        report.observations = observations

        invalid = next(
            (o for o in observations if o.outcome == OutcomeKind.INVALID_PATTERN.value),
            None,
        )
        if invalid is not None:
            report.pattern_error = invalid.error

        # 3) Minimize the first timeout sample within remaining budget.
        timeout_obs = next(
            (o for o in observations if o.outcome == OutcomeKind.TIMEOUT.value), None
        )
        if timeout_obs is not None:
            report.minimized.append(
                self._minimize(pattern, flags, mode, timeout_obs, budget)
            )

        report.assessment = self._assess(report)
        return report

    def _run_jobs(self, pattern, flags, mode, jobs, budget) -> list[Observation]:
        results: dict[int, Observation] = {}
        with ThreadPoolExecutor(max_workers=self.limits.max_concurrent_processes) as pool:
            futures = {}
            for index, (source, candidate) in enumerate(jobs):
                if budget.exhausted() or not budget.try_claim_launch():
                    results[index] = Observation(
                        input=candidate,
                        input_length=len(candidate),
                        outcome=OutcomeKind.SKIPPED_BUDGET.value,
                        error="budget exhausted before launch",
                        source=source,
                    )
                    continue
                timeout = budget.per_task_timeout()
                if timeout <= 0:
                    results[index] = Observation(
                        input=candidate,
                        input_length=len(candidate),
                        outcome=OutcomeKind.SKIPPED_BUDGET.value,
                        error="no time budget left",
                        source=source,
                    )
                    continue
                futures[
                    pool.submit(
                        self.evaluate_one, pattern, flags, mode, candidate, timeout, source
                    )
                ] = index
            for future in futures:
                # Each task's own timeout is capped by the remaining total
                # budget, so tasks self-terminate at the overall deadline;
                # procutil guarantees the child is then reaped.
                index = futures[future]
                results[index] = future.result()
        return [results[i] for i in sorted(results)]

    def _minimize(self, pattern, flags, mode, timeout_obs, budget) -> MinimizedSample:
        sample = timeout_obs.input
        record = MinimizedSample(
            original_input=sample, original_length=len(sample)
        )

        def is_still_slow(candidate: str) -> Optional[bool]:
            if not budget.try_claim_launch():
                return None
            obs = self.evaluate_one(
                pattern,
                flags,
                mode,
                candidate,
                timeout=budget.per_task_timeout(),
                source="minimized",
            )
            if obs.outcome == OutcomeKind.TIMEOUT.value:
                return True
            if obs.outcome in (
                OutcomeKind.MATCH.value,
                OutcomeKind.NO_MATCH.value,
            ):
                return False
            return None  # unstable / errored: do not trust

        try:
            minimized, note = minimize_timeout_sample(sample, is_still_slow)
        except Exception as exc:  # never let minimization break analysis
            record.note = f"minimization failed: {type(exc).__name__}: {exc}"
            return record
        record.note = note
        if minimized is not None:
            record.minimized_input = minimized
            record.minimized_length = len(minimized)
            record.verified = True
        return record

    # ------------------------------------------------------------------
    # Assessment: hints only, never proofs.
    # ------------------------------------------------------------------
    def _assess(self, report: AnalysisReport) -> Assessment:
        caveats = [
            "Results are evidence gathered within a finite budget; absence "
            "of slow samples is NOT proof that the pattern is safe.",
            "Machine load and timer resolution can distort timings; "
            "process startup overhead is reported separately from matching time.",
            "A single slow run or a single timeout is not sufficient to "
            "claim exponential (or any specific) complexity.",
        ]
        obs = report.observations
        if report.pattern_error:
            return Assessment(
                summary=f"Pattern failed to compile: {report.pattern_error}",
                growth_hint="not_applicable",
                caveats=caveats,
            )

        timeouts = [o for o in obs if o.outcome == OutcomeKind.TIMEOUT.value]
        completed = [
            o
            for o in obs
            if o.outcome in (OutcomeKind.MATCH.value, OutcomeKind.NO_MATCH.value)
            and o.match_seconds is not None
        ]

        growth_hint = "insufficient_data"
        if len(completed) >= 3:
            completed.sort(key=lambda o: o.input_length)
            growth_hint = _growth_hint(completed)

        if timeouts:
            shortest = min(t.input_length for t in timeouts)
            summary = (
                f"{len(timeouts)} sample(s) exceeded the per-match deadline "
                f"(shortest at input length {shortest}). This is concrete "
                f"evidence of potential ReDoS risk for similar inputs, "
                f"within the limits of this run."
            )
            if growth_hint == "insufficient_data":
                growth_hint = "timeout_observed"
        else:
            summary = (
                "No sample exceeded the deadline within the configured "
                "budget. This does not prove the pattern is safe."
            )

        return Assessment(
            summary=summary,
            growth_hint=growth_hint,
            timed_out=bool(timeouts),
            caveats=caveats,
        )


def _growth_hint(sorted_completed: list[Observation]) -> str:
    """Compare time growth vs length growth across observations."""
    points = [
        (o.input_length, o.match_seconds)
        for o in sorted_completed
        if o.input_length > 0 and o.match_seconds is not None
    ]
    if len(points) < 3:
        return "insufficient_data"
    first_len, first_t = points[0]
    last_len, last_t = points[-1]
    if first_t <= 0 or first_len <= 0 or last_len <= first_len:
        return "insufficient_data"
    length_ratio = last_len / first_len
    time_ratio = max(last_t, 1e-9) / max(first_t, 1e-9)
    # Doubling the input multiplies time by >=4 -> suspicious.
    if length_ratio >= 2 and time_ratio >= length_ratio**2:
        return "superlinear_growth_suspected"
    if time_ratio >= length_ratio * 1.5:
        return "mildly_superlinear"
    return "roughly_linear_or_sublinear"
