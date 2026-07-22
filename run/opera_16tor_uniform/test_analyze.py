#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

import analyze


class AnalyzeTest(unittest.TestCase):
    def test_extended_dynamic_stats_are_parsed(self):
        log = """\
FCT 0 4 33554432 5.0 0.001 40000 0
CreditStats host 4 -1 100 90 0 4 10 5 0 5 0 20 10
DataQueueStats host 4 -1 1 3000 0
CreditStats tor 1 4 90 70 0 8 15 0 0 15 0 40 25
DataQueueStats tor 1 4 2 4500 0
FlowCreditStats 0 0 4 2 100 70 190 160 30 5 0 20 0 40 25 60 5 1 4 200
TopologyClipStats 5 2 1 0
TopologyWrongDstStats 0 1 0 0
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uniform.log"
            path.write_text(log, encoding="ascii")
            parsed = analyze.parse_log(path)

        self.assertEqual(parsed["flow_credits"][0]["topology"], 5)
        self.assertEqual(parsed["flow_credits"][0]["path_hops_sum"], 200)
        self.assertEqual(parsed["queues"][0]["max_data_queue_bytes"], 3000)
        self.assertEqual(parsed["topology_clip"]["data"], 2)
        self.assertEqual(parsed["topology_wrong_dst"]["data"], 1)

    def test_queue_roles_use_four_downlinks(self):
        queues = [
            {"scope": "host", "id": 7, "port": -1, "max_data_queue_bytes": 0},
            {"scope": "tor", "id": 1, "port": 3, "max_data_queue_bytes": 1500},
            {"scope": "tor", "id": 1, "port": 6, "max_data_queue_bytes": 3000},
        ]
        analyze.add_queue_labels(queues, hosts_per_tor=4)
        self.assertEqual(queues[0]["tor"], 1)
        self.assertEqual(queues[0]["role"], "host_nic")
        self.assertEqual(queues[1]["role"], "tor_downlink")
        self.assertEqual(queues[2]["role"], "tor_uplink")
        self.assertEqual(queues[2]["rotor"], 2)
        self.assertEqual(queues[2]["max_data_queue_packets"], 2.0)

    def test_balanced_summary_and_per_tor_rows(self):
        flow_rows = []
        for source in range(64):
            source_tor = source // 4
            destination_tor = (source_tor + (1, 5, 9, 13)[source % 4]) % 16
            flow_rows.append(
                {
                    "flow_id": source,
                    "source_tor": source_tor,
                    "destination_tor": destination_tor,
                    "bytes": 1_000_000,
                    "start_ms": 0.001,
                    "finish_ms": 10.001,
                    "fct_ms": 10.0,
                    "flow_goodput_gbps": 0.8,
                    "completed": True,
                    "unfinished_marker": False,
                    "generated": 100,
                    "delivered": 80,
                    "dropped": 20,
                    "overflow": 5,
                    "timeout": 0,
                    "shaping": 10,
                    "tentative": 0,
                    "topology": 5,
                    "shaping_checks": 20,
                    "shaping_admitted": 10,
                    "waste_hops": 40,
                    "path_hops_sum": 250,
                    "path_hops_min": 1,
                    "path_hops_max": 8,
                }
            )

        queue_defaults = {
            "received": 0,
            "transmitted": 0,
            "max_queued": 0,
            "dropped": 0,
            "overflow": 0,
            "timeout": 0,
            "shaping": 0,
            "tentative": 0,
            "shaping_checks": 0,
            "shaping_admitted": 0,
            "data_drops": 0,
            "max_data_queue_bytes": 0,
        }
        queue_rows = []
        for host in range(64):
            queue_rows.append(
                {
                    **queue_defaults,
                    "scope": "host",
                    "id": host,
                    "port": -1,
                    "transmitted": 80,
                }
            )
        for tor in range(16):
            for port in range(8):
                queue_rows.append(
                    {**queue_defaults, "scope": "tor", "id": tor, "port": port}
                )

        parsed = {
            "utilization": [(1.0, 0.5)],
            "input_load": [(1.0, 0.5)],
            "topology_clip": {"credit": 320, "data": 0, "control": 0, "other": 0},
            "topology_wrong_dst": {"credit": 0, "data": 0, "control": 0, "other": 0},
        }
        summary = analyze.build_summary(
            flow_rows, queue_rows, parsed, 0.05, hosts_per_tor=4, cycle_us=232.0
        )
        tor_rows = analyze.build_tor_rows(flow_rows, queue_rows, hosts_per_tor=4)

        self.assertEqual(summary["completed_flows"], 64)
        self.assertEqual(summary["generated_credits"], 6_400)
        self.assertAlmostEqual(summary["credit_drop_ratio"], 0.2)
        self.assertEqual(len(tor_rows), 16)
        self.assertTrue(all(row["outgoing_flows"] == 4 for row in tor_rows))
        self.assertTrue(all(row["incoming_flows"] == 4 for row in tor_rows))


if __name__ == "__main__":
    unittest.main()
