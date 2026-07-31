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
FlowCreditStats 0 0 4 2 100 70 190 160 30 5 0 20 0 40 25 60 5 1 4 200 90 10 20 1 4 180 1 4 140 140 0 3
CreditHopStats 2 regular 60 55 50 5 5 100 0
CreditHopStats 2 tentative 40 35 20 5 15 40 0
CreditPriorityStats host 4 -1 high 4 40 35 0 12 5 0
CreditPriorityStats host 4 -1 medium 2 35 25 0 15 10 2
CreditPriorityStats host 4 -1 low 1 25 10 0 20 15 3
NICCreditSlot 1000 4 3 2 1 1 1 0 regular high 0 1 3 2 1 100 200 regular_first -1 0
NICCreditSlotStats 4 10 1 9 1 1 0 0
FeedbackWindowTrace 1000 4 0 1 3 50 40 100 10 7 3 1 2 20 2 18 0.3 0.2 0.05 10 3 1 0.333333333333
RegularWRRTrace 1000 4 3 0 0 1 1 0 0 0
RegularWRRStats 4 4 2 1 10 5 2 9 4 1 1 1 1
TentativeAdmission 900 4 0 3 1 high 1 1 0 2 4 admit
CreditTypeClassStats tor 1 4 tentative high 40 25 0 12 15 10 2 3 0 0
CreditLifecycleStats regular high 60 58 55 50 2 5 0 1 1 0 0 0 2 1 1 1 100
CreditLifecycleStats tentative high 40 35 35 20 5 15 4 1 0 0 0 10 3 1 1 0 70
CreditTimeSeries 0 0 55000000 60 40 55 20 50 20 30 10 5 20 15 10 30 10 5 20 15 10 60.0 100 2 50 10 0.4 0.2 1.5 1.2 0 2
CreditTimeSeries 1 55000000 110000000 40 60 35 30 30 25 20 15 5 30 10 10 20 15 5 30 10 10 35.0 100 2 40 16 0.8 0.3 1.2 0.8 0 2
TopologyClipStats 5 2 1 0
TopologyWrongDstStats 0 1 0 0
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "uniform.log"
            path.write_text(log, encoding="ascii")
            parsed = analyze.parse_log(path)
            slot_rows = (Path(directory) / "per_nic_credit_slot.csv").read_text()
            feedback_rows = (
                Path(directory) / "feedback_window_trace.csv"
            ).read_text()
            admission_rows = (Path(directory) / "tentative_admission.csv").read_text()

        self.assertEqual(parsed["flow_credits"][0]["topology"], 5)
        self.assertEqual(parsed["flow_credits"][0]["path_hops_sum"], 200)
        self.assertEqual(parsed["flow_credits"][0]["admitted"], 90)
        self.assertEqual(parsed["flow_credits"][0]["delivered_path_hops_sum"], 140)
        self.assertEqual(parsed["flow_credits"][0]["pushout"], 3)
        self.assertEqual(len(parsed["priority_queues"]), 3)
        self.assertEqual(parsed["priority_queues"][1]["priority"], "medium")
        self.assertEqual(parsed["priority_queues"][1]["pushouts"], 2)
        hop_rows = analyze.build_credit_hop_rows(parsed)
        combined = next(row for row in hop_rows if row["credit_type"] == "all")
        self.assertEqual(combined["delivered"], 70)
        self.assertEqual(combined["delivered_actual_hops"], 140)
        self.assertEqual(combined["delivered_link_bytes"], 140 * 64)
        self.assertAlmostEqual(combined["path_delivery_ratio"], 70 / 90)
        self.assertEqual(parsed["queues"][0]["max_data_queue_bytes"], 3000)
        self.assertEqual(parsed["topology_clip"]["data"], 2)
        self.assertEqual(parsed["topology_wrong_dst"]["data"], 1)
        self.assertEqual(len(parsed["lifecycle_stats"]), 2)
        self.assertEqual(parsed["lifecycle_stats"][0]["network_hops"], 100)
        self.assertEqual(parsed["type_class_stats"][0]["tentative_drop"], 10)
        self.assertEqual(len(parsed["credit_time_series"]), 2)
        time_rows, lag_rows = analyze.build_credit_time_series(
            parsed,
            [
                {
                    "start_ms": 0.0,
                    "finish_ms": 0.2,
                    "completed": True,
                }
            ],
            superslice_ns=55_000,
        )
        self.assertAlmostEqual(time_rows[0]["tentative_generated_share"], 0.4)
        self.assertAlmostEqual(time_rows[0]["mean_regular_probability"], 0.6)
        self.assertAlmostEqual(time_rows[0]["feedback_regular_loss_ratio"], 0.2)
        self.assertEqual(time_rows[0]["regular_cohort_pending"], 10)
        self.assertAlmostEqual(
            time_rows[0]["regular_cohort_drop_ratio_resolved"], 0.4
        )
        self.assertAlmostEqual(time_rows[1]["mean_feedback_rate_delta"], -0.2)
        self.assertEqual(time_rows[0]["active_flows_midpoint"], 1)
        self.assertEqual(lag_rows[1]["lag_us"], 55.0)
        self.assertEqual(set(time_rows[0]), set(analyze.CREDIT_TIME_SERIES_OUTPUT_FIELDS))
        self.assertEqual(set(lag_rows[0]), set(analyze.CREDIT_TIME_SERIES_LAG_FIELDS))
        self.assertEqual(parsed["nic_slot_totals"]["regular"], 1)
        self.assertEqual(parsed["nic_slot_totals"]["total_opportunities"], 10)
        self.assertEqual(parsed["feedback_windows"][0]["false_loss_count"], 1)
        self.assertEqual(
            parsed["feedback_windows"][0]["controller_regular_issued"], 10
        )
        self.assertEqual(parsed["regular_wrr_stats"]["selected_high"], 9)
        self.assertIn("regular,high", slot_rows)
        self.assertIn("0.333333333333", feedback_rows)
        self.assertIn("high,1,1,0,2,4,admit", admission_rows)

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
                    "admitted": 80,
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
                    "endpoint_dropped": 20,
                    "path_dropped": 0,
                    "admitted_path_hops_sum": 200,
                    "admitted_path_hops_min": 1,
                    "admitted_path_hops_max": 8,
                    "delivered_path_hops_sum": 200,
                    "delivered_path_hops_min": 1,
                    "delivered_path_hops_max": 8,
                    "delivered_actual_hops_sum": 192,
                    "delivered_hop_mismatches": 0,
                    "pushout": 0,
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
            "lifecycle_stats": [
                {
                    "credit_type": "regular",
                    "priority_class": "high",
                    "generated": 4_000,
                    "admitted": 3_900,
                    "sent": 3_800,
                    "delivered": 3_500,
                    "endpoint_drop": 500,
                    "path_drop": 300,
                    "endpoint_tentative": 0,
                    "endpoint_shaping": 200,
                    "endpoint_overflow": 200,
                    "endpoint_timeout": 100,
                    "endpoint_pushout": 0,
                    "path_tentative": 0,
                    "path_shaping": 200,
                    "path_overflow": 100,
                    "path_topology_clip": 0,
                    "path_wrong_dst": 0,
                    "network_hops": 8_000,
                },
                {
                    "credit_type": "tentative",
                    "priority_class": "low",
                    "generated": 2_400,
                    "admitted": 1_500,
                    "sent": 1_200,
                    "delivered": 1_000,
                    "endpoint_drop": 1_400,
                    "path_drop": 200,
                    "endpoint_tentative": 1_000,
                    "endpoint_shaping": 200,
                    "endpoint_overflow": 100,
                    "endpoint_timeout": 100,
                    "endpoint_pushout": 0,
                    "path_tentative": 100,
                    "path_shaping": 50,
                    "path_overflow": 50,
                    "path_topology_clip": 0,
                    "path_wrong_dst": 0,
                    "network_hops": 4_000,
                },
            ],
            "type_class_stats": [],
            "nic_slot_totals": {"total": 5_000, "fallback": 250},
            "credit_hop_stats": [
                {"credit_type": "regular", "admitted": 4_000, "delivered": 3_500},
                {"credit_type": "tentative", "admitted": 1_120, "delivered": 1_620},
            ],
            "topology_clip": {"credit": 320, "data": 0, "control": 0, "other": 0},
            "topology_wrong_dst": {"credit": 0, "data": 0, "control": 0, "other": 0},
        }
        summary = analyze.build_summary(
            flow_rows, queue_rows, parsed, 0.05, hosts_per_tor=4, cycle_us=232.0
        )
        tor_rows = analyze.build_tor_rows(flow_rows, queue_rows, hosts_per_tor=4)

        self.assertEqual(summary["completed_flows"], 64)
        self.assertEqual(summary["generated_credits"], 6_400)
        self.assertEqual(summary["admitted_credits"], 5_120)
        self.assertAlmostEqual(summary["regular_admitted_share"], 3_900 / 5_120)
        self.assertAlmostEqual(summary["regular_delivered_share"], 3_500 / 5_120)
        self.assertEqual(summary["mean_delivered_credit_path_hops"], 2.5)
        self.assertEqual(summary["mean_delivered_actual_credit_hops"], 2.4)
        self.assertEqual(summary["total_credit_network_link_bytes"], 12_000 * 64)
        self.assertAlmostEqual(summary["regular_delivered_per_nic_slot"], 0.7)
        self.assertAlmostEqual(
            summary["regular_delivered_per_credit_hop"], 3_500 / 12_000
        )
        self.assertAlmostEqual(summary["tentative_nic_slot_share"], 0.24)
        self.assertAlmostEqual(summary["nic_slot_fallback_ratio"], 0.05)
        self.assertAlmostEqual(summary["credit_drop_ratio"], 0.2)
        self.assertEqual(summary["mean_fct"], summary["mean_fct_ms"])
        self.assertEqual(
            summary["active_throughput"],
            summary["active_makespan_throughput_gbps"],
        )
        self.assertEqual(summary["data_drop"], summary["known_data_drops"])
        self.assertEqual(len(tor_rows), 16)
        self.assertTrue(all(row["outgoing_flows"] == 4 for row in tor_rows))
        self.assertTrue(all(row["incoming_flows"] == 4 for row in tor_rows))


if __name__ == "__main__":
    unittest.main()
