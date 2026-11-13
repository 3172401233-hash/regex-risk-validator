"""Tests for report save/load/replay and malformed report rejection."""

import json
import os
import tempfile
import unittest

from regex_risk import (
    AnalysisReport,
    Limits,
    RegexRiskAnalyzer,
    load_report,
    replay_report,
    save_report,
)
from regex_risk import reportio
from regex_risk.models import OutcomeKind, REPORT_FORMAT_VERSION
from regex_risk.reportio import ReportError


def make_report(tmpdir):
    analyzer = RegexRiskAnalyzer(
        Limits(max_candidates=2, max_input_length=4_000,
               total_time_seconds=30.0, per_match_timeout_seconds=2.0)
    )
    report = analyzer.analyze(r"\d+", string="id=123")
    path = os.path.join(tmpdir, "report.json")
    save_report(report, path)
    return report, path


class TestRoundTrip(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            report, path = make_report(tmp)
            loaded = load_report(path)
            self.assertEqual(loaded.pattern, report.pattern)
            self.assertEqual(loaded.flags, report.flags)
            self.assertEqual(loaded.mode, report.mode)
            self.assertEqual(len(loaded.observations), len(report.observations))
            self.assertEqual(loaded.observations[0].input, "id=123")
            self.assertEqual(loaded.limits.max_candidates, 2)
            self.assertIsNotNone(loaded.python_version)

    def test_report_contains_required_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, path = make_report(tmp)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            for key in ("format_version", "pattern", "flags", "mode",
                        "python_version", "limits", "observations"):
                self.assertIn(key, data)
            self.assertEqual(data["format_version"], REPORT_FORMAT_VERSION)

    def test_replay_returns_fresh_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            stored, path = make_report(tmp)
            fresh = replay_report(path)
            self.assertIsNot(fresh, stored)
            self.assertIsNot(fresh.observations, stored.observations)
            self.assertEqual(fresh.pattern, stored.pattern)
            self.assertEqual(fresh.mode, stored.mode)
            self.assertTrue(fresh.observations)
            self.assertEqual(fresh.observations[0].outcome,
                             OutcomeKind.MATCH.value)

    def test_replay_from_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            stored, _ = make_report(tmp)
            fresh = replay_report(stored)
            self.assertEqual(fresh.pattern, stored.pattern)


class TestInvalidReports(unittest.TestCase):
    def write(self, tmp, payload, name="bad.json"):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def valid_payload(self):
        analyzer = RegexRiskAnalyzer(Limits(max_candidates=0))
        return analyzer.analyze("a", string="a").to_dict()

    def test_not_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ReportError):
                load_report(self.write(tmp, "{not json"))

    def test_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.valid_payload()
            data["format_version"] = "99.0"
            with self.assertRaises(ReportError):
                load_report(self.write(tmp, data))

    def test_missing_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.valid_payload()
            del data["pattern"]
            with self.assertRaises(ReportError):
                load_report(self.write(tmp, data))

    def test_wrong_field_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.valid_payload()
            data["flags"] = "IGNORECASE"  # must be an int
            with self.assertRaises(ReportError):
                load_report(self.write(tmp, data))

    def test_bad_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.valid_payload()
            data["mode"] = "grep"
            with self.assertRaises(ReportError):
                load_report(self.write(tmp, data))

    def test_inconsistent_input_length(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.valid_payload()
            data["observations"][0]["input_length"] += 1
            with self.assertRaises(ReportError):
                load_report(self.write(tmp, data))

    def test_oversized_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, " " * 1024, name="big.json")
            with self.assertRaises(ReportError):
                with unittest.mock.patch.object(
                    reportio, "MAX_REPORT_FILE_BYTES", 10
                ):
                    load_report(path)

    def test_oversized_input_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.valid_payload()
            data["observations"][0]["input"] = "a" * 100
            data["observations"][0]["input_length"] = 100
            with self.assertRaises(ReportError):
                with unittest.mock.patch.object(
                    reportio, "MAX_REPORT_INPUT_LENGTH", 10
                ):
                    load_report(self.write(tmp, data))

    def test_no_pickle_involved(self):
        # A pickle payload must be rejected as invalid JSON.
        import pickle

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json")
            with open(path, "wb") as fh:
                fh.write(pickle.dumps({"pattern": "a"}))
            with self.assertRaises(ReportError):
                load_report(path)


if __name__ == "__main__":
    unittest.main()
