#!/usr/bin/env python3
"""Run the shared Opera workload analyzer."""

import importlib.util
from pathlib import Path


ANALYZER_PATH = (
    Path(__file__).resolve().parents[1] / "opera_16tor_uniform" / "analyze.py"
)
SPEC = importlib.util.spec_from_file_location("opera_uniform_analyzer", ANALYZER_PATH)
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


if __name__ == "__main__":
    ANALYZER.main()
