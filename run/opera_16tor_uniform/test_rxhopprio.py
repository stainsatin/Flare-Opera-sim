#!/usr/bin/env python3

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CREDIT_QUEUE_CPP = ROOT / "src/opera/creditqueue.cpp"
CREDIT_QUEUE_H = ROOT / "src/opera/creditqueue.h"
MAIN_CPP = ROOT / "src/opera/datacenter/main_xpass_dynexpTopology.cpp"
TOPOLOGY_CPP = ROOT / "src/opera/datacenter/dynexp_topology.cpp"


def function_body(source, signature):
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated function: {signature}")


class RxHopPriorityContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue_cpp = CREDIT_QUEUE_CPP.read_text(encoding="ascii")
        cls.queue_h = CREDIT_QUEUE_H.read_text(encoding="ascii")
        cls.main_cpp = MAIN_CPP.read_text(encoding="ascii")
        cls.topology_cpp = TOPOLOGY_CPP.read_text(encoding="ascii")

    def test_command_line_mode_is_opt_in_and_reaches_only_host_nics(self):
        self.assertIn("bool rx_hop_prio = false;", self.main_cpp)
        self.assertIn('!strcmp(argv[i],"-rxhopprio")', self.main_cpp)
        self.assertIn('{"rx_hop_prio",rx_hop_prio ? 1U : 0U}', self.main_cpp)

        alloc_src = function_body(
            self.topology_cpp, "Queue* DynExpTopology::alloc_src_queue"
        )
        alloc_tor = function_body(
            self.topology_cpp, "Queue* DynExpTopology::alloc_queue(QueueLogger* queueLogger, uint64_t"
        )
        self.assertIn('_params["rx_hop_prio"] != 0', alloc_src)
        self.assertNotIn("rx_hop_prio", alloc_tor)

    def test_priority_uses_current_real_route_and_not_tidalhop(self):
        compute = function_body(
            self.queue_cpp, "void CreditQueue::setRxCreditPriority"
        )
        self.assertIn("get_no_paths", compute)
        self.assertIn("fast_rand() % npaths", compute)
        self.assertIn("get_no_hops", compute)
        self.assertNotIn("get_tidalhop", compute)
        self.assertNotIn("get_maxhops", compute)

        select = function_body(self.queue_cpp, "Packet* CreditQueue::selectRxCredit")
        self.assertIn("time_to_slice(eventlist().now())", select)
        self.assertIn("slice != _last_priority_slice", select)
        self.assertIn("rebuildRxCreditPriorities(slice)", select)

    def test_slice_rebuild_covers_all_pending_but_not_in_service_credit(self):
        rebuild = function_body(
            self.queue_cpp, "void CreditQueue::rebuildRxCreditPriorities"
        )
        self.assertIn("_rx_credit_order.clear()", rebuild)
        self.assertIn("_enqueued_cred[0].begin()", rebuild)
        self.assertIn("pkt == _rx_credit_in_service", rebuild)
        self.assertIn("setRxCreditPriority(pkt, slice)", rebuild)
        self.assertIn("_last_priority_slice = slice", rebuild)

    def test_shortest_then_enqueue_sequence_and_exact_packet_completion(self):
        self.assertIn("lhs.hops < rhs.hops", self.queue_h)
        self.assertIn(
            "lhs.enqueue_sequence < rhs.enqueue_sequence", self.queue_h
        )
        complete = function_body(
            self.queue_cpp, "void NICCreditQueue::completeService"
        )
        self.assertIn("pkt = _rx_credit_in_service", complete)
        self.assertIn("find(_enqueued_cred[prio].begin()", complete)
        self.assertIn("scheduled_priority.route_slice == slice", complete)
        self.assertIn("path_index = scheduled_priority.path_index", complete)

    def test_uniform_run_scripts_keep_mode_off_unless_requested(self):
        for directory in ("opera_16tor_uniform", "opera_108tor_uniform"):
            script = (ROOT / "run" / directory / "run.sh").read_text(
                encoding="ascii"
            )
            self.assertIn("RX_HOP_PRIO=no", script)
            self.assertIn("--rxhopprio) RX_HOP_PRIO=yes", script)
            self.assertIn("RX_HOP_PRIO_ARGS=(-rxhopprio)", script)


if __name__ == "__main__":
    unittest.main()
