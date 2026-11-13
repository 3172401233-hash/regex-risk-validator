"""Data models for regex risk validation.

All result types are plain dataclasses so they can be serialized to the
versioned JSON report format without any custom encoding tricks.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


REPORT_FORMAT_VERSION = "1.0"

#: Hard cap on report file size accepted by the loader (16 MiB).
MAX_REPORT_FILE_BYTES = 16 * 1024 * 1024

#: Hard cap on a single input string stored in / loaded from a report.
MAX_REPORT_INPUT_LENGTH = 1_000_000


class MatchMode(str, enum.Enum):
    SEARCH = "search"
    MATCH = "match"
    FULLMATCH = "fullmatch"


class OutcomeKind(str, enum.Enum):
    """Distinguishable result categories for one sample evaluation."""

    MATCH = "match"
    NO_MATCH = "no_match"
    INVALID_PATTERN = "invalid_pattern"
    TIMEOUT = "timeout"
    WORKER_ERROR = "worker_error"  # child crashed / exited abnormally
    LAUNCH_FAILURE = "launch_failure"  # child could not be started at all
    SKIPPED_BUDGET = "skipped_budget"  # never ran: budget already exhausted


@dataclass
class Limits:
    """Budget for a single analysis run.

    Every limit is enforced before expensive work happens (e.g. input
    length is checked before a large candidate string is constructed).
    """

    max_pattern_length: int = 2_000
    max_input_length: int = 100_000
    max_candidates: int = 24
    max_subprocess_launches: int = 64
    max_concurrent_processes: int = 2
    total_time_seconds: float = 15.0
    per_match_timeout_seconds: float = 2.0
    terminate_grace_seconds: float = 1.0
    seed: int = 0

    def validate(self) -> None:
        problems = []
        for name in (
            "max_pattern_length",
            "max_input_length",
            "max_candidates",
            "max_subprocess_launches",
        ):
            if getattr(self, name) < 0:
                problems.append(f"{name} must be >= 0")
        if self.max_concurrent_processes < 1:
            problems.append("max_concurrent_processes must be >= 1")
        for name in ("total_time_seconds", "per_match_timeout_seconds"):
            if getattr(self, name) <= 0:
                problems.append(f"{name} must be > 0")
        if self.terminate_grace_seconds < 0:
            problems.append("terminate_grace_seconds must be >= 0")
        if problems:
            raise ValueError("invalid Limits: " + "; ".join(problems))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Limits":
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown Limits fields: {sorted(unknown)}")
        return cls(**data)


@dataclass
class Observation:
    """One evaluated sample."""

    input: str
    input_length: int
    outcome: str  # OutcomeKind value
    matched: Optional[bool] = None
    match_span: Optional[list[int]] = None
    compile_seconds: Optional[float] = None
    match_seconds: Optional[float] = None
    wall_seconds: Optional[float] = None
    # Wall time not accounted for by compile+match: interpreter start,
    # imports, IPC. Reported separately from matching time on purpose.
    process_overhead_seconds: Optional[float] = None
    error: Optional[str] = None
    source: str = "provided"  # "provided" | "generated" | "minimized"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Observation":
        return cls(**data)


@dataclass
class MinimizedSample:
    """A timeout sample that was shrunk and re-verified in a subprocess."""

    original_input: str
    original_length: int
    minimized_input: Optional[str] = None
    minimized_length: Optional[int] = None
    verified: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MinimizedSample":
        return cls(**data)


@dataclass
class Assessment:
    """Risk hints derived from observations.

    This is evidence collected within a finite budget, never a proof.
    """

    summary: str
    growth_hint: str = "insufficient_data"
    timed_out: bool = False
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Assessment":
        return cls(**data)


@dataclass
class AnalysisReport:
    pattern: str
    flags: int
    mode: str  # MatchMode value actually used
    python_version: str
    limits: Limits
    observations: list[Observation] = field(default_factory=list)
    minimized: list[MinimizedSample] = field(default_factory=list)
    assessment: Optional[Assessment] = None
    pattern_error: Optional[str] = None
    format_version: str = REPORT_FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "pattern": self.pattern,
            "flags": self.flags,
            "mode": self.mode,
            "python_version": self.python_version,
            "limits": self.limits.to_dict(),
            "observations": [o.to_dict() for o in self.observations],
            "minimized": [m.to_dict() for m in self.minimized],
            "assessment": self.assessment.to_dict() if self.assessment else None,
            "pattern_error": self.pattern_error,
        }
