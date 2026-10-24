#!/usr/bin/env python3
"""Entry point: puts the repo folder on sys.path and runs the cbudget CLI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cbudget.cli import main  # noqa: E402

sys.exit(main())
