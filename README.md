# Flare-Opera-sim
Packet-level simulation code for SIGCOMM 2025 paper (Flare) "Unlocking Superior Performance in Reconfigurable Data Center Networks with Credit-Based Transport"

## Table of contents
1. [Requirements and reference hardware](#requirements-and-reference-hardware)
2. [Description](#description)
3. [Running the simulations and reproducing the results](#running-the-simulations-and-reproducing-the-results)
4. [Estimation of Running Time and Memory Consumption](#estimation-of-running-time-and-memory-consumption)

## Requirements and reference hardware

### Building
Suggested compiler version from original paper is `g++-7`.
We compile using `g++ 10.2.1` on Debian 11.

We noticed that compilation often fails with several `clang` versions, we suggest sticking with `gcc`.

### Reference hardware
We ran all of our simulations on a Debian 11 machine with a Intel Xeon Gold 5317 CPU and 128GB ram.
The simulator runs on a single core.
We give a loose bound of required time and resources for each simulation.

## Description

The /src directory contains the packet simulator source code. There is a separate simulator for each network type (i.e., Clos, Opera). The packet simulator is an extension of the htsim Opera simulator (https://github.com/TritonNetworking/opera-sim).

### Repo Structure:
```
/
├─ topologies/ -- network topology files
├─ src/ -- source for the htsim simulator
│  ├─ opera/ -- Varying Expander for Opera(NSDI '20), RotorNet(SIGCOMM '17), modified to add several transport baselines
│  ├─ clos/ -- FatTree network for Opera(NSDI '20) modified to add several transport baselines
├─ run/ -- where simulator runs are initiated, results and plotting scripts are stored
├─ traffic --  where synthetic traffic traces are stored and can be generated
```

## Build instructions:

### Script

From the root directory, you should compile all the executables using:

```
run/compile.sh
```

### Manual

To compile manually, from the root directory:

#### Opera

```
cd src/opera
make
cd datacenter
make
```

#### Clos

```
cd src/clos
make
cd datacenter
make
```

Avoid using `make -j` when compiling the `libhtsim.a` library as it may interfere with the Makefile configuration.
The executables will be built in the `datacenter/` subdirectories.

## Running the simulations and reproducing the results

### Small 8-ToR ring validation

A lightweight fixed-topology Flare experiment is available for validating how
1-, 2-, and 3-hop credit paths affect credit drop, egress pressure, goodput,
packet loss, and queue occupancy. See
[`run/ring_credit_distance/README.md`](run/ring_credit_distance/README.md).

### Fetching the missing files

Traffic traces based on the Hadoop workload are above 100MB, so are provided separately to download [HERE](https://ncs-flare-project.mpi-inf.mpg.de/resources/hadoop_traces.tgz).
Please download them and copy them to `traffic/` before running the experiments for Figure 16.

### Running the simulations via automated script

The most straight-forward way to reproduce a particular figure is to run the `runs.sh` script in the corresponding figure directory (e.g., `Fig14_15/runs.sh`).
By default, the script will run all the required simulator runs sequentially for 0.5s **simulation** time, which is the duration we ran for the paper.
Furthermore, the script will also try finding existing complete simulation outputs before starting a certain simulation, so that you do not repeat existing runs.
After all simulations are done running, figures will be plotted in `figures/`.
You can check all available options using `runs.sh -h`.

As simulations are large scale, some can take up to *several days to finish while requiring 100GB+ ram.*
We provide a rough time estimation and resource requirement for each of them on our hardware if running with the default setting (i.e., no parameters provided to `runs.sh`), included in this [section](#estimation-of-running-time-and-memory-consumption).

### Evaluating our results in reasonable time

What we **strongly recommend** is to run the simulations for a much shorter amount of time then our paper.

The `runs.sh` script can take a `-s simtime` parameter to specify a different running time in seconds (e.g., `./runs.sh -s 0.1`), which will greatly speed up evaluation.
For all traces, setting *simtime* to 0.2 seconds will generally reproduce the trend of the curves in the paper.
Note that Flare's performance will improve relative to other deadlines with longer runtimes.

As we use a static random seed, the simulations are *fully deterministic*.
Another *fast way to verify the correctness of our artifact* is to compare an output file with little running time (e.g., `-s 0.01`) and `diff` your shorter output file against our full results (e.g., `diff shorter.txt <(head -n $(wc -l < shorter.txt) full.txt)` ).
As long as you run our commands with the given input files, you should see that the first part of both output files exactly matches.
We provide raw output files for each of the simulations at the bottom of each [figure table](#estimation-of-running-time-and-memory-consumption), which we suggest you copy to `run/raw`.
We also provide a simple script to automate the diff process in `run/check_diff.sh`.
You can simply run the script with a filename pattern and the script will compare all files matching the pattern in the `run/output` and `run/raw` directories.


### Running the simulations manually

The executable for a particular network+protocol combination is `src/network/datacenter/main_protocol_dynexpTopology`, where network is either `opera` for Opera or `clos` for the FatTree network, and `protocol` is either `xpass`, `bolt`, `dctcp` or `ndp`, (e.g., `src/opera/datacenter/main_bolt_dynexpTopology` to run Opera+Bolt).

#### Parameters

There are numerous parameters to the simulator, some dependent on the network and transport.

##### General parameters

* `-simtime s` run the simulation up to *s* seconds
* `-utiltime ms` sample link utilization and queue size every *ms* milliseconds
* `-topfile f` use topology file `f` (only for Opera)
* `-flowfile f` run flows from flow file `f`
* `-q q` set maximum queue size for all queues to `q` MTU-size packets
* `-cwnd c` initial window `c` for transport (which is also the first-BDP burst)

##### Flare parameters
Flare runs as a parametrized version of ExpressPass.
* `-flare` enables flare
* `-credq q` sets the maximum credit queue size as `q` credit packets
* `-qshaping q` sets the probabilistic admission shaping threshold as `q` credit packets
* `-aeolus q` sets the Aeolus threshold as `q` data packets
* `-tent q` sets the tentative credit queue threshold as `q` credit packets
* `-winit w` sets the initial credit pulling rate to a `w` ratio of the maximum bandwidth (e.g., 1.0, 0.5, 0.25...)
* `-tloss t` sets the target loss for ExpressPass feedback control to a `t` ratio (e.g., 0.05, 0.1...)
* `-fbw w` sets the weight of feedback control changes over the credit pulling rate to a `w` ratio
* `-jita j` sets the alpha jittering threshold to `j` BDP
* `-jitb j` sets the beta jittering threshold to `j` BDP
* `-fbsens` enables using current hop count to more strongly influence feedback control decisions
* `-probfile f` sets the probability file to `f`, see example format in `run/pfun_*`

##### ExpressPass parameters
* `-credq q` sets the maximum credit queue size as `q` credit packets
* `-aeolus q` sets the Aeolus threshold as `q` data packets
* `-winit w` sets the initial credit pulling rate to a `w` ratio of the maximum bandwidth (e.g., 1.0, 0.5, 0.25...)
* `-tloss t` sets the target loss for ExpressPass feedback control to a `t` ratio (e.g., 0.05, 0.1...)

##### NDP parameters
* `-pullrate p` sets rate of pulling to `p` of the maximum link bandwidth

##### HbH parameters
* `-hbhd p` sets HbH D invariant to `p` packets
* `-hbhp p` sets HbH P invariant to `p` packets

#### Reading the output

##### FCT
Flow completion time of flows is reported as:

`FCT <src> <dst> <flowsize> <fct> <start_time> <reorder_info> <rtx_info> <buffering_info> <max_hops_taken> <last_hops_taken> <total_hops_taken>`

##### Utilization
Global utilization is periodically reported as:

`Util <downlink_util> <uplink_util> <current_simtime>`

##### Queue size
Queue size is periodically reported for all queues as:

`Queue <tor> <port> <max_queuesize_in_sample> <queuesize>`

For Flare:

`Queue <tor> <port> <max_queuesize_in_sample> <queuesize> <> <credit_queuesize> DISTR <HOPS:RATIO>`

##### Checking progress

To check how far a simulation is,  you may want to execute the following bash command in the `/run/output` folder, and substitute ``$SIMULATOR_OUTPUT`` with the output name to examine the simulation progress, when the script is still running: 

``grep FCT $SIMULATOR_OUTPUT | tail | awk '{print $5+$6;}'``

The output would be the time elapsed from the start of the simulation in milliseconds.
Each simulation should finish when this output approaches the target end time.

## Estimation of Running Time and Memory Consumption
We provide *rough* estimates of the memory cost/time for the full runs of all experiments (i.e., to exactly reproduce the paper results).
Runtime is based on our server with Intel(R) Xeon(R) Gold 5317 CPU and 128GB memory.

Figure 10
| Run Name                                                           | Time   | RAM    |
|--------------------------------------------------------------------|--------|--------|
| flare_fairshare.txt, SIMTIME 7.0sec                                | 20min  | < 1GB  |

Figure 14 and 15
| Run Name                                                           | Time   | RAM    |
|--------------------------------------------------------------------|--------|--------|
| flare55us_ws30perc_0.5s_60c30t_40a_a4b16_fbw12.txt, SIMTIME 0.5sec | 16+hrs | ~80GB  |
| flare15us_ws30perc_0.3s_16c8t_8a_a4b16_fbw12.txt, SIMTIME 0.5sec   | 16+hrs | ~80GB  |
| bolt55us_ws30perc_0.5s_1q.txt, SIMTIME 0.5sec                      | 8+hrs  | ~8GB   |
| bolt15us_ws30perc_0.3s_1q.txt, SIMTIME 0.5sec                      | 8+hrs  | ~12GB  |
| xpass55us_ws30perc_0.3s_80c_40a_05winit.txt, SIMTIME 0.5sec        | 12+hrs | ~32GB  |
| xpass15us_ws30perc_0.3s_20c_8a_05winit.txt, SIMTIME 0.5sec         | 12+hrs | ~32GB  |
| hbh55us_ws30perc_0.3s_10P_5D.txt, SIMTIME 0.5sec            | 48+hrs | ~16GB  |
| hbh15us_ws30perc_0.3s_6P_2D.txt, SIMTIME 0.5sec             | 48+hrs | ~16GB  |
| tdtcp55us_ws30perc_0.3s_40ecn_nosyn_10cwnd.txt, SIMTIME 0.5sec     | 6+hrs  | ~12GB  |
| ndp55us_ws30perc_0.3s_80q.txt, SIMTIME 0.5sec                      | 16+hrs | ~128GB |
| [Raw files](https://ncs-flare-project.mpi-inf.mpg.de/resources/Fig14_15_raw.tgz)     |

Figure 16
| Run Name                                                           | Time   | RAM    |
|--------------------------------------------------------------------|--------|--------|
| flare55us_had30perc_0.5s_60c30t_40a_a4b16_fbw12.txt, SIMTIME 0.5sec| 24+hrs | ~100GB |
| flare15us_had30perc_0.3s_16c8t_8a_a4b16_fbw12.txt, SIMTIME 0.5sec  | 24+hrs | ~100GB |
| bolt55us_had30perc_0.5s_1q.txt, SIMTIME 0.5sec                     | 12+hrs | ~32GB  |
| bolt15us_had30perc_0.3s_1q.txt, SIMTIME 0.5sec                     | 12+hrs | ~32GB  |
| xpass55us_had30perc_0.3s_80c_40a_05winit.txt, SIMTIME 0.5sec       | 12+hrs | ~64GB  |
| xpass15us_had30perc_0.3s_20c_8a_05winit.txt, SIMTIME 0.5sec        | 12+hrs | ~64GB  |
| hbh55us_had30perc_0.3s_10P_5D.txt, SIMTIME 0.5sec           | 72+hrs | ~16GB  |
| hbh15us_had30perc_0.3s_6P_2D.txt, SIMTIME 0.5sec            | 72+hrs | ~16GB  |
| tdtcp55us_had30perc_0.3s_40ecn_nosyn_10cwnd.txt, SIMTIME 0.5sec    | 8+hrs  | ~32GB  |
| [Raw files](https://ncs-flare-project.mpi-inf.mpg.de/resources/Fig16_raw.tgz)        |

Figure 17
| Run Name                                                           | Time   | RAM    |
|--------------------------------------------------------------------|--------|--------|
| flare55us_rpc30perc_0.5s_60c30t_40a_a4b16_fbw12.txt, SIMTIME 0.5sec| 16+hrs | ~80GB  |
| flare15us_rpc30perc_0.3s_16c8t_8a_a4b16_fbw12.txt, SIMTIME 0.5sec  | 16+hrs | ~80GB  |
| bolt55us_rpc30perc_0.5s_1q.txt, SIMTIME 0.5sec                     | 8+hrs  | ~8GB   |
| bolt15us_rpc30perc_0.3s_1q.txt, SIMTIME 0.5sec                     | 8+hrs  | ~12GB  |
| xpass55us_rpc30perc_0.3s_80c_40a_05winit.txt, SIMTIME 0.5sec       | 12+hrs | ~32GB  |
| xpass15us_rpc30perc_0.3s_20c_8a_05winit.txt, SIMTIME 0.5sec        | 12+hrs | ~32GB  |
| hbh55us_rpc30perc_0.3s_10P_5D.txt, SIMTIME 0.5sec           | 48+hrs | ~16GB  |
| hbh15us_rpc30perc_0.3s_6P_2D.txt, SIMTIME 0.5sec            | 48+hrs | ~16GB  |
| tdtcp55us_rpc30perc_0.3s_40ecn_nosyn_10cwnd.txt, SIMTIME 0.5sec    | 6+hrs  | ~12GB  |
| [Raw files](https://ncs-flare-project.mpi-inf.mpg.de/resources/Fig17_raw.tgz)        |

Figure 18 and 19
| Run Name                                                           | Time   | RAM    |
|--------------------------------------------------------------------|--------|--------|
| flare55us_ws30perc_0.5s_60c30t_40a_a4b16_fbw12.txt, SIMTIME 0.5sec | 12+hrs | ~80GB  |
| flare55us_ws35perc_0.5s_60c30t_40a_a4b16_fbw12.txt, SIMTIME 0.5sec | 24+hrs | ~128GB |
| clos_bolt_ws30perc_0.5s_1q.txt, SIMTIME 0.5sec                     | 10+hrs | ~12GB  |
| clos_bolt_ws35perc_0.5s_1q.txt, SIMTIME 0.5sec                     | 10+hrs | ~16GB  |
| [Raw files](https://ncs-flare-project.mpi-inf.mpg.de/resources/Fig18_19_raw.tgz)     |
