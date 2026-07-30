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
RCDCP_RUN = ROOT / "run/opera_24tor_4flow_2MiB/run.sh"


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


class RcdcpContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue_cpp = CREDIT_QUEUE_CPP.read_text(encoding="ascii")
        cls.queue_h = CREDIT_QUEUE_H.read_text(encoding="ascii")
        cls.xpass_cpp = XPASS_CPP.read_text(encoding="ascii")
        cls.xpass_h = XPASS_H.read_text(encoding="ascii")
        cls.main_cpp = MAIN_CPP.read_text(encoding="ascii")
        cls.topology_cpp = TOPOLOGY_CPP.read_text(encoding="ascii")
        cls.analyzer = ANALYZER.read_text(encoding="ascii")
        cls.run_script = RCDCP_RUN.read_text(encoding="ascii")

    def test_modes_are_opt_in_and_pushout_is_independent(self):
        for declaration in (
            "bool rx_hop_prio = false;",
            "bool rx_global_tentative = false;",
            "bool rx_credit_pushout = false;",
            "bool rx_credit_slot_trace = false;",
        ):
            self.assertIn(declaration, self.main_cpp)
        for option in (
            "-rxhopprio",
            "-rxglobaltentative",
            "-rxcreditpushout",
            "-rxcreditslottrace",
            "-rxhopweights",
            "-seed",
        ):
            self.assertIn(option, self.main_cpp)
        alloc = function_body(
            self.topology_cpp, "Queue* DynExpTopology::alloc_src_queue"
        )
        self.assertIn('"rx_global_tentative"', alloc)
        self.assertIn('"rx_credit_pushout"', alloc)
        self.assertIn("new NICCreditQueue", alloc)

    def test_original_credit_generation_and_feedback_are_unchanged(self):
        event = function_body(self.xpass_cpp, "void XPassSink::doNextEvent")
        emit = function_body(self.xpass_cpp, "XPassPull* XPassSink::emitCredit")
        self.assertIn("updateSliceFeedbackState()", event)
        self.assertIn("emitCredit()", event)
        for fragment in (
            "drand() <= rate",
            "set_tentative(false)",
            "set_tentative(true)",
            "_bw_sent_creds++",
            "sendToNIC(p)",
            "feedbackControl2()",
        ):
            self.assertIn(fragment, emit)
        self.assertNotIn("RxCreditFlowScheduler", self.queue_h + self.xpass_h)
        self.assertNotIn("flow_credit_quantum", self.queue_h + self.xpass_h)

    def test_tentative_threshold_uses_total_shared_occupancy(self):
        admission = function_body(self.queue_cpp, "bool CreditQueue::handleCredit")
        self.assertIn("mem_b admission_occupancy = queuesize_cred()", admission)
        self.assertIn("admission_occupancy > _max_tent_cred", admission)
        self.assertNotIn("priorityQueuesize(credit_class)", admission)
        receive = function_body(self.queue_cpp, "void CreditQueue::receivePacket")
        self.assertIn("queuesize_cred() + pkt.size() > _maxsize_cred", receive)
        self.assertIn("_rx_credit_pushout", receive)
        self.assertNotIn("_maxsize_cred / CRED_Q_N", self.queue_cpp)

    def test_pending_credits_are_reclassified_on_slice_change(self):
        refresh = function_body(
            self.queue_cpp, "void CreditQueue::refreshPriorityClassification"
        )
        for fragment in (
            "time_to_slice(eventlist().now())",
            "slice == _last_priority_slice",
            "credit_enqueue_seq()",
            "installPriorityRoute(*pkt, slice)",
            "_enqueued_cred[credit_class].push_front(pkt)",
        ):
            self.assertIn(fragment, refresh)
        select = function_body(self.queue_cpp, "inline int CreditQueue::next_cred")
        self.assertIn("refreshPriorityClassification()", select)

    def test_classification_route_is_reused_and_rng_isolated(self):
        complete = function_body(
            self.queue_cpp, "void NICCreditQueue::completeService"
        )
        route_hash = function_body(
            self.queue_cpp, "int CreditQueue::independentPathIndex"
        )
        self.assertIn("prio = next_cred()", complete)
        self.assertNotIn("fast_rand", route_hash)
        self.assertIn("if (npaths == 1) return 0", route_hash)
        self.assertIn("npaths == 1 ? 0 : fast_rand() % npaths", self.xpass_cpp)
        self.assertIn("pkt->get_slice_sent() == slice", complete)
        self.assertIn("path_index = pkt->get_path_index()", complete)
        self.assertIn("if (npaths > 1) (void)fast_rand()", complete)

    def test_wrr_is_smooth_one_slot_and_work_conserving(self):
        build = function_body(self.queue_cpp, "void CreditQueue::buildCreditSchedule")
        select = function_body(self.queue_cpp, "inline int CreditQueue::next_cred")
        advance = function_body(
            self.queue_cpp, "void CreditQueue::advanceCreditPriority"
        )
        self.assertIn("current[prio] += _credit_weights[prio]", build)
        self.assertIn("current[selected] -= total", build)
        self.assertIn("_wrr_schedule[_wrr_schedule_position]", select)
        self.assertIn("_selected_fallback = true", select)
        self.assertIn("_wrr_schedule_position + 1", advance)
        self.assertNotIn("_wrr_remaining", self.queue_h + self.queue_cpp)

    def test_type_class_and_event_statistics_are_exported(self):
        for marker in (
            "CreditLifecycleStats",
            "CreditTypeClassStats",
            "NICCreditSlot",
            "TentativeAdmission",
        ):
            self.assertIn(marker, self.queue_cpp)
        for output in (
            "per_nic_credit_slot.csv",
            "tentative_admission.csv",
            "per_tor_uplink_credit.csv",
            "per_rotor_credit.csv",
            "credit_lifecycle.csv",
        ):
            self.assertIn(output, self.analyzer)
        for metric in (
            "regular_delivered_per_nic_slot",
            "regular_delivered_per_credit_hop",
            "credit_hops_per_regular_delivered",
            "tentative_nic_slot_share",
        ):
            self.assertIn(metric, self.analyzer)

    def test_multiseed_experiment_has_required_modes(self):
        self.assertIn("SEEDS=1,2,3,4,5", self.run_script)
        for mode in ("fifo_original", "fifo_global", "wrr421", "wrr821"):
            self.assertIn(mode, self.run_script)
        self.assertIn("-rxhopweights 4 2 1", self.run_script)
        self.assertIn("-rxhopweights 8 2 1", self.run_script)
        self.assertIn("-rxcreditslottrace", self.run_script)
        self.assertIn("summarize_seeds.py", self.run_script)


if __name__ == "__main__":
    unittest.main()
