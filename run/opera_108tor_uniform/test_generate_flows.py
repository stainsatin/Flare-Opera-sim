#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opera_108tor_generate_flows", HERE / "generate_flows.py"
)
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class GenerateFlowsTest(unittest.TestCase):
    def test_cycle_spread_is_balanced_without_per_slice_hotspots(self):
        flows = GENERATOR.build_flows()
        self.assertEqual(len(flows), 648)
        self.assertEqual({flow["source"] for flow in flows}, set(range(648)))
        self.assertEqual({flow["destination"] for flow in flows}, set(range(648)))
        self.assertEqual(
            Counter(flow["source_tor"] for flow in flows),
            Counter({tor: 6 for tor in range(108)}),
        )
        self.assertEqual(
            Counter(flow["destination_tor"] for flow in flows),
            Counter({tor: 6 for tor in range(108)}),
        )
        self.assertEqual(
            Counter(flow["start_superslice"] for flow in flows),
            Counter({slice_index: 6 for slice_index in range(108)}),
        )
        for slice_index in range(108):
            selected = [
                flow for flow in flows if flow["start_superslice"] == slice_index
            ]
            self.assertEqual(len({flow["source_tor"] for flow in selected}), 6)
            self.assertEqual(len({flow["destination_tor"] for flow in selected}), 6)
            self.assertEqual({flow["lane"] for flow in selected}, set(range(6)))

    def test_synchronized_trace_has_one_row_per_host(self):
        flows = GENERATOR.build_flows(start_mode="synchronized")
        self.assertEqual({flow["start_ns"] for flow in flows}, {1_000})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "uniform.htsim"
            GENERATOR.write_trace(output, flows)
            rows = [line.split() for line in output.read_text().splitlines()]
        self.assertEqual(len(rows), 648)
        self.assertTrue(all(len(row) == 4 for row in rows))

    def test_native_topology_header_and_timing(self):
        lines = (ROOT / "topologies/dynexp_55us_symm.txt").open(
            encoding="ascii"
        )
        try:
            self.assertEqual(next(lines).strip(), "648 6 6 108")
            self.assertEqual(next(lines).strip(), "324 43880000 620000 10000000")
        finally:
            lines.close()

    def test_invalid_mapping_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            GENERATOR.build_flows(offsets=(1, 1, 37, 55, 73, 91))
        with self.assertRaises(ValueError):
            GENERATOR.build_flows(offsets=(0, 19, 37, 55, 73, 91))
        with self.assertRaises(ValueError):
            GENERATOR.build_flows(start_stride=108)


if __name__ == "__main__":
    unittest.main()
