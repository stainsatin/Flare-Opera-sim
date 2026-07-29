#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opera_24tor_light_generator", HERE / "generate_flows.py"
)
FLOWS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLOWS)


class OperaTwentyFourTorLighterExperimentTest(unittest.TestCase):
    def test_default_workload_size_and_host_balance(self):
        flows = FLOWS.build_flows()
        self.assertEqual(len(flows), 384)
        self.assertEqual({flow["bytes"] for flow in flows}, {2 * 1024 * 1024})
        expected = Counter({host: 4 for host in range(96)})
        self.assertEqual(Counter(flow["source"] for flow in flows), expected)
        self.assertEqual(Counter(flow["destination"] for flow in flows), expected)
        self.assertEqual(
            sum(flow["bytes"] for flow in flows if flow["source"] == 0),
            8 * 1024 * 1024,
        )

    def test_tor_matrix_is_reverse_paired_and_balanced(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024)
        self.assertEqual(FLOWS.FLOW_OFFSETS, (3, 9, 15, 21))
        self.assertTrue(
            all((24 - offset) % 24 in FLOWS.FLOW_OFFSETS for offset in FLOWS.FLOW_OFFSETS)
        )
        tor_pairs = Counter(
            (flow["source_tor"], flow["destination_tor"]) for flow in flows
        )
        self.assertEqual(len(tor_pairs), 24 * 4)
        self.assertEqual(set(tor_pairs.values()), {4})
        self.assertEqual(
            Counter(flow["source_tor"] for flow in flows),
            Counter({tor: 16 for tor in range(24)}),
        )
        self.assertEqual(
            Counter(flow["destination_tor"] for flow in flows),
            Counter({tor: 16 for tor in range(24)}),
        )

    def test_default_release_is_balanced_and_inside_active_windows(self):
        flows = FLOWS.build_flows()
        starts = Counter(flow["start_superslice"] for flow in flows)
        self.assertEqual(starts, Counter({slice_index: 48 for slice_index in range(8)}))
        source_tor_starts = Counter(
            (flow["source_tor"], flow["start_superslice"]) for flow in flows
        )
        destination_tor_starts = Counter(
            (flow["destination_tor"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(source_tor_starts.values()), {2})
        self.assertEqual(set(destination_tor_starts.values()), {2})
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(receiver_starts.values()), {1})
        self.assertEqual(len({flow["start_ns"] for flow in flows}), len(flows))
        self.assertTrue(all(flow["start_ns"] % 55_000 < 54_000 for flow in flows))

    def test_sixteen_slice_mode_reduces_release_rate_further(self):
        flows = FLOWS.build_flows(start_superslices=16)
        self.assertEqual(
            Counter(flow["start_superslice"] for flow in flows),
            Counter({slice_index: 24 for slice_index in range(16)}),
        )
        tor_starts = Counter(
            (flow["source_tor"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(tor_starts.values()), {1})

    def test_trace_has_four_columns(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024)
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "uniform.htsim"
            FLOWS.write_trace(trace, flows)
            rows = [line.split() for line in trace.read_text().splitlines()]
        self.assertEqual(len(rows), 384)
        self.assertTrue(all(len(row) == 4 for row in rows))

    def test_invalid_release_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            FLOWS.build_flows(start_superslices=2)
        with self.assertRaises(ValueError):
            FLOWS.build_flows(start_superslices=12)
        with self.assertRaises(ValueError):
            FLOWS.build_flows(base_start_ns=54_000)

    def test_run_script_uses_lighter_defaults_and_821_weights(self):
        script = (HERE / "run.sh").read_text(encoding="ascii")
        self.assertIn("SIMTIME=0.02", script)
        self.assertIn("FLOW_SIZE_MIB=2", script)
        self.assertIn("START_SUPERSLICES=8", script)
        self.assertIn("RX_HOP_WEIGHTS=8:2:1", script)
        self.assertIn("CWND=4", script)
        self.assertIn("results_4x2MiB_stagger8_w821", script)
        self.assertIn("run_case fifo", script)
        self.assertIn("run_case rxhopprio", script)
        self.assertIn("priority_args=(-rxhopprio -rxhopweights", script)
        self.assertIn("opera_24tor_4host_55us.txt", script)
        self.assertTrue(
            (ROOT / "topologies" / "opera_24tor_4host_55us.txt").is_file()
        )


if __name__ == "__main__":
    unittest.main()
