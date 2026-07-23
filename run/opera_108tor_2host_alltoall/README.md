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

## Sparse staggered traffic mode

The full 107-destination matrix is intentionally a heavy stress case. For a
moderate-load comparison that still gives every receiver several competing
flows, use the sparse preset:

```bash
bash run/opera_108tor_2host_alltoall/run_sparse.sh --build
```

Its defaults are:

```text
64 remote destination ToRs per host
0.5 MiB per flow
13,824 flows total
6.750 GiB total offered data
192 new flows per superslice
72 superslices (3.924 ms) of staggered starts
about 68.4 Gbps offered release rate per host
```

The 64 destination offsets are deterministic, evenly distributed, and paired
with their reverse offsets. Consequently, every host sources and receives 64
flows, every ToR sources and receives 128 flows, and every selected directed
ToR pair carries one flow per host lane.

`staggered` phases flows by receiver. Each receiver sees at most one new flow
in any superslice. The 192 global starts in a superslice also use distinct
timestamps within the 44.5 us active optical window, rather than arriving as
one EventList burst. This removes the old offset-to-start-slice coupling and
avoids placing new starts in the 10 us reconfiguration interval.

The preset runs FIFO and `rxhopprio` with the same trace and writes to:

```text
run/opera_108tor_2host_alltoall/results_sparse_f64_512KiB_stagger72
```

Arguments after the preset override its defaults. For example:

```bash
bash run/opera_108tor_2host_alltoall/run_sparse.sh \
  --no-build \
  --fanout 16 \
  --flow-size-mib 0.25 \
  --output run/opera_108tor_2host_alltoall/results_sparse_f16_256KiB
```

The general `run.sh` accepts `--fanout 1..107`, a configurable
`--spread-superslices` window, and the `cycle_spread`, `staggered`, and
`synchronized` start modes. The original defaults remain `fanout=107`,
`flow-size=1 MiB`, and `cycle_spread` so previous commands remain reproducible.

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
bash -n run/opera_108tor_2host_alltoall/run_sparse.sh
```
