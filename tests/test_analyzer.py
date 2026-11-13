"""Tests for the analyzer: real re matching, timeouts, budgets, reuse."""

import os
import re
import sys
import unittest
from unittest import mock

from regex_risk import Limits, RegexRiskAnalyzer
from regex_risk.analyzer import _worker_argv
from regex_risk.models import OutcomeKind

HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers")

EVIL_PATTERN = r"(a+)+$"
EVIL_INPUT = "a" * 30 + "b"


def fast_limits(**kw):
    defaults = dict(
        max_candidates=0,
        per_match_timeout_seconds=2.0,
        total_time_seconds=30.0,
        terminate_grace_seconds=0.5,
    )
    defaults.update(kw)
    return Limits(**defaults)


class TestBasicMatching(unittest.TestCase):
    def setUp(self):
        self.analyzer = RegexRiskAnalyzer(fast_limits())

    def test_match(self):
        obs = self.analyzer.evaluate_one(r"\d+", 0, "search", "abc123")
        self.assertEqual(obs.outcome, OutcomeKind.MATCH.value)
        self.assertTrue(obs.matched)
        self.assertEqual(obs.match_span, [3, 6])
        self.assertIsNotNone(obs.compile_seconds)
        self.assertIsNotNone(obs.match_seconds)
        self.assertIsNotNone(obs.process_overhead_seconds)

    def test_no_match(self):
        obs = self.analyzer.evaluate_one(r"\d+", 0, "search", "abcdef")
        self.assertEqual(obs.outcome, OutcomeKind.NO_MATCH.value)
        self.assertFalse(obs.matched)

    def test_invalid_pattern(self):
        obs = self.analyzer.evaluate_one("(a+", 0, "search", "aaa")
        self.assertEqual(obs.outcome, OutcomeKind.INVALID_PATTERN.value)
        self.assertTrue(obs.error)

    def test_unicode(self):
        obs = self.analyzer.evaluate_one("é+", re.IGNORECASE, "search", "xxÉÉéyy")
        self.assertEqual(obs.outcome, OutcomeKind.MATCH.value)
        obs2 = self.analyzer.evaluate_one("你好", 0, "search", "说你好呀")
        self.assertEqual(obs2.outcome, OutcomeKind.MATCH.value)

    def test_modes(self):
        self.assertEqual(
            self.analyzer.evaluate_one("abc", 0, "search", "xabc").outcome,
            OutcomeKind.MATCH.value,
        )
        self.assertEqual(
            self.analyzer.evaluate_one("abc", 0, "match", "xabc").outcome,
            OutcomeKind.NO_MATCH.value,
        )
        self.assertEqual(
            self.analyzer.evaluate_one("abc", 0, "fullmatch", "abc").outcome,
            OutcomeKind.MATCH.value,
        )
        self.assertEqual(
            self.analyzer.evaluate_one("abc", 0, "fullmatch", "abcd").outcome,
            OutcomeKind.NO_MATCH.value,
        )

    def test_analyze_records_mode_and_flags(self):
        report = self.analyzer.analyze("abc", flags=re.IGNORECASE,
                                       mode="fullmatch", string="ABC")
        self.assertEqual(report.mode, "fullmatch")
        self.assertEqual(report.flags, re.IGNORECASE)
        self.assertEqual(report.observations[0].outcome, OutcomeKind.MATCH.value)

    def test_analyze_invalid_pattern_reported(self):
        report = self.analyzer.analyze("(a+", string="aaa")
        self.assertIsNotNone(report.pattern_error)
        self.assertEqual(report.observations[0].outcome,
                         OutcomeKind.INVALID_PATTERN.value)


class TestTimeoutAndReuse(unittest.TestCase):
    def test_evil_regex_times_out_and_analyzer_recovers(self):
        analyzer = RegexRiskAnalyzer(
            fast_limits(per_match_timeout_seconds=0.8, total_time_seconds=30.0)
        )
        obs = analyzer.evaluate_one(EVIL_PATTERN, 0, "search", EVIL_INPUT,
                                    timeout=0.8)
        self.assertEqual(obs.outcome, OutcomeKind.TIMEOUT.value)
        # Same instance keeps working after a timeout.
        obs2 = analyzer.evaluate_one(r"\d+", 0, "search", "id=42")
        self.assertEqual(obs2.outcome, OutcomeKind.MATCH.value)

    def test_analyze_evil_pattern_produces_timeout_and_assessment(self):
        analyzer = RegexRiskAnalyzer(
            fast_limits(per_match_timeout_seconds=0.8, total_time_seconds=60.0)
        )
        report = analyzer.analyze(EVIL_PATTERN, string=EVIL_INPUT)
        kinds = {o.outcome for o in report.observations}
        self.assertIn(OutcomeKind.TIMEOUT.value, kinds)
        self.assertTrue(report.assessment.timed_out)
        self.assertIn("not sufficient", " ".join(report.assessment.caveats))


class TestWorkerErrors(unittest.TestCase):
    def test_crashed_worker(self):
        analyzer = RegexRiskAnalyzer(fast_limits())
        crasher = [sys.executable, os.path.join(HELPERS, "crasher.py")]
        with mock.patch("regex_risk.analyzer._worker_argv", return_value=crasher):
            obs = analyzer.evaluate_one("abc", 0, "search", "abc")
        self.assertEqual(obs.outcome, OutcomeKind.WORKER_ERROR.value)
        self.assertIn("3", obs.error)

    def test_silent_worker_no_result(self):
        analyzer = RegexRiskAnalyzer(fast_limits())
        silent = [sys.executable, os.path.join(HELPERS, "silent.py")]
        with mock.patch("regex_risk.analyzer._worker_argv", return_value=silent):
            obs = analyzer.evaluate_one("abc", 0, "search", "abc")
        self.assertEqual(obs.outcome, OutcomeKind.WORKER_ERROR.value)

    def test_default_worker_argv_is_module_invocation(self):
        argv = _worker_argv()
        self.assertEqual(argv[1:3], ["-m", "regex_risk.worker"])


class TestBudgets(unittest.TestCase):
    def test_launch_budget_exhaustion_skips_remaining(self):
        limits = fast_limits(max_candidates=4, max_subprocess_launches=1)
        analyzer = RegexRiskAnalyzer(limits)
        report = analyzer.analyze("a", string="a")
        kinds = [o.outcome for o in report.observations]
        self.assertEqual(kinds[0], OutcomeKind.MATCH.value)
        self.assertIn(OutcomeKind.SKIPPED_BUDGET.value, kinds)

    def test_pattern_length_limit(self):
        analyzer = RegexRiskAnalyzer(fast_limits(max_pattern_length=10))
        with self.assertRaises(ValueError):
            analyzer.analyze("a" * 11, string="a")

    def test_input_length_limit(self):
        analyzer = RegexRiskAnalyzer(fast_limits(max_input_length=10))
        with self.assertRaises(ValueError):
            analyzer.analyze("a", string="a" * 11)

    def test_invalid_limits_rejected(self):
        with self.assertRaises(ValueError):
            Limits(total_time_seconds=0).validate()
        with self.assertRaises(ValueError):
            Limits(max_concurrent_processes=0).validate()

    def test_generated_candidates_run(self):
        limits = fast_limits(max_candidates=4, max_input_length=8_000)
        analyzer = RegexRiskAnalyzer(limits)
        report = analyzer.analyze(r"(ab)+$", string="ab")
        generated = [o for o in report.observations if o.source == "generated"]
        self.assertTrue(generated)
        self.assertTrue(all(o.input_length <= 8_000 for o in generated))


if __name__ == "__main__":
    unittest.main()
