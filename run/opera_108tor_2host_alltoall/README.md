# 108-ToR, two-host all-remote-ToR experiment

This experiment keeps the paper reproduction's complete 108-ToR Opera rotor
schedule and six optical uplinks per ToR, but reduces the host-facing side to
two hosts per ToR:

```text
108 ToRs
2 hosts/downlinks per ToR
6 optical uplinks per ToR
216 hosts
```

`build_topology.py` derives the topology from
`topologies/dynexp_55us_symm.txt`. It preserves every adjacency row and every
route, while shifting route ports from the original `6..11` uplink range to
the new `2..7` range. The generated topology is excluded from Git.

The topology loader now reads adjacency width as `ToRs * uplinks`, rather than
using the unrelated host count. The native 648-host topology is unchanged
because both values were 648 there; the correction permits two host downlinks
and six uplinks to coexist.

## Traffic matrix

Each host sends one flow to the same host lane in every other ToR:

```text
destination ToR = (source ToR + offset) mod 108
offset = 1..107
destination lane = source lane
```

The resulting matrix has:

```text
107 outgoing and 107 incoming flows per host
214 outgoing and 214 incoming flows per ToR
2 flows per ordered pair of distinct ToRs
23,112 flows total
```

The default flow size is 1 MiB, for 22.57 GiB of offered data.

The recommended `cycle_spread` mode starts offset `k` in superslice `k-1`.
For superslices 0 through 106, every host starts one flow and every receiver
gets one new flow. This creates progressive overlap at each receiver NIC while
avoiding a 23,112-flow instantaneous burst. Superslice 107 has no new starts.

`synchronized` starts all flows together. It is a burst stress control and is
expected to be dominated by receiver NIC overflow/tentative drops, so it
should not be the primary priority comparison.

## Main FIFO versus rxhopprio run

Clean-build once on the Linux server and run both cases:

```bash
bash run/opera_108tor_2host_alltoall/run.sh \
  --build \
  --scheduler both \
  --flow-size-mib 1 \
  --simtime 0.1 \
  --start-mode cycle_spread \
  --output run/opera_108tor_2host_alltoall/results_1MiB_cycle_spread
```

The script first runs FIFO, then starts a new process with `-rxhopprio`. Both
use the exact same generated traffic file. Later runs can use `--no-build`.

Small pipeline smoke test:

```bash
bash run/opera_108tor_2host_alltoall/run.sh \
  --no-build \
  --scheduler both \
  --flow-size-mib 0.0625 \
  --simtime 0.02 \
  --output run/opera_108tor_2host_alltoall/results_smoke
```

Run only one scheduler:

```bash
bash run/opera_108tor_2host_alltoall/run.sh \
  --no-build \
  --scheduler rxhopprio \
  --output run/opera_108tor_2host_alltoall/results_prio_only
```

## Queue defaults

The default parameters retain the paper's native 108-ToR configuration:

```text
cwnd=40
data queue=600 packets
credit queue=60 credits
shaping threshold=30 credits
Aeolus allowance=40 packets
tentative threshold=4 credits
```

There are many flows per receiver, so endpoint Credit drops can still be
large. That is a real consequence of independent per-flow Credit generation.
The comparison should therefore report endpoint drops separately from ToR
path drops, and should not interpret total Credit drop alone as a fabric
effect.

## Outputs

The root output contains:

```text
workload/uniform.htsim       shared 23,112-flow trace
fifo/summary.csv
fifo/per_flow.csv
fifo/per_queue.csv
fifo/per_tor.csv
fifo/uniform.log
fifo/command.txt
rxhopprio/summary.csv
rxhopprio/per_flow.csv
rxhopprio/per_queue.csv
rxhopprio/per_tor.csv
rxhopprio/uniform.log
rxhopprio/command.txt
comparison.csv               FIFO versus prio selected metrics
```

The primary comparison metrics are completion ratio, mean/p95/p99 FCT,
throughput, Jain fairness, endpoint Credit drops, ToR Credit drops,
path-conditional Credit drop probability, Credit waste, data drops, and peak
queue occupancy.

## Tests

```bash
python3 -m unittest discover \
  -s run/opera_108tor_2host_alltoall \
  -p 'test_*.py' -v

bash -n run/opera_108tor_2host_alltoall/run.sh
```
