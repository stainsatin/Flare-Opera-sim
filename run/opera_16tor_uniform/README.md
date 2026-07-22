# Balanced 64-flow experiment on the 16-ToR Opera topology

This experiment provides one finite inter-ToR flow per host. Its default
topology is `topologies/opera_16tor_4host_55us.txt`, whose superslice is:

```text
53.38 us epsilon + 0.62 us delta + 1.0 us reconfiguration = 55 us
```

The 55 us topology and the retained 14.5 us control topology use the same
rotor schedule and strictly symmetric routes. Only their timing differs.

## Traffic matrix

The default workload contains 64 flows. Each host sources exactly one flow and
receives exactly one flow. Each ToR sources four equal-size flows and receives
four equal-size flows.

For source ToR `t`, host lane `h` sends to the same host lane in:

```text
destination ToR = (t + offset[h]) mod 16
offset = [1, 5, 9, 13]
```

The selected 64 ordered ToR pairs each carry one 32 MiB flow. This balances
per-ToR ingress and egress bytes. With only four hosts per ToR, a single
one-flow-per-host round cannot cover all 15 remote destination ToRs.

The recommended `cycle_spread` start mode places four flow starts in each of
the 16 superslices of one 880 us topology cycle. Every flow starts 1 us into
its assigned epsilon phase. This avoids a single synchronized first-window
burst and samples every logical topology equally. The flows are large enough
to overlap for many topology cycles.

Use `--start-mode synchronized` to start all 64 flows together for a burst
sensitivity control.

## Run

The script builds the dynamic Opera executable automatically when it is absent:

```bash
bash run/opera_16tor_uniform/run.sh
```

Explicit build and a custom output directory:

```bash
bash run/opera_16tor_uniform/run.sh \
  --build \
  --flow-size-mib 32 \
  --simtime 0.05 \
  --output run/opera_16tor_uniform/results_32MiB_cycle_spread
```

Run the otherwise identical 14.5 us timing control with:

```bash
bash run/opera_16tor_uniform/run.sh \
  --no-build \
  --topology topologies/opera_16tor_4host_15us.txt \
  --output run/opera_16tor_uniform/results_32MiB_cycle_spread_15us
```

`run.sh` reads the timing header from the selected topology and passes the
duration to both flow generation and result analysis. There is no separate
hard-coded 14.5/55 us value to keep synchronized.

`--build` performs a clean rebuild of both `src/opera` and
`src/opera/datacenter`. This is necessary because the upstream Makefiles do not
track every cross-directory header dependency; an incremental build after a
`Queue` layout change can link ABI-incompatible object files and corrupt the
heap.

Synchronized comparison:

```bash
bash run/opera_16tor_uniform/run.sh \
  --start-mode synchronized \
  --output run/opera_16tor_uniform/results_32MiB_synchronized
```

The default Flare queue parameters match the original 15 us Opera experiment:

```text
data queue       600 packets
credit queue      16 credits
shaping threshold  8 credits
Aeolus allowance   8 packets
tentative limit     2 credits
```

`run/pfun_exp2.txt` now includes hops 8 and 9 because superslices 12-14 in the
small topology can have eight-hop routes and hop jitter may add one shaping
distance level. The original Flare `hopJitter()` five-hop assertion is also
generalized to the active topology size.

## Outputs

The result directory contains:

```text
uniform.log          simulator stdout and final statistics
uniform.htsim        htsim binary log
command.txt          exact simulator command
traffic/uniform.htsim
summary.csv          experiment-wide metrics
per_flow.csv         FCT, goodput, path hops, and credit outcomes per flow
per_queue.csv        host and ToR queue metrics per port
per_tor.csv          per-ToR ingress/egress, FCT, credit drop, and queue metrics
```

Important summary metrics include:

```text
completion ratio and incomplete flow count
mean / median / p95 / p99 / max FCT
active-makespan aggregate throughput
periodically sampled fabric goodput and input rate
flow-goodput and ToR-goodput Jain fairness
mean / min / max dynamic credit path hops
end-to-end credit drop and delivery ratios
endpoint, ToR-queue, topology-clipping, and wrong-destination credit drops
probabilistic shaping checks and admission ratio
credit waste in consumed link-hops
data queue drops, topology data clipping, and peak queue occupancy
```

`simulation_throughput_gbps` divides completed bytes by the full configured
simulation time and includes idle drain time. `active_makespan_throughput_gbps`
divides completed bytes by the interval from the first flow start to the last
flow completion. `mean_sampled_goodput_gbps` is derived from periodic bytes
delivered to host downlinks while flows are active.

## Analyze an existing run

```bash
python3 run/opera_16tor_uniform/analyze.py \
  run/opera_16tor_uniform/results_32MiB_cycle_spread \
  --simtime 0.05 \
  --superslice-ns 55000
```

## Tests

```bash
python3 -m unittest discover -s run/opera_16tor_uniform -p 'test_*.py'
```
