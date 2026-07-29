#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opera_24tor_8flow_generator", HERE / "generate_flows.py"
)
FLOWS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLOWS)


class OperaTwentyFourTorExperimentTest(unittest.TestCase):
    def test_default_workload_has_requested_size_and_balance(self):
        flows = FLOWS.build_flows()
        self.assertEqual(len(flows), 768)
        self.assertEqual({flow["bytes"] for flow in flows}, {4 * 1024 * 1024})
        expected = Counter({host: 8 for host in range(96)})
        self.assertEqual(Counter(flow["source"] for flow in flows), expected)
        self.assertEqual(Counter(flow["destination"] for flow in flows), expected)
        self.assertEqual(
            sum(flow["bytes"] for flow in flows if flow["source"] == 0),
            32 * 1024 * 1024,
        )

    def test_offsets_and_tor_matrix_are_symmetric_and_balanced(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024)
        offsets = FLOWS.select_destination_offsets(8)
        self.assertEqual(offsets, [2, 5, 7, 10, 14, 17, 19, 22])
        self.assertTrue(all((24 - offset) % 24 in offsets for offset in offsets))
        tor_pairs = Counter(
            (flow["source_tor"], flow["destination_tor"]) for flow in flows
        )
        self.assertEqual(len(tor_pairs), 24 * 8)
        self.assertEqual(set(tor_pairs.values()), {4})
        self.assertEqual(
            Counter(flow["source_tor"] for flow in flows),
            Counter({tor: 32 for tor in range(24)}),
        )
        self.assertEqual(
            Counter(flow["destination_tor"] for flow in flows),
            Counter({tor: 32 for tor in range(24)}),
        )

    def test_starts_are_balanced_unique_and_inside_active_windows(self):
        flows = FLOWS.build_flows()
        self.assertEqual(
            Counter(flow["start_superslice"] for flow in flows),
            Counter({0: 384, 1: 384}),
        )
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(receiver_starts.values()), {4})
        self.assertEqual(len({flow["start_ns"] for flow in flows}), 768)
        self.assertTrue(all(flow["start_ns"] % 55_000 < 54_000 for flow in flows))
        self.assertGreaterEqual(min(flow["start_ns"] for flow in flows), 1_000)
        self.assertLess(max(flow["start_ns"] for flow in flows), 2 * 55_000)

    def test_trace_has_four_columns_and_all_flows(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "uniform.htsim"
            FLOWS.write_trace(trace, flows)
            rows = [line.split() for line in trace.read_text().splitlines()]
        self.assertEqual(len(rows), 768)
        self.assertTrue(all(len(row) == 4 for row in rows))

    def test_invalid_release_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            FLOWS.build_flows(start_superslices=3)
        with self.assertRaises(ValueError):
            FLOWS.build_flows(flows_per_host=7, start_superslices=2)
        with self.assertRaises(ValueError):
            FLOWS.build_flows(base_start_ns=54_000)

    def test_run_script_has_requested_topology_and_modes(self):
        script = (HERE / "run.sh").read_text(encoding="ascii")
        self.assertIn("SIMTIME=0.05", script)
        self.assertIn("FLOW_SIZE_MIB=4", script)
        self.assertIn("FLOWS_PER_HOST=8", script)
        self.assertIn("START_SUPERSLICES=2", script)
        self.assertIn("CWND=4", script)
        self.assertIn("results_8x4MiB_nicprio_stagger2_w421", script)
        self.assertIn("run_case fifo", script)
        self.assertIn("run_case rxhopprio", script)
        self.assertIn("RX_HOP_WEIGHTS=4:2:1", script)
        self.assertIn("priority_args=(-rxhopprio -rxhopweights", script)
        self.assertIn("opera_24tor_4host_55us.txt", script)
        self.assertTrue(
            (ROOT / "topologies" / "opera_24tor_4host_55us.txt").is_file()
        )


if __name__ == "__main__":
    unittest.main()
