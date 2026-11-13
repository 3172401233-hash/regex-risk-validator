"""Runs last (module name sorts last): no child processes may survive."""

import multiprocessing
import os
import unittest


def _child_pids_of(pid):
    """Linux /proc-based child lookup; returns None when unsupported."""
    if not os.path.isdir("/proc"):
        return None
    children = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as fh:
                parts = fh.read().rsplit(b")", 1)[1].split()
            if int(parts[1]) == pid:  # ppid field
                children.append(int(entry))
        except (OSError, ValueError, IndexError):
            continue
    return children


class TestNoLeftoverProcesses(unittest.TestCase):
    def test_no_child_processes_remain(self):
        self.assertEqual(
            multiprocessing.active_children(), [],
            "multiprocessing children still alive",
        )
        leftovers = _child_pids_of(os.getpid())
        if leftovers is not None:
            self.assertEqual(leftovers, [], f"leftover child pids: {leftovers}")


if __name__ == "__main__":
    unittest.main()
