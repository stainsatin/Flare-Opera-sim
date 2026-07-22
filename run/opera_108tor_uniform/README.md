# Balanced 648-flow experiment on the paper's 108-ToR Opera topology

This experiment scales the one-flow-per-host workload to the native Opera
topology used by the paper reproduction:

```text
108 ToRs
6 hosts and 6 rotor uplinks per ToR
648 hosts and 648 flows
default topology: topologies/dynexp_55us_symm.txt
actual superslice: 43.88 us + 0.62 us + 10 us = 54.5 us
full 108-superslice cycle: 5.886 ms
```

## Traffic matrix

Every host sources one 32 MiB flow and receives one 32 MiB flow. Every ToR
therefore sends and receives 192 MiB. Host lane `l` at source ToR `t` sends to
the same lane at:

```text
destination ToR = (t + offset[l]) mod 108
offset = [1, 19, 37, 55, 73, 91]
```

The resulting 648 ordered ToR pairs each carry one equal-size flow. Six hosts
per ToR cannot cover all 107 remote ToRs in one round, but total ingress and
egress bytes are exactly balanced at every ToR.

In `cycle_spread` mode:

```text
start superslice = (source ToR + 9 * host lane) mod 108
```

Each superslice starts exactly six flows, one per host lane. Those six flows
have six different source ToRs and six different destination ToRs. This avoids
the per-superslice single-receiver concentration present in the earlier small
workload. `synchronized` remains available as a burst control.

## Paper-compatible defaults

The default queue settings match the paper's native 55 us Flare command:

```text
cwnd=40, data queue=600
credit queue=60, shaping threshold=30
Aeolus allowance=40, tentative threshold=4
winit=1.0, target loss=0.1, feedback weight=1.2
```

The topology filename rounds the duration to 55 us; the run script reads the
54.5 us value from the file header and passes it consistently to traffic
generation and analysis.

## Run

The input-throughput counter initialization changed with this experiment, so
clean-build once after updating the repository:

```bash
bash run/opera_108tor_uniform/run.sh \
  --build \
  --output run/opera_108tor_uniform/results_32MiB_cycle_spread_55us
```

Subsequent runs can reuse the executable:

```bash
bash run/opera_108tor_uniform/run.sh \
  --no-build \
  --start-mode synchronized \
  --output run/opera_108tor_uniform/results_32MiB_synchronized_55us
```

A smaller smoke run before the 20.25 GiB main workload:

```bash
bash run/opera_108tor_uniform/run.sh \
  --no-build \
  --flow-size-mib 1 \
  --simtime 0.02 \
  --output run/opera_108tor_uniform/results_smoke_1MiB
```

The result directory contains `summary.csv`, `per_flow.csv`, `per_queue.csv`,
and `per_tor.csv` with the same credit, FCT, throughput, fairness, topology
error, and queue metrics as the 16-ToR experiment. `per_tor.csv` has 108 rows.

## Tests

```bash
python3 -m unittest discover -s run/opera_108tor_uniform -p 'test_*.py'
python3 -m unittest discover -s run/opera_16tor_uniform -p 'test_*.py'
```
