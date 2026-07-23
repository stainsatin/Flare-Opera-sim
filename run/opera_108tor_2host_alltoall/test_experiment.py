#!/usr/bin/env python3

import csv
import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOPOLOGY = load_module("two_host_topology", "build_topology.py")
FLOWS = load_module("two_host_flows", "generate_flows.py")
COMPARE = load_module("two_host_compare", "compare.py")
ANALYZE = load_module("two_host_analyze", "analyze.py")


class TwoHostAllToAllExperimentTest(unittest.TestCase):
    def test_topology_conversion_preserves_adjacency_and_shifts_uplinks(self):
        source_text = """\
4 2 2 2
3 10 20 30
1 -1 -1 0
-1 0 1 -1
1 -1 -1 0
0
0 1 2
1 0 3 2
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.txt"
            output = Path(directory) / "output.txt"
            source.write_text(source_text, encoding="ascii")
            summary = TOPOLOGY.transform_topology(source, output, hosts_per_tor=1)
            lines = output.read_text(encoding="ascii").splitlines()

        self.assertEqual(lines[0], "2 1 2 2")
        self.assertEqual(lines[1], "3 10 20 30")
        self.assertEqual(lines[2:5], source_text.splitlines()[2:5])
        self.assertEqual(lines[5:], ["0", "0 1 1", "1 0 2 1"])
        self.assertEqual(summary["routes"], 2)
        self.assertEqual(summary["max_hops"], 2)

    def test_full_flow_matrix_is_balanced(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024)
        self.assertEqual(len(flows), 23_112)
        self.assertEqual(
            Counter(flow["source"] for flow in flows),
            Counter({host: 107 for host in range(216)}),
        )
        self.assertEqual(
            Counter(flow["destination"] for flow in flows),
            Counter({host: 107 for host in range(216)}),
        )
        self.assertEqual(
            set(Counter(
                (flow["source_tor"], flow["destination_tor"])
                for flow in flows
            ).values()),
            {2},
        )

    def test_cycle_spread_adds_one_flow_per_receiver_per_used_slice(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024)
        starts = Counter(flow["start_superslice"] for flow in flows)
        self.assertEqual(
            starts, Counter({slice_index: 216 for slice_index in range(107)})
        )
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(receiver_starts.values()), {1})

    def test_sparse_flow_matrix_is_balanced_and_symmetric(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024, fanout=64)
        offsets = {flow["offset"] for flow in flows}
        self.assertEqual(len(flows), 13_824)
        self.assertEqual(len(offsets), 64)
        self.assertTrue(all((108 - offset) % 108 in offsets for offset in offsets))
        self.assertEqual(
            Counter(flow["source"] for flow in flows),
            Counter({host: 64 for host in range(216)}),
        )
        self.assertEqual(
            Counter(flow["destination"] for flow in flows),
            Counter({host: 64 for host in range(216)}),
        )
        self.assertEqual(
            set(Counter(
                (flow["source_tor"], flow["destination_tor"])
                for flow in flows
            ).values()),
            {2},
        )

    def test_staggered_sparse_starts_are_balanced_and_unique(self):
        flows = FLOWS.build_flows(
            flow_size_bytes=512 * 1024,
            fanout=64,
            start_mode="staggered",
            active_window_ns=44_500,
            spread_superslices=72,
        )
        starts = Counter(flow["start_superslice"] for flow in flows)
        self.assertEqual(
            starts, Counter({slice_index: 192 for slice_index in range(72)})
        )
        self.assertEqual(len({flow["start_ns"] for flow in flows}), len(flows))
        self.assertTrue(all(flow["start_ns"] % 54_500 < 44_500 for flow in flows))
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(receiver_starts.values()), {1})

    def test_shared_analyzer_uses_two_host_tor_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "uniform.htsim"
            trace.write_text(
                "0 2 1048576 1000\n"
                "215 1 1048576 55500\n",
                encoding="ascii",
            )
            rows = ANALYZE.ANALYZER.parse_trace(trace, 2, 108, 54_500)
        self.assertEqual(rows[0]["source_tor"], 0)
        self.assertEqual(rows[0]["destination_tor"], 1)
        self.assertEqual(rows[1]["source_tor"], 107)
        self.assertEqual(rows[1]["destination_tor"], 0)
        self.assertEqual(rows[1]["start_superslice"], 1)

    def test_comparison_reports_direction_and_winner(self):
        fifo = {
            "completion_ratio": "0.8",
            "mean_fct_ms": "10",
            "credit_drop_ratio": "0.4",
        }
        priority = {
            "completion_ratio": "0.9",
            "mean_fct_ms": "8",
            "credit_drop_ratio": "0.5",
        }
        rows = {
            row["metric"]: row
            for row in COMPARE.compare_summaries(fifo, priority)
        }
        self.assertEqual(rows["completion_ratio"]["winner"], "rxhopprio")
        self.assertEqual(rows["mean_fct_ms"]["winner"], "rxhopprio")
        self.assertEqual(rows["credit_drop_ratio"]["winner"], "fifo")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "comparison.csv"
            COMPARE.write_comparison(output, list(rows.values()))
            with output.open(newline="", encoding="utf-8") as stream:
                written = list(csv.DictReader(stream))
        self.assertEqual(len(written), len(COMPARE.METRICS))

    def test_core_loader_and_run_script_support_unequal_downlinks_and_uplinks(self):
        topology_source = (
            ROOT / "src/opera/datacenter/dynexp_topology.cpp"
        ).read_text(encoding="ascii")
        run_script = (HERE / "run.sh").read_text(encoding="ascii")
        self.assertIn("j < _nul * _ntor", topology_source)
        self.assertIn("--scheduler MODE", run_script)
        self.assertIn("--fanout COUNT", run_script)
        self.assertIn("--spread-superslices N", run_script)
        self.assertIn("cycle_spread|staggered|synchronized", run_script)
        self.assertIn("priority_args=(-rxhopprio)", run_script)
        self.assertIn("run_case fifo", run_script)
        self.assertIn("run_case rxhopprio", run_script)


if __name__ == "__main__":
    unittest.main()
