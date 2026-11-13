"""Candidate input generation and timeout-sample minimization.

Generation is deterministic for a given seed: callers get the same
sequence of candidates every run. Length limits are always checked
*before* a large string is constructed.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional


@dataclass
class CandidateRule:
    """Caller-supplied construction rule for growing inputs.

    ``fragment`` is repeated to reach each target length, wrapped in
    ``prefix``/``suffix``. ``lengths`` lists the target total lengths;
    entries beyond the configured input limit are skipped before any
    string is built.
    """

    fragment: str
    prefix: str = ""
    suffix: str = ""
    lengths: list[int] = field(default_factory=list)

    def build(self, target_length: int) -> Optional[str]:
        fixed = len(self.prefix) + len(self.suffix)
        if target_length < fixed or not self.fragment:
            return None
        body_len = target_length - fixed
        repeats = body_len // len(self.fragment)
        body = (self.fragment * repeats) + self.fragment[: body_len - repeats * len(self.fragment)]
        return self.prefix + body + self.suffix


def default_lengths(max_input_length: int, count: int) -> list[int]:
    """Geometric-ish growth: 1k, 2k, 4k, ... capped by the input limit."""
    lengths = []
    n = 1_000
    while n <= max_input_length and len(lengths) < count:
        lengths.append(n)
        n *= 2
    if not lengths and max_input_length > 0:
        lengths.append(min(max_input_length, 256))
    return lengths[:count]


# Fragments that often appear inside quantified groups, e.g. "(ab)+",
# "(\w+,)*". This is only a heuristic to *generate test inputs*; it is
# never used to conclude that a pattern is safe or unsafe.
_GROUP_FRAGMENT_RE = re.compile(r"\(([^(){}]*)\)[+*{]")
_LITERAL_RUN_RE = re.compile(r"[A-Za-z0-9_]{1,8}[+*{]")


def heuristic_rules(pattern: str, max_input_length: int, count: int) -> list[CandidateRule]:
    """Derive a small, bounded set of candidate rules from the pattern."""
    fragments: list[str] = []
    for m in _GROUP_FRAGMENT_RE.finditer(pattern):
        frag = _literalize(m.group(1))
        if frag and frag not in fragments:
            fragments.append(frag)
    for m in _LITERAL_RUN_RE.finditer(pattern):
        frag = m.group(0)[:-1]
        if frag and frag not in fragments:
            fragments.append(frag)
    if not fragments:
        fragments = ["a"]
    fragments = fragments[:4]

    lengths = default_lengths(max_input_length, max(1, count // max(1, len(fragments))))
    rules = []
    for frag in fragments:
        # A trailing character that fails the match is a classic
        # backtracking trigger; include both variants, bounded.
        rules.append(CandidateRule(fragment=frag, suffix="!", lengths=list(lengths)))
        rules.append(CandidateRule(fragment=frag, lengths=list(lengths)))
    return rules


def _literalize(fragment_pattern: str) -> str:
    """Best-effort literal text for a group body; '' if nothing usable."""
    out = []
    i = 0
    while i < len(fragment_pattern):
        ch = fragment_pattern[i]
        if ch == "\\" and i + 1 < len(fragment_pattern):
            nxt = fragment_pattern[i + 1]
            mapping = {"w": "a", "d": "1", "s": " ", "n": "\n", "t": "\t"}
            out.append(mapping.get(nxt, nxt if nxt.isalnum() else "a"))
            i += 2
        elif ch.isalnum():
            out.append(ch)
            i += 1
        elif ch in " .,;:-_":
            out.append(ch)
            i += 1
        else:
            out.append("a")
            i += 1
    return "".join(out)[:16]


def generate_candidates(
    pattern: str,
    rules: Optional[list[CandidateRule]],
    max_input_length: int,
    max_candidates: int,
    seed: int = 0,
) -> Iterator[tuple[str, str]]:
    """Yield ``(source_label, candidate)`` pairs, shortest first.

    Deterministic for a given ``seed``. Length checks happen before any
    string is materialized.
    """
    rng = random.Random(seed)
    if rules is None:
        rules = heuristic_rules(pattern, max_input_length, max_candidates)
        rng.shuffle(rules)  # deterministic: seeded

    produced = 0
    seen: set[str] = set()
    for rule in rules:
        lengths = sorted(L for L in rule.lengths if 0 < L <= max_input_length)
        for target in lengths:
            if produced >= max_candidates:
                return
            candidate = rule.build(target)
            if candidate is None or len(candidate) > max_input_length:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            produced += 1
            yield "generated", candidate


def minimize_timeout_sample(
    sample: str,
    is_still_slow: Callable[[str], Optional[bool]],
    min_length: int = 1,
) -> tuple[Optional[str], str]:
    """Shrink a timing-out input, re-verifying every candidate.

    ``is_still_slow`` returns True (still times out), False (completes),
    or None (not verified, e.g. budget exhausted). A conclusion is only
    kept when it was actually re-verified; nothing is inherited from the
    original sample.

    Returns ``(minimized_or_None, note)``. Failure to minimize, unstable
    results, and budget exhaustion are all reported honestly in the note.
    """
    current = sample
    note_parts: list[str] = []
    verified_any = False

    # Chunk-removal pass (ddmin-flavoured), then char-level trimming.
    granularity = 2
    while len(current) > min_length:
        chunk = max(1, (len(current) - min_length) // granularity)
        reduced_this_round = False
        i = 0
        while i < len(current):
            candidate = current[:i] + current[i + chunk :]
            if len(candidate) < min_length or candidate == current:
                i += chunk
                continue
            verdict = is_still_slow(candidate)
            if verdict is None:
                note_parts.append("minimization stopped: verification budget exhausted")
                return (current if verified_any else None), "; ".join(note_parts)
            if verdict is True:
                current = candidate
                verified_any = True
                reduced_this_round = True
                # restart with finer chunks on the smaller string
                granularity = 2
                break
            i += chunk
        if not reduced_this_round:
            if granularity >= len(current):
                break
            granularity = min(len(current), granularity * 2)

    if not verified_any:
        return None, "no shorter verified slow sample found"
    note_parts.append("minimized sample re-verified in subprocess")
    return current, "; ".join(note_parts)
