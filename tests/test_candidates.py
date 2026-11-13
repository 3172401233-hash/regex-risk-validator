"""Tests for candidate generation and timeout-sample minimization."""

import unittest

from regex_risk.candidates import (
    CandidateRule,
    default_lengths,
    generate_candidates,
    heuristic_rules,
    minimize_timeout_sample,
)


class TestCandidateRule(unittest.TestCase):
    def test_build_reaches_target_length(self):
        rule = CandidateRule(fragment="ab", prefix=">", suffix="<", lengths=[11])
        built = rule.build(11)
        self.assertEqual(len(built), 11)
        self.assertTrue(built.startswith(">"))
        self.assertTrue(built.endswith("<"))

    def test_build_too_short_returns_none(self):
        rule = CandidateRule(fragment="ab", prefix="12345", suffix="67890")
        self.assertIsNone(rule.build(5))


class TestGeneration(unittest.TestCase):
    def test_lengths_capped(self):
        lengths = default_lengths(max_input_length=3_000, count=10)
        self.assertTrue(all(n <= 3_000 for n in lengths))
        self.assertEqual(lengths, [1000, 2000])

    def test_caller_rules_respected(self):
        rules = [CandidateRule(fragment="xy", prefix="p", suffix="s",
                               lengths=[10, 20, 40])]
        got = list(generate_candidates("xy", rules, 100, 10, seed=0))
        self.assertEqual([len(c) for _, c in got], [10, 20, 40])
        self.assertTrue(all(c.startswith("p") and c.endswith("s") for _, c in got))

    def test_length_limit_checked_before_construction(self):
        rules = [CandidateRule(fragment="a", lengths=[50, 500, 5_000])]
        got = list(generate_candidates("a", rules, max_input_length=100,
                                       max_candidates=10, seed=0))
        self.assertTrue(all(len(c) <= 100 for _, c in got))
        self.assertEqual(len(got), 1)  # only the length-50 entry survives

    def test_candidate_count_limit(self):
        rules = [CandidateRule(fragment="a", lengths=[10, 20, 30, 40, 50])]
        got = list(generate_candidates("a", rules, 1000, max_candidates=2, seed=0))
        self.assertEqual(len(got), 2)

    def test_deterministic_for_same_seed(self):
        a = list(generate_candidates("(ab)+c", None, 10_000, 8, seed=42))
        b = list(generate_candidates("(ab)+c", None, 10_000, 8, seed=42))
        self.assertEqual(a, b)

    def test_heuristic_fallback_produces_candidates(self):
        rules = heuristic_rules("(foo,)+bar", 10_000, 6)
        self.assertTrue(rules)
        got = list(generate_candidates("(foo,)+bar", None, 10_000, 6, seed=1))
        self.assertTrue(got)

    def test_heuristic_without_literals_still_works(self):
        got = list(generate_candidates(r"\d*", None, 2_000, 4, seed=0))
        self.assertTrue(got)


class TestMinimization(unittest.TestCase):
    def test_shrinks_with_reverification(self):
        sample = "a" * 100 + "!"
        calls = []

        def is_slow(candidate):
            calls.append(len(candidate))
            return len(candidate) > 50

        minimized, note = minimize_timeout_sample(sample, is_slow)
        self.assertIsNotNone(minimized)
        self.assertLess(len(minimized), len(sample))
        self.assertTrue(len(minimized) > 50)
        self.assertTrue(calls)  # every conclusion was re-verified

    def test_budget_exhaustion_reported(self):
        sample = "a" * 60

        def is_slow(candidate):
            return None  # simulates exhausted verification budget

        minimized, note = minimize_timeout_sample(sample, is_slow)
        self.assertIsNone(minimized)
        self.assertIn("budget", note)

    def test_no_shorter_slow_sample(self):
        sample = "a" * 40

        def is_slow(candidate):
            return False  # everything shorter completes

        minimized, note = minimize_timeout_sample(sample, is_slow)
        self.assertIsNone(minimized)
        self.assertIn("no shorter", note)


if __name__ == "__main__":
    unittest.main()
