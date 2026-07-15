// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-
#include "graph_topology.h"
#include <vector>
#include "dynexp_topology.h"
#include "string.h"
#include <sstream>
#include <strstream>
#include <iostream>
#include <fstream> // to read from file
#include <map>
#include "main.h"
#include "queue.h"
#include "pipe.h"
#include "compositequeue.h"
#include "ecnqueue.h"
#include "intqueue.h"
#include "creditqueue.h"
#include "ecn.h"
//#include "prioqueue.h"

#include "rlbmodule.h"

extern uint32_t delay_host2ToR; // nanoseconds, host-to-tor link
extern uint32_t delay_ToR2ToR; // nanoseconds, tor-to-tor link

string ntoa(double n);
string itoa(uint64_t n);

GraphTopology::GraphTopology(mem_b queuesize, Logfile* lg, EventList* ev,queue_type q, string topfile, map<string,uint64_t> params)
  : DynExpTopology(queuesize, lg, ev, q, params)
{
    _queuesize = queuesize;
    logfile = lg;
    eventlist = ev;
    qt = q;

    read_params(topfile);
 
    set_params();

    init_network();
}

GraphTopology::GraphTopology(mem_b queuesize, Logfile* lg, EventList* ev,queue_type q, string topfile) 
  : GraphTopology(queuesize, lg, ev, q, topfile, map<string,uint64_t>()) {}

// read the topology info from file (generated in Matlab)
void GraphTopology::read_params(string topfile) {

  ifstream input(topfile);

  if (!input.is_open()) {
    cout << "Could not open topology file " << topfile << endl;
    exit(1);
  }

  string line;
  getline(input, line);
  stringstream header(line);
  if (!(header >> _ntor >> _ndl) || _ntor <= 0 || _ndl <= 0) {
    cout << "Invalid graph topology header in " << topfile << endl;
    exit(1);
  }
  _no_of_nodes = _ntor * _ndl;
  _nslice = 1;
  _nsuperslice = 1;
  _slicetime.assign(4, 0);
  failed_links = 0;

  int node;
  _adjacency.resize(_ntor);
  for (int tor = 0; tor < _ntor; tor++) {
    if (!getline(input, line)) {
      cout << "Missing adjacency row for ToR " << tor << " in " << topfile << endl;
      exit(1);
    }
    stringstream adjacency_stream(line);
    while (adjacency_stream >> node) {
      if (node < 0 || node >= _ntor || node == tor) {
        cout << "Invalid neighbor " << node << " for ToR " << tor << endl;
        exit(1);
      }
      if (_adj_to_port.count(make_pair(tor, node)) > 0) {
        cout << "Duplicate neighbor " << node << " for ToR " << tor << endl;
        exit(1);
      }
      int port = _ndl + _adjacency[tor].size();
      _adjacency[tor].push_back(node);
      _adj_to_port[make_pair(tor, node)] = port;
    }
    if (_adjacency[tor].empty()) {
      cout << "ToR " << tor << " has no graph neighbors" << endl;
      exit(1);
    }
    if (tor == 0) {
      _nul = _adjacency[tor].size();
    } else if (_adjacency[tor].size() != (size_t)_nul) {
      cout << "GraphTopology requires a regular graph; ToR " << tor
           << " has " << _adjacency[tor].size() << " neighbors, expected "
           << _nul << endl;
      exit(1);
    }
  }

  _lbls.resize(_ntor);
  _connected_slices.resize(_ntor);
  for (int tor = 0; tor < _ntor; tor++) {
    _lbls[tor].resize(_ntor);
    _connected_slices[tor].resize(_ntor);
    for (int uplink = 0; uplink < _nul; uplink++) {
      int neighbor = _adjacency[tor][uplink];
      _connected_slices[tor][neighbor].push_back(
          make_pair(0, _ndl + uplink));
    }
  }

  cout << "Loading topology..." << endl;
  while (getline(input, line)) {
    if (line.length() <= 0) continue;
    vector<int> route;
    int value;
    stringstream route_stream(line);
    while (route_stream >> value) route.push_back(value);
    if (route.size() < 3) {
      cout << "Invalid graph route (expected src dst next-hop...): " << line << endl;
      exit(1);
    }

    int src = route[0];
    int dst = route[1];
    if (src < 0 || src >= _ntor || dst < 0 || dst >= _ntor || src == dst) {
      cout << "Invalid graph route endpoints: " << line << endl;
      exit(1);
    }

    int path_index = _lbls[src][dst].size();
    _lbls[src][dst].resize(path_index + 1);
    int current = src;
    for (size_t hop = 2; hop < route.size(); hop++) {
      int next = route[hop];
      map<pair<int,int>, int>::const_iterator port_it =
          _adj_to_port.find(make_pair(current, next));
      if (port_it == _adj_to_port.end()) {
        cout << "Route uses missing edge " << current << " -> " << next
             << ": " << line << endl;
        exit(1);
      }
      _lbls[src][dst][path_index].push_back(port_it->second);
      current = next;
    }
    if (current != dst) {
      cout << "Route does not end at destination " << dst << ": " << line << endl;
      exit(1);
    }
  }
  cout << "Loaded topology." << endl;
}

// set number of possible pipes and queues
void GraphTopology::set_params() {

    pipes_serv_tor.resize(_no_of_nodes); // servers to tors
    queues_serv_tor.resize(_no_of_nodes);

    rlb_modules.resize(_no_of_nodes);

    pipes_tor.resize(_ntor, vector<Pipe*>(_ndl+_nul)); // tors
    queues_tor.resize(_ntor, vector<Queue*>(_ndl+_nul));
}

// initializes all the pipes and queues in the Topology
void GraphTopology::init_network() {
  QueueLoggerSampling* queueLogger;

  // initialize server to ToR pipes / queues
  for (int j = 0; j < _no_of_nodes; j++) { // sweep nodes
    rlb_modules[j] = NULL;
    queues_serv_tor[j] = NULL;
    pipes_serv_tor[j] = NULL;
  }
      
  // create server to ToR pipes / queues / RlbModules
  for (int j = 0; j < _no_of_nodes; j++) { // sweep nodes
    queueLogger = new QueueLoggerSampling(timeFromMs(1000), *eventlist);
    logfile->addLogger(*queueLogger);

    rlb_modules[j] = alloc_rlb_module(this, j);

    queues_serv_tor[j] = alloc_src_queue(this, queueLogger, j);
    ostringstream oss;
    oss << "NICQueue " << j;
    queues_serv_tor[j]->setName(oss.str());
    //queues_serv_tor[j][k]->setName("Queue-SRC" + ntoa(k + j*_ndl) + "->TOR" +ntoa(j));
    //logfile->writeName(*(queues_serv_tor[j][k]));
    pipes_serv_tor[j] = new Pipe(timeFromNs(delay_host2ToR), *eventlist);
    //pipes_serv_tor[j][k]->setName("Pipe-SRC" + ntoa(k + j*_ndl) + "->TOR" + ntoa(j));
    //logfile->writeName(*(pipes_serv_tor[j][k]));
  }

  // initialize ToR outgoing pipes / queues
  for (int j = 0; j < _ntor; j++) // sweep ToR switches
    for (int k = 0; k < _nul+_ndl; k++) { // sweep ports
      queues_tor[j][k] = NULL;
      pipes_tor[j][k] = NULL;
    }

  for (int j = 0; j < _ntor; j++) { // sweep ToR switches
  // create ToR outgoing pipes / queues
    for (int k = 0; k < _ndl; k++) { // sweep ports
      queueLogger = new QueueLoggerSampling(timeFromMs(1000), *eventlist);
      logfile->addLogger(*queueLogger);
      queues_tor[j][k] = alloc_queue(queueLogger, _queuesize, j, k);
      ostringstream oss;
      oss << "DLQueue" << j << ":" << k;
      queues_tor[j][k]->setName(oss.str());
      //queues_tor[j][k]->setName("Queue-TOR" + ntoa(j) + "->DST" + ntoa(k + j*_ndl));
      //logfile->writeName(*(queues_tor[j][k]));
      pipes_tor[j][k] = new Pipe(timeFromNs(delay_host2ToR), *eventlist);
      //pipes_tor[j][k]->setName("Pipe-TOR" + ntoa(j)  + "->DST" + ntoa(k + j*_ndl));
      //logfile->writeName(*(pipes_tor[j][k]));
      
    }
  }
  for (int j = 0; j < _ntor; j++) { // sweep ToR switches
    for (int k = 0; k < _adjacency[j].size(); k++) { // sweep ports  
        // it's a link to another ToR
        int port = k+_ndl;
        queueLogger = new QueueLoggerSampling(timeFromMs(1000), *eventlist);
        logfile->addLogger(*queueLogger);
        queues_tor[j][port] = alloc_queue(queueLogger, _queuesize, j, port);
        ostringstream oss;
        oss << "ULQueue" << j << ":" << port;
        queues_tor[j][port]->setName(oss.str());
        //queues_tor[j][k]->setName("Queue-TOR" + ntoa(j) + "->uplink" + ntoa(k - _ndl));
        //logfile->writeName(*(queues_tor[j][k]));
        pipes_tor[j][port] = new Pipe(timeFromNs(delay_ToR2ToR), *eventlist);
        //pipes_tor[j][k]->setName("Pipe-TOR" + ntoa(j)  + "->uplink" + ntoa(k - _ndl));
        //logfile->writeName(*(pipes_tor[j][k]));
    }
  }
}


int GraphTopology::get_nextToR(int slice, int crtToR, int crtport) {
  int uplink = crtport - _ndl;
  //cout << "Getting next ToR..." << endl;
  //cout << "   uplink = " << uplink << endl;
  //cout << "   next ToR = " << _adjacency[slice][uplink] << endl;
  return _adjacency[crtToR][uplink];
}

int GraphTopology::get_port(int srcToR, int dstToR, int slice, int path_ind, int hop) {
  //cout << "Getting port..." << endl;
  //cout << "   Inputs: srcToR = " << srcToR << ", dstToR = " << dstToR << ", slice = " << slice << ", path_ind = " << path_ind << ", hop = " << hop << endl;
  //cout << "   Port = " << _lbls[srcToR][dstToR][slice][path_ind][hop] << endl;
  return _lbls[srcToR][dstToR][path_ind][hop];
}

int GraphTopology::get_no_paths(int srcToR, int dstToR, int slice) {
  int sz = _lbls[srcToR][dstToR].size();
  return sz;
}

int GraphTopology::get_no_hops(int srcToR, int dstToR, int slice, int path_ind) {
  int sz = _lbls[srcToR][dstToR][path_ind].size();
  return sz;
}
