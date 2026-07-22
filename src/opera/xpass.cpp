// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-    
#include <cstdint>
#include <cstdlib>
#include <math.h>
#include <iostream>
#include "config.h"
#include "creditqueue.h"
#include "datacenter/dynexp_topology.h"
#include "eventlist.h"
#include "xpass.h"
#include "queue.h"
#include "xpasspacket.h"
#include "pipe.h"
#include <stdio.h>
#include <type_traits>

static bool debug_flow(uint64_t flow_id) {
    return false;
    if(flow_id == 419259) return true;
    return false;
    if(flow_id == 5196) return true;
    if(flow_id == 5836) return true;
    return false;
}

map<int, uint64_t> src_to_flows;
map<int, uint64_t> dst_to_flows;

static uint64_t id_gen;

////////////////////////////////////////////////////////////////
//  XPASS SOURCE
////////////////////////////////////////////////////////////////

#define SLICE_RESET
//#define TP_SAMPLING

simtime_picosec XPassSrc::_min_rto = timeFromMs(0.2);

static uint64_t flow_id_gen;

XPassSrc::XPassSrc(EventList &eventlist, int flow_src, int flow_dst, bool is_flare, DynExpTopology *top)
: EventSource(eventlist,"xpass"),  _top(top), _flow(NULL), _flow_src(flow_src), _flow_dst(flow_dst){
  _mss = Packet::data_packet_size();

  _base_rtt = timeInf;
  _acked_packets = 0;
  _packets_sent = 0;
  _new_packets_sent = 0;
  _rtx_packets_sent = 0;

  _flight_size = 0;

  _highest_sent = 0;
  _last_acked = 0;
  _finished = false;
  _last_unsched_sent = 0;

  _sink = 0;

  _rtt = 0;
  _rto = timeFromMs(0.2);
  _cwnd = 1 * Packet::data_packet_size();
  _init_cwnd = _cwnd;
  _mdev = 0;
  _drops = 0;
  _flow_size = ((uint64_t)1)<<63;
  _last_pull = 0;

  _crt_path = 0; // used for SCATTER_PERMUTE route strategy
  _is_flare = is_flare;

  _feedback_count = 0;
  _rtx_timeout_pending = false;
  _rtx_timeout = timeInf;
  _nodename = "xpasssrc" + to_string(_node_num);

  // debugging hack
  _log_me = false;
}

void XPassSrc::set_flowsize(uint64_t flow_size_in_bytes) {

  _flow_size = flow_size_in_bytes; //+1 for ackreq in first unscheduled BDP
  if (_flow_size < _mss)
    _pkt_size = _flow_size;
  else
    _pkt_size = _mss;
}

void XPassSrc::set_traffic_logger(TrafficLogger* pktlogger) {
  _flow.set_logger(pktlogger);
}

void XPassSrc::log_me() {
  // avoid looping
  if (_log_me == true)
    return;
  cout << "Enabling logging on XPassSrc " << _nodename << endl;
  _log_me = true;
  if (_sink)
    _sink->log_me();
}

void XPassSrc::startflow(){
  //cout << "startflow size " << _flow_size << endl;
  _flow_id = flow_id_gen++;
  _path_index = fast_rand();
  _highest_sent = 0;
  _last_acked = 0;

  _acked_packets = 0;
  _packets_sent = 0;
  _rtx_timeout_pending = false;
  _rtx_timeout = timeInf;

  _flight_size = 0;
  _first_window_count = 0;
  _sink->_crt_slice = -1;
  _sink->_crt_slice_route = -1;
  _sink->_crt_rate = _sink->_max_rate*_sink->_w_init;
  while (_flight_size < _cwnd && _flight_size < _flow_size) {
    //send out unscheduled packets (may get dropped at queue)
    send_packet(0, -1, false, 0, true, 0);
    _first_window_count++;
  }
  //after first unscheduled BDP request cumulative ack
  send_cumack_req();
}

void
XPassSrc::setFinished() {
  if(!_finished) {
    _finished = true;
    cout << "UNFINISHED " << flow_id() << " " << _flow_size << endl;
  }
}


void XPassSrc::connect(XPassSink& sink, simtime_picosec starttime) {

  _sink = &sink;
  _flow.id = id; // identify the packet flow with the XPASS source that generated it
  _flow._name = _name;
  _sink->_is_flare = _is_flare;
  _sink->connect(*this);

  set_start_time(starttime); // record the start time in _start_time
  eventlist().sourceIsPending(*this,starttime);

}

#define ABS(X) ((X)>0?(X):-(X))

void XPassSrc::receivePacket(Packet& pkt) 
{
  //cout << "xpasssrc " << flow_id() << " receivePacket " << eventlist().now() << endl;
  pkt.clear_path();
  switch (pkt.type()) {
    case XPCREDIT: 
      {
        XPassPull *p = (XPassPull*)(&pkt);
        XPassPull::seq_t cum_ackno = p->cumulative_ack();
        if (cum_ackno > _last_acked) { // a brand new ack    
          // we should probably cancel the rtx timer for any acked by
          // the cumulative ack, but we'll get an ACK or NACK anyway in
          // due course.
          _last_acked = cum_ackno;
          update_rtx_time();
	  list<XPassPacket*>::iterator it;
	  int to_pop = 0;
	  for(it = _rtx_queue.begin(); it != _rtx_queue.end(); ++it) {
            if((*it)->seqno() < _last_acked) {
              if(debug_flow(flow_id())) {
                cout << "POP_RTX seqno " << (*it)->seqno() << " cumack " << cum_ackno << endl;
              }
              to_pop++;
	    }
	  }
	  for(int i = 0; i < to_pop; i++) {
            XPassPacket* rtxp = _rtx_queue.front();
	    _rtx_queue.pop_front();
              if(debug_flow(flow_id())) {
                cout << "POPPING seqno " << rtxp->seqno() << endl;
              }
	    rtxp->free();
	  }
        }
        if(p->tentative()) {
            _tentative_received++;
            //cout << "TENTATIVE " << _tentative_received << " flow " << _flow_id << endl;
        }
        unsigned queueing = p->get_queueing();
        send_packet(p->pacerno(), p->get_slice_sent(), p->tentative(), p->ts(), false, queueing);
        _last_pull++;
        pkt.free();
        return;
      }
    case XPCTL:
      {
        XPassCtl *p = (XPassCtl*)(&pkt);
        uint64_t ackno = p->ackno(); 
	if(debug_flow(flow_id())){
        cout << "ACKREQ last_unsched " << _last_unsched_sent << " ACKNO " << ackno << " flowsize " << _flow_size << endl;
	}
        while(ackno < _last_unsched_sent) {
          unsigned pkt_size = ackno + _pkt_size > _flow_size ? _flow_size-ackno : _pkt_size;
          assert(pkt_size > 0);
          assert(pkt_size <= Packet::data_packet_size());
          bool last_packet = false;
          if (ackno + pkt_size >= _flow_size) {
            last_packet = true;
          }
          XPassPacket *p = XPassPacket::newpkt(_top, _flow_src, _flow_dst, this, _sink, 
              ackno+1, 0, pkt_size, false, last_packet); 
          p->set_packetid(id_gen++);
          _rtx_queue.push_back(p);
          //cout << "\tpush to rtxq seq " << ackno+1 << " size " << pkt_size << endl;
          ackno += pkt_size;
        }
        pkt.free();
        return;
      }
    default: assert(0);
  }
}

//send a 1byte packet requesting a cumulative ack.
void XPassSrc::send_cumack_req() {
    XPassPacket *p = XPassPacket::newpkt(_top, _flow_src, _flow_dst, this, _sink, 
        _highest_sent+1, 0, 1, true, false);
    p->set_ackreq(true);
    _highest_sent += 1;  //XX beware wrapping
    _packets_sent++;
    _new_packets_sent++;
    p->set_packetid(id_gen++);

    p->set_credit_ts(-1);
    p->set_ts(eventlist().now());
    p->set_tentative(false);
    p->set_flow_id(_flow_id);
    p->set_path_index(_path_index);

    PacketSink* sink = sendToNIC(p);
    NICCreditQueue *q = dynamic_cast<NICCreditQueue*>(sink);
    assert(q);
}

// Note: the data sequence number is the number of Byte1 of the packet, not the last byte.
// packets embed the corresponding credit slice.
// if a packet is sent without a credit, slice should be set to -1.
void XPassSrc::send_packet(XPassPull::seq_t pacer_no, int credit_slice, 
        bool tentative, simtime_picosec ts, bool unsched, unsigned queueing) {
  XPassPacket* p;
  if (!_rtx_queue.empty()) {
    // There are packets in the RTX queue for us to send
    assert(!unsched);
    p = _rtx_queue.front();
    _rtx_queue.pop_front();
    p->set_credit_ts(credit_slice);
    p->set_ts(ts);
    p->set_pacerno(pacer_no);
    p->set_tentative(tentative);
    p->set_flow_id(_flow_id);
    p->set_path_index(_path_index);
    p->set_queueing(queueing);

    //cout << "RTX " << _flow_id << " seqno " << p->seqno() << " size " << p->size()-64 << endl;
    PacketSink* sink = sendToNIC(p);
    NICCreditQueue *q = dynamic_cast<NICCreditQueue*>(sink);
    assert(q);
    //Figure out how long before the feeder queue sends this
    //packet, and add it to the sent time. Packets can spend quite
    //a bit of time in the feeder queue.  It would be better to
    //have the feeder queue update the sent time, because the
    //feeder queue isn't a FIFO but that would be hard to
    //implement in a real system, so this is a rough proxy.
    uint32_t service_time = q->serviceTime();  
    _sent_times[p->seqno()] = eventlist().now() + service_time;
    _packets_sent ++;
    _rtx_packets_sent++;
    update_rtx_time();
    if (_rtx_timeout == timeInf) {
      _rtx_timeout = eventlist().now() + _rto;
    }
  } else {
    // there are no packets in the RTX queue, so we'll send a new one
    if (_highest_sent >= _flow_size) {
      return;
    } 
    unsigned pkt_size = _highest_sent + _pkt_size > _flow_size ? _flow_size-_highest_sent : _pkt_size;
    assert(pkt_size > 0);
    assert(pkt_size <= (unsigned)Packet::data_packet_size());
    bool last_packet = false;
    if (_highest_sent + pkt_size >= _flow_size) {
      last_packet = true;
    }
    p = XPassPacket::newpkt(_top, _flow_src, _flow_dst, this, _sink, _highest_sent+1, pacer_no, pkt_size, false, last_packet);
    p->set_credit_ts(credit_slice);
    p->set_ts(eventlist().now());
    p->set_tentative(tentative);
    p->set_unsched(unsched);
    p->set_flow_id(_flow_id);
    p->set_path_index(_path_index);
    p->set_packetid(id_gen++);
    p->set_queueing(queueing);
    //cout << "xpasssrc " << _flow_id << " send_pkt seqno " << p->seqno() << " segsize " << pkt_size << " " << eventlist().now() << endl;

    _flight_size += pkt_size;
    // 	if (_log_me) {
    // 	    cout << "Sent " << _highest_sent+1 << " FSz: " << _flight_size << endl;
    // 	}

    //keep track of the last unscheduled packet for potential recovery
    if(unsched) {
        _last_unsched_sent = _highest_sent+1;
    }
    _highest_sent += pkt_size;  //XX beware wrapping
    _packets_sent++;
    _new_packets_sent++;

    PacketSink* sink = sendToNIC(p);
    NICCreditQueue *q = dynamic_cast<NICCreditQueue*>(sink);
    assert(q);
    //Figure out how long before the feeder queue sends this
    //packet, and add it to the sent time. Packets can spend quite
    //a bit of time in the feeder queue.  It would be better to
    //have the feeder queue update the sent time, because the
    //feeder queue isn't a FIFO but that would be hard to
    //implement in a real system, so this is a rough proxy.
    uint32_t service_time = q->serviceTime();  
    //cout << "service_time2: " << service_time << endl;
    _sent_times[p->seqno()] = eventlist().now() + service_time;
    _first_sent_times[p->seqno()] = eventlist().now();

    if (_rtx_timeout == timeInf) {
      _rtx_timeout = eventlist().now() + _rto;
    }
  }
}

void 
XPassSrc::update_rtx_time() {
  //simtime_picosec now = eventlist().now();
  if (_sent_times.empty()) {
    _rtx_timeout = timeInf;
    return;
  }
  map<XPassPacket::seq_t, simtime_picosec>::iterator i;
  simtime_picosec first_senttime = timeInf;
  int c = 0;
  for (i = _sent_times.begin(); i != _sent_times.end(); i++) {
    simtime_picosec sent = i->second;
    if (sent < first_senttime || first_senttime == timeInf) {
      first_senttime = sent;
    }
    c++;
  }
  _rtx_timeout = first_senttime + _rto;
}

void 
XPassSrc::process_cumulative_ack(XPassPacket::seq_t cum_ackno) {
  map<XPassPacket::seq_t, simtime_picosec>::iterator i, i_next;
  i = _sent_times.begin();
  while (i != _sent_times.end()) {
    if (i->first <= cum_ackno) {
      i_next = i; //juggling to keep i valid
      i_next++;
      _sent_times.erase(i);
      i = i_next;
      } else {
      return;
    }
  }
  //need to call update_rtx_time right after this!
}

void 
XPassSrc::retransmit_packet() {
  //cout << "starting retransmit_packet\n";
  XPassPacket* p;
  map<XPassPacket::seq_t, simtime_picosec>::iterator i, i_next;
  i = _sent_times.begin();
  list <XPassPacket::seq_t> rtx_list;
  // we build a list first because otherwise we're adding to and
  // removing from _sent_times and the iterator gets confused
  while (i != _sent_times.end()) {
    if (i->second + _rto <= eventlist().now()) {
      //cout << "_sent_time: " << timeAsUs(i->second) << "us rto " << timeAsUs(_rto) << "us now " << timeAsUs(eventlist().now()) << "us\n";
      //this one is due for retransmission
      rtx_list.push_back(i->first);
      i_next = i; //we're about to invalidate i when we call erase
      i_next++;
      _sent_times.erase(i);
      i = i_next;
    } else {
      i++;
    }
  }
  list <XPassPacket::seq_t>::iterator j;
  for (j = rtx_list.begin(); j != rtx_list.end(); j++) {
    XPassPacket::seq_t seqno = *j;
    bool last_packet = (seqno + _pkt_size - 1) >= _flow_size;
    p = XPassPacket::newpkt(_top, _flow_src, _flow_dst, this, _sink, seqno, 0, _pkt_size, true, last_packet);
    p->set_ts(eventlist().now());
    p->set_flow_id(_flow_id);
    p->set_path_index(_path_index);
    //_sent_times[seqno] = eventlist().now();
    // 	if (_log_me) {
    //cout << "Sent " << seqno << " RTx" << " flow id " << p->flow().id << endl;
    // 	}
    p->set_packetid(id_gen++);
    sendToNIC(p);
    _packets_sent++;
    _rtx_packets_sent++;
  }
  update_rtx_time();
}

void XPassSrc::rtx_timer_hook(simtime_picosec now, simtime_picosec period) {
  return;

  if (_highest_sent == 0) return;
  if (_rtx_timeout==timeInf || now + period < _rtx_timeout) return;

  cout <<"At " << timeAsUs(now) << "us RTO " << timeAsUs(_rto) << "us MDEV " << timeAsUs(_mdev) << "us RTT "<< timeAsUs(_rtt) << "us SEQ " << _last_acked / _mss << " CWND "<< _cwnd/_mss << " Flow ID " << str()  << endl;
  bool last_packet = false;
  if (_last_acked + _pkt_size >= _flow_size) {
      last_packet = true;
  }
  XPassPacket *p = XPassPacket::newpkt(_top, _flow_src, _flow_dst, this, _sink, 
          _last_acked, 0, _pkt_size, false, last_packet); 
  p->set_packetid(id_gen++);
  _rtx_queue.push_back(p);
  // here we can run into phase effects because the timer is checked
  // only periodically for ALL flows but if we keep the difference
  // between scanning time and real timeout time when restarting the
  // flows we should minimize them !
  if(!_rtx_timeout_pending) {
    _rtx_timeout_pending = true;

    // check the timer difference between the event and the real value
    simtime_picosec too_early = _rtx_timeout - now;
    if (now > _rtx_timeout) {
      // this shouldn't happen
      cout << "late_rtx_timeout: " << _rtx_timeout << " now: " << now << " now+rto: " << now + _rto << " rto: " << _rto << endl;
      too_early = 0;
    }
    eventlist().sourceIsPendingRel(*this, too_early);
  }
}

void XPassSrc::doNextEvent() {
  if (_rtx_timeout_pending) {
    _rtx_timeout_pending = false;
    retransmit_packet();
  } else {
    //cout << "Starting flow" << endl; // modification

    // debug:
    //if ( get_flow_src() == 0 && get_flow_dst() == 6) {
    //cout << "FST " << get_flow_src() << " " << get_flow_dst() << " " << get_flowsize() <<
    //    " " << timeAsMs(eventlist().now()) << " " << get_id() << endl;
    //}
    startflow();
  }
}

Queue* 
XPassSrc::sendToNIC(Packet* pkt) {
    DynExpTopology* top = pkt->get_topology();
    Queue* nic = top->get_queue_serv_tor(pkt->get_src()); // returns pointer to nic queue
    assert(nic);
    nic->receivePacket(*pkt); // send this packet to the nic queue
    return nic; // return pointer so NDP source can update send time
}

double
XPassSrc::get_rate_as_ratio() {
  return (long double)_sink->_crt_rate / _sink->_max_rate;
}

////////////////////////////////////////////////////////////////
//  XPASS SINK
////////////////////////////////////////////////////////////////

XPassSink::XPassSink(EventList& eventlist) 
    : EventSource(eventlist, "sink"), _cumulative_ack(0) , _total_received(0) 
{
  _src = 0;
  _nodename = "xpsink";
  _pull_no = 0;
  _last_packet_seqno = 0;
  _last_packet_pacerno = 0;
  _log_me = false;
  _total_received = 0;
  _max_rate = speedFromMbps((uint64_t)95000);
  //_max_rate = speedFromMbps((uint64_t)100);
  //_min_rate = speedFromMbps((uint64_t)10000);
  //_min_rate = speedFromMbps((uint64_t)1000);
  _min_rate = speedFromMbps((uint64_t)100);
  //_min_rate = speedFromMbps((uint64_t)99);
  _max_weight = 0.5;
  _weight = _max_weight;
  _min_weight = 0.01;
  _weight_factor = 1.2;
  _target_loss = 0.1;
  _w_init = 0.5;
  _jit_alpha = -1;
  _jit_beta = -1;
  _fb_sens = false;
  _max_hop_jitter = 1.0;
  _min_hop_jitter = -1.0;
  //_hop_jitter = _min_hop_jitter;
  _hop_jitter = 0.0;
  _bdp = 240000;
  _is_increasing = true;
  _last_fb_update = 0;
  _recvd_data = 0;
  _last_tp_sample_t = 0;
  _last_valid_cred_t = 0;
  _total_hops = 0;
  _crt_hops = -1;
  _is_pulling = false;
  _is_recovering = false;
  _rtt = timeInf;
  _bw_sent_in_quantum.resize(256);
  _tp_sampling_freq = 0;
}

void XPassSink::log_me() {
  // avoid looping
  if (_log_me == true)
    return;

  _log_me = true;
  if (_src)
    _src->log_me();

}

uint64_t XPassSink::inflight_data() {
  return _src->_pkt_size*(_credit_counter-_last_packet_pacerno);
}
uint64_t XPassSink::inflight_creds() {
  return _credit_counter-_last_packet_pacerno;
}

uint64_t XPassSink::max_rtt_pkts() {
    int hops = max(_crt_hops, 1);
    int delta = 20; //expected pkts on data path
    //hops+1 to count also NIC->ToR
    return 60*hops;
}
uint64_t XPassSink::crt_rtt_pkts() {
    simtime_picosec spacing = (simtime_picosec)((Packet::data_packet_size()+64) 
            * (pow(10.0,12.0) * 8) / _max_rate);
    uint64_t rtt_pkts = _rtt/spacing;
    return rtt_pkts < max_rtt_pkts() ?
        rtt_pkts : max_rtt_pkts();
}
simtime_picosec XPassSink::max_rtt_picosec() {
    simtime_picosec spacing = (simtime_picosec)((Packet::data_packet_size()+64) 
            * (pow(10.0,12.0) * 8) / _max_rate);
    simtime_picosec rtt_pkts = spacing*max_rtt_pkts();
    return rtt_pkts;
}

uint64_t XPassSink::remaining_in_creds() {
    unsigned remaining_bytes = _src->_flow_size - _cumulative_ack;
    unsigned remaining_pkts = remaining_bytes/Packet::data_packet_size();
    double add_inflight_ratio = 0.1; //how many more in-flight to allow proportionally
    return remaining_pkts + remaining_pkts*add_inflight_ratio;
}

/* Connect a src to this sink.  We normally won't use this route if
   we're sending across multiple paths - call set_paths() after
   connect to configure the set of paths to be used. */
void XPassSink::connect(XPassSrc& src)
{
  _src = &src;
  
  _cumulative_ack = 0;
  _drops = 0;
}

// Receive a packet.
// Note: _cumulative_ack is the last byte we've ACKed.
// seqno is the first byte of the new packet.
void XPassSink::receivePacket(Packet& pkt) {
  pkt.clear_path();
  XPassPacket *p = (XPassPacket*)(&pkt);
  XPassPacket::seq_t seqno = p->seqno();
  XPassPacket::seq_t pacer_no = p->pacerno();
  simtime_picosec ts = p->ts();
  bool last_packet = ((XPassPacket*)&pkt)->last_packet();
  bool ack_req = p->ackreq();
  if(!p->tentative() && !p->unsched()){
    _tot_recvd_pkts++;
    _recvd_in_quantum++;
    _inflight_estimate = max(_inflight_estimate-1, (uint64_t)0);
  }
  //for TP sampling
  _recvd_data += p->size()-64;
  _tot_recvd_data += p->size()-64;
  if(_tp_sampling_freq > 0 && (eventlist().now() - _last_tp_sample_t > _tp_sampling_freq)) {
      cout << "TP " << flow_id() << " " << _recvd_data*8/(_tp_sampling_freq/1E3) << 
          " " << eventlist().now() << endl;  
      _recvd_data = 0;
      _last_tp_sample_t = eventlist().now();
  }
  assert(pkt.type() == XPDATA);
  assert(p->size() >= ACKSIZE);

  int size = p->size()-ACKSIZE;
  _total_hops += pkt.get_crthop();

  __global_network_tot_hops += pkt.get_crthop();
  __global_network_tot_hops_samples++;

  if(debug_flow(flow_id())){
  cout << "Sink " << _src->flow_id() << " receivePacket seqno " << seqno << " cumack " << _cumulative_ack << " credno " << pacer_no << " " << eventlist().now() << endl;
  }
  incJitter();

  if(!_is_pulling && _src->_flow_size > _src->_init_cwnd) { //first packet
    //start pulling 
    src_to_flows[_src->get_flow_src()] += 1;
    dst_to_flows[_src->get_flow_dst()] += 1;
    _is_pulling = true;
    eventlist().sourceIsPendingRel(*this, 0); 
  }

  if (last_packet) {
    // we've seen the last packet of this flow, but may not have
    // seen all the preceding packets
    //cout << "last_packet " << _src->_flow_id << " seqno " << seqno << " segsize " << size << endl;
    _last_packet_seqno = p->seqno() + size - 1;
  }
  p->free();

  updateRTT(ts);
  //no update if: data from tentative credit, different route, obsolete(reordered) data
  if(!_is_flare) {
      if(!p->tentative() && pacer_no > _last_packet_pacerno) {
          assert(pacer_no > _last_packet_pacerno);
          long distance = pacer_no - _last_packet_pacerno;
          _tot_credits += distance;
          //credit number gap is counted as lost credits
          if(distance > 0) {
              _drop_credits += distance-1;
          }
          _last_packet_pacerno = pacer_no;
      }
  }
  if(p->tentative()) {
    _tent_credits_recvd++;
  }

  _total_received+=size;

  if (seqno == _cumulative_ack+1) { // it's the next expected seq no
    _cumulative_ack = seqno + size - 1;
    if(_cumulative_ack >= _src->_init_cwnd) {
      _is_recovering = false;
    }
    // are there any additional received packets we can now ack?
    while (!_received.empty() && (_received.front().first == _cumulative_ack+1) ) {
      //cout << "xpasssink " << _src->_flow_id << " front: " << _received.front().first << " cumack+1: " << _cumulative_ack+1 << endl;
      _cumulative_ack+= _received.front().second;
      _received.pop_front();
    }
  } else if (seqno < _cumulative_ack+1) {
    //must have been a bad retransmit
  } else { // it's not the next expected sequence number
    if (_received.empty()) {
      _received.push_front({seqno,size});
      } else if (seqno > _received.back().first) { // likely case
      _received.push_back({seqno,size});
      } 
    else { // uncommon case - it fills a hole
      list<pair<uint64_t,uint64_t>>::iterator i;
      for (i = _received.begin(); i != _received.end(); i++) {
        if (seqno == i->first) break; // it's a bad retransmit
        if (seqno < i->first) {
          _received.insert(i, {seqno,size});
          break;
        }
      }
    }
  }

  if(ack_req) {
    //send ack up to requested seqno
    uint64_t to_ack = _cumulative_ack > seqno ? seqno : _cumulative_ack;
    //missing data, start pulling
    if(_cumulative_ack <= seqno && !_is_pulling) {
      //cout << "missing data start pulling\n";
      _is_recovering = true;
      eventlist().sourceIsPendingRel(*this, 0);       
    }
    send_cumack(to_ack);
  }
  //update rate based on credit loss each rtt
  if(_rtt != timeInf && eventlist().now()-_last_fb_update > _rtt) {
      if(!_is_flare) {
          feedbackControl();
      }
    _last_fb_update = eventlist().now();
  }
  //send_ack(ts, seqno, pacer_no);
  if(_tp_sampling_freq > 0){
      if(flow_id() == 0 && timeAsMs(eventlist().now()) > 4000.0) {
          _src->_finished = true;
      }
      if(flow_id() == 1 && timeAsMs(eventlist().now()) > 5000.0) {
          _src->_finished = true;
      }
      if(flow_id() == 2 && timeAsMs(eventlist().now()) > 6000.0) {
          _src->_finished = true;
      }
      if(flow_id() == 3 && timeAsMs(eventlist().now()) > 7000.0) {
          _src->_finished = true;
      }
   }
  // have we seen everything yet?
  if (_last_packet_seqno > 0 && _cumulative_ack >= _last_packet_seqno && !_src->_finished) {
    cout << "FCT " << _src->get_flow_src() << " " << _src->get_flow_dst() << " " << _src->get_flowsize() <<
      " " << timeAsMs(_src->eventlist().now() - _src->get_start_time()) << " " << timeAsMs(_src->get_start_time()) << " " << _total_hops << " " << _src->flow_id() << endl;
    _src->_finished = true;
  }
  if(last_packet && !_src->_finished) {
    //cout << "??? cumack " << _cumulative_ack << " lastseqno " << _last_packet_seqno << " flowsize " << _src->_flow_size << " flowid " << _src->_flow_id << endl;
  }
}

void XPassSink::send_cumack(uint64_t to_ack) {
    XPassCtl *p = XPassCtl::newpkt(_src->_top, _src->_flow_dst, _src->_flow_src, _src, this);
    p->set_ackno(to_ack);
    p->set_flow_id(_src->flow_id());
    p->set_path_index(_src->_path_index);
    p->set_packetid(id_gen++);
    sendToNIC(p);
}

void
XPassSink::updateRTT(simtime_picosec ts) {
  //compute rtt
  uint64_t m = eventlist().now()-ts;
  if(_rtt == timeInf) {
    _last_fb_update = eventlist().now();
    _rtt = m;
    return;
  }
  if (m!=0){
    _max_rtt = m > _max_rtt ? m : _max_rtt;
    if (_rtt>0){
      _rtt = 7*_rtt/8 + m/8;
    } else {
      _rtt = m;
    }
  }
}

static map<int,double> hops_to_prob = {{-1,1.0},{1,1.0}, {2,0.50}, {3,0.25}, {4,0.125}, {5,0.0625}};


int
XPassSink::hopJitter(int hop) {
  assert(hop > 0);
  assert(hop < _src->_top->no_of_tors());
  //jitter disabled
  if(_jit_alpha <= 0 || _jit_beta <= 0) {
    return hop;
  }
  unsigned bdp = _src->_init_cwnd;
  unsigned sent = max(_cumulative_ack, (uint64_t)Packet::data_packet_size());
  //cout << "hops " << hop << " cumack " << _cumulative_ack << " e1 " << e1 << " e2 " << e2 << " e " << e;
  if(sent <= _jit_alpha*bdp) {
    //cout << " -1 " << endl;
    return hop-1;
  } else if (sent >= _jit_beta*bdp) {
    //cout << " +1 " << endl;
    //return hop+1;
    return hop+(rand()%2);
  } else {
    //cout << " 0 " << endl;
    return hop;
  }
}

int
XPassSink::hopJitterOld(int hop) {
    assert(hop > 0);
    //return hop;
    double halfrand = drand() - 0.5;
    assert(halfrand >= -0.5 && halfrand <= 0.5);
    halfrand += _hop_jitter;
    //halfrand += crtJittering();
    int jitter_hop = round((double)hop+halfrand);
    return max(1, jitter_hop);
    //new jitter tryout vvvv
    double jitter_factor = 1.0 - 
        (max(hops_to_prob[hop],hops_to_prob[jitter_hop])
         -min(hops_to_prob[hop],hops_to_prob[jitter_hop]));
    int hops = round((double)hop+(halfrand*jitter_factor));
    if(debug_flow(flow_id())){
        cout << "hop " << hop << " hop_jitter " << _hop_jitter << " jitter_hop " << jitter_hop << " factor " << jitter_factor << " hops " << hops << endl;
    }
    return max(1, hops);
}

//increase based on total amount of transmitted data in proportion to set bdp
void
XPassSink::incJitter() {
    double pkts_in_bdp = _bdp/(double)Packet::data_packet_size();
    double pkts_recvd = _tot_recvd_data/(double)Packet::data_packet_size();
    pkts_recvd -= _src->_cwnd/(double)Packet::data_packet_size();
    pkts_recvd = max(pkts_recvd, 0.0);
    _hop_jitter += (pkts_recvd/pkts_in_bdp);
    _hop_jitter = min(_hop_jitter, _max_hop_jitter);
    //cout << "INCJITTER pkts_in_bdp " << pkts_in_bdp << " pkts_recvd " << pkts_recvd << " _hop_jitter " << _hop_jitter << endl; 
}

//decrease based on a flat amount for each credit sent out in proportion to set bdp
void
XPassSink::decJitter() {
    double pkts_in_bdp = _bdp/(double)Packet::data_packet_size();
    _hop_jitter -= (1.0/pkts_in_bdp);
    _hop_jitter = max(_hop_jitter, _min_hop_jitter);
    if(debug_flow(flow_id())){
        cout << "DECJITTER pkts_in_bdp " << pkts_in_bdp << " _hop_jitter " << _hop_jitter << endl; 
    }
}

void
XPassSink::feedbackControl2() {
  if(_tot_credits == 0) return;
  int hops = hopJitter(max(_crt_hops,1));
  double credit_loss = (double)_drop_credits / _tot_credits;
  double target_loss = (1.0 - (long double)_crt_rate/_max_rate) * _target_loss;
  double hops_chance = hops == -1 ? 1.0 : _src->_top->get_prob_hops(hops);
  double fb_sens = max(1-hops_chance, 0.1);
  if(credit_loss < target_loss) {
    if(_is_increasing){
      _weight = (_weight+_max_weight)/_weight_factor;
      _weight = min(_weight, _max_weight);
    }
    uint64_t new_rate = (1.0-_weight)*_crt_rate + _weight*_max_rate*(1+_target_loss);
    _crt_rate = _crt_rate*fb_sens + new_rate*(1.0-fb_sens);
    if(!_fb_sens){
        _crt_rate = new_rate;
    }
    _crt_rate = min(_crt_rate, _max_rate);
    _is_increasing = true;
  } else {
    uint64_t new_rate = _crt_rate * (1.0-credit_loss)*(1.0+target_loss);
    //new_rate = _weight*_crt_rate + (1.0-_weight)*new_rate;
    _crt_rate = _crt_rate*(1.0-fb_sens) + new_rate*fb_sens;
    if(!_fb_sens){
        _crt_rate = new_rate;
    }
    _crt_rate = max(_crt_rate, _min_rate);
    _weight = max(_weight/_weight_factor, _min_weight);
    _is_increasing = false;
  }
  //cout << "Flow " << flow_id() << " loss " << credit_loss << " sent " << _tot_credits << " tot_lost " << _drop_credits << " rate " << _crt_rate/1E9 <<  " hops " << _crt_hops << " t " << eventlist().now() << " rtt " << _rtt << endl;
  _tot_recvd_pkts = 0;
  _bw_sent_creds = 0;
  _tot_credits = 0;
  _drop_credits = 0;
  _tent_credits = 0;
  _tent_credits_recvd = 0;
}

void
XPassSink::feedbackControl() {
  if(_tot_credits <= 0) {
    return;
  }
  double credit_loss = (double)_drop_credits / _tot_credits;
  double target_loss = (1.0 - (long double)_crt_rate/_max_rate) * _target_loss;
  assert(_drop_credits <= _tot_credits);
  if(credit_loss < target_loss) {
    if(_is_increasing){
      _weight = (_weight+_max_weight)/2.0;
      _weight = min(_weight, _max_weight);
    }
    _crt_rate = (1.0-_weight)*_crt_rate + _weight*_max_rate*(1+_target_loss);
    _crt_rate = min(_crt_rate, _max_rate);
    _is_increasing = true;
  } else {
    _crt_rate = _crt_rate * (1.0-credit_loss)*(1.0+target_loss);
    _crt_rate = max(_crt_rate, _min_rate);
    _weight = max(_weight/2.0, _min_weight);
    _is_increasing = false;
  }
  _tot_credits = 0;
  _drop_credits = 0;
  _tent_credits = 0;
  _tent_credits_recvd = 0;
  if(debug_flow(flow_id())) {
  cout << "Feedback " << flow_id() << " loss " << credit_loss << " rate " << _crt_rate/1E9 <<  " hops " << _crt_hops << " t " << eventlist().now() << " rtt " << _rtt << endl;
  }
}

simtime_picosec
XPassSink::nextCreditWait() {
    simtime_picosec spacing;
    if(_is_flare) {
        //if it's flare, always pace at maximum rate
        spacing = (simtime_picosec)((Packet::data_packet_size()+64) 
                * (pow(10.0,12.0) * 8) / _max_rate);
    } else {
        spacing = (simtime_picosec)((Packet::data_packet_size()+64) 
                * (pow(10.0,12.0) * 8) / _crt_rate);
    }
    //cout << "flow " << _src->flow_id() << " spacing " << spacing << " crt_rate " << _crt_rate << endl;
    //random jittering between packets to account for HW
    long jitter = 8000 - rand()%16000; //picoseconds
    //cout << "spacing " << spacing << " jitter " << jitter << " tot " << spacing+jitter << endl;
    return spacing+jitter;
}

void
XPassSink::doNextEvent() {
  int slice = _src->_top->time_to_slice(eventlist().now());
#ifdef SLICE_RESET
  if(_is_flare && slice != _crt_slice) {
    _crt_slice = slice;
    DynExpTopology *top = _src->_top;
    int srcToR = _src->_flow_src/top->no_of_hpr();
    int dstToR = _src->_flow_dst/top->no_of_hpr();
    int hops = 1;
    if(srcToR != dstToR) {
        int npaths = top->get_no_paths(srcToR, dstToR, slice);
        //what path actually
        int path_index = fast_rand() % npaths;
        hops = top->get_no_hops(srcToR, dstToR, slice, path_index);
    }
    if(_crt_hops == -1) {
      _crt_hops = hops;
    } else if(_crt_hops != hops) {
        //if route changed, reset
        if(hops < _crt_hops) {
            uint64_t prev_rate = _crt_rate;
            double next_hops_chance = hops == -1 ? 1.0 : _src->_top->get_prob_hops(hops);
            double crt_hops_chance = hops == -1 ? 1.0 : _src->_top->get_prob_hops(_crt_hops);
            double chance_diff = next_hops_chance-crt_hops_chance;
            uint64_t inc = _max_rate - ((_crt_rate+_max_rate)/2);
            _crt_rate += inc*chance_diff;
            if(debug_flow(flow_id())) {
                cout << "SLICE_RESET prev " << _crt_hops << " " << prev_rate << " crt " << hops << " " << _crt_rate << endl;
            }
        }
        _crt_slice_route = slice;
        _crt_hops = hops;
        _rtt = max_rtt_picosec();
        _last_fb_update = eventlist().now();
        _bw_sent_creds = 0;
        _tot_recvd_pkts = 0;
        _recvd_in_quantum = 0;
        _inflight_estimate = 0;
        _crt_quantum = 0;
        _bw_sent_in_quantum[0] = 0;
        _tot_credits = 0;
        _drop_credits = 0;
        _tot_sent_creds = 0;
        _tot_sent_creds_slice = 0;
    }
  }
#endif
  XPassPull *p = XPassPull::newpkt(_src->_top, _src->_flow_dst, _src->_flow_src, _src, this);
  p->set_ackno(p->get_xpsink()->cumulative_ack());
  p->set_flow_id(_src->flow_id());
  p->set_path_index(_src->_path_index);
  p->set_packetid(id_gen++);
  if(_is_flare){
      double rate = min((long double)1.0, (long double)_crt_rate/_max_rate);
      if(drand() <= rate) {
          p->set_tentative(false);
          p->set_pacerno(_credit_counter++);
          __global_network_tot_valid_creds++;
          _last_valid_cred_t = eventlist().now();
          _bw_sent_creds++; 
          _tent_since_last_bw = 0;
          _inflight_estimate++;
          _bw_sent_in_quantum[_crt_quantum]++;
      } else {
          p->set_tentative(true);
          p->set_pacerno(0);
          _tent_since_last_bw++;
          _tent_credits++;
      }
      _tot_sent_creds++;
      _tot_sent_creds_slice++;
      _tot_sent_in_quantum++;
  } else {
      p->set_tentative(false);
      p->set_pacerno(_credit_counter++);
      __global_network_tot_valid_creds++;
  }
  sendToNIC(p);
  decJitter();
  if(!_src->_finished) {
    eventlist().sourceIsPendingRel(*this, nextCreditWait());
  }
  //reset credit counters after 2RTT worth of credits
  //then do feedbackControl2
  if(_is_flare && _tot_sent_creds_slice > crt_rtt_pkts()*2) {
    if(_tot_sent_creds > crt_rtt_pkts()) {
      //cout << _tot_sent_creds << " " << _tot_recvd_pkts << endl;
      _tot_credits = _bw_sent_creds;
      _drop_credits = _bw_sent_creds > _tot_recvd_pkts ?
        _bw_sent_creds-_tot_recvd_pkts : 0;
      feedbackControl2();
      _tot_sent_creds = 0;
      _tot_recvd_pkts = 0;
      _bw_sent_creds = 0;
    }
  }
}

Queue* 
XPassSink::sendToNIC(Packet* pkt) {
  DynExpTopology* top = pkt->get_topology();
  int slice = top->time_to_slice(eventlist().now());

  //set routing info to allow credit shaping at NIC
  if(top->get_firstToR(pkt->get_src()) == top->get_firstToR(pkt->get_dst())) {
    pkt->set_maxhops(0);
    pkt->set_crthop(0);
  } else {
  pkt->set_src_ToR(top->get_firstToR(pkt->get_src()));
  pkt->set_crthop(0);
  int npaths = top->get_no_paths(pkt->get_src_ToR(),
                                 top->get_firstToR(pkt->get_dst()), slice);
  int path_index = fast_rand() % npaths;
  pkt->set_maxhops(top->get_no_hops(pkt->get_src_ToR(),
                                    top->get_firstToR(pkt->get_dst()), slice, path_index));
  }
  pkt->set_tidalhop(max(hopJitter(max(pkt->get_maxhops(),1)),1));

  //get NIC queue for this node
  Queue* nic = top->get_queue_serv_tor(pkt->get_src()); // returns pointer to nic queue
  assert(nic);
  nic->receivePacket(*pkt); // send this packet to the nic queue
  return nic; // return pointer so NDP source can update send time
}

////////////////////////////////////////////////////////////////
//  XPASS RETRANSMISSION TIMER
////////////////////////////////////////////////////////////////

XPassRtxTimerScanner::XPassRtxTimerScanner(simtime_picosec scanPeriod, EventList& eventlist)
  : EventSource(eventlist,"RtxScanner"), 
  _scanPeriod(scanPeriod)
{
  eventlist.sourceIsPendingRel(*this, 0);
}

void 
XPassRtxTimerScanner::registerXPass(XPassSrc &tcpsrc)
{
  _tcps.push_back(&tcpsrc);
}

void
XPassRtxTimerScanner::doNextEvent() 
{
  simtime_picosec now = eventlist().now();
  tcps_t::iterator i;
  for (i = _tcps.begin(); i!=_tcps.end(); i++) {
    (*i)->rtx_timer_hook(now,_scanPeriod);
  }
  eventlist().sourceIsPendingRel(*this, _scanPeriod);
}


