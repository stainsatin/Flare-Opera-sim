import importlib.util
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = load_module("multibottleneck_generator", HERE / "generate_flows.py")
ANALYZER = load_module("multibottleneck_analyzer", HERE / "analyze.py")
ISOLATED_ANALYZER = load_module(
    "multibottleneck_isolated_analyzer", HERE / "analyze_isolated.py"
)


class MultiBottleneckExperimentTest(unittest.TestCase):
    def test_topology_is_irregular_and_contains_all_experiment_routes(self):
        lines = (ROOT / "topologies/multibottleneck_13tor_graph.txt").read_text().splitlines()
        nodes, downlinks = map(int, lines[0].split())
        self.assertEqual((nodes, downlinks), (13, 1))
        adjacency = [list(map(int, line.split())) for line in lines[1 : 1 + nodes]]
        self.assertEqual(min(map(len, adjacency)), 1)
        self.assertEqual(max(map(len, adjacency)), 5)
        for tor, neighbors in enumerate(adjacency):
            for neighbor in neighbors:
                self.assertIn(tor, adjacency[neighbor])

        routes = {}
        for line in lines[1 + nodes :]:
            src, dst, *next_hops = map(int, line.split())
            current = src
            for next_tor in next_hops:
                self.assertIn(next_tor, adjacency[current])
                current = next_tor
            self.assertEqual(current, dst)
            routes[src, dst] = next_hops

        expected_credit_paths = {
            (12, 3): [3],
            (12, 4): [3, 7, 8, 4],
            (10, 2): [6, 2],
            (10, 12): [6, 7, 3, 12],
            (0, 7): [7],
            (0, 4): [7, 8, 4],
            (1, 7): [7],
            (1, 5): [7, 8, 9, 5],
            (2, 6): [6],
            (2, 1): [6, 7, 1],
        }
        for endpoints, path in expected_credit_paths.items():
            self.assertEqual(routes[endpoints], path)
            src, dst = endpoints
            reverse_nodes = list(reversed([src, *path]))
            self.assertEqual(routes[dst, src], reverse_nodes[1:])

    def test_generator_encodes_explicit_order_without_cross_wave_overlap(self):
        short_first = GENERATOR.generate_rows(
            "short_first", 1_000_000, 2, 10_000_000, 1_000
        )
        self.assertEqual(len(short_first), 20)
        self.assertEqual(short_first[0]["flow_class"], "short")
        self.assertEqual(short_first[0]["start_ns"], 0)
        self.assertEqual(short_first[1]["flow_class"], "long")
        self.assertEqual(short_first[1]["start_ns"], 1_000)
        self.assertEqual(short_first[10]["start_ns"], 10_000_000)

        long_first = GENERATOR.generate_rows(
            "long_first", 1_000_000, 1, 10_000_000, 1_000
        )
        self.assertEqual(long_first[0]["flow_class"], "short")
        self.assertEqual(long_first[0]["start_ns"], 1_000)
        self.assertEqual(long_first[1]["flow_class"], "long")
        self.assertEqual(long_first[1]["start_ns"], 0)

        simultaneous = GENERATOR.generate_rows(
            "simultaneous", 1_000_000, 2, 10_000_000, 1_000
        )
        self.assertEqual({row["start_ns"] for row in simultaneous[:10]}, {0})
        self.assertEqual(simultaneous[0]["flow_class"], "short")
        self.assertEqual(simultaneous[10]["flow_class"], "long")

    def test_generator_and_analyzer_support_isolated_rounds(self):
        short_only = GENERATOR.generate_rows(
            "short_only", 10_000_000, 2, 20_000_000, 0
        )
        long_only = GENERATOR.generate_rows(
            "long_only", 10_000_000, 2, 20_000_000, 0
        )
        self.assertEqual(len(short_only), 10)
        self.assertEqual(len(long_only), 10)
        self.assertEqual({row["flow_class"] for row in short_only}, {"short"})
        self.assertEqual({row["flow_class"] for row in long_only}, {"long"})
        self.assertEqual(short_only[5]["start_ns"], 20_000_000)
        self.assertEqual(long_only[5]["start_ns"], 20_000_000)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "short_only.htsim"
            path.write_text(
                "\n".join(
                    f"{row['sender']} {row['receiver']} {row['bytes']} "
                    f"{row['start_ns']}"
                    for row in short_only
                )
                + "\n",
                encoding="ascii",
            )
            parsed = ISOLATED_ANALYZER.parse_trace(path, "short_only")
            self.assertEqual(parsed[4]["round"], 0)
            self.assertEqual(parsed[5]["round"], 1)

    def test_analyzer_combines_fct_and_per_flow_credit_waste(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            trace_path = root / "short_first.htsim"
            trace_path.write_text(
                "3 12 1000000 0\n4 12 1000000 1000\n", encoding="ascii"
            )
            log_path = root / "short_first.log"
            log_path.write_text(
                "\n".join(
                    (
                        "FCT 3 12 1000000 0.1 0.0 700 0",
                        "FCT 4 12 1000000 0.4 0.001 2800 1",
                        "CreditStats host 12 -1 200 160 0 16 40 10 0 30 0 100 70",
                        "CreditStats tor 3 1 120 100 0 16 20 5 0 15 0 80 65",
                        "DataQueueStats host 12 -1 0",
                        "DataQueueStats tor 3 1 0",
                        "Packetloss 0 0 0 0",
                        "FlowCreditStats 0 3 12 1 100 80 180 160 20 10 0 10 0 50 40 5",
                        "FlowCreditStats 1 4 12 4 100 40 300 220 60 20 0 40 0 100 60 90",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            trace = ANALYZER.parse_trace(trace_path)
            queues, credits, fcts, data_drops, losses = ANALYZER.parse_log(log_path)
            rows = ANALYZER.build_flow_rows("short_first", trace, credits, fcts)
            short = ANALYZER.summarize(rows, "short_first", "short", 0.01)
            long = ANALYZER.summarize(rows, "short_first", "long", 0.01)

            self.assertEqual(len(queues), 2)
            self.assertEqual(data_drops, 0)
            self.assertEqual(losses, 0)
            self.assertEqual(short["completed_flows"], 1)
            self.assertAlmostEqual(short["mean_fct_ms"], 0.1)
            self.assertAlmostEqual(short["mean_flow_goodput_gbps"], 80.0)
            self.assertAlmostEqual(short["credit_drop_ratio"], 0.2)
            self.assertAlmostEqual(short["waste_hops_per_generated"], 0.05)
            self.assertAlmostEqual(long["credit_drop_ratio"], 0.6)
            self.assertAlmostEqual(long["waste_hops_per_drop"], 1.5)

            credits[0]["path_hops"] = 2
            with self.assertRaisesRegex(RuntimeError, "expected 1"):
                ANALYZER.build_flow_rows("short_first", trace, credits, fcts)

    def test_core_and_run_script_expose_required_features(self):
        graph_source = (ROOT / "src/opera/datacenter/graph_topology.cpp").read_text()
        self.assertIn("_nul = max(_nul", graph_source)
        main_source = (
            ROOT / "src/opera/datacenter/main_xpass_graphTopology.cpp"
        ).read_text()
        self.assertIn("reportFlowCreditStats();", main_source)
        credit_source = (ROOT / "src/opera/creditqueue.cpp").read_text()
        self.assertIn('cout << "FlowCreditStats "', credit_source)

        script = (HERE / "run.sh").read_text()
        self.assertIn("CASES=(short_first long_first simultaneous)", script)
        self.assertIn("INTERVAL_NS=10000000", script)
        self.assertIn("ORDER_GAP_NS=1000", script)

        isolated_script = (HERE / "run_isolated.sh").read_text()
        self.assertIn("CASES=(short_only long_only)", isolated_script)
        self.assertIn("FLOW_SIZE=10000000", isolated_script)
        self.assertIn("INTERVAL_NS=20000000", isolated_script)


if __name__ == "__main__":
    unittest.main()
