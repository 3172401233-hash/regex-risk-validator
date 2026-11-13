"""Integration test: real subprocess minimization of a timeout sample.

Uses a genuinely catastrophic pattern with a short per-match deadline.
No wall-clock thresholds are asserted; only outcomes and re-verification.
"""

import unittest

from regex_risk import Limits, RegexRiskAnalyzer
from regex_risk.models import OutcomeKind


class TestMinimizeIntegration(unittest.TestCase):
    def test_timeout_sample_is_minimized_and_reverified(self):
        analyzer = RegexRiskAnalyzer(
            Limits(
                max_candidates=0,
                per_match_timeout_seconds=0.6,
                total_time_seconds=90.0,
                terminate_grace_seconds=0.5,
                max_subprocess_launches=80,
            )
        )
        report = analyzer.analyze(r"(a+)+$", string="a" * 26 + "b")
        self.assertEqual(report.observations[0].outcome, OutcomeKind.TIMEOUT.value)
        self.assertEqual(len(report.minimized), 1)
        record = report.minimized[0]
        self.assertEqual(record.original_length, 27)
        if record.verified:
            # A verified shorter sample was found and re-checked live.
            self.assertIsNotNone(record.minimized_input)
            self.assertLess(record.minimized_length, record.original_length)
        else:
            # Budget ran out before verification: must be recorded honestly.
            self.assertTrue(record.note)


if __name__ == "__main__":
    unittest.main()
