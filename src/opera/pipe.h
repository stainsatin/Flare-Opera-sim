// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        
#ifndef PIPE_H
#define PIPE_H

/*
 * A pipe is a dumb device which simply delays all incoming packets
 */

#include <list>
#include <utility>
#include "config.h"
#include "eventlist.h"
#include "network.h"
#include "loggertypes.h"


class Pipe : public EventSource, public PacketSink {
 public:

    Pipe(simtime_picosec delay, EventList& eventlist);
    void receivePacket(Packet& pkt); // inherited from PacketSink
    void doNextEvent(); // inherited from EventSource

    void sendFromPipe(Packet *pkt);

    uint64_t reportDeliveredBytes(); // reports to the UtilMonitor
    uint64_t reportInputBytes(); // reports to the UtilMonitor
    uint64_t _bytes_delivered; // keep track of how many (non-hdr,ACK,NACK,PULL,RTX) packets were delivered to hosts
    uint64_t _bytes_input; // keep track of how many (non-hdr,ACK,NACK,PULL,RTX) packets were input into the fabric

    simtime_picosec delay() { return _delay; }
    const string& nodename() { return _nodename; }
 private:
    simtime_picosec _delay;
    typedef pair<simtime_picosec,Packet*> pktrecord_t;
    list<pktrecord_t> _inflight; // the packets in flight (or being serialized)
    string _nodename;
};

class UtilMonitor : public EventSource {
 public:

    UtilMonitor(DynExpTopology* top, EventList &eventlist);
    UtilMonitor(DynExpTopology* top, EventList &eventlist, map<uint64_t, TcpSrc*> flow_map);

    void start(simtime_picosec period);
    void doNextEvent();
    void printAggUtil();
    map<uint64_t, float> findIdealShare();

    DynExpTopology* _top;
    simtime_picosec _period; // picoseconds between utilization reports
    map<uint64_t, TcpSrc*> _flow_map;
    uint64_t _max_agg_Bps; // delivered to endhosts, across the whole network
    uint64_t _max_B_in_period;
    int _H; // number of hosts
    int _N; // number of racks
    int _hpr; // number of hosts per rack
};
extern uint64_t __global_network_tot_hops;
extern uint64_t __global_network_tot_hops_samples;
extern uint64_t __global_network_tot_valid_creds;
extern uint64_t __global_network_tot_cred_waste;
extern uint64_t __global_topology_clipped_credits;
extern uint64_t __global_topology_clipped_data;
extern uint64_t __global_topology_clipped_control;
extern uint64_t __global_topology_clipped_other;
extern uint64_t __global_topology_wrong_dst_credits;
extern uint64_t __global_topology_wrong_dst_data;
extern uint64_t __global_topology_wrong_dst_control;
extern uint64_t __global_topology_wrong_dst_other;

#endif
