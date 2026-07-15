// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#include "creditqueue.h"
#include "config.h"
#include "datacenter/dynexp_topology.h"
#include "network.h"
#include "tcp.h"
#include "tcppacket.h"
#include "xpass.h"
#include "xpasspacket.h"
#include "pipe.h"
#include <cmath>
#include <iostream>
#include <math.h>


static bool debug_flow(uint64_t flow_id) {
  return false;
  if (flow_id == 30262)
    return true;
  if (flow_id == 12933)
    return true;
  if (flow_id == 14265)
    return true;
  if (flow_id == 24374)
    return true;
  return false;
}
static bool debug_q(int tor, int port) { return false; }

#define NO_PENDING_TX (simtime_picosec)(-1); // unsigned so max unit

#define SYMM_ROUTING
#define SELECTIVE_DROP
#define PROB_REMAINING
#define CRED_Q_N 1

CreditQueue::CreditQueue(linkspeed_bps bitrate, mem_b maxsize,
                         EventList &eventlist, QueueLogger *logger, int tor,
                         int port, DynExpTopology *top, mem_b credsize,
                         mem_b shaping_thresh, mem_b aeolus_thresh, mem_b tent_thresh)
    : Queue(bitrate, maxsize, eventlist, logger, tor, port, top) {
  _maxsize_cred = credsize;
  _shaping_thresh = shaping_thresh;
  _max_tent_cred = tent_thresh;
  _max_avail_cred = 2;
  _maxsize_unsched = aeolus_thresh;
  //cout << _maxsize_cred/64 << " " << _shaping_thresh/64 << " " << _maxsize_unsched/1500 << " " << _max_tent_cred/64 << endl;
  _avail_cred = 1;
  _queuesize_cred.resize(CRED_Q_N);
  std::fill(_queuesize_cred.begin(), _queuesize_cred.end(), 0);
  _enqueued_cred.resize(CRED_Q_N);
  _last_cred_t = 0;
  _last_cred_tx_t = 0;
  _tot_creds = 0;
  _tx_creds = 0;
  _drop_creds = 0;
  _drop_overflow = 0;
  _drop_timeout = 0;
  _drop_shaping = 0;
  _drop_tentative = 0;
  _max_cred_queue = 0;
  _next_sched_tx = NO_PENDING_TX;
  _tx_next = NONE;
  _cred_tx_pending = false;
  _data_size = 1575;
  _cred_timeout = 100 * _data_size * _ps_per_byte;
  _next_prio = -1;
}

simtime_picosec CreditQueue::cred_tx_delta() {
  return eventlist().now() - _last_cred_t;
}

// for future self: if there is a weird bug with pacing, it may be here :) cause
// double events
bool CreditQueue::credit_ready() {
  updateAvailCredit();
  // there is leftover credit available, can transmit
  if (_avail_cred > 0) {
    _avail_cred--;
    return true;
  } else {
    return false;
  }
}

void CreditQueue::scheduleCredit() {
  simtime_picosec spacing = _ps_per_byte * _data_size;
  if (!_cred_tx_pending) {
    assert(spacing > cred_tx_delta());
    eventlist().sourceIsPendingRel(*this, spacing - cred_tx_delta());
    _cred_tx_pending = true;
  }
}

void CreditQueue::updateAvailCredit() {
  simtime_picosec spacing = _ps_per_byte * _data_size;
  int new_cred = cred_tx_delta() / spacing;
  _avail_cred += new_cred;
  _avail_cred = min(_avail_cred, _max_avail_cred);
  // cout << nodename() << " updateAvailCredit new_cred " << new_cred << " avail
  // " << _avail_cred << " elapsed " << eventlist().now()-_last_cred_t << endl;
  _last_cred_t += new_cred * spacing;
}

// extract priority information from credit packet
inline int CreditQueue::credit_prio(Packet &pkt) {
  assert(pkt.type() == XPCREDIT);
  XPassPull *p = (XPassPull *)&pkt;
  if (p->prio()) {
    return 0;
  } else {
    return CRED_Q_N - 1;
  }
}

// check for next possible credit to send and drops credits that timed out
// returns priority of next available credit queue to send out from
// returns -1 if no credit to send out
inline int CreditQueue::next_cred() {
  for (int i = 0; i < _queuesize_cred.size(); i++) {
    if (_queuesize_cred[i] > 0) {
      assert(_enqueued_cred[i].size() > 0);
      // look for not expired packet
      while (!_enqueued_cred[i].empty()) {
        Packet *p = _enqueued_cred[i].back();
        // if not expired, return prio queue idx
        if (eventlist().now() + drainTime(p) <= p->get_tmp_ts()) {
          return i;
        }
        // else packet expired, drop and try next
        assert(_queuesize_cred[i] > 0);
        _queuesize_cred[i] -= p->size();
        _hops_to_creds[max((p->get_maxhops() - p->get_crthop()), 1)] -= 1;
        _drop_creds++;
        _drop_timeout++;
        p->free();
        _enqueued_cred[i].pop_back();
      }
      // cout << "next cred prio " << i << endl;
    }
  }
  return -1;
}

mem_b CreditQueue::queuesize_cred(int prio) {
  // cout << "queuesize_cred prio " << prio << endl;
  mem_b size = 0;
  for (int i = 0; i <= prio && i < _queuesize_cred.size(); i++) {
    size += _queuesize_cred[i];
  }
  return size;
}

mem_b CreditQueue::queuesize_cred() { return queuesize_cred(CRED_Q_N - 1); }

static double hops_to_chance(int hops) {
  double from = 0.9;
  if (hops >= 5)
    return from / 16;
  else if (hops == 4)
    return from / 8;
  else if (hops == 3)
    return from / 4;
  else if (hops == 2)
    return from / 2;
  else if (hops == 1)
    return from;
  else
    return 1;
}
static double hops_to_chance_exp(int hops) {
  double chance = 1.0;
  for (int i = 1; i <= 5; i++) {
    if (hops == i)
      break;
    chance /= 2;
  }
  return chance;
}

bool CreditQueue::handleCredit(Packet &pkt) {
  assert(pkt.type() == XPCREDIT);
  int prio = credit_prio(pkt);
  if (queuesize_cred(prio) > _max_tent_cred &&
      ((XPassPull *)&pkt)->tentative()) {
    // cout << nodename() << " TENTATIVE DROPPED for " << pkt.flow_id() << endl;
    _drop_creds++;
    _drop_tentative++;
    pkt.free();
    return false;
  }
  if(((XPassPull*)&pkt)->get_xpsrc()->_is_flare) {
      if (queuesize_cred(prio) > _shaping_thresh) {
          int remaining_hops = pkt.get_tidalhop();
          //more hops, less chance
          double remaining_hops_chance = _top->get_prob_hops(remaining_hops);
          //cout << "remaining " << remaining_hops << " chance " << remaining_hops_chance << endl;
          // cout << "crt_hop " << pkt.get_crthop() << " max_hops " <<
          // pkt.get_maxhops() << " remaining " << remaining_hops << endl;
          double drop_chance = 1.0;
          drop_chance -= remaining_hops_chance;
          // cout << "flow " << pkt.flow_id() << " drop chance " << drop_chance << "
          // remaining " << remaining_hops_chance << " hops " << remaining_hops <<
          // endl;
          assert(drop_chance >= 0);
          double res = drand();
          if (res <= drop_chance) {
              //cout << nodename() << " CREDIT DROPPED (chance) for " << pkt.flow_id() << endl; cout << "dropping packet with " << remaining_hops << " remaining hops\n";
              __global_network_tot_cred_waste += pkt.get_crthop();
              pkt.free();
              _drop_creds++;
              _drop_shaping++;
              return false;
          }
      }
  }
  // credit timeout is set to expected max queueing delay if queue was FIFO
  pkt.set_tmp_ts(eventlist().now() + _cred_timeout);
  _enqueued_cred[prio].push_front(&pkt);
  _queuesize_cred[prio] += pkt.size();
  _max_cred_queue = max(_max_cred_queue, queuesize_cred());
  _hops_to_creds[max((pkt.get_maxhops() - pkt.get_crthop()), 1)] += 1;
  // measure in term of data packet size
  pkt.inc_queueing(_enqueued_cred[prio].size() * 1500);
  return true;
}

void CreditQueue::receivePacket(Packet &pkt) {
  updateAvailCredit();
  bool queueWasEmpty = _enqueued.empty() && queuesize_cred() == 0;
  /*
  cout << nodename() << " receivePacket " << pkt.size() << " " << pkt.flow_id()
  << " " << _queuesize << " " << pkt.get_slice_sent() << " " <<
  _top->time_to_slice(eventlist().now()) << " hop " << pkt.get_crthop() << " t "
  << eventlist().now() << endl;
  */
  if (pkt.type() == XPCREDIT) {
    int prio = credit_prio(pkt);
    _tot_creds++;
    // cout << "xpcredit\n";
    if (queuesize_cred(prio) + pkt.size() > _maxsize_cred) {
      // if the credit doesn't fit in the queue, drop it
      // cout << nodename() << " CREDIT DROPPED (overflow) for " <<
      // pkt.flow_id() << endl;
      __global_network_tot_cred_waste += pkt.get_crthop();
      pkt.free();
      _drop_creds++;
      _drop_overflow++;
      return;
    }
    if (!handleCredit(pkt)) return;
  } else {
    // cout << "xpdata\n";
    if (_queuesize + pkt.size() > _maxsize) {
      /* if the packet doesn't fit in the queue, drop it */
      if (pkt.type() == TCP) {
        TcpPacket *tcppkt = (TcpPacket *)&pkt;
        tcppkt->get_tcpsrc()->add_to_dropped(tcppkt->seqno());
      }
      if (pkt.type() == XPCTL) {
        cout << "!!! " << nodename() << " DROPPED XPCTL" << endl;
      }
      cout << nodename() << " DROPPED " << _queuesize << " " << pkt.size()
           << " " << _top->time_to_slice(eventlist().now()) << endl;
      if(pkt.type() == XPDATA) {
          XPassPacket *xppkt = (XPassPacket*)&pkt;
          xppkt->get_xpsrc()->setFinished();
      }
      pkt.free();
      _num_drops++;
      return;
    }
    // drop unscheduled packets early to prevent queue overflow
    if (pkt.type() == XPDATA && ((XPassPacket *)&pkt)->unsched() &&
        _queuesize + pkt.size() > _maxsize_unsched) {
      // cout << nodename() << " DROPPED UNSCHED " << _queuesize << " " <<
      // _top->time_to_slice(eventlist().now()) << endl;
      pkt.free();
      _num_drops++;
      return;
    }
    /* enqueue the packet */
    if (queuesize() > _max_recorded_size) {
      _max_recorded_size = queuesize();
    }
    _enqueued.push_front(&pkt);
    pkt.inc_queueing(_queuesize);
    _queuesize += pkt.size();
    pkt.set_last_queueing(_queuesize);
    updatePktIn(pkt.flow_id());
    // cout << "enqueued xpdata\n";
  }

  if (queueWasEmpty) {
    /* schedule the dequeue event */
    if (pkt.type() == XPCREDIT) {
      int prio = next_cred();
      assert(_enqueued_cred[prio].size() == 1 && _enqueued.size() == 0);
    } else {
      assert(_enqueued.size() == 1 && queuesize_cred() == 0);
    }
    beginService();
  }
}

void CreditQueue::beginService() {
  /* schedule the next dequeue event */
  // cout << "data " << _enqueued.size() << " cred " << _enqueued_cred.size() <<
  // endl;
  assert(!(_enqueued.empty() && queuesize_cred() == 0));
  assert(_tx_next == NONE);
  // find next credit prio q idx if available, else -1
  int prio = next_cred();
  // if credit clock is ready and credits are enqueued, send credit
  if (credit_ready() && prio >= 0) {
    _cred_tx_pending = false;
    eventlist().sourceIsPendingRel(*this,
                                   drainTime(_enqueued_cred[prio].back()));
    _next_sched_tx = eventlist().now() + drainTime(_enqueued_cred[prio].back());
    _tx_next = CRED;
    _next_prio = prio;
  } else if (!_enqueued.empty()) {
    eventlist().sourceIsPendingRel(*this, drainTime(_enqueued.back()));
    _next_sched_tx = eventlist().now() + drainTime(_enqueued.back());
    _tx_next = DATA;
    // if nothing to send out, try to schedule a credit to send
  } else if (queuesize_cred() > 0 && prio != -1) {
    _cred_tx_pending = false;
    _next_prio = prio;
    scheduleCredit();
  }
}

void CreditQueue::completeService() {
  /* dequeue the packet */
  Packet *pkt = NULL;
  if (_tx_next == CRED) {
    int prio = _next_prio;
    assert(_next_prio >= 0 && _next_prio < CRED_Q_N);
    // cout << "creditq completeService\n";
    assert(queuesize_cred(prio) > 0);
    pkt = _enqueued_cred[prio].back();
    _enqueued_cred[prio].pop_back();
    updatePktOut(pkt->flow_id());
    _queuesize_cred[prio] -= pkt->size();
    _tx_creds++;
    _hops_to_creds[max((pkt->get_maxhops() - pkt->get_crthop()), 1)] -= 1;
    assert(_hops_to_creds[pkt->get_tidalhop()] >= 0);
    _last_cred_tx_t = eventlist().now();
  } else {
    assert(!_enqueued.empty());
    pkt = _enqueued.back();
    _enqueued.pop_back();
    updatePktOut(pkt->flow_id());
    _queuesize -= pkt->size();
  }
  assert(pkt != NULL);
  /* tell the packet to move on to the next pipe */
  sendFromQueue(pkt);
  _next_sched_tx = NO_PENDING_TX;
  _tx_next = NONE;
  /* schedule the next dequeue event */
  if (!(_enqueued.empty() && queuesize_cred() == 0)) {
    beginService();
  }
}

void CreditQueue::doNextEvent() {
  if (eventlist().now() == _next_sched_tx) {
    // tx event
    completeService();
  } else if (_cred_tx_pending) {
    // credit queue timer event
    assert(queuesize_cred(_next_prio) > 0);
    if (_tx_next == NONE) {
      beginService();
    }
  }
}

void CreditQueue::reportLoss() {
  cout << " " << _tot_creds << " " << _drop_creds;
}

void CreditQueue::reportCreditStats(const string& scope, int id, int port) {
  cout << "CreditStats " << scope << " " << id << " " << port << " "
       << _tot_creds << " " << _tx_creds << " " << queuesize_cred() / 64
       << " " << _max_cred_queue / 64 << " " << _drop_creds << " "
       << _drop_overflow << " " << _drop_timeout << " " << _drop_shaping
       << " " << _drop_tentative << endl;
}

void CreditQueue::reportMaxqueuesize() {
  simtime_picosec crt_t = eventlist().now();
  cout << "Queue " << _tor << " " << _port << " " << _max_recorded_size << " " << queuesize() << " " << _queuesize_cred[0]/64.0 << " ";
  reportLoss();
  map<int, uint64_t>::iterator it;
  cout << " DISTR ";
  for (it = _hops_to_creds.begin(); it != _hops_to_creds.end(); ++it) {
    int hops = it->first;
    uint64_t creds = it->second;
    if (creds > 0) {
      cout << hops << ":" << (double)creds / (_queuesize_cred[0] / 64.0) << " ";
    }
  }
  cout << endl;
  _max_recorded_size = 0;
  _flows_at_queues.clear();
  _prev_txbytes = _txbytes;
  _prev_sample_t = crt_t;
}

NICCreditQueue::NICCreditQueue(linkspeed_bps bitrate, mem_b maxsize,
                               EventList &eventlist, QueueLogger *logger,
                               DynExpTopology *top, mem_b credsize,
                               mem_b shaping_thresh, mem_b aeolus_thresh, mem_b tent_thresh)
    : CreditQueue(bitrate, maxsize, eventlist, logger, 0, 0, top, credsize,
                  shaping_thresh, aeolus_thresh, tent_thresh) {}

void NICCreditQueue::completeService() {
  // cout << nodename() << " completeService " << eventlist().now() << endl;
  /* dequeue the packet */
  Packet *pkt = NULL;
  if (_tx_next == CRED) {
    int prio = _next_prio;
    assert(_next_prio >= 0);
    // cout << "creditq completeService\n";
    assert(queuesize_cred(prio) > 0);
    pkt = _enqueued_cred[prio].back();
    _enqueued_cred[prio].pop_back();
    updatePktOut(pkt->flow_id());
    _queuesize_cred[prio] -= pkt->size();
    _tx_creds++;
    _hops_to_creds[max((pkt->get_maxhops() - pkt->get_crthop()), 1)] -= 1;
    _last_cred_tx_t = eventlist().now();
    updatePktOut(pkt->flow_id());
    // cout << nodename() << " completeService credit " << eventlist().now() <<
    // " dist " << eventlist().now()-_last_cred_tx_t << endl;
    _last_cred_tx_t = eventlist().now();
  } else {
    assert(!_enqueued.empty());
    pkt = _enqueued.back();
    _enqueued.pop_back();
    updatePktOut(pkt->flow_id());
    _queuesize -= pkt->size();
  }
  assert(pkt != NULL);
  /* tell the packet to move on to the next pipe */
  pkt->set_fabricts(eventlist().now());
  pkt->set_src_ToR(
      _top->get_firstToR(pkt->get_src())); // set the sending ToR. This is used
                                           // for subsequent routing
  if (pkt->get_src_ToR() == _top->get_firstToR(pkt->get_dst())) {
    // the packet is being sent within the same rack
    pkt->set_lasthop(false);
    pkt->set_crthop(-1);
    pkt->set_crtToR(-1);
    pkt->set_maxhops(0); // want to select a downlink port immediately
    pkt->set_tidalhop(pkt->get_xpsink()->hopJitter(1));
  } else {
    // the packet is being sent between racks
    // we will choose the path based on the current slice
    int slice;
#ifdef SYMM_ROUTING
    if (pkt->type() == XPDATA && ((XPassPacket*)pkt)->get_xpsrc()->_is_flare) {
      slice = ((XPassPacket *)pkt)->credit_ts();
      // data sent without credit has slice set to -1
      if (slice == -1) {
        slice = _top->time_to_slice(eventlist().now());
      }
    } else {
      slice = _top->time_to_slice(eventlist().now());
    }
#else
    slice = _top->time_to_slice(eventlist().now());
#endif
    // get the number of available paths for this packet during this slice
    int npaths = _top->get_no_paths(pkt->get_src_ToR(),
                                    _top->get_firstToR(pkt->get_dst()), slice);

    if (npaths == 0)
      cout << "Error: there were no paths for slice " << slice << " src "
           << pkt->get_src_ToR() << " dst "
           << _top->get_firstToR(pkt->get_dst()) << endl;
    assert(npaths > 0);

    // randomly choose a path for the packet
    // !!! todo: add other options like permutation, etc...
    int path_index = fast_rand() % npaths;
    // cout << "path_index " << path_index << endl;

    pkt->set_slice_sent(slice); // "timestamp" the packet
    pkt->set_fabricts(eventlist().now());
    pkt->set_path_index(path_index); // set which path the packet will take

    // set some initial packet parameters used for label switching
    // *this could also be done in NDP before the packet is sent to the NIC
    pkt->set_lasthop(false);
    pkt->set_crthop(-1);
    pkt->set_crtToR(-1);
    pkt->set_maxhops(_top->get_no_hops(pkt->get_src_ToR(),
                                       _top->get_firstToR(pkt->get_dst()),
                                       slice, path_index));
    pkt->set_tidalhop(pkt->get_xpsink()->hopJitter(pkt->get_maxhops()));
    // cout << "HOPS flow " << pkt->flow_id() << " " <<
    // _top->get_no_hops(pkt->get_src_ToR(), _top->get_firstToR(pkt->get_dst()),
    // slice, path_index) << endl;
  }

  /* tell the packet to move on to the next pipe */
  sendFromQueue(pkt);
  _next_sched_tx = NO_PENDING_TX;
  _tx_next = NONE;
  /* schedule the next dequeue event */
  if (!(_enqueued.empty() && queuesize_cred() == 0)) {
    beginService();
  }
}
