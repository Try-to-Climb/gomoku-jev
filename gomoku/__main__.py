"""``python -m gomoku`` is the same as ``python -m gomoku.cli``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
