"""regex_risk: bounded-budget regex backtracking risk validation.

Public API:
    analyze(...)            one-shot convenience function
    RegexRiskAnalyzer       reusable analyzer instance
    Limits                  budget configuration
    CandidateRule           caller-supplied input construction rule
    save_report/load_report versioned JSON persistence
    replay_report(path)     re-execute a stored report's checks

This library provides *evidence within a finite budget only*. It cannot
decide the complexity of an arbitrary regex and cannot prove the absence
of ReDoS.
"""

from __future__ import annotations

from typing import Optional, Union

from .analyzer import RegexRiskAnalyzer
from .candidates import CandidateRule
from .models import (
    REPORT_FORMAT_VERSION,
    AnalysisReport,
    Assessment,
    Limits,
    MatchMode,
    MinimizedSample,
    Observation,
    OutcomeKind,
)
from .reportio import ReportError, load_report, save_report

__version__ = "0.1.0"

__all__ = [
    "analyze",
    "replay_report",
    "RegexRiskAnalyzer",
    "Limits",
    "CandidateRule",
    "AnalysisReport",
    "Assessment",
    "MatchMode",
    "MinimizedSample",
    "Observation",
    "OutcomeKind",
    "ReportError",
    "REPORT_FORMAT_VERSION",
    "save_report",
    "load_report",
    "__version__",
]


def analyze(
    pattern: str,
    flags: int = 0,
    mode: str = "search",
    string: Optional[str] = None,
    rules: Optional[list[CandidateRule]] = None,
    limits: Optional[Limits] = None,
) -> AnalysisReport:
    """One-shot analysis. See RegexRiskAnalyzer.analyze."""
    return RegexRiskAnalyzer(limits).analyze(
        pattern, flags=flags, mode=mode, string=string, rules=rules
    )


def replay_report(
    source: Union[str, AnalysisReport],
    limits: Optional[Limits] = None,
) -> AnalysisReport:
    """Re-execute the checks stored in a report and return FRESH results.

    The stored observations are never returned as-is; every input is
    evaluated again in a new subprocess. ``limits`` overrides the stored
    budget if given.
    """
    if isinstance(source, str):
        stored = load_report(source)
    elif isinstance(source, AnalysisReport):
        stored = source
    else:
        raise TypeError("source must be a path or an AnalysisReport")

    effective_limits = limits or stored.limits
    analyzer = RegexRiskAnalyzer(effective_limits)

    provided = [o for o in stored.observations if o.source == "provided"]
    fresh = analyzer.analyze(
        stored.pattern,
        flags=stored.flags,
        mode=stored.mode,
        string=provided[0].input if provided else None,
        rules=None,  # regenerated deterministically from the stored seed
    )
    return fresh
