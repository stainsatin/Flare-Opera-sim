// -*- c-basic-offset: 4; tab-width: 8; indent-tabs-mode: t -*-        
#include "config.h"
#include <sstream>
#include <strstream>
#include <fstream> // need to read flows
#include <iostream>
#include <string.h>
#include <math.h>
#include "network.h"
#include "randomqueue.h"
#include "pipe.h"
#include "eventlist.h"
#include "logfile.h"
#include "loggers.h"
#include "clock.h"
#include "xpass.h"
#include "creditqueue.h"
#include "topology.h"

#include "graph_topology.h"
#include <list>

// Simulation params

#define PRINT_PATHS 0

#define PERIODIC 0
#include "main.h"

uint32_t delay_host2ToR = 0; // ns
uint32_t delay_ToR2ToR = 500; // ns

#define DEFAULT_PACKET_SIZE 1500 // full packet (including header), Bytes
#define DEFAULT_HEADER_SIZE 64 // header size, Bytes
#define DEFAULT_QUEUE_SIZE 46


string ntoa(double n);
string itoa(uint64_t n);

map<int,double> read_probfun(const string& fname) {
    map<int,double> hop_to_prob;
    ifstream input(fname);
    if (!input.is_open()) {
        cout << "Could not open probability file " << fname << endl;
        exit(1);
    }
    int hops;
    double probability;
    while (input >> hops >> probability) {
        hop_to_prob[hops] = probability;
    }
    return hop_to_prob;
}

void report_credit_stats(GraphTopology* top) {
    cout << "# CreditStats scope id port received transmitted queued max_queued "
         << "dropped overflow timeout shaping tentative" << endl;
    cout << "# DataQueueStats scope id port dropped" << endl;
    for (int host = 0; host < top->no_of_nodes(); host++) {
        CreditQueue* queue = dynamic_cast<CreditQueue*>(top->get_queue_serv_tor(host));
        assert(queue);
        queue->reportCreditStats("host", host, -1);
        cout << "DataQueueStats host " << host << " -1 "
             << queue->num_drops() << endl;
    }
    for (int tor = 0; tor < top->no_of_tors(); tor++) {
        for (int port = 0; port < top->no_of_hpr() + top->no_of_uplinks(); port++) {
            CreditQueue* queue = dynamic_cast<CreditQueue*>(top->get_queue_tor(tor, port));
            assert(queue);
            queue->reportCreditStats("tor", tor, port);
            cout << "DataQueueStats tor " << tor << " " << port << " "
                 << queue->num_drops() << endl;
        }
    }
}

EventList eventlist;
Logfile* lg;

void exit_error(char* progr) {
    cout << "Usage " << progr << " [UNCOUPLED(DEFAULT)|COUPLED_INC|FULLY_COUPLED|COUPLED_EPSILON] [epsilon][COUPLED_SCALABLE_TCP" << endl;
    exit(1);
}

int main(int argc, char **argv) {
    
    Packet::set_packet_size(DEFAULT_PACKET_SIZE - DEFAULT_HEADER_SIZE);
    mem_b queuesize = DEFAULT_QUEUE_SIZE * DEFAULT_PACKET_SIZE;
    mem_b aeolus_thresh = DEFAULT_QUEUE_SIZE * DEFAULT_PACKET_SIZE;
    mem_b cred_queuesize = DEFAULT_QUEUE_SIZE * DEFAULT_HEADER_SIZE;
    mem_b shaping_thresh = DEFAULT_QUEUE_SIZE * DEFAULT_HEADER_SIZE;
    mem_b tent_thresh = 0;
    
    int cwnd = 10;
    stringstream filename(ios_base::out);
    string flowfile; // so we can read the flows from a specified file
    string topfile; // read the topology from a specified file
    double target_loss = 0.1; //target credit waste ratio
    double w_init = 0.5; //initial credit pull rate ratio (w.r.t bandwdith)
    bool fb_sens = false; //weight feedback adjustment with prob function
    bool is_flare = false; // false=ExpressPass, true=Flare
    int jit_a = -1;
    int jit_b = -1;
    double fb_w_factor = 2.0; //weight adjustment factor
    double simtime = 0.01; // seconds
    double utiltime=1.0; // milliseconds
    double tp_sampling= 0.0; //milliseconds
    map<int,double> hops_to_prob = {{1,1.0},{2,1.0},{3,1.0},{4,1.0},{5,1.0}};

    int i = 1;
    filename << "logout.dat";
    RouteStrategy route_strategy = NOT_SET;

    while (i<argc) {
	if (!strcmp(argv[i],"-o")){
	    filename.str(std::string());
	    filename << argv[i+1];
	    i++;
	} else if (!strcmp(argv[i],"-cwnd")){
	    cwnd = atoi(argv[i+1]);
	    i++;
	} else if (!strcmp(argv[i],"-q")){
	    queuesize = atoi(argv[i+1]) * DEFAULT_PACKET_SIZE;
	    i++;
	} else if (!strcmp(argv[i],"-credq")){
	    cred_queuesize = atoi(argv[i+1]) * DEFAULT_HEADER_SIZE;
	    i++;
	} else if (!strcmp(argv[i],"-qshaping")){
	    shaping_thresh = atoi(argv[i+1]) * DEFAULT_HEADER_SIZE;
	    i++;
	} else if (!strcmp(argv[i],"-aeolus")){
	    aeolus_thresh = atoi(argv[i+1]) * DEFAULT_PACKET_SIZE;
	    i++;
	} else if (!strcmp(argv[i],"-tent")){
	    tent_thresh = atoi(argv[i+1]) * DEFAULT_HEADER_SIZE;
	    i++;
	} else if (!strcmp(argv[i],"-fbw")){
	    fb_w_factor = atof(argv[i+1]);
	    i++;
	} else if (!strcmp(argv[i],"-jitk")){
	    jit_a = atoi(argv[i+1]);
	    jit_b = jit_a;
	    i++;
	} else if (!strcmp(argv[i],"-jita")){
	    jit_a = atoi(argv[i+1]);
	    i++;
	} else if (!strcmp(argv[i],"-jitb")){
	    jit_b = atoi(argv[i+1]);
	    i++;
	} else if (!strcmp(argv[i],"-fbsens")){
	    fb_sens = true;
	} else if (!strcmp(argv[i],"-flare")){
	    is_flare = true;
	} else if (!strcmp(argv[i],"-probfile")) {
	    hops_to_prob = read_probfun(argv[i+1]);
	    i++;
	} else if (!strcmp(argv[i],"-strat")){
	    if (!strcmp(argv[i+1], "perm")) {
			route_strategy = SCATTER_PERMUTE;
	    } else if (!strcmp(argv[i+1], "rand")) {
			route_strategy = SCATTER_RANDOM;
	    } else if (!strcmp(argv[i+1], "pull")) {
			route_strategy = PULL_BASED;
	    } else if (!strcmp(argv[i+1], "single")) {
			route_strategy = SINGLE_PATH;
	    }
	    i++;
	} else if (!strcmp(argv[i],"-flowfile")) {
		flowfile = argv[i+1];
		i++;
  } else if (!strcmp(argv[i],"-topfile")) {
      topfile = argv[i+1];
      i++;
    } else if (!strcmp(argv[i],"-winit")) {
        w_init = atof(argv[i+1]);
        i++;
    } else if (!strcmp(argv[i],"-tloss")) {
        target_loss = atof(argv[i+1]);
        i++;
    } else if (!strcmp(argv[i],"-simtime")) {
        simtime = atof(argv[i+1]);
        i++;
	} else if (!strcmp(argv[i],"-utiltime")) {
            utiltime = atof(argv[i+1]);
            i++;
	} else if (!strcmp(argv[i],"-tp")) {
            tp_sampling = atof(argv[i+1]);
            i++;
    } else 
        exit_error(argv[0]);
      i++;
    }
    fast_srand(13);
    srand(13);

    if (topfile.empty() || flowfile.empty()) {
        cout << "Both -topfile and -flowfile are required" << endl;
        exit(1);
    }


    eventlist.setEndtime(timeFromSec(simtime));
    Clock c(timeFromSec(5 / 100.), eventlist);


    if (route_strategy == NOT_SET) {
	fprintf(stderr, "Route Strategy not set.  Use the -strat param.  \nValid values are perm, rand, pull, rg and single\n");
	exit(1);
    }

    Logfile logfile(filename.str(), eventlist);

#if PRINT_PATHS
    filename << ".paths";
    cout << "Logging path choices to " << filename.str() << endl;
    std::ofstream paths(filename.str().c_str());
    if (!paths){
	cout << "Can't open for writing paths file!"<<endl;
	exit(1);
    }
#endif

    lg = &logfile;




    // !!!!!!!!!!!!!!!!!!!!!!!
    logfile.setStartTime(timeFromSec(10));


    XPassRtxTimerScanner xpassRtxScanner(timeFromMs(1), eventlist);

    map<string,uint64_t> params = 
        {{"cq_size",cred_queuesize},{"sh_thresh",shaping_thresh},
        {"ae_thresh",aeolus_thresh},{"te_thresh",tent_thresh}};
    GraphTopology* top = new GraphTopology(queuesize, &logfile, &eventlist, 
        CREDIT, topfile, params);
    top->set_prob_hops(hops_to_prob);

    // initialize all sources/sinks
    XPassSrc::setMinRTO(1000); //increase RTO to avoid spurious retransmits

    //ifstream input("flows.txt");
    ifstream input(flowfile);
    if (input.is_open()){
        string line;
        int64_t temp;
        // get flows. Format: (src) (dst) (bytes) (start time in nanoseconds)
        while(!input.eof()){
            vector<int64_t> vtemp;
            getline(input, line);
            if(line.length() <= 0) continue;
            stringstream stream(line);
            while (stream >> temp)
                vtemp.push_back(temp);
            if (vtemp.size() != 4) {
                cout << "Invalid flow row (expected src dst bytes start_ns): " << line << endl;
                exit(1);
            }
            if (vtemp[0] < 0 || vtemp[0] >= top->no_of_nodes() ||
                vtemp[1] < 0 || vtemp[1] >= top->no_of_nodes() ||
                vtemp[0] == vtemp[1] || vtemp[2] <= 0 || vtemp[3] < 0) {
                cout << "Invalid flow values: " << line << endl;
                exit(1);
            }
            cout << "src = " << vtemp[0] << " dest = " << vtemp[1] << " bytes " << vtemp[2] << " time " << vtemp[3] << endl;
            
            // source and destination hosts for this flow
            int flow_src = vtemp[0];
            int flow_dst = vtemp[1];

            XPassSrc* flowSrc = new XPassSrc(eventlist, flow_src, flow_dst, is_flare, top);
            flowSrc->setCwnd(cwnd*Packet::data_packet_size());
            flowSrc->set_flowsize(vtemp[2]); // bytes

            XPassSink* flowSnk = new XPassSink(eventlist);
            flowSnk->set_w_init(w_init);
            flowSnk->set_target_loss(target_loss);
            flowSnk->set_fb_w_factor(fb_w_factor);
            flowSnk->set_fb_sens(fb_sens);
            flowSnk->set_jit_alpha(jit_a);
            flowSnk->set_jit_beta(jit_b);
            flowSnk->set_tp_sampling(timeFromMs(tp_sampling));
            xpassRtxScanner.registerXPass(*flowSrc);

            flowSrc->connect(*flowSnk, timeFromNs(vtemp[3]/1.));

        }
    } else {
        cout << "Could not open flow file " << flowfile << endl;
        exit(1);
    }


    UtilMonitor* UM = new UtilMonitor(top, eventlist);
    UM->start(timeFromMs(utiltime));

    // Record the setup
    int pktsize = Packet::data_packet_size();
    logfile.write("# pktsize=" + ntoa(pktsize) + " bytes");
    //logfile.write("# subflows=" + ntoa(subflow_count));
    logfile.write("# hostnicrate = " + ntoa(HOST_NIC) + " pkt/sec");
    logfile.write("# corelinkrate = " + ntoa(HOST_NIC*CORE_TO_HOST) + " pkt/sec");
    //logfile.write("# buffer = " + ntoa((double) (queues_na_ni[0][1]->_maxsize) / ((double) pktsize)) + " pkt");
    //double rtt = timeAsSec(timeFromUs(RTT));
    //logfile.write("# rtt =" + ntoa(rtt));

    // GO!
    while (eventlist.doNextEvent()) { }
    report_credit_stats(top);

}

string ntoa(double n) {
    stringstream s;
    s << n;
    return s.str();
}

string itoa(uint64_t n) {
    stringstream s;
    s << n;
    return s.str();
}
