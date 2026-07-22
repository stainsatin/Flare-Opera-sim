// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        
#ifndef QUEUE_H
#define QUEUE_H

/*
 * A simple FIFO queue
 */

#include <list>
#include <set>
#include "config.h"
#include "datacenter/dynexp_topology.h"
#include "eventlist.h"
#include "network.h"
#include "loggertypes.h"


class Queue : public EventSource, public PacketSink {
 public:

    Queue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist, 
	  QueueLogger* logger);
    Queue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist, 
	  QueueLogger* logger, int tor, int port, DynExpTopology *top);
    virtual void receivePacket(Packet& pkt);
    void doNextEvent();
    void sendEarlyFeedback(Packet &pkt);

    void sendFromQueue(Packet* pkt);

    // should really be private, but loggers want to see
    mem_b _maxsize; 
    mem_b _max_recorded_size;
    mem_b _max_ever_recorded_size;
    vector <mem_b> _max_recorded_size_slice;
    int _tor; // the ToR switch this queue belongs to
    int _port; // the port this queue belongs to
    DynExpTopology* _top; //network topology

    inline simtime_picosec drainTime(Packet *pkt) { 
        return (simtime_picosec)(pkt->size() * _ps_per_byte); 
    }
    inline mem_b serviceCapacity(simtime_picosec t) { 
        return (mem_b)(timeAsSec(t) * (double)_bitrate); 
    }
    virtual mem_b queuesize();
    simtime_picosec serviceTime();
    int num_drops() const {return _num_drops;}
    mem_b max_ever_recorded_size() const {return _max_ever_recorded_size;}
    void reset_drops() {_num_drops = 0;}

    virtual void setRemoteEndpoint(Queue* q) {_remoteEndpoint = q;};
    virtual void setRemoteEndpoint2(Queue* q) {_remoteEndpoint = q;q->setRemoteEndpoint(this);};
    Queue* getRemoteEndpoint() {return _remoteEndpoint;}
    
    void reportMaxqueuesize_perslice();
    virtual void reportMaxqueuesize();
    virtual void reportQueuesize();
    virtual void reportLoss();
    set<uint64_t> getFlows();

    /* HbH methods */
    virtual void packetWasDropped(Packet& pkt) {} //for HbH only
    virtual void poll() {} //for HbH only
    virtual void pollme(Queue* me) {} //for HbH only
    virtual bool extractNext(int port, map<uint64_t, unsigned> &flowid_to_queued, map<int, unsigned> &dst_to_queued, map<int, unsigned> &port_to_queued) { return false; } //for HbH
    virtual inline bool isTherePkt(int port) {return false;} //for HbH only
    virtual bool is_servicing() {return false;} //for HbH only
    virtual inline unsigned get_pkts_per_flow(uint64_t flow_id) { return 0; } //for HbH only
    /* =========== */

    virtual void setName(const string& name) {
        Logged::setName(name);
        _nodename += name;
    }
    virtual void setLogger(QueueLogger* logger) {
        _logger = logger;
    }
    virtual const string& nodename() { return _nodename; }

 protected:
    // Housekeeping
    Queue* _remoteEndpoint;

    QueueLogger* _logger;

    // Mechanism
    // start serving the item at the head of the queue
    virtual void beginService(); 

    // wrap up serving the item at the head of the queue
    virtual void completeService(); 

    void updatePktOut(uint64_t flow_id);
    void updatePktIn(uint64_t flow_id);

    linkspeed_bps _bitrate; 
    double _byteps;
    simtime_picosec _ps_per_byte;  // service time, in picoseconds per byte
    mem_b _queuesize;
    uint64_t _txbytes, _prev_txbytes;
    simtime_picosec _prev_sample_t;
    list<Packet*> _enqueued;
    int _num_drops;
    string _nodename;

    //track flows at queue
    map<uint64_t, int> _pkts_per_flow;
    set<uint64_t> _flows_at_queues;
};

/* implement a 4-level priority queue */
// modified to include RLB
class PriorityQueue : public Queue {
 public:
    typedef enum {Q_RLB=0, Q_LO=1, Q_MID=2, Q_HI=3, Q_NONE=4} queue_priority_t;
    PriorityQueue(linkspeed_bps bitrate, mem_b maxsize, EventList &eventlist, 
		  QueueLogger* logger, int node, DynExpTopology *top);
    virtual void receivePacket(Packet& pkt);
    virtual mem_b queuesize();
    simtime_picosec serviceTime(Packet& pkt);

    void doorbell(bool rlbwaiting);

    int _bytes_sent; // keep track so we know when to send a push to RLB module

    int _node;
    int _crt_slice = -1; // the first packet to be sent will cause a path update

 protected:
    // start serving the item at the head of the queue
    virtual void beginService(); 
    // wrap up serving the item at the head of the queue
    virtual void completeService();

    PriorityQueue::queue_priority_t getPriority(Packet& pkt);
    list <Packet*> _queue[Q_NONE];
    mem_b _queuesize[Q_NONE];
    queue_priority_t _servicing;
    int _state_send;
};

#endif
