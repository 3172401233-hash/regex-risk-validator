"""Tests for the parent-controlled subprocess timeout mechanism."""

import os
import sys
import time
import unittest

from regex_risk.procutil import run_subprocess_with_timeout

HELPERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "helpers")


def helper_argv(name, *args):
    return [sys.executable, os.path.join(HELPERS, name), *args]


class TestProcutil(unittest.TestCase):
    def test_normal_exit(self):
        result = run_subprocess_with_timeout(
            helper_argv("sleeper.py", "0.05"), input_text="x\n", timeout=10.0
        )
        self.assertFalse(result.timed_out)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("done", result.stdout)

    def test_timeout_terminates_and_reaps(self):
        start = time.monotonic()
        result = run_subprocess_with_timeout(
            helper_argv("sleeper.py", "60"),
            input_text="x\n",
            timeout=0.5,
            terminate_grace=0.5,
        )
        elapsed = time.monotonic() - start
        self.assertTrue(result.timed_out)
        self.assertIsNotNone(result.exit_code)
        self.assertNotEqual(result.exit_code, 0)
        # Generous bound: must return long before the 60s sleep ends.
        self.assertLess(elapsed, 30.0)

    def test_abnormal_exit_reported(self):
        result = run_subprocess_with_timeout(
            helper_argv("crasher.py"), input_text="x\n", timeout=10.0
        )
        self.assertFalse(result.timed_out)
        self.assertEqual(result.exit_code, 3)
        self.assertIn("simulated worker crash", result.stderr)

    def test_launch_failure(self):
        result = run_subprocess_with_timeout(
            [sys.executable, "-m", "nonexistent_module_xyz_123"],
            timeout=10.0,
        )
        # The interpreter starts but exits non-zero; either way the
        # caller gets a result instead of hanging.
        self.assertFalse(result.timed_out)
        self.assertNotEqual(result.exit_code, 0)

    def test_missing_executable(self):
        result = run_subprocess_with_timeout(
            ["/nonexistent/definitely-not-a-binary-xyz"], timeout=5.0
        )
        self.assertIsNotNone(result.launch_error)
        self.assertIsNone(result.exit_code)


if __name__ == "__main__":
    unittest.main()
