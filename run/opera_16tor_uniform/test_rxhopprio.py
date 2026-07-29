#!/usr/bin/env python3

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CREDIT_QUEUE_CPP = ROOT / "src/opera/creditqueue.cpp"
CREDIT_QUEUE_H = ROOT / "src/opera/creditqueue.h"
XPASS_CPP = ROOT / "src/opera/xpass.cpp"
XPASS_H = ROOT / "src/opera/xpass.h"
MAIN_CPP = ROOT / "src/opera/datacenter/main_xpass_dynexpTopology.cpp"
TOPOLOGY_CPP = ROOT / "src/opera/datacenter/dynexp_topology.cpp"
ANALYZER = ROOT / "run/opera_16tor_uniform/analyze.py"


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
        cls.xpass_cpp = XPASS_CPP.read_text(encoding="ascii")
        cls.xpass_h = XPASS_H.read_text(encoding="ascii")
        cls.main_cpp = MAIN_CPP.read_text(encoding="ascii")
        cls.topology_cpp = TOPOLOGY_CPP.read_text(encoding="ascii")
        cls.analyzer = ANALYZER.read_text(encoding="ascii")

    def test_command_line_mode_is_opt_in_and_weights_reach_only_host_nics(self):
        self.assertIn("bool rx_hop_prio = false;", self.main_cpp)
        self.assertIn("bool rx_prio_admit = false;", self.main_cpp)
        self.assertIn('!strcmp(argv[i],"-rxhopprio")', self.main_cpp)
        self.assertIn('!strcmp(argv[i],"-rxprioadmit")', self.main_cpp)
        self.assertIn('!strcmp(argv[i],"-rxhopweights")', self.main_cpp)
        self.assertIn("uint32_t rx_hop_weight_high = 4;", self.main_cpp)
        self.assertIn("uint32_t rx_hop_weight_medium = 2;", self.main_cpp)
        self.assertIn("uint32_t rx_hop_weight_low = 1;", self.main_cpp)
        self.assertNotIn("rxhopquantum", self.main_cpp)

        alloc_src = function_body(
            self.topology_cpp, "Queue* DynExpTopology::alloc_src_queue"
        )
        alloc_tor = function_body(
            self.topology_cpp,
            "Queue* DynExpTopology::alloc_queue(QueueLogger* queueLogger, uint64_t",
        )
        self.assertIn('_params.find("rx_hop_prio")', alloc_src)
        self.assertIn('_params.find("rx_prio_admit")', alloc_src)
        self.assertIn('rx_hop_weight_high', alloc_src)
        self.assertIn("new NICCreditQueue", alloc_src)
        self.assertNotIn("rx_hop_prio", alloc_tor)
        self.assertNotIn("rx_prio_admit", alloc_tor)

    def test_original_per_flow_credit_generation_is_restored(self):
        event = function_body(self.xpass_cpp, "void XPassSink::doNextEvent")
        emit = function_body(self.xpass_cpp, "XPassPull* XPassSink::emitCredit")
        self.assertIn("updateSliceFeedbackState()", event)
        self.assertIn("emitCredit()", event)
        for fragment in (
            "XPassPull::newpkt",
            "drand() <= rate",
            "set_tentative(false)",
            "set_tentative(true)",
            "_credit_counter++",
            "_tot_sent_creds++",
            "sendToNIC(p)",
            "nextCreditWait()",
            "feedbackControl2()",
        ):
            self.assertIn(fragment, emit)
        for removed in (
            "RxCreditFlowScheduler",
            "RxCreditFlowRequest",
            "requestCredit",
            "runFlowCreditScheduler",
            "_active_credit_flow",
            "_flow_credit_quantum",
            "_credit_request_pending",
            "_scheduled_credit_slice",
        ):
            self.assertNotIn(removed, self.queue_h + self.queue_cpp + self.xpass_h)

    def test_three_hop_classes_share_one_capacity(self):
        classify = function_body(self.queue_cpp, "inline int CreditQueue::creditClass")
        receive = function_body(self.queue_cpp, "void CreditQueue::receivePacket")
        self.assertIn("#define CRED_Q_N 3", self.queue_cpp)
        self.assertIn("pkt.get_maxhops()", classify)
        self.assertIn("hops <= 1", classify)
        self.assertIn("hops == 2", classify)
        self.assertIn("return 2", classify)
        self.assertIn("queuesize_cred() > _maxsize_cred", receive)
        self.assertNotIn("_maxsize_cred / CRED_Q_N", self.queue_cpp)
        self.assertNotIn("_maxsize_cred / 3", self.queue_cpp)

    def test_each_priority_class_is_fifo(self):
        admission = function_body(self.queue_cpp, "bool CreditQueue::handleCredit")
        complete = function_body(
            self.queue_cpp, "void NICCreditQueue::completeService"
        )
        self.assertIn("_enqueued_cred[queue_index].push_front(&pkt)", admission)
        self.assertIn("pkt = _enqueued_cred[prio].back()", complete)
        self.assertIn("_enqueued_cred[prio].pop_back()", complete)

    def test_admission_pushout_is_type_and_hop_aware(self):
        evict = function_body(
            self.queue_cpp, "bool CreditQueue::evictPriorityCredit"
        )
        find_victim = function_body(
            self.queue_cpp, "bool CreditQueue::evictCreditVictim"
        )
        receive = function_body(self.queue_cpp, "void CreditQueue::receivePacket")
        self.assertIn("arriving_tentative", evict)
        self.assertIn("evictCreditVictim(victim_class, true)", evict)
        self.assertIn("evictCreditVictim(victim_class, false)", evict)
        self.assertIn("victim_class > arriving_class", evict)
        self.assertIn("candidate == queue.back()", find_victim)
        self.assertIn("queue.erase(it)", find_victim)
        self.assertIn("evictPriorityCredit(pkt, credit_class)", receive)
        self.assertIn(
            "dropQueuedCredit(&pkt, queue_index, credit_class, false)", receive
        )

    def test_service_and_admission_flags_are_independent(self):
        queue_index = function_body(
            self.queue_cpp, "inline int CreditQueue::creditQueueIndex"
        )
        admission = function_body(self.queue_cpp, "bool CreditQueue::handleCredit")
        receive = function_body(self.queue_cpp, "void CreditQueue::receivePacket")
        self.assertIn("_rx_hop_prio", queue_index)
        self.assertNotIn("_rx_prio_admit", queue_index)
        self.assertIn("_rx_prio_admit", admission)
        self.assertIn("priorityQueuesize(credit_class)", admission)
        self.assertIn("bool priority_admission = _is_nic && _rx_prio_admit", receive)
        self.assertNotIn("evictPriorityCredit", function_body(
            self.queue_cpp, "inline int CreditQueue::next_cred"
        ))

    def test_credit_pacing_timer_reselects_after_pushout(self):
        event = function_body(self.queue_cpp, "void CreditQueue::doNextEvent")
        self.assertIn("_cred_tx_pending = false", event)
        self.assertIn("beginService()", event)
        self.assertNotIn("queuesize_cred(_next_prio) > 0", event)

    def test_service_is_weighted_and_work_conserving(self):
        select = function_body(self.queue_cpp, "inline int CreditQueue::next_cred")
        advance = function_body(
            self.queue_cpp, "void CreditQueue::advanceCreditPriority"
        )
        self.assertIn("_credit_weights = {4, 2, 1}", self.queue_cpp)
        self.assertIn("(_wrr_priority + offset) % CRED_Q_N", select)
        self.assertIn("!_enqueued_cred[prio].empty()", select)
        self.assertIn("_wrr_remaining--", advance)
        self.assertIn("(_wrr_priority + 1) % CRED_Q_N", advance)
        self.assertIn("_credit_weights[_wrr_priority]", advance)

    def test_priority_is_fixed_at_nic_arrival_but_route_can_change_at_send(self):
        send = function_body(self.xpass_cpp, "XPassSink::sendToNIC")
        receive = function_body(self.queue_cpp, "void CreditQueue::receivePacket")
        select = function_body(self.queue_cpp, "inline int CreditQueue::next_cred")
        complete = function_body(
            self.queue_cpp, "void NICCreditQueue::completeService"
        )
        for fragment in (
            "time_to_slice(eventlist().now())",
            "get_no_paths",
            "get_no_hops",
            "set_slice_sent(slice)",
            "set_path_index(path_index)",
            "set_maxhops(hops)",
        ):
            self.assertIn(fragment, send)
        self.assertIn("int credit_class = creditClass(pkt)", receive)
        self.assertNotIn("time_to_slice", select)
        self.assertIn("pkt->get_slice_sent() == slice", complete)
        self.assertIn("path_index = pkt->get_path_index()", complete)
        self.assertIn("path_index = fast_rand() % npaths", complete)

    def test_priority_and_pushout_statistics_are_exported(self):
        self.assertIn("CreditPriorityStats", self.queue_cpp)
        self.assertIn("_priority_queued_bytes", self.queue_h)
        self.assertIn("pushout", self.queue_h)
        self.assertIn("priority_queues", self.analyzer)
        self.assertIn('"per_priority_queue.csv"', self.analyzer)
        self.assertIn('"pushout_credit_drops"', self.analyzer)

    def test_uniform_run_scripts_keep_mode_off_unless_requested(self):
        for directory in ("opera_16tor_uniform", "opera_108tor_uniform"):
            script = (ROOT / "run" / directory / "run.sh").read_text(
                encoding="ascii"
            )
            self.assertIn("RX_HOP_PRIO=no", script)
            self.assertIn("--rxhopprio) RX_HOP_PRIO=yes", script)
            self.assertIn("RX_HOP_WEIGHTS=4:2:1", script)
            self.assertIn("-rxhopprio -rxhopweights", script)
            self.assertNotIn("rxhopquantum", script)


if __name__ == "__main__":
    unittest.main()
