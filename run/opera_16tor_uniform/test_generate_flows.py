#!/usr/bin/env python3

import tempfile
import unittest
from collections import Counter
from pathlib import Path

import generate_flows


class GenerateFlowsTest(unittest.TestCase):
    def test_cycle_spread_is_balanced(self):
        flows = generate_flows.build_flows()
        self.assertEqual(len(flows), 64)
        self.assertEqual({flow["source"] for flow in flows}, set(range(64)))
        self.assertEqual({flow["destination"] for flow in flows}, set(range(64)))
        self.assertEqual(
            Counter(flow["source_tor"] for flow in flows),
            Counter({tor: 4 for tor in range(16)}),
        )
        self.assertEqual(
            Counter(flow["destination_tor"] for flow in flows),
            Counter({tor: 4 for tor in range(16)}),
        )
        self.assertEqual(
            Counter(flow["start_superslice"] for flow in flows),
            Counter({slice_index: 4 for slice_index in range(16)}),
        )

    def test_synchronized_trace_has_one_row_per_host(self):
        flows = generate_flows.build_flows(start_mode="synchronized")
        self.assertEqual({flow["start_ns"] for flow in flows}, {1_000})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "uniform.htsim"
            generate_flows.write_trace(output, flows)
            rows = [line.split() for line in output.read_text().splitlines()]
        self.assertEqual(len(rows), 64)
        self.assertTrue(all(len(row) == 4 for row in rows))

    def test_invalid_offsets_are_rejected(self):
        with self.assertRaises(ValueError):
            generate_flows.build_flows(offsets=(1, 1, 5, 9))
        with self.assertRaises(ValueError):
            generate_flows.build_flows(offsets=(0, 1, 5, 9))

    def test_probability_file_covers_path_hops_plus_jitter(self):
        root = Path(__file__).resolve().parents[2]
        topology_lines = (
            root / "topologies/opera_16tor_4host_15us.txt"
        ).read_text(encoding="ascii").splitlines()
        slices = int(topology_lines[1].split()[0])
        route_lines = topology_lines[2 + slices :]
        max_hops = max(
            len(line.split()) - 2 for line in route_lines if len(line.split()) > 2
        )
        probabilities = {
            int(hops): float(probability)
            for hops, probability in (
                line.split()
                for line in (root / "run/pfun_exp2.txt")
                .read_text(encoding="ascii")
                .splitlines()
                if line.strip()
            )
        }
        self.assertEqual(max_hops, 8)
        self.assertIn(max_hops + 1, probabilities)


if __name__ == "__main__":
    unittest.main()
