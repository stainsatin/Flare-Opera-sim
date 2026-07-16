import importlib.util
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GENERATOR = load_module("ring_fct_generator", HERE / "generate_flows.py")
ANALYZER = load_module("ring_fct_analyzer", HERE / "analyze.py")


class RingFctExperimentTest(unittest.TestCase):
    def test_default_trace_has_repeated_symmetric_flow_waves(self):
        rows = GENERATOR.generate_rows(
            distance=3, flow_size=1_000_000, rounds=20, interval_ns=100_000
        )
        self.assertEqual(len(rows), 160)
        for round_index in range(20):
            wave = rows[round_index * 8 : (round_index + 1) * 8]
            self.assertEqual({row[3] for row in wave}, {round_index * 100_000})
            for src, dst, flow_size, _start_ns in wave:
                self.assertEqual(dst, (src + 3) % 8)
                self.assertEqual(flow_size, 1_000_000)

    def test_analyzer_combines_drop_throughput_and_fct_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            results = Path(temp_dir)
            traffic = results / "traffic"
            traffic.mkdir()
            trace_path = traffic / "k2.htsim"
            trace_path.write_text(
                "0 2 1000000 0\n1 3 1000000 100000\n", encoding="ascii"
            )
            log_path = results / "k2.log"
            log_path.write_text(
                "\n".join(
                    (
                        "Util 0.500000 0.1",
                        "Input 0.500000 0 0 0.1",
                        "FCT 0 2 1000000 0.2 0.0 100 10",
                        "FCT 1 3 1000000 0.3 0.1 100 11",
                        "CreditStats host 0 -1 100 100 0 4 0 0 0 0 0 0 0",
                        "CreditStats tor 0 2 150 100 0 16 50 10 0 40 0 80 40",
                        "CreditStats tor 2 0 100 100 0 2 0 0 0 0 0 0 0",
                        "DataQueueStats host 0 -1 0",
                        "DataQueueStats tor 0 2 0",
                        "DataQueueStats tor 2 0 0",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            summary, queues, flows = ANALYZER.analyze_case(
                log_path, trace_path, distance=2
            )
            self.assertEqual(len(queues), 3)
            self.assertEqual(len(flows), 2)
            self.assertEqual(summary["offered_flows"], 2)
            self.assertEqual(summary["completed_flows"], 2)
            self.assertEqual(summary["incomplete_flows"], 0)
            self.assertEqual(summary["completion_ratio"], 1.0)
            self.assertAlmostEqual(summary["offered_load_gbps"], 80.0)
            self.assertAlmostEqual(summary["workload_throughput_gbps"], 40.0)
            self.assertAlmostEqual(summary["mean_fct_ms"], 0.25)
            self.assertAlmostEqual(summary["median_fct_ms"], 0.2)
            self.assertAlmostEqual(summary["p95_fct_ms"], 0.3)
            self.assertAlmostEqual(summary["p99_fct_ms"], 0.3)
            self.assertEqual(summary["credit_drops"], 50)
            self.assertEqual(summary["overflow_drops"], 10)
            self.assertEqual(summary["shaping_drops"], 40)
            self.assertAlmostEqual(summary["sim_sampled_goodput_gbps"], 400.0)

    def test_run_script_defaults_match_documented_workload(self):
        script = (HERE / "run.sh").read_text()
        self.assertIn("SIMTIME=0.02", script)
        self.assertIn("FLOW_SIZE=1000000", script)
        self.assertIn("ROUNDS=20", script)
        self.assertIn("INTERVAL_NS=100000", script)
        self.assertIn("DISTANCES=(1 2 3)", script)
        self.assertIn('python3 "${SCRIPT_DIR}/analyze.py"', script)


if __name__ == "__main__":
    unittest.main()
