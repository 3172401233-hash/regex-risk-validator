"""Controlled test child: reads one line, then sleeps. Ignores input."""

import sys
import time


def main() -> None:
    sys.stdin.readline()
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    time.sleep(seconds)
    sys.stdout.write("done\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()

