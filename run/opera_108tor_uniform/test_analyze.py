#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "opera_108tor_analyze_wrapper", HERE / "analyze.py"
)
WRAPPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WRAPPER)


class AnalyzeTest(unittest.TestCase):
    def test_trace_uses_108_tor_modulus(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "uniform.htsim"
            trace.write_text(
                "0 6 33554432 1000\n"
                "5 551 33554432 5832500\n",
                encoding="ascii",
            )
            rows = WRAPPER.ANALYZER.parse_trace(trace, 6, 108, 54_500)

        self.assertEqual(rows[0]["tor_offset"], 1)
        self.assertEqual(rows[0]["start_superslice"], 0)
        self.assertEqual(rows[1]["source_tor"], 0)
        self.assertEqual(rows[1]["destination_tor"], 91)
        self.assertEqual(rows[1]["tor_offset"], 91)
        self.assertEqual(rows[1]["start_superslice"], 107)


if __name__ == "__main__":
    unittest.main()
