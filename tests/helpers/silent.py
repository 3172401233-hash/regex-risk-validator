"""Controlled test child: exits 0 without producing any result."""

import sys


def main() -> None:
    sys.stdin.readline()
    sys.exit(0)


if __name__ == "__main__":
    main()
