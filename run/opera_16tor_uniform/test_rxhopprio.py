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

    def test_command_line_mode_is_opt_in_and_reaches_only_host_nics(self):
        self.assertIn("bool rx_hop_prio = false;", self.main_cpp)
        self.assertIn("uint32_t rx_hop_quantum = 16;", self.main_cpp)
        self.assertIn('!strcmp(argv[i],"-rxhopprio")', self.main_cpp)
        self.assertIn('!strcmp(argv[i],"-rxhopquantum")', self.main_cpp)
        self.assertIn('{"rx_hop_prio",rx_hop_prio ? 1U : 0U}', self.main_cpp)
        self.assertIn('{"rx_hop_quantum",rx_hop_quantum}', self.main_cpp)
        self.assertIn("host-level flow-aware Credit", self.main_cpp)

        alloc_src = function_body(
            self.topology_cpp, "Queue* DynExpTopology::alloc_src_queue"
        )
        alloc_tor = function_body(
            self.topology_cpp, "Queue* DynExpTopology::alloc_queue(QueueLogger* queueLogger, uint64_t"
        )
        self.assertIn('_params.find("rx_hop_prio")', alloc_src)
        self.assertIn('_params.find("rx_hop_quantum")', alloc_src)
        self.assertIn("uint32_t rx_hop_quantum = 16", alloc_src)
        self.assertNotIn("rx_hop_prio", alloc_tor)

    def test_generated_credit_queue_is_fifo_again(self):
        next_credit = function_body(
            self.queue_cpp, "inline int CreditQueue::next_cred"
        )
        begin = function_body(self.queue_cpp, "void CreditQueue::beginService")
        admission = function_body(self.queue_cpp, "bool CreditQueue::handleCredit")
        nic_complete = function_body(
            self.queue_cpp, "void NICCreditQueue::completeService"
        )
        self.assertNotIn("rx_hop", next_credit)
        self.assertNotIn("selectRx", next_credit)
        self.assertNotIn("rx_hop", begin)
        self.assertNotIn("trackRx", admission)
        self.assertIn("pkt = _enqueued_cred[prio].back()", nic_complete)
        self.assertIn("_enqueued_cred[prio].pop_back()", nic_complete)
        for old_symbol in (
            "RxCreditPriority",
            "RxFlowPriority",
            "_rx_credit_order",
            "_rx_flow_order",
            "_rx_selected_credit",
            "_rx_credit_in_service",
        ):
            self.assertNotIn(old_symbol, self.queue_h)
            self.assertNotIn(old_symbol, self.queue_cpp)

    def test_sink_event_submits_request_without_materializing_credit(self):
        event = function_body(self.xpass_cpp, "void XPassSink::doNextEvent")
        self.assertIn("updateSliceFeedbackState()", event)
        self.assertIn("_credit_request_pending", event)
        self.assertIn("requestCredit(this)", event)
        self.assertIn("emitCredit()", event)
        self.assertNotIn("XPassPull::newpkt", event)
        self.assertNotIn("_tot_sent_creds++", event)
        self.assertNotIn("_credit_counter++", event)
        self.assertNotIn("sendToNIC", event)

    def test_emit_credit_preserves_original_generation_and_feedback(self):
        emit = function_body(self.xpass_cpp, "XPassPull* XPassSink::emitCredit")
        for fragment in (
            "XPassPull::newpkt",
            "set_ackno",
            "set_flow_id",
            "drand() <= rate",
            "set_tentative(false)",
            "set_tentative(true)",
            "_credit_counter++",
            "_bw_sent_creds++",
            "_tot_sent_creds++",
            "sendToNIC(p)",
            "nextCreditWait()",
            "feedbackControl2()",
        ):
            self.assertIn(fragment, emit)

    def test_one_pending_request_per_flow(self):
        event = function_body(self.xpass_cpp, "void XPassSink::doNextEvent")
        request = function_body(
            self.queue_cpp, "void NICCreditQueue::requestCredit"
        )
        self.assertIn("if (!_credit_request_pending)", event)
        self.assertIn("bool pending", self.queue_h)
        self.assertIn("map<uint32_t, RxCreditFlowRequest>", self.queue_h)
        self.assertIn("_rx_credit_requests.find(flow_id)", request)
        self.assertIn("_next_request_sequence++", request)
        self.assertIn("_credit_request_pending = false", self.queue_cpp)

    def test_each_opportunity_recomputes_all_pending_current_routes(self):
        select = function_body(
            self.queue_cpp, "void NICCreditQueue::runFlowCreditScheduler"
        )
        compute = function_body(
            self.queue_cpp, "int NICCreditQueue::computeRequestRoute"
        )
        self.assertIn("time_to_slice(eventlist().now())", select)
        self.assertIn("_rx_credit_requests.begin()", select)
        self.assertIn("computeRequestRoute(it->second, slice)", select)
        self.assertIn("get_no_paths", compute)
        self.assertIn("fast_rand() % npaths", compute)
        self.assertIn("get_no_hops", compute)
        self.assertNotIn("get_tidalhop", compute)
        self.assertNotIn("get_maxhops", compute)

    def test_priority_is_hops_then_request_fifo_without_aging(self):
        select = function_body(
            self.queue_cpp, "void NICCreditQueue::runFlowCreditScheduler"
        )
        self.assertIn("current_hops < shortest->second.current_hops", select)
        self.assertIn("request_sequence < shortest->second.request_sequence", select)
        self.assertIn("request_sequence < fifo_first->second.request_sequence", select)
        for unsupported in ("aging", "slo", "flow_size", "wait_time"):
            self.assertNotIn(unsupported, select.lower())

    def test_scheduler_materializes_one_credit_without_bypassing_pacing(self):
        schedule = function_body(
            self.queue_cpp, "void NICCreditQueue::scheduleFlowCreditScheduler"
        )
        select = function_body(
            self.queue_cpp, "void NICCreditQueue::runFlowCreditScheduler"
        )
        begin = function_body(self.queue_cpp, "void CreditQueue::beginService")
        self.assertIn("updateAvailCredit()", schedule)
        self.assertIn("_avail_cred == 0", schedule)
        self.assertIn("_materialized_flow_credit", schedule)
        self.assertIn("sink->emitCredit()", select)
        self.assertNotIn("sendFromQueue", select)
        self.assertIn("credit_ready()", begin)

    def test_selected_flow_keeps_a_bounded_credit_quantum(self):
        select = function_body(
            self.queue_cpp, "void NICCreditQueue::runFlowCreditScheduler"
        )
        self.assertIn("_active_credit_flow", self.queue_h)
        self.assertIn("_active_quantum_remaining", self.queue_h)
        self.assertIn("_flow_credit_quantum", self.queue_h)
        self.assertIn("_active_credit_flow->flow_id()", select)
        self.assertIn("selected = active", select)
        self.assertIn("_active_quantum_remaining--", select)
        self.assertIn("_active_credit_flow = NULL", select)
        self.assertIn("continued_quantum", select)

        schedule = function_body(
            self.queue_cpp, "void NICCreditQueue::scheduleFlowCreditScheduler"
        )
        self.assertIn("_active_credit_flow->_src->_finished", schedule)
        self.assertIn("_rx_credit_requests.find(_active_credit_flow->flow_id())", schedule)
        self.assertIn("return;", schedule)

    def test_credit_lifecycle_hops_are_recorded_at_real_boundaries(self):
        nic_complete = function_body(
            self.queue_cpp, "void NICCreditQueue::completeService"
        )
        source_receive = function_body(
            self.xpass_cpp, "void XPassSrc::receivePacket"
        )
        self.assertIn("recordFlowCreditAdmission(*pkt)", nic_complete)
        self.assertIn("recordFlowCreditDelivery(pkt)", source_receive)
        self.assertIn("admitted_path_hops_sum", self.queue_h)
        self.assertIn("delivered_path_hops_sum", self.queue_h)
        self.assertIn("reportCreditHopStats", self.queue_cpp)
        self.assertIn('"per_credit_hop.csv"', self.analyzer)

    def test_selected_route_is_attached_and_reused_only_in_same_slice(self):
        send = function_body(self.xpass_cpp, "XPassSink::sendToNIC")
        complete = function_body(
            self.queue_cpp, "void NICCreditQueue::completeService"
        )
        self.assertIn("_scheduled_credit_slice == slice", send)
        self.assertIn("set_slice_sent(_scheduled_credit_slice)", send)
        self.assertIn("set_path_index(path_index)", send)
        self.assertIn("pkt->get_slice_sent() == slice", complete)
        self.assertIn("path_index = pkt->get_path_index()", complete)

    def test_scheduler_csv_is_emitted_by_shared_analyzer(self):
        self.assertIn('fields[0] == "FlowCreditScheduler"', self.analyzer)
        self.assertIn('"flow_credit_scheduler.csv"', self.analyzer)
        for field in (
            "time",
            "slice",
            "selected_flow",
            "selected_hops",
            "fifo_first_flow",
            "fifo_first_hops",
            "num_pending_flows",
            "shortest_flow",
            "shortest_hops",
            "quantum_remaining_before",
            "continued_quantum",
        ):
            self.assertIn(f'"{field}"', self.analyzer)

    def test_uniform_run_scripts_keep_mode_off_unless_requested(self):
        for directory in ("opera_16tor_uniform", "opera_108tor_uniform"):
            script = (ROOT / "run" / directory / "run.sh").read_text(
                encoding="ascii"
            )
            self.assertIn("RX_HOP_PRIO=no", script)
            self.assertIn("--rxhopprio) RX_HOP_PRIO=yes", script)
            self.assertIn("RX_HOP_PRIO_ARGS=(-rxhopprio -rxhopquantum", script)


if __name__ == "__main__":
    unittest.main()
