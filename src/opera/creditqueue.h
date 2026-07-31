// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        
#ifndef CRED_QUEUE_H
#define CRED_QUEUE_H
#include "datacenter/dynexp_topology.h"
#include "queue.h"
/*
 * A credit queue based on ExpressPass, Tidal adds probablistic shaping
 */

#include <list>
#include <map>
#include <vector>
#include "config.h"
#include "eventlist.h"
#include "network.h"
#include "loggertypes.h"

struct FlowCreditCounters {
    uint32_t sender = 0;
    uint32_t receiver = 0;
    uint32_t path_hops = 0;
    uint64_t generated = 0;
    uint64_t delivered = 0;
    uint64_t queue_arrivals = 0;
    uint64_t queue_transmissions = 0;
    uint64_t dropped = 0;
    uint64_t overflow = 0;
    uint64_t timeout = 0;
    uint64_t shaping = 0;
    uint64_t tentative = 0;
    uint64_t shaping_checks = 0;
    uint64_t shaping_admitted = 0;
    uint64_t waste_hops = 0;
    uint64_t topology = 0;
    uint32_t path_hops_min = 0;
    uint32_t path_hops_max = 0;
    uint64_t path_hops_sum = 0;
    uint64_t admitted = 0;
    uint64_t endpoint_dropped = 0;
    uint64_t path_dropped = 0;
    uint32_t admitted_path_hops_min = 0;
    uint32_t admitted_path_hops_max = 0;
    uint64_t admitted_path_hops_sum = 0;
    uint32_t delivered_path_hops_min = 0;
    uint32_t delivered_path_hops_max = 0;
    uint64_t delivered_path_hops_sum = 0;
    uint64_t delivered_actual_hops_sum = 0;
    uint64_t delivered_hop_mismatches = 0;
    uint64_t pushout = 0;
};

void reportFlowCreditStats();
void reportCreditHopStats();
void reportCreditLifecycleStats();
void configureCreditTimeSeries(DynExpTopology* top, bool enabled);
void recordCreditGenerationRate(DynExpTopology* top, simtime_picosec time,
                                double regular_probability);
void recordCreditFeedbackWindow(DynExpTopology* top, simtime_picosec time,
                                uint64_t regular_credits,
                                uint64_t regular_drops,
                                double measured_loss, double target_loss,
                                double rate_before, double rate_after);
void reportCreditTimeSeriesStats();
void configureFeedbackWindowTrace(bool enabled);
void recordFeedbackCreditIssue(Packet& pkt);
void recordFeedbackCreditDrop(Packet& pkt);
void recordFeedbackDataReturn(uint32_t flow_id, uint64_t window_id,
                              uint64_t pacer_no, bool tentative,
                              simtime_picosec time);
void recordDetailedFeedbackWindow(
    DynExpTopology* top, simtime_picosec time, uint32_t host_id,
    uint32_t flow_id, uint64_t window_id, uint64_t rate_before,
    uint64_t rate_after, uint64_t max_rate, uint64_t regular_issued,
    uint64_t regular_returned, uint64_t tentative_issued,
    uint64_t tentative_returned, uint64_t feedback_sample_size,
    uint64_t feedback_reported_lost, double computed_loss,
    double target_loss);
void reportFeedbackWindowTrace();
void recordFlowCreditDelivery(Packet& pkt);
void recordFlowCreditTopologyDrop(Packet& pkt, uint32_t consumed_hops,
                                  bool wrong_destination = false);
void recordCreditNetworkHop(Packet& pkt);

struct CreditClassQueueCounters {
    uint64_t arrived = 0;
    uint64_t transmitted = 0;
    uint64_t dropped = 0;
    uint64_t tentative_threshold = 0;
    uint64_t shaping = 0;
    uint64_t overflow = 0;
    uint64_t timeout = 0;
    uint64_t pushout = 0;
    mem_b queued_bytes = 0;
    mem_b max_queued_bytes = 0;
};

class CreditQueue : public Queue {
 public:
    CreditQueue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist, 
		QueueLogger* logger, int tor, int port, DynExpTopology *top,
        mem_b credsize, mem_b shaping_thresh, mem_b aeolus_thresh, mem_b tent_thresh);
    void receivePacket(Packet & pkt);
    void beginService();
    void completeService();
    void doNextEvent();
    void reportLoss();
    virtual void reportMaxqueuesize();
    void reportCreditStats(const string& scope, int id, int port);
    void reportPriorityStats(const string& scope, int id, int port);
    void reportTypeClassStats(const string& scope, int id, int port);
    void reportNICSlotStats();
    void reportRegularWRRStats();
 protected:
    enum pkt_type {NONE, DATA, CRED};
    pkt_type _tx_next;
    int _next_prio;
    void updateAvailCredit();
    void scheduleCredit();
    bool handleCredit(Packet &pkt, int credit_class, int queue_index);
    simtime_picosec cred_tx_delta();
    bool credit_ready();
    bool receiverPriorityEnabled() const;
    int creditClass(Packet &pkt) const;
    int creditQueueIndex(Packet &pkt);
    int creditType(Packet &pkt) const;
    int next_cred(bool commit_selection = false);
    void advanceCreditPriority(int served_prio);
    void buildCreditSchedule();
    void refreshPriorityClassification();
    int independentPathIndex(Packet &pkt, int absolute_slice,
                             int npaths) const;
    void installPriorityRoute(Packet &pkt, int slice);
    bool evictPriorityCredit(Packet &arriving, int arriving_class);
    bool evictOldestTentativeCredit();
    bool evictCreditVictim(int victim_class, bool tentative);
    void accountCreditEnqueue(Packet &pkt, int queue_index, int credit_class);
    void accountCreditDequeue(Packet &pkt, int queue_index, int credit_class);
    void dropQueuedCredit(Packet* pkt, int queue_index, int credit_class,
                          bool pushout);
    void notePriorityDrop(int credit_class, bool pushout);
    void noteTypeClassDrop(Packet &pkt, int credit_class, int reason);
    void reportTentativeAdmission(Packet &pkt, int credit_class,
                                  const vector<mem_b>& occupancy,
                                  mem_b total_occupancy,
                                  const char* decision) const;
    void reportNICCreditSlot(Packet &pkt, int credit_class,
                             const vector<mem_b>& class_lengths,
                             uint64_t regular_pending,
                             uint64_t tentative_pending,
                             uint64_t regular_oldest_age,
                             uint64_t tentative_oldest_age) const;
    Packet* oldestCreditOfType(bool tentative, int* queue_index) const;
    Packet* oldestRegularInClass(int credit_class, int* queue_index) const;
    void moveCreditToHeadOfService(Packet* pkt, int queue_index);
    uint64_t oldestCreditAge(bool tentative) const;
    void pendingCreditCounts(uint64_t* regular, uint64_t* tentative,
                             vector<mem_b>* regular_classes) const;
    int selectRegularCredit(bool commit_selection);
    int selectTentativeCredit();
    bool receiverSchedulerEnabled() const;
    mem_b queuesize_cred(int prio); //queue within a certain prio
    mem_b queuesize_cred(); //full queue size
    mem_b _maxsize_cred;
    mem_b _maxsize_unsched;
    mem_b _shaping_thresh;
    vector<mem_b> _queuesize_cred;
    mem_b _data_size;
    int _max_tent_cred;
    int _avail_cred, _max_avail_cred;
    simtime_picosec _last_cred_t;
    simtime_picosec _last_cred_tx_t;
    simtime_picosec _next_sched_tx;
    simtime_picosec _cred_timeout;
    bool _cred_tx_pending;
    Packet* _credit_in_service;
    vector<list<Packet*>> _enqueued_cred;
    uint64_t _tot_creds;
    uint64_t _tx_creds;
    uint64_t _drop_creds;
    uint64_t _drop_overflow;
    uint64_t _drop_timeout;
    uint64_t _drop_shaping;
    uint64_t _drop_tentative;
    uint64_t _shaping_checks;
    uint64_t _shaping_admitted;
    mem_b _max_cred_queue;
    map<int, uint64_t> _hops_to_creds;
    bool _is_nic;
    bool _rx_hop_prio;
    bool _rx_regular_first;
    bool _rx_regular_hop_prio;
    bool _rx_global_tentative;
    bool _rx_credit_pushout;
    bool _rx_regular_pushout_tentative;
    bool _rx_credit_slot_trace;
    uint32_t _tentative_probe_interval;
    uint32_t _regular_backlogged_slots_since_probe;
    bool _selected_probe;
    string _selected_reason;
    uint64_t _priority_seed;
    int _host_id;
    int _last_priority_slice;
    uint64_t _credit_enqueue_sequence;
    vector<uint32_t> _credit_weights;
    vector<int> _wrr_schedule;
    uint32_t _wrr_schedule_position;
    int _selected_schedule_position;
    int _selected_schedule_target;
    bool _selected_fallback;
    vector<uint64_t> _priority_arrivals;
    vector<uint64_t> _priority_transmissions;
    vector<uint64_t> _priority_drops;
    vector<uint64_t> _priority_pushouts;
    vector<mem_b> _priority_queued_bytes;
    vector<mem_b> _priority_max_queued;
    CreditClassQueueCounters _type_class_stats[2][3];
    uint64_t _slots_with_regular_pending;
    uint64_t _regular_selected_while_regular_pending;
    uint64_t _tentative_selected_while_regular_pending;
    uint64_t _tentative_probe_slots;
    uint64_t _used_nic_credit_slots;
    uint64_t _total_nic_credit_opportunities;
    uint64_t _selected_regular_pending;
    uint64_t _selected_tentative_pending;
    vector<mem_b> _selected_regular_classes;
    uint64_t _selected_regular_oldest_age;
    uint64_t _selected_tentative_oldest_age;
    uint64_t _regular_wrr_fallbacks[3];
    uint64_t _regular_wrr_scheduled[3];
    uint64_t _regular_wrr_selected[3];
};

class NICCreditQueue : public CreditQueue {
 public:
    NICCreditQueue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist,
		QueueLogger* logger, DynExpTopology *top,
        mem_b credsize, mem_b shaping_thresh, mem_b aeolus_thresh,
        mem_b tent_thresh, int host_id, bool rx_hop_prio = false,
        bool rx_global_tentative = false,
        bool rx_credit_pushout = false,
        bool rx_credit_slot_trace = false,
        bool rx_regular_first = false,
        uint32_t tentative_probe_interval = 0,
        bool rx_regular_hop_prio = false,
        bool rx_regular_pushout_tentative = false,
        uint64_t priority_seed = 13,
        uint32_t high_weight = 4, uint32_t medium_weight = 2,
        uint32_t low_weight = 1);
    void completeService();
};

#endif
