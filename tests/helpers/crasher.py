"""Controlled test child: exits abnormally with a non-zero code."""

import os
import sys


def main() -> None:
    sys.stdin.readline()
    sys.stderr.write("simulated worker crash\n")
    sys.stderr.flush()
    os._exit(3)


if __name__ == "__main__":
    main()

