// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        
#include "pipe.h"
#include <cstdint>
#include <iostream>
#include <sstream>
#include <set>
#include <algorithm>

#include "queue.h"
#include "tcp.h"
#include "ndp.h"
//#include "rlb.h"
#include "rlbmodule.h"
#include "ndppacket.h"
#include "rlbpacket.h" // added for debugging
#include "xpass.h"
#include "xpasspacket.h"
#include "creditqueue.h"
#include "hbh.h"
#include "hbhpacket.h"

Pipe::Pipe(simtime_picosec delay, EventList& eventlist)
: EventSource(eventlist,"pipe"), _delay(delay)
{
    //stringstream ss;
    //ss << "pipe(" << delay/1000000 << "us)";
    //_nodename= ss.str();

    _bytes_delivered = 0;

}

void Pipe::receivePacket(Packet& pkt)
{
    //pkt.flow().logTraffic(pkt,*this,TrafficLogger::PKT_ARRIVE);
    //cout << "Pipe: Packet received at " << timeAsUs(eventlist().now()) << " us" << endl;
    if (_inflight.empty()){
        /* no packets currently inflight; need to notify the eventlist
            we've an event pending */
        eventlist().sourceIsPendingRel(*this,_delay);
    }
    _inflight.push_front(make_pair(eventlist().now() + _delay, &pkt));
}

void Pipe::doNextEvent() {
    if (_inflight.size() == 0) 
        return;

    Packet *pkt = _inflight.back().second;
    _inflight.pop_back();
    //pkt->flow().logTraffic(*pkt, *this,TrafficLogger::PKT_DEPART);

    // tell the packet to move itself on to the next hop
    //pkt->sendOn();
    //pkt->sendFromPipe();
    sendFromPipe(pkt);

    if (!_inflight.empty()) {
        // notify the eventlist we've another event pending
        simtime_picosec nexteventtime = _inflight.back().first;
        _eventlist.sourceIsPending(*this, nexteventtime);
    }
}

uint64_t Pipe::reportDeliveredBytes() {
    uint64_t temp;
    temp = _bytes_delivered;
    _bytes_delivered = 0; // reset the counter
    return temp;
}

uint64_t Pipe::reportInputBytes() {
    uint64_t temp;
    temp = _bytes_input;
    _bytes_input = 0; // reset the counter
    return temp;
}

void Pipe::sendFromPipe(Packet *pkt) {
    if(pkt->id() == 45048)
        cout << "sendFromPipe crtToR " << pkt->get_crtToR() << endl;
    if (pkt->is_lasthop()) {
        // we'll be delivering to a sink/src based on packet type
        // ! OR an RlbModule
        switch (pkt->type()) {
            case RLB:
                {
                    // check if it's really the last hop for this packet
                    // otherwise, it's getting indirected, and doesn't count.
                    if (pkt->get_dst() == pkt->get_real_dst()) {

                        _bytes_delivered = _bytes_delivered + pkt->size(); // increment packet delivered

                        // debug:
                        //cout << "!!! incremented packets delivered !!!" << endl;

                    }

                    DynExpTopology* top = pkt->get_topology();
                    RlbModule * mod = top->get_rlb_module(pkt->get_dst());
                    assert(mod);
                    mod->receivePacket(*pkt, 0);
                    break;
                }
            case NDP:
                {
                    if (pkt->bounced() == false) {

                        //NdpPacket* ndp_pkt = dynamic_cast<NdpPacket*>(pkt);
                        //if (!ndp_pkt->retransmitted())
                        if (pkt->size() > 64) // not a header
                            _bytes_delivered = _bytes_delivered + pkt->size()-64; // increment packet delivered

                        // send it to the sink
                        NdpSink* sink = pkt->get_ndpsink();
                        assert(sink);
                        sink->receivePacket(*pkt);
                    } else {
                        // send it to the source
                        NdpSrc* src = pkt->get_ndpsrc();
                        assert(src);
                        src->receivePacket(*pkt);
                    }

                    break;
                }
            case TCP:
                {
                    // send it to the sink
                    
                    TcpSink *sink = pkt->get_tcpsink();
                    assert(sink);
                
                    if(!sink->is_duplicate(*pkt))
                        _bytes_delivered += pkt->size()-64;
                    sink->receivePacket(*pkt);
                    
                    break;
                }
            case TCPACK:
                {
                    TcpSrc* src = pkt->get_tcpsrc();
                    assert(src);
                    src->receivePacket(*pkt);
                    break;
                }
            case SAMPLE:
                {
                    RTTSampler* sampler = ((SamplePacket*)pkt)->get_sampler();
                    assert(sampler);
                    sampler->receivePacket(*pkt);
                    break;
                }
            case NDPACK:
            case NDPNACK:
            case NDPPULL:
                {
                    NdpSrc* src = pkt->get_ndpsrc();
                    assert(src);
                    src->receivePacket(*pkt);
                    break;
                }
            case XPDATA:
                {
                    _bytes_delivered += pkt->size()-64;
                    XPassSink* sink = pkt->get_xpsink();
                    assert(sink);
                    sink->receivePacket(*pkt);
                    break;
                }
            case XPCREDIT:
            case XPCTL:
                {
                    XPassSrc* src = pkt->get_xpsrc();
                    assert(src);
                    src->receivePacket(*pkt);
                    break;
                }
            case HBHDATA:
                {
                    _bytes_delivered += pkt->size()-64;
                    HbHPacket *hbhp = (HbHPacket*)pkt;
                    HbHSink* sink = hbhp->get_hbhsink();
                    assert(sink);
                    sink->receivePacket(*pkt);
                    break;
                }
        }
    } else {
        // we'll be delivering to a ToR queue
        DynExpTopology* top = pkt->get_topology();
        const bool traversed_tor_link = pkt->get_crtToR() >= 0;
        switch(pkt->type()) {
            case TCP:
            case NDP:
            case XPDATA: 
            {
                _bytes_input += pkt->size()-ACKSIZE;
            }
        }

        if (pkt->get_crtToR() < 0){
            pkt->set_crtToR(pkt->get_src_ToR());
            assert(pkt->get_crtToR() >= 0);
        } else {
            // under a changing topology, we have to use the current slice:
            // we compute the current slice at the end of the packet transmission
            int slice = top->time_to_slice(eventlist().now());
            int nextToR = top->get_nextToR(slice, pkt->get_crtToR(), pkt->get_crtport());
            if (nextToR >= 0) {// the rotor switch is up
                pkt->set_crtToR(nextToR);

            } else { // the rotor switch is down, "drop" the packet

                switch (pkt->type()) {
                    case XPCREDIT:
                        __global_topology_clipped_credits++;
                        recordFlowCreditTopologyDrop(
                            *pkt, max(pkt->get_crthop() + 1, 1));
                        break;
                    case XPDATA:
                        __global_topology_clipped_data++;
                        break;
                    case XPCTL:
                        __global_topology_clipped_control++;
                        break;
                    default:
                        __global_topology_clipped_other++;
                        break;
                }

                switch (pkt->type()) {
                    case RLB:
                        {
                            // for now, let's just return the packet rather than implementing the RLB NACK mechanism
                            RlbPacket *p = (RlbPacket*)(pkt);
                            RlbModule* module = top->get_rlb_module(p->get_src()); // returns pointer to Rlb module that sent the packet
                            module->receivePacket(*p, 1); // 1 means to put it at the front of the queue
                            break;
                        }
                    case NDP:
                        cout << "!!! NDP packet clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
                        pkt->free();
                        break;
                    case NDPACK:
                        cout << "!!! NDP ACK clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
                        pkt->free();
                        break;
                    case NDPNACK:
                        cout << "!!! NDP NACK clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
                        pkt->free();
                        break;
                    case NDPPULL:
                        cout << "!!! NDP PULL clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
                        pkt->free();
                        break;
                    case TCP:
			{
                        cout << "!!! TCP packet clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
                        TcpPacket *tcppkt = (TcpPacket*)pkt;
                        tcppkt->get_tcpsrc()->add_to_dropped(tcppkt->seqno());
      if(tcppkt->flow_id() == 75184) cout << "DROPPED HERE (pipe) seqno " << tcppkt->seqno() << endl;
                        pkt->free();
                        break;
			}
		    case XPDATA:
			{
                        cout << "!!! XPDATA packet clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
			XPassPacket *xppkt = (XPassPacket*)pkt;
			xppkt->get_xpsrc()->setFinished();
			pkt->free();
			break;
			}
		    case XPCREDIT:
                        cout << "!!! XPCREDIT packet clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
			pkt->free();
			break;
		    case XPCTL:
                        cout << "!!! XPCTL packet clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
			pkt->free();
			break;
            case HBHDATA:
            {
                        cout << "!!! HBHDATA packet clipped in pipe (rotor switch down)" << endl;
                        cout << "    time = " << timeAsUs(eventlist().now()) << " us";
                        cout << "    current slice = " << slice << endl;
                        cout << "    slice sent = " << pkt->get_slice_sent() << endl;
                        cout << "    src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << endl;
            HbHPacket *hbhpkt = (HbHPacket*)pkt;
            Queue *q = hbhpkt->nextq();
            assert(q != NULL);
            q->packetWasDropped(*pkt);
            HbHSrc *hbhsrc = hbhpkt->get_hbhsrc();
            hbhsrc->addToLost(hbhpkt);
            //pkt->free();
            break;
            }
                }
                top->inc_losses();
                return;
            }
        }
        pkt->inc_crthop(); // increment the hop
        // Tidal hops count ToR-to-ToR links. The access pipe only moves a
        // packet from its host NIC into the source ToR.
        if (traversed_tor_link) {
            pkt->set_tidalhop(max(pkt->get_tidalhop()-1, 1));
        }

        // get the port:
        if (pkt->get_crthop() == pkt->get_maxhops()) { // no more hops defined, need to set downlink port
            pkt->set_crtport(top->get_lastport(pkt->get_dst()));

        } else {
            pkt->set_crtport(top->get_port(pkt->get_src_ToR(), top->get_firstToR(pkt->get_dst()),
                        pkt->get_slice_sent(), pkt->get_path_index(), pkt->get_crthop()));
        }

        Queue* nextqueue = top->get_queue_tor(pkt->get_crtToR(), pkt->get_crtport());
        assert(nextqueue);
        nextqueue->receivePacket(*pkt);
    }
}


//////////////////////////////////////////////
//      Aggregate utilization monitor       //
//////////////////////////////////////////////


uint64_t __global_network_tot_hops = 0;
uint64_t __global_network_tot_hops_samples = 0;
uint64_t __global_network_tot_valid_creds = 0;
uint64_t __global_network_tot_cred_waste = 0;
uint64_t __global_topology_clipped_credits = 0;
uint64_t __global_topology_clipped_data = 0;
uint64_t __global_topology_clipped_control = 0;
uint64_t __global_topology_clipped_other = 0;
uint64_t __global_topology_wrong_dst_credits = 0;
uint64_t __global_topology_wrong_dst_data = 0;
uint64_t __global_topology_wrong_dst_control = 0;
uint64_t __global_topology_wrong_dst_other = 0;

UtilMonitor::UtilMonitor(DynExpTopology* top, EventList &eventlist)
  : EventSource(eventlist,"utilmonitor"), _top(top)
{
    _H = _top->no_of_nodes(); // number of hosts
    _N = _top->no_of_tors(); // number of racks
    _hpr = _top->no_of_hpr(); // number of hosts per rack
    uint64_t rate = (uint64_t)1E11 / 8; // bytes / second
    rate = rate * _H;
    //rate = rate / 1500; // total packets per second

    _max_agg_Bps = rate;

    // debug:
    //cout << "max bytes per second = " << rate << endl;

}

UtilMonitor::UtilMonitor(DynExpTopology* top, EventList &eventlist, map<uint64_t, TcpSrc*> flow_map)
  : EventSource(eventlist,"utilmonitor"), _top(top), _flow_map(flow_map)
{
    _H = _top->no_of_nodes(); // number of hosts
    _N = _top->no_of_tors(); // number of racks
    _hpr = _top->no_of_hpr(); // number of hosts per rack
    uint64_t rate = (uint64_t)1E11 / 8; // bytes / second
    rate = rate * _H;
    //rate = rate / 1500; // total packets per second

    _max_agg_Bps = rate;

    // debug:
    //cout << "max bytes per second = " << rate << endl;

}

void UtilMonitor::start(simtime_picosec period) {
    _period = period;
    _max_B_in_period = _max_agg_Bps * timeAsSec(_period);

    // debug:
    //cout << "_max_pkts_in_period = " << _max_pkts_in_period << endl;
    //cout << "_max_B_in_period = " << _max_B_in_period << endl;

    eventlist().sourceIsPending(*this, _period);
}

void UtilMonitor::doNextEvent() {
    printAggUtil();
}

void UtilMonitor::printAggUtil() {

    uint64_t B_sum = 0;
    for (int tor = 0; tor < _N; tor++) {
        for (int downlink = 0; downlink < _hpr; downlink++) {
            Pipe* pipe = _top->get_pipe_tor(tor, downlink);
            B_sum = B_sum + pipe->reportDeliveredBytes();
        }
    }
    double util = (double)B_sum / (double)_max_B_in_period;
    // debug:
    //cout << "Packets counted = " << (int)pkt_sum << endl;
    //cout << "Max packets = " << _max_pkts_in_period << endl;

    B_sum = 0;
    for (int h = 0; h < _H; h++) {
        Pipe* pipe = _top->get_pipe_serv_tor(h);
        B_sum = B_sum + pipe->reportInputBytes();
    }
    double input = (double)B_sum / (double)_max_B_in_period;

    cout << "sum " << (double)B_sum << endl;
    cout << "Util " << fixed << util << " " << timeAsMs(eventlist().now()) << endl;
    cout << "Input " << fixed << input << " " << __global_network_tot_hops << 
        " " << __global_network_tot_hops_samples << " " << timeAsMs(eventlist().now()) << endl;
    cout << "Waste " << __global_network_tot_valid_creds << " " << __global_network_tot_cred_waste << " " << timeAsMs(eventlist().now()) << endl;
    __global_network_tot_hops = 0;
    __global_network_tot_hops_samples = 0;
    uint64_t sample_packets = _top->get_sample_packets();
    uint64_t sample_losses = _top->get_sample_losses();
    uint64_t tot_packets = _top->get_all_packets();
    uint64_t tot_losses = _top->get_all_losses();
    cout << "Packetloss " << sample_packets << " " << tot_packets << " " << 
        sample_losses << " " << tot_losses << endl;

    //downlink queues
    for (int host = 0; host < _top->no_of_nodes(); host++) {
        Queue* q = _top->get_queue_serv_tor(host);
	if(q){
            q->reportMaxqueuesize();
	}
    }
    //uplink queues
    for (int tor = 0; tor < _top->no_of_tors(); tor++) {
        for (int uplink = 0;
             uplink < _top->no_of_hpr() + _top->no_of_uplinks(); uplink++) {
            Queue* q = _top->get_queue_tor(tor, uplink);
            if(q){
            q->reportMaxqueuesize();
            }
        }
    }
    /*
    cout << "QueueReport" << endl;
    for (int tor = 0; tor < _top->no_of_tors(); tor++) {
        for (int uplink = _top->no_of_hpr(); uplink < _top->no_of_hpr()*2; uplink++) {
            Queue* q = _top->get_queue_tor(tor, uplink);
            q->reportMaxqueuesize_perslice();
        }
    }
    */
    if (eventlist().now() + _period < eventlist().getEndtime())
        eventlist().sourceIsPendingRel(*this, _period);
}
