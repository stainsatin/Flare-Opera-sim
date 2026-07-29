#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC = importlib.util.spec_from_file_location(
    "opera_24tor_sparse_generator", HERE / "generate_flows.py"
)
FLOWS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FLOWS)


def read_route_hops(path):
    lines = path.read_text(encoding="ascii").splitlines()
    _, _, _, tors = map(int, lines[0].split())
    slices = int(lines[1].split()[0])
    cursor = 2 + slices
    routes = []
    for slice_index in range(slices):
        if int(lines[cursor]) != slice_index:
            raise AssertionError("invalid route section")
        cursor += 1
        route_hops = {}
        for _ in range(tors * (tors - 1)):
            fields = list(map(int, lines[cursor].split()))
            cursor += 1
            route_hops[fields[0], fields[1]] = len(fields) - 2
        routes.append(route_hops)
    return routes


class OperaTwentyFourTorSparseExperimentTest(unittest.TestCase):
    def test_default_load_is_balanced_and_one_quarter_the_previous_bytes(self):
        flows = FLOWS.build_flows()
        self.assertEqual(len(flows), 192)
        self.assertEqual({flow["bytes"] for flow in flows}, {1024 * 1024})
        expected = Counter({host: 2 for host in range(96)})
        self.assertEqual(Counter(flow["source"] for flow in flows), expected)
        self.assertEqual(Counter(flow["destination"] for flow in flows), expected)
        self.assertEqual(sum(flow["bytes"] for flow in flows), 192 * 1024 * 1024)

    def test_matrix_is_lane_preserving_reverse_paired_and_balanced(self):
        flows = FLOWS.build_flows(flow_size_bytes=1024)
        self.assertTrue(
            all(flow["source"] % 4 == flow["destination"] % 4 for flow in flows)
        )
        tor_pairs = Counter(
            (flow["source_tor"], flow["destination_tor"]) for flow in flows
        )
        self.assertEqual(len(tor_pairs), 24 * 4)
        self.assertEqual(set(tor_pairs.values()), {2})
        self.assertEqual(
            Counter(flow["source_tor"] for flow in flows),
            Counter({tor: 8 for tor in range(24)}),
        )
        self.assertEqual(
            Counter(flow["destination_tor"] for flow in flows),
            Counter({tor: 8 for tor in range(24)}),
        )

    def test_starts_are_globally_sparse_but_locally_paired(self):
        flows = FLOWS.build_flows()
        starts = Counter(flow["start_superslice"] for flow in flows)
        self.assertEqual(starts, Counter({slice_index: 12 for slice_index in range(16)}))
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(receiver_starts.values()), {2})
        source_tor_starts = Counter(
            (flow["source_tor"], flow["start_superslice"]) for flow in flows
        )
        self.assertEqual(set(source_tor_starts.values()), {1})
        for receiver in range(96):
            times = sorted(
                flow["start_ns"]
                for flow in flows
                if flow["destination"] == receiver
            )
            self.assertEqual(times[1] - times[0], 1_000)
        self.assertEqual(len({flow["start_ns"] for flow in flows}), 192)
        self.assertTrue(all(flow["start_ns"] % 55_000 < 53_000 for flow in flows))

    def test_most_receiver_pairs_have_a_hop_priority_choice(self):
        routes = read_route_hops(ROOT / "topologies" / "opera_24tor_4host_55us.txt")
        flows = FLOWS.build_flows()
        grouped = {}
        for flow in flows:
            grouped.setdefault(flow["destination"], []).append(flow)
        contrasting = 0
        for pair in grouped.values():
            superslice = pair[0]["start_superslice"]
            internal_slice = 3 * superslice
            hops = {
                routes[internal_slice][
                    flow["destination_tor"], flow["source_tor"]
                ]
                for flow in pair
            }
            contrasting += len(hops) > 1
        self.assertEqual(contrasting, 63)

    def test_trace_has_four_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "sparse.htsim"
            FLOWS.write_trace(trace, FLOWS.build_flows(flow_size_bytes=1024))
            rows = [line.split() for line in trace.read_text().splitlines()]
        self.assertEqual(len(rows), 192)
        self.assertTrue(all(len(row) == 4 for row in rows))

    def test_runner_reuses_four_case_harness_with_sparse_defaults(self):
        script = (HERE / "run.sh").read_text(encoding="ascii")
        self.assertIn("opera_24tor_4flow_2MiB/run.sh", script)
        self.assertIn('--flow-generator "${SCRIPT_DIR}/generate_flows.py"', script)
        self.assertIn("--flow-size-mib 1", script)
        self.assertIn("--start-superslices 16", script)
        self.assertIn("--scheduler all", script)
        self.assertIn("--rxhop-weights 8:2:1", script)


if __name__ == "__main__":
    unittest.main()
