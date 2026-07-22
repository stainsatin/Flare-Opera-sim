// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        
#include <sstream>
#include <math.h>
#include "datacenter/dynexp_topology.h"
#include "queue.h"
#include "tcppacket.h"
#include "ndppacket.h"
#include "rlbpacket.h" // added
#include "hbhpacket.h"
#include "queue_lossless.h"

#include "pipe.h"
#include "creditqueue.h"

#include "rlb.h" // needed to make dummy packet
#include "tcp.h"
#include "xpass.h"
#include "hbh.h"
#include "rlbmodule.h"

Queue::Queue(linkspeed_bps bitrate, mem_b maxsize, EventList& eventlist, QueueLogger* logger)
  : EventSource(eventlist,"queue"), _maxsize(maxsize), _tor(0), _port(0), _top(NULL),
    _logger(logger), _bitrate(bitrate), _num_drops(0)
{
    _queuesize = 0;
    _max_recorded_size = 0;
    _max_ever_recorded_size = 0;
    _byteps = _bitrate/8;
    _ps_per_byte = (simtime_picosec)((pow(10.0, 12.0) * 8) / _bitrate);
    stringstream ss;
    _txbytes = 0;
    _prev_txbytes = 0;
    _prev_sample_t = 0;
    //ss << "queue(" << bitrate/1000000 << "Mb/s," << maxsize << "bytes)";
    //_nodename = ss.str();
}

Queue::Queue(linkspeed_bps bitrate, mem_b maxsize, EventList& eventlist, QueueLogger* logger, int tor, int port, DynExpTopology *top)
  : EventSource(eventlist,"queue"), _maxsize(maxsize), _tor(tor), _port(port), _top(top),
    _logger(logger), _bitrate(bitrate), _num_drops(0)
{
    _queuesize = 0;
    _max_ever_recorded_size = 0;
    _ps_per_byte = (simtime_picosec)((pow(10.0, 12.0) * 8) / _bitrate);
    stringstream ss;
    _max_recorded_size_slice.resize(_top->get_nsuperslice());
    _max_recorded_size = 0;
    //ss << "queue(" << bitrate/1000000 << "Mb/s," << maxsize << "bytes)";
    //_nodename = ss.str();
}


void Queue::beginService() {
    /* schedule the next dequeue event */
    assert(!_enqueued.empty());
    eventlist().sourceIsPendingRel(*this, drainTime(_enqueued.back()));
}

void Queue::completeService() {
    /* dequeue the packet */
    assert(!_enqueued.empty());
    Packet* pkt = _enqueued.back();
    _enqueued.pop_back();
    _queuesize -= pkt->size();
    
    /* tell the packet to move on to the next pipe */
    //pkt->sendFromQueue();
    sendFromQueue(pkt);

    if (!_enqueued.empty()) {
        /* schedule the next dequeue event */
        beginService();
    }
}

void Queue::sendFromQueue(Packet* pkt) {
    updatePktOut(pkt->flow_id());
    Pipe* nextpipe; // the next packet sink will be a pipe
    DynExpTopology* top = pkt->get_topology();
    _txbytes += pkt->size();
    if (pkt->get_crthop() < 0) {
        //cout << "sendFromQueue (from NIC) " << _tor << " " << _port << endl; 
        // we're sending out of the NIC
        nextpipe = top->get_pipe_serv_tor(pkt->get_src());
        nextpipe->receivePacket(*pkt);
    } else {
        if (!top->is_last_hop(_port)) {
            int next_tor = top->get_nextToR(top->time_to_slice(eventlist().now()),
                    pkt->get_crtToR(), pkt->get_crtport());
            pkt->add_hop({_tor, next_tor});
        } else {
            pkt->add_hop({-1, -1}); //downlink
        }
        //cout << "sendFromQueue (from ToR) " << _tor << " " << _port << endl; 
        // we're sending out of a ToR queue
        if (top->is_last_hop(pkt->get_crtport())) {
            pkt->set_lasthop(true);
            int slice = pkt->get_topology()->time_to_slice(eventlist().now());
            // if this port is not connected to _dst, then drop the packet
            if (!top->port_dst_match(pkt->get_crtport(), pkt->get_crtToR(), pkt->get_dst())) {

                switch (pkt->type()) {
                    case RLB:
                        cout << "!!! RLB";
                        break;
                    case NDP:
                        cout << "!!! NDP";
                        break;
                    case NDPACK:
                        cout << "!!! NDPACK";
                        break;
                    case NDPNACK:
                        cout << "!!! NDPNACK";
                        break;
                    case NDPPULL:
                        cout << "!!! NDPPULL";
                        break;
                    case TCP:
                    {
                        cout << "!!! TCP";
                        TcpPacket *tcppkt = (TcpPacket*)pkt;
                        tcppkt->get_tcpsrc()->add_to_dropped(tcppkt->seqno());
      if(tcppkt->flow_id() == 75184) cout << "DROPPED HERE (wrongdst) seqno " << tcppkt->seqno() << endl;
                        break;
                    }
                    case XPDATA:
		    {
                        __global_topology_wrong_dst_data++;
                        cout << "!!! XPDATA";
                        break;
		    }
                    case XPCREDIT:
                        __global_topology_wrong_dst_credits++;
                        recordFlowCreditTopologyDrop(
                            *pkt, max(pkt->get_crthop(), 0), true);
                        cout << "!!! XPCREDIT";
                        break;
                    case XPCTL:
                        __global_topology_wrong_dst_control++;
                        cout << "!!! XPCTL";
                        break;
                    case HBHDATA:
                    {
                        cout << "!!! HBHDATA";
                        break;
                    }
                    default:
                        __global_topology_wrong_dst_other++;
                        break;
                }
                cout << " packet dropped at " << nodename() << ": port & dst didn't match! (queue.cpp)" << endl;
                cout << "    ToR = " << pkt->get_crtToR() << ", port = " << pkt->get_crtport() <<
                    ", src = " << pkt->get_src() << ", dst = " << pkt->get_dst() << 
                    ", pktid = " << pkt->id() << ", qbytes = " << pkt->get_queueing() << 
                    ", sentslice = " << pkt->get_slice_sent() << ", crtslice = " << slice << endl;
                top->inc_losses();
                //assert(pkt->id() != 549007);
                //assert(0);
		if(pkt->type() == XPDATA) {
		    XPassPacket *xppkt = (XPassPacket*)pkt;
		    xppkt->get_xpsrc()->setFinished();
		}
                if(pkt->type() != HBHDATA){
                pkt->free(); // drop the packet
                } else {
                    HbHPacket *hbhpkt = (HbHPacket*)pkt;
                    HbHSrc *hbhsrc = hbhpkt->get_hbhsrc();
                    hbhsrc->addToLost(hbhpkt);
                }
                return;
            }
        }
        /*
        if(pkt->type() == TCP && pkt->get_src() == 186 && pkt->get_dst() == 121) {
        cout << "SENTOUT " << eventlist().now() << endl;
        }
        */
        nextpipe = top->get_pipe_tor(pkt->get_crtToR(), pkt->get_crtport());
        nextpipe->receivePacket(*pkt);

    }
}

void Queue::doNextEvent() {
    completeService();
}


void Queue::receivePacket(Packet& pkt) {
    updatePktIn(pkt.flow_id());
    if (_queuesize+pkt.size() > _maxsize) {
	/* if the packet doesn't fit in the queue, drop it */
    /*
	if (_logger) 
	    _logger->logQueue(*this, QueueLogger::PKT_DROP, pkt);
	pkt.flow().logTraffic(pkt, *this, TrafficLogger::PKT_DROP);
    */
    _top->inc_losses();
	pkt.free();
    //cout << "!!! Packet dropped: queue overflow!" << endl;
    if(pkt.type() == TCP){
        TcpPacket *tcppkt = (TcpPacket*)&pkt;
        tcppkt->get_tcpsrc()->add_to_dropped(tcppkt->seqno());
    }
    _num_drops++;
	return;
    }

    /* enqueue the packet */
    bool queueWasEmpty = _enqueued.empty();
    _enqueued.push_front(&pkt);
    _queuesize += pkt.size();
    _max_recorded_size = max(_max_recorded_size, _queuesize);
    _max_ever_recorded_size = max(_max_ever_recorded_size, _queuesize);
    pkt.inc_queueing(_queuesize);

    if (queueWasEmpty) {
	/* schedule the dequeue event */
	assert(_enqueued.size() == 1);
	beginService();
    }
}

mem_b Queue::queuesize() {
    return _queuesize;
}

simtime_picosec Queue::serviceTime() {
    return _queuesize * _ps_per_byte;
}

void Queue::reportMaxqueuesize(){
    simtime_picosec crt_t = eventlist().now();
    cout << "Queue " << _tor << " " << _port << " " << _max_recorded_size << " " <<
        queuesize();
    reportLoss();
    cout << endl;
    _max_recorded_size = 0;
    _flows_at_queues.clear();
    _prev_txbytes = _txbytes;
    _prev_sample_t = crt_t;
}

//format "tor,port,slice0max,slice1max,slice2max...,slicenmax"
void Queue::reportMaxqueuesize_perslice(){
    cout << _tor << "," << _port << ",";
    for(size_t i = 0; i < _max_recorded_size_slice.size(); i++) {
        cout << _max_recorded_size_slice[i] << ",";
        _max_recorded_size_slice[i] = 0;
    }
    cout << endl;
}

void Queue::reportQueuesize() {
    cout << "Queuesize " << _tor << "," << _port << "," << queuesize() << endl;
}

void Queue::reportLoss() {
  return;
}

void Queue::updatePktOut(uint64_t id){
    return;
    assert(_pkts_per_flow[id] > 0);
    _pkts_per_flow[id] -= 1; 
    if(_pkts_per_flow[id] == 0) {
        _flows_at_queues.erase(id);
    }
}

void Queue::updatePktIn(uint64_t id){
    return;
    _flows_at_queues.insert(id);
    _pkts_per_flow[id] += 1; 
    if(_pkts_per_flow[id] == 1){
        _flows_at_queues.insert(id);
    }
}

set<uint64_t> Queue::getFlows(){
    auto tmp = _flows_at_queues;
    _flows_at_queues.clear();
    return tmp;
}


//////////////////////////////////////////////////
//              Priority Queue                  //
//////////////////////////////////////////////////

PriorityQueue::PriorityQueue(linkspeed_bps bitrate, mem_b maxsize, 
			     EventList& eventlist, QueueLogger* logger, int node, DynExpTopology *top)
    : Queue(bitrate, maxsize, eventlist, logger)
{
    _node = node;
    _top = top;

    _bytes_sent = 0;

    _queuesize[Q_RLB] = 0;
    _queuesize[Q_LO] = 0;
    _queuesize[Q_MID] = 0;
    _queuesize[Q_HI] = 0;
    _servicing = Q_NONE;
    //_state_send = LosslessQueue::READY;
}

PriorityQueue::queue_priority_t PriorityQueue::getPriority(Packet& pkt) {
    queue_priority_t prio = Q_LO;
    switch (pkt.type()) {
    case TCPACK:
    case NDPACK:
    case NDPNACK:
    case NDPPULL:
    case NDPLITEACK:
    case NDPLITERTS:
    case NDPLITEPULL:
    case SAMPLE:
        prio = Q_HI;
        break;
    case NDP:
        if (pkt.header_only()) {
            prio = Q_HI;
        } else {
        /*
            NdpPacket* np = (NdpPacket*)(&pkt);
            if (np->retransmitted()) {
                prio = Q_MID;
            } else {
                prio = Q_LO;
            }
        */
        }
        break;
    case RLB:
        prio = Q_RLB;
        break;
    case TCP:
    case IP:
    case NDPLITE:
        prio = Q_LO;
        break;
    default:
        cout << "NIC couldn't identify packet type." << endl;
        abort();
    }
    
    return prio;
}

simtime_picosec PriorityQueue::serviceTime(Packet& pkt) {
    queue_priority_t prio = getPriority(pkt);
    switch (prio) {
    case Q_LO:
	   //cout << "q_lo: " << _queuesize[Q_HI] + _queuesize[Q_MID] + _queuesize[Q_LO] << " ";
	   return (_queuesize[Q_HI] + _queuesize[Q_MID] + _queuesize[Q_LO]) * _ps_per_byte;
    case Q_MID:
	   //cout << "q_mid: " << _queuesize[Q_MID] + _queuesize[Q_LO] << " ";
	   return (_queuesize[Q_HI] + _queuesize[Q_MID]) * _ps_per_byte;
    case Q_HI:
	   //cout << "q_hi: " << _queuesize[Q_LO] << " ";
	   return _queuesize[Q_HI] * _ps_per_byte;
    case Q_RLB:
        abort(); // we should never check this for an RLB packet
    default:
	   abort();
    }
}

void PriorityQueue::doorbell(bool rlbwaiting) {

    if (rlbwaiting) { // add a dummy packet to the queue

        // debug:
        //cout << "NIC[node" << _node << "] - doorbell that RLB has packets" << endl;

        //if (_node == 345 && timeAsUs(eventlist().now()) > 18100) {
    	//	cout << "   Doorbell TRUE at " << timeAsUs(eventlist().now()) << " us." << endl;
    	//}


        RlbPacket* pkt = RlbPacket::newpkt(1500); // make a dummy packet
        pkt->set_dummy(true);
        receivePacket(*pkt); // put that dummy packet in the RLB queue
            // ! note - use `receivePacket` so we trigger service to begin
    } else {
        // the RLB module isn't ready to send more packets right now
        // drop the dummy packet in the queue so we don't try to pull from the RLB module

        //if (_node == 345 && timeAsUs(eventlist().now()) > 18100) {
    	//	cout << "   Doorbell FALSE at " << timeAsUs(eventlist().now()) << " us." << endl;
    	//}

        Packet* pkt = _queue[0].front(); // RLB enumerates to 0
        _top->inc_losses();
        pkt->free();
        _queue[0].pop_front();
        _queuesize[0] = 0; // set queuesize to zero. 
    }
}

void PriorityQueue::receivePacket(Packet& pkt) {
    queue_priority_t prio = getPriority(pkt);
    // TcpSrc* tcp_src = pkt.get_tcpsrc();
    // uint32_t seqno = ((TcpPacket *)&pkt)->seqno();
    // if(tcp_src->_seqno_to_slice[seqno] == 0 && tcp_src->get_flow_src() == 274) {
    //     cout << "queue received " << seqno << ' ' << drainTime(&pkt) << '\n';
    // }
    // debug:
    //cout << "   NIC received a packet." << endl;

    //pkt.flow().logTraffic(pkt, *this, TrafficLogger::PKT_ARRIVE);

    /* enqueue the packet */
    bool queueWasEmpty = false;
    if (queuesize() == 0)
        queueWasEmpty = true;

/*
    if (queuesize()+pkt.size() > _maxsize) {
        //if the packet doesn't fit in the queue, drop it
        if(pkt.type() == TCP){
            TcpPacket *tcppkt = (TcpPacket*)&pkt;
            tcppkt->get_tcpsrc()->add_to_dropped(tcppkt->seqno());
        }
        cout << nodename() << " DROPPED " << queuesize() << " " << _top->time_to_slice(eventlist().now()) << endl;
        pkt.free();
        return;
    }
*/

    _queuesize[prio] += pkt.size();
    _queue[prio].push_front(&pkt);

    //if (_logger)
    //    _logger->logQueue(*this, QueueLogger::PKT_ENQUEUE, pkt);

    updatePktIn(pkt.flow_id());
    if (queueWasEmpty) { // && _state_send==LosslessQueue::READY) {
        /* schedule the dequeue event */
        assert(_queue[Q_RLB].size() + _queue[Q_LO].size() + _queue[Q_MID].size() + _queue[Q_HI].size() == 1);
        beginService();
    }
}

void PriorityQueue::beginService() {
    //assert(_state_send == LosslessQueue::READY);

    /* schedule the next dequeue event */
    for (int prio = Q_HI; prio >= Q_RLB; --prio) {
        if (_queuesize[prio] > 0) {
            eventlist().sourceIsPendingRel(*this, drainTime(_queue[prio].back()));
            //cout << "PrioQueue (node " << _node << ") sending a packet at " << timeAsUs(eventlist().now()) << " us" << endl;
            //cout << "   will be drained in " << timeAsUs(drainTime(_queue[prio].back())) << " us" << endl;

            _servicing = (queue_priority_t)prio;

            // debug:
            //if (_node == 345 && timeAsUs(eventlist().now()) > 18100) {
    		//	cout << "   beginService on _servicing: [" << _servicing << "] at " << timeAsUs(eventlist().now()) << " us." << endl;
    		//}

            return;
        }
    }
}

void PriorityQueue::completeService() {
	if (!_queue[_servicing].empty()) {

	    Packet* pkt;

	    switch (_servicing) {
	    case Q_RLB:
	    {
	        RlbModule* mod = _top->get_rlb_module(_node);
	        pkt = mod->NICpull(); // get the packet from the RLB module

	        // check if the packet is a dummy (spacer packet for rate limiting)
	        // If so, free the packet, and return;
	        if (pkt->is_dummy()) {
	            pkt->free();
	            break;
	        } else {
	            // RLB only applies to packets between racks.
                // for RLB, we actually set `slice_sent` when it's committed by the RLB module, not when it's sent by the NIC

                pkt->set_src_ToR(_top->get_firstToR(pkt->get_src())); // set the sending ToR. This is used for subsequent routing

	            // send on the first path (index 0) to the "intermediate" destination
	            int path_index = 0; // index 0 ensures it's the direct path
	            pkt->set_path_index(path_index); // set which path the packet will take

	            // set some initial packet parameters used for routing
	            pkt->set_lasthop(false);
	            pkt->set_crthop(-1);
	            pkt->set_crtToR(-1);
	            pkt->set_maxhops(_top->get_no_hops(pkt->get_src_ToR(),
	                _top->get_firstToR(pkt->get_dst()), pkt->get_slice_sent(), path_index));
	        }
	        /* tell the packet to move on to the next pipe */
	        sendFromQueue(pkt);

	        break;
	    }
	    case Q_LO:
	    case Q_MID:
	    case Q_HI:
	    {

	        pkt = _queue[_servicing].back(); // get the pointer to the packet
	        _queue[_servicing].pop_back(); // delete the element of the queue
	        _queuesize[_servicing] -= pkt->size(); // decrement the queue size

	        int new_bytes_sent = _bytes_sent + pkt->size();
	        if (new_bytes_sent / 1500 > _bytes_sent / 1500) {
	            // we sent a "full" packet ahead of RLB, notify RLB to push
	            RlbModule* mod = _top->get_rlb_module(_node);
	            mod->NICpush();
	        }
	        _bytes_sent = new_bytes_sent;



	        // set the routing info

            pkt->set_src_ToR(_top->get_firstToR(pkt->get_src())); // set the sending ToR. This is used for subsequent routing

	        if (pkt->get_src_ToR() == _top->get_firstToR(pkt->get_dst())) {
	            // the packet is being sent within the same rack
	            pkt->set_lasthop(false);
	            pkt->set_crthop(-1);
	            pkt->set_crtToR(-1);
	            pkt->set_maxhops(0); // want to select a downlink port immediately
	        } else {
	            // the packet is being sent between racks

	            // we will choose the path based on the current slice
                int slice = _top->time_to_slice(eventlist().now());
	            // get the number of available paths for this packet during this slice
	            int npaths = _top->get_no_paths(pkt->get_src_ToR(),
	                _top->get_firstToR(pkt->get_dst()), slice);

	            if (npaths == 0)
	                cout << "Error: there were no paths for slice " << slice  << " src " << pkt->get_src_ToR() <<
                        " dst " << _top->get_firstToR(pkt->get_dst()) << endl;
	            assert(npaths > 0);

	            // randomly choose a path for the packet
	            // !!! todo: add other options like permutation, etc...
	            //int path_index = random() % npaths; //spraying
	            int path_index = pkt->get_path_index() % npaths; //fixed path per flow
                    //cout << "path_index " << path_index << endl;

	            pkt->set_slice_sent(slice); // "timestamp" the packet
                pkt->set_fabricts(eventlist().now());
	            pkt->set_path_index(path_index); // set which path the packet will take

	            // set some initial packet parameters used for label switching
	            // *this could also be done in NDP before the packet is sent to the NIC
	            pkt->set_lasthop(false);
	            pkt->set_crthop(-1);
	            pkt->set_crtToR(-1);
	            pkt->set_maxhops(_top->get_no_hops(pkt->get_src_ToR(),
	                _top->get_firstToR(pkt->get_dst()), slice, path_index));
                //TEST early feedback notification
               /*
                if(pkt->type() == TCP && !pkt->early_fb()){
                pkt->set_earlyhop(random()%(pkt->get_maxhops()+1));
                pkt->set_chosenhop(pkt->get_earlyhop());
                }
                */
	        }

            if(pkt->type() == TCP) {
                TcpSrc* tcp_src = pkt->get_tcpsrc();
                uint32_t seqno = ((TcpPacket*)pkt)->seqno();
                tcp_src->map_seqno_to_slice(seqno);
            }
            
	        /* tell the packet to move on to the next pipe */
	        sendFromQueue(pkt);

	        break;
	    }
	    case Q_NONE:
	    	break;
	        //abort();
	    }
	}

    if (queuesize() > 0) {
        beginService();
    } else {
        _servicing = Q_NONE;
    }
}

mem_b PriorityQueue::queuesize() {
    return _queuesize[Q_RLB] + _queuesize[Q_LO] + _queuesize[Q_MID] + _queuesize[Q_HI];
}

void
Queue::sendEarlyFeedback(Packet &pkt) {
        //return the packet to the sender
        DynExpTopology* top = pkt.get_topology();

        TcpSrc* tcpsrc = NULL;
        unsigned seqno;
        if(pkt.type() == TCP) {
            tcpsrc = ((TcpPacket*)(&pkt))->get_tcpsrc();
            seqno = ((TcpPacket*)(&pkt))->seqno();
        } else {
            return;
        }
        assert(tcpsrc != NULL);
        TcpAck *ack = tcpsrc->alloc_tcp_ack();
        ack->set_packetid(pkt.id());
        // flip the source and dst of the packet:
        int s = pkt.get_src();
        int d = pkt.get_dst();
        ack->sack_check = -1;
        ack->set_src(d);
        ack->set_dst(s);
        ack->set_ts(pkt.get_fabricts());
        ack->set_crtToR(pkt.get_crtToR());
        ack->set_src_ToR(pkt.get_src_ToR());
        ack->set_path(pkt.get_path());
        ack->set_early_fb();
        ack->set_lasthop(false);
        ack->set_chosenhop(pkt.get_chosenhop());
        ack->add_hop({-1,-1});
        ack->set_int(pkt.get_int());

        // get the current ToR, this will be the new src_ToR of the packet
        int new_src_ToR = ack->get_crtToR();

        if (new_src_ToR == ack->get_src_ToR()) {
            // the packet got returned at the source ToR
            // we need to send on a downlink right away
            ack->set_src_ToR(new_src_ToR);
            ack->set_crtport(top->get_lastport(ack->get_dst()));
            ack->set_maxhops(0);

            // debug:
            //cout << "   packet RTSed at the first ToR (ToR = " << new_src_ToR << ")" << endl;

        } else {
            ack->set_src_ToR(new_src_ToR);

            // get paths
            // get the current slice:
            int slice = _top->time_to_slice(eventlist().now());
            ack->set_slice_sent(slice); // "timestamp" the packet
            // get the number of available paths for this packet during this slice
            int npaths = top->get_no_paths(ack->get_src_ToR(),
                    top->get_firstToR(ack->get_dst()), slice);
            if (npaths == 0)
                cout << "Error: there were no paths!" << endl;
            assert(npaths > 0);

            // randomly choose a path for the packet
            int path_index = random() % npaths;
            ack->set_path_index(path_index); // set which path the packet will take
            ack->set_maxhops(top->get_no_hops(ack->get_src_ToR(),
                        top->get_firstToR(ack->get_dst()), slice, path_index));

            ack->set_crtport(top->get_port(new_src_ToR, top->get_firstToR(ack->get_dst()),
                        slice, path_index, 0)); // current hop = 0 (hardcoded)

        }
        ack->set_crthop(0);
        ack->set_earlyhop(-1);
        ack->set_crtToR(new_src_ToR);
        Queue* nextqueue = top->get_queue_tor(ack->get_crtToR(), ack->get_crtport());
        nextqueue->receivePacket(*ack);
}
