import io
import unittest
from pathlib import Path

import analyze


ROOT = Path(__file__).resolve().parents[2]


class MemoryLog:
    def __init__(self, text):
        self.text = text

    def open(self, **_kwargs):
        return io.StringIO(self.text)

    def __str__(self):
        return "memory.log"


class RingExperimentTest(unittest.TestCase):
    def test_topology_contains_valid_shortest_ring_routes(self):
        lines = (ROOT / "topologies/ring_8tor_graph.txt").read_text().splitlines()
        nodes, downlinks = map(int, lines[0].split())
        self.assertEqual((nodes, downlinks), (8, 1))
        adjacency = [list(map(int, line.split())) for line in lines[1 : 1 + nodes]]

        routes = {}
        for line in lines[1 + nodes :]:
            src, dst, *hops = map(int, line.split())
            current = src
            for next_tor in hops:
                self.assertIn(next_tor, adjacency[current])
                current = next_tor
            self.assertEqual(current, dst)
            self.assertEqual(len(hops), min((dst - src) % nodes, (src - dst) % nodes))
            routes[src, dst] = hops

        self.assertEqual(len(routes), nodes * (nodes - 1))
        self.assertEqual(max(map(len, routes.values())), 4)
        for distance in (1, 2, 4):
            for src in range(nodes):
                dst = (src + distance) % nodes
                forward = [src, *routes[src, dst]]
                reverse = [dst, *routes[dst, src]]
                self.assertEqual(reverse, list(reversed(forward)))

    def test_flow_files_match_requested_distance(self):
        for distance in (1, 2, 4):
            rows = [
                list(map(int, line.split()))
                for line in (ROOT / f"traffic/ring_8tor_k{distance}.htsim")
                .read_text()
                .splitlines()
            ]
            self.assertEqual(len(rows), 8)
            for src, dst, flow_size, start_ns in rows:
                self.assertEqual(dst, (src + distance) % 8)
                self.assertGreater(flow_size, 0)
                self.assertEqual(start_ns, 0)

    def test_analyzer_aggregates_credit_and_performance_metrics(self):
        log = MemoryLog(
            "\n".join(
                (
                    "Util 0.500000 0.1",
                    "Input 0.600000 0 0 0.1",
                    "Util 0.750000 1.0",
                    "Input 0.800000 0 0 1.0",
                    "Packetloss 10 100 2 4",
                    "Queue 0 1 3000 0",
                    "FCT 0 1 10000 2.5 0 1 0",
                    "CreditStats host 0 -1 100 95 0 10 5 3 1 0 1",
                    "DataQueueStats host 0 -1 2",
                    "CreditStats tor 0 1 90 88 0 16 2 2 0 0 0",
                    "DataQueueStats tor 0 1 3",
                    "CreditStats tor 1 0 88 88 0 4 0 0 0 0 0",
                    "DataQueueStats tor 1 0 0",
                )
            )
        )

        summary, queues = analyze.parse_log(log, distance=1, warmup_fraction=0.2)
        self.assertEqual(len(queues), 3)
        self.assertEqual(summary["generated_credits"], 100)
        self.assertEqual(summary["delivered_credits"], 88)
        self.assertEqual(summary["credit_drops"], 7)
        self.assertAlmostEqual(summary["credit_drop_ratio"], 0.07)
        self.assertEqual(summary["max_uplink_credit_queue_packets"], 16)
        self.assertAlmostEqual(summary["mean_goodput_gbps"], 600.0)
        self.assertEqual(summary["data_queue_drops"], 5)
        self.assertEqual(summary["topology_losses"], 4)
        self.assertEqual(summary["completed_flows"], 1)
        self.assertEqual(summary["mean_fct_ms"], 2.5)


if __name__ == "__main__":
    unittest.main()
