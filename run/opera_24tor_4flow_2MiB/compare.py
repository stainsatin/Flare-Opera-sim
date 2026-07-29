#!/usr/bin/env python3
"""Run the shared FIFO versus rxhopprio summary comparison."""

import importlib.util
from pathlib import Path


COMPARER_PATH = (
    Path(__file__).resolve().parents[1]
    / "opera_108tor_2host_alltoall"
    / "compare.py"
)
SPEC = importlib.util.spec_from_file_location("opera_scheduler_compare", COMPARER_PATH)
COMPARER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARER)


if __name__ == "__main__":
    COMPARER.main()
