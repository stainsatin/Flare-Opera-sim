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
COMPARE_SPEC = importlib.util.spec_from_file_location(
    "opera_24tor_light_compare", HERE / "compare.py"
)
COMPARE = importlib.util.module_from_spec(COMPARE_SPEC)
COMPARE_SPEC.loader.exec_module(COMPARE)
SUMMARY_SPEC = importlib.util.spec_from_file_location(
    "opera_24tor_rcdcp_summary", HERE / "summarize_seeds.py"
)
SUMMARY = importlib.util.module_from_spec(SUMMARY_SPEC)
SUMMARY_SPEC.loader.exec_module(SUMMARY)


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

    def test_run_script_uses_multiseed_rcdcp_modes(self):
        script = (HERE / "run.sh").read_text(encoding="ascii")
        self.assertIn("SIMTIME=0.02", script)
        self.assertIn("FLOW_SIZE_MIB=2", script)
        self.assertIn("START_SUPERSLICES=8", script)
        self.assertIn("SEEDS=1,2,3,4,5", script)
        self.assertIn("CWND=4", script)
        self.assertIn("results_rcdcp_4x2MiB_stagger8", script)
        self.assertIn('FLOW_GENERATOR="${SCRIPT_DIR}/generate_flows.py"', script)
        self.assertIn("--flow-generator", script)
        self.assertIn('python3 "${FLOW_GENERATOR}"', script)
        for mode in ("fifo_original", "fifo_global", "wrr421", "wrr821"):
            self.assertIn(mode, script)
        self.assertIn("-rxglobaltentative -rxhopprio -rxhopweights 4 2 1", script)
        self.assertIn("-rxglobaltentative -rxhopprio -rxhopweights 8 2 1", script)
        self.assertIn("-rxcreditslottrace", script)
        self.assertIn("summarize_seeds.py", script)
        self.assertIn('--mode MODE               fifo_original, fifo_global, wrr421, wrr821, or all', script)
        self.assertIn("opera_24tor_4host_55us.txt", script)
        self.assertTrue(
            (ROOT / "topologies" / "opera_24tor_4host_55us.txt").is_file()
        )

    def test_four_way_comparison_tracks_rcdcp_efficiency(self):
        comparer = (HERE / "compare.py").read_text(encoding="ascii")
        self.assertIn(
            'CASES = ("fifo_original", "fifo_global", "wrr421", "wrr821")',
            comparer,
        )
        for metric in (
            "mean_admitted_credit_path_hops",
            "mean_delivered_credit_path_hops",
            "mean_delivered_actual_credit_hops",
            "regular_delivered_share",
            "tor_queue_credit_drops",
            "total_credit_network_link_bytes",
            "mean_fct_ms",
            "regular_delivered_per_nic_slot",
            "regular_delivered_per_credit_hop",
            "tentative_nic_slot_share",
        ):
            self.assertIn(metric, comparer)

        summaries = {
            "fifo_original": {"mean_fct_ms": "4.0"},
            "fifo_global": {"mean_fct_ms": "3.5"},
            "wrr421": {"mean_fct_ms": "3.0"},
            "wrr821": {"mean_fct_ms": "2.5"},
        }
        rows = {row["metric"]: row for row in COMPARE.compare_summaries(summaries)}
        self.assertEqual(rows["mean_fct_ms"]["best"], "wrr821")
        self.assertEqual(rows["mean_fct_ms"]["wrr421_delta_vs_fifo"], -1.0)

    def test_multiseed_summary_statistics(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(SUMMARY.aggregate(values, "mean"), 3.0)
        self.assertAlmostEqual(SUMMARY.aggregate(values, "stddev"), 2.5 ** 0.5)
        self.assertAlmostEqual(
            SUMMARY.aggregate(values, "ci95"), 2.776 * (2.5 ** 0.5) / (5 ** 0.5)
        )
        for metric in (
            "regular_delivered_per_nic_slot",
            "regular_delivered_per_credit_hop",
            "tentative_slot_share",
            "active_throughput",
        ):
            self.assertIn(metric, SUMMARY.METRICS)


if __name__ == "__main__":
    unittest.main()
