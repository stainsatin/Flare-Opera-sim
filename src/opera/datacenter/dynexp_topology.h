#ifndef DYNEXP
#define DYNEXP
#include "main.h"
//#include "randomqueue.h"
//#include "pipe.h" // mod
#include "config.h"
#include "loggers.h" // mod
//#include "network.h" // mod
#include "topology.h"
#include "logfile.h" // mod
#include "eventlist.h"
#include <ostream>
#include <vector>

#ifndef QT
#define QT
typedef enum {DEFAULT, COMPOSITE, ECN, INT, CREDIT, BOLT, HBH} queue_type;
#endif

class Queue;
class Pipe;
class Logfile;
class RlbModule;

class DynExpTopology: public Topology{
  public:

  // basic topology elements: pipes, queues, and RlbModules:

  vector<Pipe*> pipes_serv_tor; // vector of pointers to pipe
  vector<Queue*> queues_serv_tor; // vector of pointers to queue

  vector<RlbModule*> rlb_modules; // each host has an rlb module

  vector<vector<Pipe*>> pipes_tor; // matrix of pointers to pipe
  vector<vector<Queue*>> queues_tor; // matrix of pointers to queue

  // these functions are used for label switching
  Pipe* get_pipe_serv_tor(int node) {return pipes_serv_tor[node];}
  Queue* get_queue_serv_tor(int node) {return queues_serv_tor[node];}
  Pipe* get_pipe_tor(int tor, int port) {return pipes_tor[tor][port];}
  Queue* get_queue_tor(int tor, int port) {return queues_tor[tor][port];}

  RlbModule* get_rlb_module(int host) {return rlb_modules[host];}

  virtual int64_t get_nsuperslice() {return _nsuperslice;}
  virtual simtime_picosec get_slicetime(int ind) {return _slicetime[ind];} // picoseconds spent in each slice
  virtual int time_to_superslice(simtime_picosec t);
  virtual int time_to_slice(simtime_picosec t); // Given a time, return the slice number (within a cycle)
  virtual int time_to_absolute_slice(simtime_picosec t); // Given a time, return the absolute slice number
  virtual simtime_picosec get_slice_start_time(int slice); // Get the start of a slice
  int get_firstToR(int node) {return node / _ndl;}
  int get_lastport(int dst) {return dst % _ndl;}
  int uplink_to_tor(int uplink);
  int uplink_to_port(int uplink);

  // defined in source file
  virtual int get_nextToR(int slice, int crtToR, int crtport);
  virtual int get_port(int srcToR, int dstToR, int slice, int path_ind, int hop);
  bool is_last_hop(int port);
  bool port_dst_match(int port, int crtToR, int dst);
  virtual int get_no_paths(int srcToR, int dstToR, int slice);
  virtual int get_no_hops(int srcToR, int dstToR, int slice, int path_ind);
  virtual int get_nslices() {return _nslice;} 
  pair<int, int> get_direct_routing(int srcToR, int dstToR, int slice); // Direct routing between src and dst ToRs
  unsigned get_host_buffer(int host);
  void inc_host_buffer(int host);
  void decr_host_buffer(int host);


  Logfile* logfile;
  EventList* eventlist;
  int failed_links;
  queue_type qt;
  map<string,uint64_t> _params;

  DynExpTopology(mem_b queuesize, Logfile* log, EventList* ev, queue_type q, string topfile, 
    map<string,uint64_t> params);
  DynExpTopology(mem_b queuesize, Logfile* log, EventList* ev, queue_type q, string topfile);
  DynExpTopology(mem_b queuesize, Logfile* lg, EventList* ev,queue_type q);
  DynExpTopology(mem_b queuesize, Logfile* lg, EventList* ev,queue_type q, 
    map<string,uint64_t> params);

  void init_network();

  RlbModule* alloc_rlb_module(DynExpTopology* top, int node);

  Queue* alloc_src_queue(DynExpTopology* top, QueueLogger* q, int node);
  Queue* alloc_queue(QueueLogger* q, mem_b queuesize, int tor, int port);
  Queue* alloc_queue(QueueLogger* q, uint64_t speed, mem_b queuesize, int tor, int port);

  void count_queue(Queue*);
  //vector<int>* get_neighbours(int src) {return NULL;};
  int no_of_nodes() const {return _no_of_nodes;} // number of servers
  int no_of_tors() const {return _ntor;} // number of racks
  int no_of_hpr() const {return _ndl;} // number of hosts per rack = number of downlinks
  int no_of_uplinks() const {return _nul;}

  void inc_packets() { _total_packets++; _total_packets_sample++;}
  uint64_t get_all_packets() { return _total_packets; }
  uint64_t get_sample_packets() { uint64_t ret = _total_packets_sample; 
    _total_packets_sample = 0; return ret; }
  void inc_losses() { _total_losses++; _total_losses_sample++;}
  uint64_t get_all_losses() { return _total_losses; }
  uint64_t get_sample_losses() { uint64_t ret = _total_losses_sample; 
    _total_losses_sample = 0; return ret; }
  void set_prob_hops(map<int, double> hops_to_prob) { _hops_to_prob = hops_to_prob; }
  double get_prob_hops(int hops) { return _hops_to_prob[hops]; }
  

 protected:
  map<Queue*,int> _link_usage;
  map<int, unsigned> _host_buffers;
  map<int, unsigned> _max_host_buffers;
  void read_params(string topfile);
  void set_params();
  // Tor-to-Tor connections across time
  // indexing: [slice][uplink (indexed from 0 to _ntor*_nul-1)]
  vector<vector<int>> _adjacency;
  // label switched paths
  // indexing: [src][dst][slice][path_ind][sequence of switch ports (queues)]
  vector<vector<vector<vector<vector<int>>>>> _lbls;
  // Connected time slices
  // indexing: [src][dst] -> <time_slice, port> where the src-dst ToRs are connected
  vector<vector<vector<pair<int, int>>>> _connected_slices;
  int _ndl, _nul, _ntor, _no_of_nodes; // number down links, number uplinks, number ToRs, number servers
  int _nslice; // number of topologies
  int64_t _nsuperslice; // number of "superslices" (periodicity of topology)
  vector<simtime_picosec> _slicetime; // picoseconds spent in each topology slice type (3 types)
  mem_b _queuesize; // queue sizes
  //<hops>-><prob> for flare
  map<int,double> _hops_to_prob;
  uint64_t _total_packets = 0;
  uint64_t _total_packets_sample = 0;
  uint64_t _total_losses = 0;
  uint64_t _total_losses_sample = 0;
};

#endif
