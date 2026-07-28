// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        


#ifndef XPASS_H
#define XPASS_H

/*
 * A XPASS source and sink
 */

#include <list>
#include <map>
#include "config.h"
#include "datacenter/dynexp_topology.h"
#include "network.h"
#include "xpasspacket.h"
#include "fairpullqueue.h"
#include "eventlist.h"

#define timeInf 0
#define XPASS_PACKET_SCATTER

//#define LOAD_BALANCED_SCATTER

//min RTO bound in us
// *** don't change this default - override it by calling XPassSrc::setMinRTO()
#define DEFAULT_RTO_MIN 5000

class XPassSink;
class NICCreditQueue;

class XPassSrc : public PacketSink, public EventSource {
    friend class XPassSink;
 public:
    XPassSrc(EventList &eventlist, int flow_src, int flow_dst, bool is_flare, DynExpTopology *top);
    uint32_t get_id(){ return id;}
    virtual void connect(XPassSink& sink, simtime_picosec startTime);
    void set_traffic_logger(TrafficLogger* pktlogger);
    void startflow();
    void setCwnd(uint32_t cwnd) {_cwnd = cwnd; _init_cwnd = cwnd;}
    static void setMinRTO(uint32_t min_rto_in_us) {_min_rto = timeFromUs((uint32_t)min_rto_in_us);}
    void set_flowsize(uint64_t flow_size_in_bytes);
    inline uint64_t get_flowsize() {return _flow_size;} // bytes
    inline void set_start_time(simtime_picosec startTime) {_start_time = startTime;}
    inline simtime_picosec get_start_time() {return _start_time;};
    double get_rate_as_ratio();
    void setFinished();

    inline int get_flow_src() {return _flow_src;}
    inline int get_flow_dst() {return _flow_dst;}

    virtual void doNextEvent();
    virtual void receivePacket(Packet& pkt);
    virtual void rtx_timer_hook(simtime_picosec now,simtime_picosec period);

    // should really be private, but loggers want to see:
    uint64_t _highest_sent;  //seqno is in bytes
    uint64_t _packets_sent;
    uint64_t _new_packets_sent;
    uint64_t _rtx_packets_sent;
    uint32_t _cwnd, _init_cwnd;
    uint64_t _last_acked;
    uint32_t _flight_size;
    uint32_t _acked_packets;
    uint64_t _tentative_received;
    uint64_t _last_unsched_sent;
    bool _is_flare;
    bool _finished;
    DynExpTopology *_top;
    uint64_t _flow_id;

    // the following are used with SCATTER_PERMUTE, SCATTER_RANDOM and
    // PULL_BASED route strategies
    uint16_t _crt_path;
    uint16_t _crt_direction;
    int _path_index;

    map<XPassPacket::seq_t, simtime_picosec> _sent_times;
    map<XPassPacket::seq_t, simtime_picosec> _first_sent_times;

    void print_stats();

    int _pull_window; // Used to keep track of expected pulls so we
                      // can handle return-to-sender cleanly.
                      // Increase by one for each Ack/Nack received.
                      // Decrease by one for each Pull received.
                      // Indicates how many pulls we expect to
                      // receive, if all currently sent but not yet
                      // acked/nacked packets are lost
                      // or are returned to sender.
    int _first_window_count;

    //round trip time estimate, needed for coupled congestion control
    simtime_picosec _rtt, _rto, _mdev,_base_rtt;

    uint16_t _mss; // maximum segment size
    uint16_t _pkt_size; // packet size. Equal to _flow_size when _flow_size < _mss. Else equal to _mss
 
    uint32_t _drops;

    XPassSink* _sink;
 
    simtime_picosec _rtx_timeout;
    bool _rtx_timeout_pending;

    void pull_packets(XPassPull::seq_t pull_no, XPassPull::seq_t pacer_no);
    void send_packet(XPassPull::seq_t pacer_no, int credit_slice, bool tentative, simtime_picosec ts, bool unsched, unsigned queueing);

    virtual const string& nodename() { return _nodename; }
    inline uint32_t flow_id() const { return _flow.flow_id();}
 
    //debugging hack
    void log_me();
    bool _log_me;

    static uint32_t _global_rto_count;  // keep track of the total number of timeouts across all srcs
    static simtime_picosec _min_rto;
    int _node_num;
    PacketFlow _flow;

    int _flow_src; // the sender (source) for this flow
    int _flow_dst; // the receiver (sink) for this flow

 private:
    // Connectivity
    string _nodename;

    enum  FeedbackType {ACK, NACK, BOUNCE, UNKNOWN};
    static const int HIST_LEN=12;
    FeedbackType _feedback_history[HIST_LEN];
    int _feedback_count;

    // Mechanism
    void clear_timer(uint64_t start,uint64_t end);
    void retransmit_packet();
    void permute_paths();
    void update_rtx_time();
    void process_cumulative_ack(XPassPacket::seq_t cum_ackno);
    inline void count_ack(int32_t path_id) {count_feedback(path_id, ACK);}
    inline void count_nack(int32_t path_id) {count_feedback(path_id, NACK);}
    inline void count_bounce(int32_t path_id) {count_feedback(path_id, BOUNCE);}
    void count_feedback(int32_t path_id, FeedbackType fb);
    bool is_bad_path();
    void send_cumack_req();
    Queue* sendToNIC(Packet* pkt);
    XPassPull::seq_t _last_pull;
    simtime_picosec _start_time;
    uint64_t _flow_size;  //The flow size in bytes.  Stop sending after this amount.
    list <XPassPacket*> _rtx_queue; //Packets queued for (hopefuly) imminent retransmission
};


class XPassSink : public PacketSink, public EventSource, public DataReceiver {
    friend class XPassSrc;
    friend class NICCreditQueue;
 public:
    XPassSink(EventList &eventlist); 

    uint32_t get_id(){ return id;}
    void receivePacket(Packet& pkt);
    void doNextEvent();
    uint32_t _drops;
    uint64_t total_received() const { return _total_received;}
    uint32_t drops(){ return _src->_drops;}
    virtual const string& nodename() { return _nodename; }
    void increase_window() {_pull_no++;} 
    uint64_t cumulative_ack() {return _cumulative_ack;}
    double crtJittering();
    int hopJitter(int hop);
    //list<XPassAck::seq_t> _received; // list of packets above a hole, that we've received
    int hopJitterOld(int hop);
    void incJitter();
    void decJitter();

    //parameter setting
    void set_w_init(double w_init) { _w_init = w_init; }
    void set_target_loss(double target_loss) { _target_loss = target_loss; }
    void set_fb_w_factor(double fb_w_factor) { _weight_factor = fb_w_factor; }
    void set_fb_sens(bool fb_sens) { _fb_sens = fb_sens; }
    void set_jit_alpha(double jit_alpha) { _jit_alpha = jit_alpha; }
    void set_jit_beta(double jit_beta) { _jit_beta = jit_beta; }
    void set_tp_sampling(simtime_picosec freq) { _tp_sampling_freq = freq; }
 
    XPassSrc* _src;

    //debugging hack
    void log_me();
    bool _log_me;

 private:
 
    void connect(XPassSrc& src);
    simtime_picosec nextCreditWait();
    void updateSliceFeedbackState();
    XPassPull* emitCredit();
    void setScheduledCreditRoute(int slice, int path_index, int hops);
    void updateRTT(simtime_picosec ts);
    void feedbackControl();
    void feedbackControl2();
    uint64_t inflight_data();
    uint64_t inflight_creds();
    uint64_t max_rtt_pkts();
    simtime_picosec max_rtt_picosec();
    uint64_t crt_rtt_pkts();
    uint64_t remaining_in_creds();
    void send_cumack(uint64_t to_ack);
    Queue* sendToNIC(Packet* pkt);

    inline uint32_t flow_id() const {
	return _src->flow_id();
    };

    // the following are used with SCATTER_PERMUTE, SCATTER_RANDOM,
    // and PULL_BASED route strategies
    uint16_t _crt_path;
    uint16_t _crt_direction;

   string _nodename;
 
    XPassPull::seq_t _pull_no; // pull sequence number (local to connection)
    XPassPacket::seq_t _last_packet_seqno; //sequence number of the last
                                         //packet in the connection (or 0 if not known)
    XPassPacket::seq_t _last_packet_pacerno; //sequence number of the last
    uint64_t _cumulative_ack;
    uint64_t _total_received;
    list<pair<uint64_t,uint64_t>> _received; // list of packets above a hole, <seqno,segsize>

    //credit and rate state-keeping
    uint64_t _credit_counter = 0;
    uint64_t _tot_sent_creds = 0;
    uint64_t _tot_sent_creds_slice = 0;
    uint64_t _tot_sent_creds_since_recvd = 0;
    uint64_t _bw_sent_creds = 0;
    uint64_t _tent_since_last_bw = 0;
    uint64_t _inflight_estimate = 0;
    uint64_t _tot_recvd_pkts = 0;
    uint64_t _tot_credits = 0;
    uint64_t _drop_credits = 0; 
    uint64_t _tent_credits = 0;
    uint64_t _tent_credits_recvd = 0;
    uint64_t _recvd_in_quantum = 0;
    uint64_t _tot_sent_in_quantum = 0;
    vector<uint64_t> _bw_sent_in_quantum;
    unsigned _crt_quantum = 0;
    double _target_loss;
    double _weight, _max_weight, _min_weight, _weight_factor;
    double _w_init;
    bool _fb_sens;
    int _jit_alpha, _jit_beta;
    double _max_hop_jitter, _min_hop_jitter, _hop_jitter;
    unsigned _bdp;
    uint64_t _crt_rate, _max_rate, _min_rate; //bps
    bool _is_increasing;
    simtime_picosec _rtt, _max_rtt;
    simtime_picosec _last_fb_update;
    simtime_picosec _tp_sampling_freq;
    uint64_t _recvd_data, _tot_recvd_data;
    simtime_picosec _last_tp_sample_t;
    simtime_picosec _last_valid_cred_t;
    int _crt_slice;
    int _crt_slice_route;
    int _crt_hops;
    unsigned _total_hops;
    bool _is_flare;
    bool _is_pulling;
    bool _is_recovering;
    NICCreditQueue* _receiver_credit_queue;
    bool _flow_credit_scheduler_enabled;
    bool _credit_request_pending;
    bool _scheduled_credit_route_valid;
    int _scheduled_credit_slice;
    int _scheduled_credit_path_index;
    int _scheduled_credit_hops;
};

class XPassRtxTimerScanner : public EventSource {
 public:
    XPassRtxTimerScanner(simtime_picosec scanPeriod, EventList& eventlist);
    void doNextEvent();
    void registerXPass(XPassSrc &tcpsrc);
 private:
    simtime_picosec _scanPeriod;
    typedef list<XPassSrc*> tcps_t;
    tcps_t _tcps;
};

#endif

