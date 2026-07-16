// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        
#ifndef CRED_QUEUE_H
#define CRED_QUEUE_H
#include "datacenter/dynexp_topology.h"
#include "queue.h"
/*
 * A credit queue based on ExpressPass, Tidal adds probablistic shaping
 */

#include <list>
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
};

void reportFlowCreditStats();

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
 protected:
    enum pkt_type {NONE, DATA, CRED};
    pkt_type _tx_next;
    int _next_prio;
    void updateAvailCredit();
    void scheduleCredit();
    bool handleCredit(Packet &pkt);
    simtime_picosec cred_tx_delta();
    bool credit_ready();
    int credit_prio(Packet &pkt);
    int next_cred();
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
};

class NICCreditQueue : public CreditQueue {
 public:
    NICCreditQueue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist, 
		QueueLogger* logger, DynExpTopology *top,
        mem_b credsize, mem_b shaping_thresh, mem_b aeolus_thresh, mem_b tent_thresh);
    void completeService();

};

#endif
