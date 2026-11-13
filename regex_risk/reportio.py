"""Versioned JSON report persistence.

Reports are plain JSON. Loading never uses pickle, never imports
modules named by the report, and never executes anything. Structure,
field types, file size, and embedded input lengths are all validated.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .models import (
    MAX_REPORT_FILE_BYTES,
    MAX_REPORT_INPUT_LENGTH,
    REPORT_FORMAT_VERSION,
    AnalysisReport,
    Assessment,
    Limits,
    MatchMode,
    MinimizedSample,
    Observation,
    OutcomeKind,
)


class ReportError(ValueError):
    """Raised when a report file is malformed or fails validation."""


def save_report(report: AnalysisReport, path: str) -> None:
    payload = report.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ReportError(message)


def _check_str(data: dict, key: str, max_len: int) -> str:
    value = data.get(key)
    _require(isinstance(value, str), f"field {key!r} must be a string")
    _require(len(value) <= max_len, f"field {key!r} exceeds {max_len} chars")
    return value


def _check_number_or_none(value: Any, key: str) -> None:
    _require(
        value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)),
        f"field {key!r} must be a number or null",
    )


def _validate_observation(raw: Any, index: int) -> Observation:
    _require(isinstance(raw, dict), f"observation {index} must be an object")
    where = f"observation {index}"
    text = _check_str(raw, "input", MAX_REPORT_INPUT_LENGTH)
    outcome = raw.get("outcome")
    _require(
        outcome in {k.value for k in OutcomeKind},
        f"{where}: unknown outcome {outcome!r}",
    )
    _require(
        isinstance(raw.get("input_length"), int)
        and raw["input_length"] == len(text),
        f"{where}: input_length missing or inconsistent",
    )
    for key in ("compile_seconds", "match_seconds", "wall_seconds",
                "process_overhead_seconds"):
        _check_number_or_none(raw.get(key), f"{where}.{key}")
    span = raw.get("match_span")
    _require(
        span is None
        or (isinstance(span, list) and len(span) == 2
            and all(isinstance(x, int) for x in span)),
        f"{where}: match_span must be [start, end] or null",
    )
    source = raw.get("source", "provided")
    _require(source in ("provided", "generated", "minimized"),
             f"{where}: bad source {source!r}")
    return Observation(
        input=text,
        input_length=raw["input_length"],
        outcome=outcome,
        matched=raw.get("matched") if raw.get("matched") is None else bool(raw["matched"]),
        match_span=span,
        compile_seconds=raw.get("compile_seconds"),
        match_seconds=raw.get("match_seconds"),
        wall_seconds=raw.get("wall_seconds"),
        process_overhead_seconds=raw.get("process_overhead_seconds"),
        error=raw.get("error"),
        source=source,
    )


def _validate_minimized(raw: Any, index: int) -> MinimizedSample:
    _require(isinstance(raw, dict), f"minimized {index} must be an object")
    original = _check_str(raw, "original_input", MAX_REPORT_INPUT_LENGTH)
    minimized = raw.get("minimized_input")
    if minimized is not None:
        _require(isinstance(minimized, str), "minimized_input must be a string")
        _require(
            len(minimized) <= MAX_REPORT_INPUT_LENGTH,
            "minimized_input too long",
        )
    return MinimizedSample(
        original_input=original,
        original_length=int(raw.get("original_length", len(original))),
        minimized_input=minimized,
        minimized_length=raw.get("minimized_length"),
        verified=bool(raw.get("verified", False)),
        note=str(raw.get("note", "")),
    )


def load_report(path: str) -> AnalysisReport:
    """Load and validate a report. Raises ReportError on any problem."""
    size = os.path.getsize(path)
    _require(size <= MAX_REPORT_FILE_BYTES, f"report file too large: {size} bytes")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except UnicodeDecodeError as exc:
        raise ReportError(f"report is not valid UTF-8 JSON: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(f"invalid JSON: {exc}") from exc

    _require(isinstance(data, dict), "report root must be an object")
    _require(
        data.get("format_version") == REPORT_FORMAT_VERSION,
        f"unsupported format_version {data.get('format_version')!r}",
    )

    pattern = _check_str(data, "pattern", 1_000_000)
    flags = data.get("flags")
    _require(isinstance(flags, int) and not isinstance(flags, bool),
             "field 'flags' must be an integer")
    mode = data.get("mode")
    _require(mode in {m.value for m in MatchMode}, f"unknown mode {mode!r}")
    python_version = _check_str(data, "python_version", 100)

    raw_limits = data.get("limits")
    _require(isinstance(raw_limits, dict), "field 'limits' must be an object")
    try:
        limits = Limits.from_dict(raw_limits)
        limits.validate()
    except (TypeError, ValueError) as exc:
        raise ReportError(f"invalid limits: {exc}") from exc

    raw_obs = data.get("observations")
    _require(isinstance(raw_obs, list), "field 'observations' must be a list")
    observations = [_validate_observation(o, i) for i, o in enumerate(raw_obs)]

    raw_min = data.get("minimized", [])
    _require(isinstance(raw_min, list), "field 'minimized' must be a list")
    minimized = [_validate_minimized(m, i) for i, m in enumerate(raw_min)]

    assessment = None
    raw_assess = data.get("assessment")
    if raw_assess is not None:
        _require(isinstance(raw_assess, dict), "field 'assessment' must be an object")
        assessment = Assessment(
            summary=str(raw_assess.get("summary", "")),
            growth_hint=str(raw_assess.get("growth_hint", "insufficient_data")),
            timed_out=bool(raw_assess.get("timed_out", False)),
            caveats=[str(c) for c in raw_assess.get("caveats", [])],
        )

    pattern_error = data.get("pattern_error")
    _require(pattern_error is None or isinstance(pattern_error, str),
             "field 'pattern_error' must be a string or null")

    return AnalysisReport(
        pattern=pattern,
        flags=flags,
        mode=mode,
        python_version=python_version,
        limits=limits,
        observations=observations,
        minimized=minimized,
        assessment=assessment,
        pattern_error=pattern_error,
    )
