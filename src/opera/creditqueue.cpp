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
#include <algorithm>
#include <cmath>
#include <iostream>
#include <math.h>
#include <map>
#include <vector>


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

static map<uint32_t, FlowCreditCounters> flow_credit_counters;

enum CreditDropReason {
  CREDIT_DROP_TENTATIVE = 0,
  CREDIT_DROP_SHAPING = 1,
  CREDIT_DROP_OVERFLOW = 2,
  CREDIT_DROP_TIMEOUT = 3,
  CREDIT_DROP_PUSHOUT = 4,
  CREDIT_DROP_TOPOLOGY_CLIP = 5,
  CREDIT_DROP_WRONG_DST = 6,
  CREDIT_DROP_REASON_N = 7
};

enum CreditLifecycleStage {
  CREDIT_GENERATED = 0,
  CREDIT_ADMITTED = 1,
  CREDIT_SENT = 2,
  CREDIT_DELIVERED = 3,
  CREDIT_STAGE_N = 4
};

struct CreditLifecycleCounters {
  uint64_t stage[CREDIT_STAGE_N] = {0, 0, 0, 0};
  uint64_t endpoint_dropped = 0;
  uint64_t path_dropped = 0;
  uint64_t endpoint_reason[CREDIT_DROP_REASON_N] = {0};
  uint64_t path_reason[CREDIT_DROP_REASON_N] = {0};
  uint64_t network_hops = 0;
};

static CreditLifecycleCounters credit_lifecycle_counters[2][3];

static int rawCreditClass(Packet& pkt) {
  int hops = max(pkt.get_maxhops(), 0);
  if (hops <= 1) return 0;
  if (hops == 2) return 1;
  return 2;
}

static int rawCreditType(Packet& pkt) {
  assert(pkt.type() == XPCREDIT);
  return ((XPassPull*)&pkt)->tentative() ? 1 : 0;
}

static CreditLifecycleCounters& lifecycleCounters(Packet& pkt) {
  return credit_lifecycle_counters[rawCreditType(pkt)][rawCreditClass(pkt)];
}

static void recordCreditStage(Packet& pkt, CreditLifecycleStage stage) {
  lifecycleCounters(pkt).stage[stage]++;
}

static void recordCreditLifecycleDrop(Packet& pkt, bool endpoint,
                                      CreditDropReason reason) {
  CreditLifecycleCounters& counters = lifecycleCounters(pkt);
  if (endpoint) {
    counters.endpoint_dropped++;
    counters.endpoint_reason[reason]++;
  } else {
    counters.path_dropped++;
    counters.path_reason[reason]++;
  }
}

struct CreditHopCounters {
  uint64_t generated = 0;
  uint64_t admitted = 0;
  uint64_t delivered = 0;
  uint64_t endpoint_dropped = 0;
  uint64_t path_dropped = 0;
  uint64_t delivered_actual_hops = 0;
  uint64_t delivered_hop_mismatches = 0;
};

static map<pair<uint32_t, bool>, CreditHopCounters> credit_hop_counters;

static CreditHopCounters& creditHopCounters(Packet& pkt) {
  assert(pkt.type() == XPCREDIT);
  uint32_t path_hops = max(pkt.get_maxhops(), 0);
  bool tentative = ((XPassPull*)&pkt)->tentative();
  return credit_hop_counters[make_pair(path_hops, tentative)];
}

static FlowCreditCounters& flowCreditCounters(Packet& pkt) {
  FlowCreditCounters& counters = flow_credit_counters[pkt.flow_id()];
  counters.sender = pkt.get_dst();
  counters.receiver = pkt.get_src();
  counters.path_hops = max(pkt.get_maxhops(), 0);
  return counters;
}

static void recordFlowCreditAdmission(Packet& pkt) {
  FlowCreditCounters& counters = flowCreditCounters(pkt);
  uint32_t path_hops = max(pkt.get_maxhops(), 0);
  if (counters.admitted == 0) counters.admitted_path_hops_min = path_hops;
  counters.admitted++;
  counters.admitted_path_hops_min =
      min(counters.admitted_path_hops_min, path_hops);
  counters.admitted_path_hops_max =
      max(counters.admitted_path_hops_max, path_hops);
  counters.admitted_path_hops_sum += path_hops;
  creditHopCounters(pkt).admitted++;
}

static void recordFlowCreditDrop(Packet& pkt,
                                 uint64_t FlowCreditCounters::*reason,
                                 bool endpoint_drop) {
  FlowCreditCounters& counters = flowCreditCounters(pkt);
  counters.dropped++;
  counters.*reason += 1;
  if (endpoint_drop) {
    counters.endpoint_dropped++;
    creditHopCounters(pkt).endpoint_dropped++;
  } else {
    counters.path_dropped++;
    creditHopCounters(pkt).path_dropped++;
  }
  counters.waste_hops += max(pkt.get_crthop(), 0);
}

void recordFlowCreditDelivery(Packet& pkt) {
  FlowCreditCounters& counters = flowCreditCounters(pkt);
  uint32_t path_hops = max(pkt.get_maxhops(), 0);
  uint32_t actual_hops = max(pkt.get_crthop(), 0);
  if (counters.delivered == 0) counters.delivered_path_hops_min = path_hops;
  counters.delivered++;
  counters.delivered_path_hops_min =
      min(counters.delivered_path_hops_min, path_hops);
  counters.delivered_path_hops_max =
      max(counters.delivered_path_hops_max, path_hops);
  counters.delivered_path_hops_sum += path_hops;
  counters.delivered_actual_hops_sum += actual_hops;
  if (actual_hops != path_hops) counters.delivered_hop_mismatches++;
  CreditHopCounters& hop_counters = creditHopCounters(pkt);
  hop_counters.delivered++;
  hop_counters.delivered_actual_hops += actual_hops;
  if (actual_hops != path_hops) hop_counters.delivered_hop_mismatches++;
  recordCreditStage(pkt, CREDIT_DELIVERED);
}

void recordFlowCreditTopologyDrop(Packet& pkt, uint32_t consumed_hops,
                                  bool wrong_destination) {
  FlowCreditCounters& counters = flowCreditCounters(pkt);
  counters.dropped++;
  counters.path_dropped++;
  counters.topology++;
  counters.waste_hops += consumed_hops;
  creditHopCounters(pkt).path_dropped++;
  __global_network_tot_cred_waste += consumed_hops;
  recordCreditLifecycleDrop(
      pkt, false,
      wrong_destination ? CREDIT_DROP_WRONG_DST : CREDIT_DROP_TOPOLOGY_CLIP);
}

void recordCreditNetworkHop(Packet& pkt) {
  if (pkt.type() != XPCREDIT) return;
  lifecycleCounters(pkt).network_hops++;
}

#define NO_PENDING_TX (simtime_picosec)(-1); // unsigned so max unit

#define SYMM_ROUTING
#define SELECTIVE_DROP
#define PROB_REMAINING
#define CRED_Q_N 3

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
  _shaping_checks = 0;
  _shaping_admitted = 0;
  _max_cred_queue = 0;
  _next_sched_tx = NO_PENDING_TX;
  _tx_next = NONE;
  _cred_tx_pending = false;
  _credit_in_service = NULL;
  _data_size = 1575;
  _cred_timeout = 100 * _data_size * _ps_per_byte;
  _next_prio = -1;
  _is_nic = false;
  _rx_hop_prio = false;
  _rx_global_tentative = false;
  _rx_credit_pushout = false;
  _rx_credit_slot_trace = false;
  _priority_seed = 13;
  _host_id = -1;
  _last_priority_slice = -1;
  _credit_enqueue_sequence = 0;
  _credit_weights = {4, 2, 1};
  _wrr_schedule_position = 0;
  _selected_schedule_position = -1;
  _selected_schedule_target = -1;
  _selected_fallback = false;
  _priority_arrivals.assign(CRED_Q_N, 0);
  _priority_transmissions.assign(CRED_Q_N, 0);
  _priority_drops.assign(CRED_Q_N, 0);
  _priority_pushouts.assign(CRED_Q_N, 0);
  _priority_queued_bytes.assign(CRED_Q_N, 0);
  _priority_max_queued.assign(CRED_Q_N, 0);
  buildCreditSchedule();
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

bool CreditQueue::receiverPriorityEnabled() const {
  return _is_nic && _rx_hop_prio;
}

// Class is independent from the physical queue so admission-only mode can
// retain one global FIFO while still accounting by hop class.
inline int CreditQueue::creditClass(Packet &pkt) {
  assert(pkt.type() == XPCREDIT);
  return rawCreditClass(pkt);
}

inline int CreditQueue::creditQueueIndex(Packet &pkt) {
  return (_is_nic && _rx_hop_prio) ? creditClass(pkt) : CRED_Q_N - 1;
}

inline int CreditQueue::creditType(Packet &pkt) const {
  return rawCreditType(pkt);
}

void CreditQueue::buildCreditSchedule() {
  _wrr_schedule.clear();
  uint64_t total = 0;
  for (int prio = 0; prio < CRED_Q_N; prio++)
    total += _credit_weights[prio];
  assert(total > 0);

  vector<int64_t> current(CRED_Q_N, 0);
  _wrr_schedule.reserve(total);
  for (uint64_t slot = 0; slot < total; slot++) {
    int selected = 0;
    for (int prio = 0; prio < CRED_Q_N; prio++) {
      current[prio] += _credit_weights[prio];
      if (current[prio] > current[selected]) selected = prio;
    }
    _wrr_schedule.push_back(selected);
    current[selected] -= total;
  }
  _wrr_schedule_position = 0;
}

int CreditQueue::independentPathIndex(Packet &pkt, int absolute_slice,
                                      int npaths) const {
  assert(npaths > 0);
  if (npaths == 1) return 0;

  uint64_t value = _priority_seed;
  value ^= ((uint64_t)pkt.flow_id() << 32) ^ pkt.id();
  value ^= (uint64_t)(absolute_slice + 1) * 0x9e3779b97f4a7c15ULL;
  value += 0x9e3779b97f4a7c15ULL;
  value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
  value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
  value ^= value >> 31;
  return value % npaths;
}

void CreditQueue::installPriorityRoute(Packet &pkt, int slice) {
  assert(_is_nic && pkt.type() == XPCREDIT);
  int src_tor = _top->get_firstToR(pkt.get_src());
  int dst_tor = _top->get_firstToR(pkt.get_dst());
  pkt.set_src_ToR(src_tor);
  pkt.set_crthop(0);
  pkt.set_slice_sent(slice);
  if (src_tor == dst_tor) {
    pkt.set_path_index(0);
    pkt.set_maxhops(0);
    return;
  }

  int npaths = _top->get_no_paths(src_tor, dst_tor, slice);
  assert(npaths > 0);
  int absolute_slice = _top->time_to_absolute_slice(eventlist().now());
  int path_index = independentPathIndex(pkt, absolute_slice, npaths);
  pkt.set_path_index(path_index);
  pkt.set_maxhops(
      _top->get_no_hops(src_tor, dst_tor, slice, path_index));
}

void CreditQueue::refreshPriorityClassification() {
  if (!_is_nic || !_rx_hop_prio) return;
  int slice = _top->time_to_slice(eventlist().now());
  if (slice == _last_priority_slice) return;

  vector<Packet*> pending;
  for (int queue_index = 0; queue_index < CRED_Q_N; queue_index++) {
    pending.insert(pending.end(), _enqueued_cred[queue_index].begin(),
                   _enqueued_cred[queue_index].end());
    _enqueued_cred[queue_index].clear();
    _queuesize_cred[queue_index] = 0;
  }
  std::fill(_priority_queued_bytes.begin(), _priority_queued_bytes.end(), 0);
  _hops_to_creds.clear();
  for (int type = 0; type < 2; type++)
    for (int prio = 0; prio < CRED_Q_N; prio++)
      _type_class_stats[type][prio].queued_bytes = 0;

  sort(pending.begin(), pending.end(), [](Packet* first, Packet* second) {
    return ((XPassPull*)first)->credit_enqueue_seq() <
           ((XPassPull*)second)->credit_enqueue_seq();
  });
  for (vector<Packet*>::iterator it = pending.begin(); it != pending.end();
       ++it) {
    Packet* pkt = *it;
    installPriorityRoute(*pkt, slice);
    int credit_class = creditClass(*pkt);
    _enqueued_cred[credit_class].push_front(pkt);
    accountCreditEnqueue(*pkt, credit_class, credit_class);
    _hops_to_creds[max((pkt->get_maxhops() - pkt->get_crthop()), 1)] += 1;
  }
  _last_priority_slice = slice;
}

// check for next possible credit to send and drops credits that timed out
// returns priority of next available credit queue to send out from
// returns -1 if no credit to send out
inline int CreditQueue::next_cred() {
  refreshPriorityClassification();
  for (int i = 0; i < _queuesize_cred.size(); i++) {
    if (_queuesize_cred[i] > 0) {
      assert(_enqueued_cred[i].size() > 0);
      // look for not expired packet
      while (!_enqueued_cred[i].empty()) {
        Packet *p = _enqueued_cred[i].back();
        // Stop once the oldest packet in this FIFO is not expired.
        if (eventlist().now() + drainTime(p) <= p->get_tmp_ts()) break;
        // else packet expired, drop and try next
        int credit_class = creditClass(*p);
        accountCreditDequeue(*p, i, credit_class);
        _hops_to_creds[max((p->get_maxhops() - p->get_crthop()), 1)] -= 1;
        _drop_creds++;
        _drop_timeout++;
        notePriorityDrop(credit_class, false);
        noteTypeClassDrop(*p, credit_class, CREDIT_DROP_TIMEOUT);
        recordCreditLifecycleDrop(*p, _is_nic, CREDIT_DROP_TIMEOUT);
        recordFlowCreditDrop(*p, &FlowCreditCounters::timeout, _is_nic);
        p->free();
        _enqueued_cred[i].pop_back();
      }
    }
  }

  if (_is_nic && _rx_hop_prio) {
    assert(!_wrr_schedule.empty());
    _selected_schedule_position = _wrr_schedule_position;
    _selected_schedule_target = _wrr_schedule[_wrr_schedule_position];
    _selected_fallback = false;
    if (!_enqueued_cred[_selected_schedule_target].empty())
      return _selected_schedule_target;
    for (uint32_t offset = 1; offset < _wrr_schedule.size(); offset++) {
      int position = (_wrr_schedule_position + offset) % _wrr_schedule.size();
      int prio = _wrr_schedule[position];
      if (!_enqueued_cred[prio].empty()) {
        _selected_fallback = true;
        return prio;
      }
    }
    return -1;
  }

  _selected_schedule_position = -1;
  _selected_schedule_target = -1;
  _selected_fallback = false;
  for (int i = 0; i < CRED_Q_N; i++) {
    if (!_enqueued_cred[i].empty()) return i;
  }
  return -1;
}

void CreditQueue::advanceCreditPriority(int served_prio) {
  if (!_is_nic || !_rx_hop_prio) return;
  assert(served_prio >= 0 && served_prio < CRED_Q_N);
  _wrr_schedule_position =
      (_wrr_schedule_position + 1) % _wrr_schedule.size();
}

void CreditQueue::notePriorityDrop(int prio, bool pushout) {
  if (!_is_nic || !_rx_hop_prio) return;
  assert(prio >= 0 && prio < CRED_Q_N);
  _priority_drops[prio]++;
  if (pushout) _priority_pushouts[prio]++;
}

void CreditQueue::noteTypeClassDrop(Packet &pkt, int credit_class,
                                    int reason) {
  assert(reason >= 0 && reason < CREDIT_DROP_REASON_N);
  CreditClassQueueCounters& counters =
      _type_class_stats[creditType(pkt)][credit_class];
  counters.dropped++;
  switch (reason) {
    case CREDIT_DROP_TENTATIVE:
      counters.tentative_threshold++;
      break;
    case CREDIT_DROP_SHAPING:
      counters.shaping++;
      break;
    case CREDIT_DROP_OVERFLOW:
      counters.overflow++;
      break;
    case CREDIT_DROP_TIMEOUT:
      counters.timeout++;
      break;
    case CREDIT_DROP_PUSHOUT:
      counters.pushout++;
      break;
    default:
      break;
  }
}

void CreditQueue::accountCreditEnqueue(Packet &pkt, int queue_index,
                                       int credit_class) {
  assert(queue_index >= 0 && queue_index < CRED_Q_N);
  assert(credit_class >= 0 && credit_class < CRED_Q_N);
  _queuesize_cred[queue_index] += pkt.size();
  _priority_queued_bytes[credit_class] += pkt.size();
  CreditClassQueueCounters& counters =
      _type_class_stats[creditType(pkt)][credit_class];
  counters.queued_bytes += pkt.size();
  counters.max_queued_bytes =
      max(counters.max_queued_bytes, counters.queued_bytes);
  _priority_max_queued[credit_class] =
      max(_priority_max_queued[credit_class],
          _priority_queued_bytes[credit_class]);
}

void CreditQueue::accountCreditDequeue(Packet &pkt, int queue_index,
                                       int credit_class) {
  assert(queue_index >= 0 && queue_index < CRED_Q_N);
  assert(credit_class >= 0 && credit_class < CRED_Q_N);
  assert(_queuesize_cred[queue_index] >= pkt.size());
  _queuesize_cred[queue_index] -= pkt.size();
  assert(_priority_queued_bytes[credit_class] >= pkt.size());
  _priority_queued_bytes[credit_class] -= pkt.size();
  CreditClassQueueCounters& counters =
      _type_class_stats[creditType(pkt)][credit_class];
  assert(counters.queued_bytes >= pkt.size());
  counters.queued_bytes -= pkt.size();
}

void CreditQueue::dropQueuedCredit(Packet* pkt, int queue_index,
                                   int credit_class, bool pushout) {
  assert(pkt != NULL);
  accountCreditDequeue(*pkt, queue_index, credit_class);
  _hops_to_creds[max((pkt->get_maxhops() - pkt->get_crthop()), 1)] -= 1;
  _drop_creds++;
  _drop_overflow++;
  notePriorityDrop(credit_class, pushout);
  noteTypeClassDrop(*pkt, credit_class,
                    pushout ? CREDIT_DROP_PUSHOUT : CREDIT_DROP_OVERFLOW);
  recordCreditLifecycleDrop(
      *pkt, _is_nic,
      pushout ? CREDIT_DROP_PUSHOUT : CREDIT_DROP_OVERFLOW);
  if (pushout) flowCreditCounters(*pkt).pushout++;
  __global_network_tot_cred_waste += max(pkt->get_crthop(), 0);
  recordFlowCreditDrop(*pkt, &FlowCreditCounters::overflow, _is_nic);
  pkt->free();
}

bool CreditQueue::evictCreditVictim(int victim_class, bool tentative) {
  for (int queue_index = 0; queue_index < CRED_Q_N; queue_index++) {
    list<Packet*>& queue = _enqueued_cred[queue_index];
    for (list<Packet*>::iterator it = queue.begin(); it != queue.end(); ++it) {
      Packet* candidate = *it;
      bool in_service = _tx_next == CRED && candidate == _credit_in_service;
      if (in_service || creditClass(*candidate) != victim_class ||
          ((XPassPull*)candidate)->tentative() != tentative)
        continue;

      queue.erase(it);
      dropQueuedCredit(candidate, queue_index, victim_class, true);
      return true;
    }
  }
  return false;
}

bool CreditQueue::evictPriorityCredit(Packet &arriving, int arriving_class) {
  assert(_is_nic && _rx_credit_pushout);
  bool arriving_tentative = ((XPassPull*)&arriving)->tentative();

  if (arriving_tentative) {
    for (int victim_class = CRED_Q_N - 1;
         victim_class > arriving_class; victim_class--)
      if (evictCreditVictim(victim_class, true)) return true;
    return false;
  }

  // Every regular Credit outranks every tentative Credit. Within a type,
  // displace the worst hop class first and never replace the same class/type.
  for (int victim_class = CRED_Q_N - 1; victim_class >= 0; victim_class--)
    if (evictCreditVictim(victim_class, true)) return true;
  for (int victim_class = CRED_Q_N - 1;
       victim_class > arriving_class; victim_class--)
    if (evictCreditVictim(victim_class, false)) return true;
  return false;
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

void CreditQueue::reportTentativeAdmission(
    Packet &pkt, int credit_class, const vector<mem_b>& occupancy,
    mem_b total_occupancy, const char* decision) const {
  if (!_is_nic || !_rx_credit_slot_trace ||
      !((XPassPull*)&pkt)->tentative())
    return;
  assert(occupancy.size() == CRED_Q_N);
  static const char* names[CRED_Q_N] = {"high", "medium", "low"};
  cout << "TentativeAdmission " << eventlist().now() << " " << _host_id
       << " " << pkt.flow_id() << " "
       << _top->time_to_slice(eventlist().now()) << " "
       << max(pkt.get_maxhops(), 0) << " " << names[credit_class] << " "
       << occupancy[0] / pkt.size() << " " << occupancy[1] / pkt.size()
       << " " << occupancy[2] / pkt.size() << " "
       << total_occupancy / pkt.size() << " "
       << _max_tent_cred / pkt.size() << " " << decision << '\n';
}

bool CreditQueue::handleCredit(Packet &pkt, int credit_class,
                               int queue_index) {
  assert(pkt.type() == XPCREDIT);
  mem_b admission_occupancy = queuesize_cred();
  if (_is_nic && _rx_global_tentative) {
    mem_b classified_occupancy = 0;
    for (int prio = 0; prio < CRED_Q_N; prio++)
      classified_occupancy += _priority_queued_bytes[prio];
    assert(admission_occupancy == classified_occupancy);
  }
  if (admission_occupancy > _max_tent_cred &&
      ((XPassPull *)&pkt)->tentative()) {
    // cout << nodename() << " TENTATIVE DROPPED for " << pkt.flow_id() << endl;
    _drop_creds++;
    _drop_tentative++;
    notePriorityDrop(credit_class, false);
    noteTypeClassDrop(pkt, credit_class, CREDIT_DROP_TENTATIVE);
    recordCreditLifecycleDrop(pkt, _is_nic, CREDIT_DROP_TENTATIVE);
    reportTentativeAdmission(pkt, credit_class, _priority_queued_bytes,
                             admission_occupancy, "drop_threshold");
    recordFlowCreditDrop(pkt, &FlowCreditCounters::tentative, _is_nic);
    pkt.free();
    return false;
  }
  if(((XPassPull*)&pkt)->get_xpsrc()->_is_flare) {
      if (admission_occupancy > _shaping_thresh) {
          _shaping_checks++;
          flowCreditCounters(pkt).shaping_checks++;
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
          if (res < drop_chance) {
              //cout << nodename() << " CREDIT DROPPED (chance) for " << pkt.flow_id() << endl; cout << "dropping packet with " << remaining_hops << " remaining hops\n";
              __global_network_tot_cred_waste += pkt.get_crthop();
              recordFlowCreditDrop(pkt, &FlowCreditCounters::shaping, _is_nic);
              _drop_creds++;
              _drop_shaping++;
              notePriorityDrop(credit_class, false);
              noteTypeClassDrop(pkt, credit_class, CREDIT_DROP_SHAPING);
              recordCreditLifecycleDrop(pkt, _is_nic, CREDIT_DROP_SHAPING);
              reportTentativeAdmission(pkt, credit_class,
                                        _priority_queued_bytes,
                                        admission_occupancy,
                                        "drop_shaping");
              pkt.free();
              return false;
          }
          _shaping_admitted++;
          flowCreditCounters(pkt).shaping_admitted++;
      }
  }
  // credit timeout is set to expected max queueing delay if queue was FIFO
  pkt.set_tmp_ts(eventlist().now() + _cred_timeout);
  _enqueued_cred[queue_index].push_front(&pkt);
  accountCreditEnqueue(pkt, queue_index, credit_class);
  _hops_to_creds[max((pkt.get_maxhops() - pkt.get_crthop()), 1)] += 1;
  // measure in term of data packet size
  uint64_t queued_credits =
      (_is_nic && _rx_hop_prio)
          ? queuesize_cred() / pkt.size()
          : _enqueued_cred[queue_index].size();
  pkt.inc_queueing(queued_credits * 1500);
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
    refreshPriorityClassification();
    int credit_class = creditClass(pkt);
    int queue_index = creditQueueIndex(pkt);
    vector<mem_b> occupancy_before = _priority_queued_bytes;
    mem_b total_occupancy_before = queuesize_cred();
    _tot_creds++;
    _type_class_stats[creditType(pkt)][credit_class].arrived++;
    if (_is_nic && _rx_hop_prio) _priority_arrivals[credit_class]++;
    FlowCreditCounters& counters = flowCreditCounters(pkt);
    counters.queue_arrivals++;
    if (_is_nic) {
      uint32_t path_hops = max(pkt.get_maxhops(), 0);
      if (counters.generated == 0) counters.path_hops_min = path_hops;
      counters.generated++;
      counters.path_hops_min = min(counters.path_hops_min, path_hops);
      counters.path_hops_max = max(counters.path_hops_max, path_hops);
      counters.path_hops_sum += path_hops;
      creditHopCounters(pkt).generated++;
      recordCreditStage(pkt, CREDIT_GENERATED);
      ((XPassPull*)&pkt)->set_credit_enqueue_seq(
          _credit_enqueue_sequence++);
    }
    // cout << "xpcredit\n";
    bool priority_pushout = _is_nic && _rx_credit_pushout;
    if (!priority_pushout &&
        queuesize_cred() + pkt.size() > _maxsize_cred) {
      // if the credit doesn't fit in the queue, drop it
      // cout << nodename() << " CREDIT DROPPED (overflow) for " <<
      // pkt.flow_id() << endl;
      __global_network_tot_cred_waste += pkt.get_crthop();
      recordFlowCreditDrop(pkt, &FlowCreditCounters::overflow, _is_nic);
      _drop_creds++;
      _drop_overflow++;
      notePriorityDrop(credit_class, false);
      noteTypeClassDrop(pkt, credit_class, CREDIT_DROP_OVERFLOW);
      recordCreditLifecycleDrop(pkt, _is_nic, CREDIT_DROP_OVERFLOW);
      reportTentativeAdmission(pkt, credit_class, occupancy_before,
                               total_occupancy_before, "drop_overflow");
      pkt.free();
      return;
    }
    if (!handleCredit(pkt, credit_class, queue_index)) return;
    if (priority_pushout) {
      while (queuesize_cred() > _maxsize_cred &&
             evictPriorityCredit(pkt, credit_class)) {
      }
      if (queuesize_cred() > _maxsize_cred) {
        assert(!_enqueued_cred[queue_index].empty());
        assert(_enqueued_cred[queue_index].front() == &pkt);
        _enqueued_cred[queue_index].pop_front();
        reportTentativeAdmission(pkt, credit_class, occupancy_before,
                                 total_occupancy_before, "drop_overflow");
        dropQueuedCredit(&pkt, queue_index, credit_class, false);
        return;
      }
    }
    if (_is_nic) {
      recordCreditStage(pkt, CREDIT_ADMITTED);
      reportTentativeAdmission(pkt, credit_class, occupancy_before,
                               total_occupancy_before, "admit");
    }
    _max_cred_queue = max(_max_cred_queue, queuesize_cred());
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
    _max_recorded_size = max(_max_recorded_size, _queuesize);
    _max_ever_recorded_size = max(_max_ever_recorded_size, _queuesize);
    pkt.set_last_queueing(_queuesize);
    updatePktIn(pkt.flow_id());
    // cout << "enqueued xpdata\n";
  }

  if (queueWasEmpty) {
    /* schedule the dequeue event */
    if (pkt.type() == XPCREDIT) {
      int prio = next_cred();
      assert(prio >= 0 && _enqueued.size() == 0);
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
    _credit_in_service = _enqueued_cred[prio].back();
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
    int credit_class = creditClass(*pkt);
    accountCreditDequeue(*pkt, prio, credit_class);
    _tx_creds++;
    _type_class_stats[creditType(*pkt)][credit_class].transmitted++;
    if (_is_nic && _rx_hop_prio)
      _priority_transmissions[credit_class]++;
    advanceCreditPriority(prio);
    FlowCreditCounters& counters = flowCreditCounters(*pkt);
    counters.queue_transmissions++;
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
  _credit_in_service = NULL;
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
    // The queue selected when this pacing timer was armed may have become
    // empty after priority admission pushed out its last waiting Credit.
    // The timer is only a send opportunity, so select again from current
    // queue state instead of requiring the stale queue index to stay valid.
    _cred_tx_pending = false;
    if (_tx_next == NONE &&
        !(_enqueued.empty() && queuesize_cred() == 0)) {
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
       << " " << _drop_tentative << " " << _shaping_checks << " "
       << _shaping_admitted << endl;
}

void CreditQueue::reportPriorityStats(const string& scope, int id, int port) {
  if (!receiverPriorityEnabled()) return;
  static const char* names[CRED_Q_N] = {"high", "medium", "low"};
  for (int prio = 0; prio < CRED_Q_N; prio++) {
    cout << "CreditPriorityStats " << scope << " " << id << " " << port
         << " " << names[prio] << " "
         << (_rx_hop_prio ? _credit_weights[prio] : 0) << " "
         << _priority_arrivals[prio] << " "
         << _priority_transmissions[prio] << " "
         << _priority_queued_bytes[prio] / 64 << " "
         << _priority_max_queued[prio] / 64 << " "
         << _priority_drops[prio] << " " << _priority_pushouts[prio]
         << endl;
  }
}

void CreditQueue::reportTypeClassStats(const string& scope, int id, int port) {
  static const char* type_names[2] = {"regular", "tentative"};
  static const char* class_names[CRED_Q_N] = {"high", "medium", "low"};
  for (int type = 0; type < 2; type++) {
    for (int prio = 0; prio < CRED_Q_N; prio++) {
      const CreditClassQueueCounters& counters =
          _type_class_stats[type][prio];
      cout << "CreditTypeClassStats " << scope << " " << id << " " << port
           << " " << type_names[type] << " " << class_names[prio] << " "
           << counters.arrived << " " << counters.transmitted << " "
           << counters.queued_bytes / 64 << " "
           << counters.max_queued_bytes / 64 << " " << counters.dropped
           << " " << counters.tentative_threshold << " " << counters.shaping
           << " " << counters.overflow << " " << counters.timeout << " "
           << counters.pushout << endl;
    }
  }
}

void reportFlowCreditStats() {
  cout << "# FlowCreditStats flow_id sender receiver path_hops generated "
       << "delivered queue_arrivals queue_transmissions dropped overflow "
       << "timeout shaping tentative shaping_checks shaping_admitted waste_hops "
       << "topology path_hops_min path_hops_max path_hops_sum admitted "
       << "endpoint_dropped path_dropped admitted_path_hops_min "
       << "admitted_path_hops_max admitted_path_hops_sum "
       << "delivered_path_hops_min delivered_path_hops_max "
       << "delivered_path_hops_sum delivered_actual_hops_sum "
       << "delivered_hop_mismatches pushout"
       << endl;
  for (map<uint32_t, FlowCreditCounters>::const_iterator it =
           flow_credit_counters.begin();
       it != flow_credit_counters.end(); ++it) {
    const FlowCreditCounters& counters = it->second;
    cout << "FlowCreditStats " << it->first << " " << counters.sender << " "
         << counters.receiver << " " << counters.path_hops << " "
         << counters.generated << " " << counters.delivered << " "
         << counters.queue_arrivals << " " << counters.queue_transmissions
         << " " << counters.dropped << " " << counters.overflow << " "
         << counters.timeout << " " << counters.shaping << " "
         << counters.tentative << " " << counters.shaping_checks << " "
         << counters.shaping_admitted << " " << counters.waste_hops << " "
         << counters.topology << " " << counters.path_hops_min << " "
         << counters.path_hops_max << " " << counters.path_hops_sum << " "
         << counters.admitted << " " << counters.endpoint_dropped << " "
         << counters.path_dropped << " "
         << counters.admitted_path_hops_min << " "
         << counters.admitted_path_hops_max << " "
         << counters.admitted_path_hops_sum << " "
         << counters.delivered_path_hops_min << " "
         << counters.delivered_path_hops_max << " "
         << counters.delivered_path_hops_sum << " "
         << counters.delivered_actual_hops_sum << " "
         << counters.delivered_hop_mismatches << " " << counters.pushout
         << endl;
  }
}

void reportCreditHopStats() {
  cout << "# CreditHopStats hops credit_type generated admitted delivered "
       << "endpoint_dropped path_dropped delivered_actual_hops "
       << "delivered_hop_mismatches" << endl;
  for (map<pair<uint32_t, bool>, CreditHopCounters>::const_iterator it =
           credit_hop_counters.begin();
       it != credit_hop_counters.end(); ++it) {
    const CreditHopCounters& counters = it->second;
    cout << "CreditHopStats " << it->first.first << " "
         << (it->first.second ? "tentative" : "regular") << " "
         << counters.generated << " " << counters.admitted << " "
         << counters.delivered << " " << counters.endpoint_dropped << " "
         << counters.path_dropped << " " << counters.delivered_actual_hops
         << " " << counters.delivered_hop_mismatches << endl;
  }
}

void reportCreditLifecycleStats() {
  static const char* type_names[2] = {"regular", "tentative"};
  static const char* class_names[CRED_Q_N] = {"high", "medium", "low"};
  cout << "# CreditLifecycleStats credit_type priority generated admitted sent "
       << "delivered endpoint_dropped path_dropped endpoint_tentative "
       << "endpoint_shaping endpoint_overflow endpoint_timeout endpoint_pushout "
       << "path_tentative path_shaping path_overflow path_topology_clip "
       << "path_wrong_dst network_hops" << endl;
  for (int type = 0; type < 2; type++) {
    for (int prio = 0; prio < CRED_Q_N; prio++) {
      const CreditLifecycleCounters& counters =
          credit_lifecycle_counters[type][prio];
      cout << "CreditLifecycleStats " << type_names[type] << " "
           << class_names[prio] << " " << counters.stage[CREDIT_GENERATED]
           << " " << counters.stage[CREDIT_ADMITTED] << " "
           << counters.stage[CREDIT_SENT] << " "
           << counters.stage[CREDIT_DELIVERED] << " "
           << counters.endpoint_dropped << " " << counters.path_dropped << " "
           << counters.endpoint_reason[CREDIT_DROP_TENTATIVE] << " "
           << counters.endpoint_reason[CREDIT_DROP_SHAPING] << " "
           << counters.endpoint_reason[CREDIT_DROP_OVERFLOW] << " "
           << counters.endpoint_reason[CREDIT_DROP_TIMEOUT] << " "
           << counters.endpoint_reason[CREDIT_DROP_PUSHOUT] << " "
           << counters.path_reason[CREDIT_DROP_TENTATIVE] << " "
           << counters.path_reason[CREDIT_DROP_SHAPING] << " "
           << counters.path_reason[CREDIT_DROP_OVERFLOW] << " "
           << counters.path_reason[CREDIT_DROP_TOPOLOGY_CLIP] << " "
           << counters.path_reason[CREDIT_DROP_WRONG_DST] << " "
           << counters.network_hops << endl;
    }
  }
}

void CreditQueue::reportMaxqueuesize() {
  simtime_picosec crt_t = eventlist().now();
  double queued_credits = queuesize_cred() / 64.0;
  cout << "Queue " << _tor << " " << _port << " " << _max_recorded_size
       << " " << queuesize() << " " << queued_credits << " ";
  reportLoss();
  map<int, uint64_t>::iterator it;
  cout << " DISTR ";
  for (it = _hops_to_creds.begin(); it != _hops_to_creds.end(); ++it) {
    int hops = it->first;
    uint64_t creds = it->second;
    if (creds > 0 && queued_credits > 0) {
      cout << hops << ":" << (double)creds / queued_credits << " ";
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
                               mem_b shaping_thresh, mem_b aeolus_thresh,
                               mem_b tent_thresh, int host_id,
                               bool rx_hop_prio,
                               bool rx_global_tentative,
                               bool rx_credit_pushout,
                               bool rx_credit_slot_trace,
                               uint64_t priority_seed,
                               uint32_t high_weight,
                               uint32_t medium_weight,
                               uint32_t low_weight)
    : CreditQueue(bitrate, maxsize, eventlist, logger, 0, 0, top, credsize,
                  shaping_thresh, aeolus_thresh, tent_thresh) {
  assert(high_weight > 0 && medium_weight > 0 && low_weight > 0);
  _is_nic = true;
  _host_id = host_id;
  _rx_hop_prio = rx_hop_prio;
  _rx_global_tentative = rx_global_tentative;
  _rx_credit_pushout = rx_credit_pushout;
  _rx_credit_slot_trace = rx_credit_slot_trace;
  _priority_seed = priority_seed;
  _credit_weights = {high_weight, medium_weight, low_weight};
  buildCreditSchedule();
}

void CreditQueue::reportNICCreditSlot(
    Packet &pkt, int credit_class, const vector<mem_b>& class_lengths,
    uint64_t regular_pending, uint64_t tentative_pending) const {
  if (!_is_nic || !_rx_credit_slot_trace) return;
  static const char* type_names[2] = {"regular", "tentative"};
  static const char* class_names[CRED_Q_N] = {"high", "medium", "low"};
  cout << "NICCreditSlot " << eventlist().now() << " " << _host_id << " "
       << _top->time_to_slice(eventlist().now()) << " "
       << type_names[rawCreditType(pkt)] << " " << class_names[credit_class]
       << " " << pkt.flow_id() << " " << max(pkt.get_maxhops(), 0) << " "
       << class_lengths[0] << " " << class_lengths[1] << " "
       << class_lengths[2] << " "
       << class_lengths[0] + class_lengths[1] + class_lengths[2] << " "
       << regular_pending << " " << tentative_pending << " "
       << _selected_schedule_position << " "
       << (_selected_fallback ? 1 : 0) << '\n';
}

void NICCreditQueue::completeService() {
  // cout << nodename() << " completeService " << eventlist().now() << endl;
  /* dequeue the packet */
  Packet *pkt = NULL;
  vector<mem_b> selected_class_lengths(CRED_Q_N, 0);
  uint64_t selected_regular_pending = 0;
  uint64_t selected_tentative_pending = 0;
  if (_tx_next == CRED) {
    int prio = _next_prio;
    if (_rx_hop_prio) {
      prio = next_cred();
    }
    assert(_next_prio >= 0 && _next_prio < CRED_Q_N);
    assert(prio >= 0 && prio < CRED_Q_N);
    // cout << "creditq completeService\n";
    assert(queuesize_cred(prio) > 0);
    pkt = _enqueued_cred[prio].back();
    for (int credit_prio = 0; credit_prio < CRED_Q_N; credit_prio++)
      selected_class_lengths[credit_prio] =
          _priority_queued_bytes[credit_prio] / pkt->size();
    for (int credit_prio = 0; credit_prio < CRED_Q_N; credit_prio++) {
      selected_regular_pending +=
          _type_class_stats[0][credit_prio].queued_bytes / pkt->size();
      selected_tentative_pending +=
          _type_class_stats[1][credit_prio].queued_bytes / pkt->size();
    }
    _enqueued_cred[prio].pop_back();
    updatePktOut(pkt->flow_id());
    int credit_class = creditClass(*pkt);
    accountCreditDequeue(*pkt, prio, credit_class);
    _tx_creds++;
    advanceCreditPriority(prio);
    flowCreditCounters(*pkt).queue_transmissions++;
    _hops_to_creds[max((pkt->get_maxhops() - pkt->get_crthop()), 1)] -= 1;
    _last_cred_tx_t = eventlist().now();
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
    int path_index;
    // A prioritized Credit carries the arrival-time route used to classify
    // it. Reuse that route only if NIC waiting did not cross a slice.
    if (_is_nic && _rx_hop_prio && pkt->type() == XPCREDIT &&
        pkt->get_slice_sent() == slice) {
      path_index = pkt->get_path_index();
      assert(path_index >= 0 && path_index < npaths);
      // FIFO consumes one route draw at NIC departure. Advance the same
      // baseline stream while reusing the classified route so priority does
      // not shift later Flare random decisions.
      if (npaths > 1) (void)fast_rand();
    } else {
      path_index = npaths == 1 ? 0 : fast_rand() % npaths;
    }
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

  if (pkt->type() == XPCREDIT) {
    int sent_credit_class = creditClass(*pkt);
    _type_class_stats[creditType(*pkt)][sent_credit_class].transmitted++;
    if (_rx_hop_prio)
      _priority_transmissions[sent_credit_class]++;
    reportNICCreditSlot(*pkt, sent_credit_class, selected_class_lengths,
                        selected_regular_pending,
                        selected_tentative_pending);
    recordCreditStage(*pkt, CREDIT_SENT);
    recordFlowCreditAdmission(*pkt);
  }

  /* tell the packet to move on to the next pipe */
  sendFromQueue(pkt);
  _next_sched_tx = NO_PENDING_TX;
  _tx_next = NONE;
  _credit_in_service = NULL;
  /* schedule the next dequeue event */
  if (!(_enqueued.empty() && queuesize_cred() == 0)) {
    beginService();
  }
}
