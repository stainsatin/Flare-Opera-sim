// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#ifndef XPASSPACKET_H
#define XPASSPACKET_H

#include <list>
#include "datacenter/dynexp_topology.h"
#include "network.h"

// XPassPacket and XPassAck are subclasses of Packet.
// They incorporate a packet database, to reuse packet objects that are no longer needed.
// Note: you never construct a new XPassPacket or XPassAck directly; 
// rather you use the static method newpkt() which knows to reuse old packets from the database.

#define ACKSIZE 64
#define VALUE_NOT_SET -1
#define PULL_MAXPATHS 256 // maximum path ID that can be pulled
class XPassSink;

class XPassPacket : public Packet {
public:
  typedef uint32_t seq_t;

  // pseudo-constructor for a routeless packet - routing information
  // must be filled in later
  inline static XPassPacket* newpkt(DynExpTopology *top, int src, int dst, XPassSrc *xpsrc, XPassSink *xpsink, 
                                    seq_t seqno, seq_t pacerno, int size, 
                                    bool retransmitted, 
                                    bool last_packet) {
    XPassPacket* p = _packetdb.allocPacket();
    p->_size = size+ACKSIZE;
    p->_type = XPDATA;
    p->_is_header = false;
    p->_bounced = false;
    p->_seqno = seqno;
    p->_pacerno = pacerno;
    p->_retransmitted = retransmitted;
    p->_last_packet = last_packet;
    p->_src = src;
    p->_dst = dst;
    p->_tentative = false;
    p->_unsched = false;
    p->_ackreq = false;
    p->_xpsrc = xpsrc;
    p->_xpsink = xpsink;
    p->_top = top;
    p->_queueing = 0;
    return p;
  }

  void free() {_packetdb.freePacket(this);}
  virtual ~XPassPacket(){}
  inline seq_t seqno() const {return _seqno;}
  inline seq_t pacerno() const {return _pacerno;}
  inline void set_pacerno(seq_t pacerno) {_pacerno = pacerno;}
  inline int credit_ts() const {return _credit_ts;}
  inline void set_credit_ts(int credit_ts) {_credit_ts = credit_ts;}
  inline bool retransmitted() const {return _retransmitted;}
  inline bool last_packet() const {return _last_packet;}
  inline simtime_picosec ts() const {return _ts;}
  inline void set_ts(simtime_picosec ts) {_ts = ts;}
  void set_tentative(bool tentative) {_tentative = tentative;}
  bool tentative() {return _tentative;}
  void set_unsched(bool unsched) {_unsched = unsched;}
  bool unsched() {return _unsched;}
  void set_ackreq(bool ackreq) {_ackreq = ackreq;}
  bool ackreq() {return _ackreq;}
  XPassSrc *get_xpsrc() {return _xpsrc;}
  XPassSink *get_xpsink() {return _xpsink;}

protected:
  XPassSrc *_xpsrc;
  XPassSink *_xpsink;
  seq_t _seqno;
  seq_t _pacerno;  // the pacer sequence number from the pull, seq space is common to all flows on that pacer
  simtime_picosec _ts;
  int16_t _credit_ts;
  bool _retransmitted;
  bool _last_packet;  // set to true in the last packet in a flow.
  bool _tentative;
  bool _unsched;
  bool _ackreq;
  static PacketDB<XPassPacket> _packetdb;
};

class XPassPull : public Packet {
public:
  typedef XPassPacket::seq_t seq_t;
  inline static XPassPull* newpkt(DynExpTopology *top, int src, int dst, XPassSrc *xpsrc, XPassSink *xpsink) {
    XPassPull* p = _packetdb.allocPacket();
    p->_size = ACKSIZE;
    p->_type = XPCREDIT;
    p->_top = top;
    p->_src = src;
    p->_dst = dst;
    p->_xpsrc = xpsrc;
    p->_xpsink = xpsink;
    p->_tentative = false;
    p->_prio = false;
    p->_credit_enqueue_seq = 0;
    p->_generation_superslice = -1;
    p->_queueing = 0;
    return p;
  }

  void free() {_packetdb.freePacket(this);}
  inline seq_t pacerno() const {return _pacerno;}
  inline void set_pacerno(seq_t pacerno) {_pacerno = pacerno;}
  inline seq_t ackno() const {return _ackno;}
  inline void set_ackno(seq_t ackno) {_ackno = ackno;}
  inline seq_t cumulative_ack() const {return _cumulative_ack;}
  void set_tentative(bool tentative) {_tentative = tentative;}
  bool tentative() {return _tentative;}
  void set_prio(bool prio) {_prio = prio;}
  bool prio() {return _prio;}
  void set_credit_enqueue_seq(uint64_t sequence) {_credit_enqueue_seq = sequence;}
  uint64_t credit_enqueue_seq() const {return _credit_enqueue_seq;}
  void set_generation_superslice(int64_t superslice) {
    _generation_superslice = superslice;
  }
  int64_t generation_superslice() const {return _generation_superslice;}
  inline simtime_picosec ts() const {return _ts;}
  inline void set_ts(simtime_picosec ts) {_ts = ts;}
  XPassSrc *get_xpsrc() {return _xpsrc;}
  XPassSink *get_xpsink() {return _xpsink;}

  virtual ~XPassPull(){}

protected:
  XPassSrc *_xpsrc;
  XPassSink *_xpsink;
  simtime_picosec _ts;
  seq_t _pacerno;
  seq_t _ackno;
  seq_t _cumulative_ack;
  bool _tentative;
  bool _prio;
  uint64_t _credit_enqueue_seq;
  int64_t _generation_superslice;
  static PacketDB<XPassPull> _packetdb;
};

class XPassCtl : public Packet {
public:
  typedef XPassPacket::seq_t seq_t;
  inline static XPassCtl* newpkt(DynExpTopology *top, int src, int dst, XPassSrc *xpsrc, XPassSink *xpsink) {
    XPassCtl* p = _packetdb.allocPacket();
    p->_size = ACKSIZE;
    p->_type = XPCTL;
    p->_top = top;
    p->_src = src;
    p->_dst = dst;
    p->_xpsrc = xpsrc;
    p->_xpsink = xpsink;
    p->_tentative = false;
    p->_queueing = 0;
    return p;
  }

  void free() {_packetdb.freePacket(this);}
  inline seq_t ackno() const {return _ackno;}
  inline void set_ackno(seq_t ackno) {_ackno = ackno;}
  void set_tentative(bool tentative) {_tentative = tentative;}
  bool tentative() {return _tentative;}
  XPassSrc *get_xpsrc() {return _xpsrc;}
  XPassSink *get_xpsink() {return _xpsink;}

  virtual ~XPassCtl(){}

protected:
  XPassSrc *_xpsrc;
  XPassSink *_xpsink;
  seq_t _ackno;
  bool _tentative;
  static PacketDB<XPassCtl> _packetdb;
};

#endif
