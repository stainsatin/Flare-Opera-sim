# 8-ToR ring credit-distance experiment

This experiment isolates how the Flare credit path length changes shared egress
pressure. The fixed topology has eight ToRs, one 100 Gbit/s host per ToR, and
two directed ToR links per switch forming a bidirectional ring. The ToR-to-ToR
propagation delay is 500 ns per hop.

Each run installs eight long-lived flows:

```text
source n -> destination (n + k) mod 8, for every n in [0, 7]
```

The separate `k=1`, `k=2`, and `k=4` runs therefore use credit paths of 1, 2,
and 4 ToR hops. Routes at distance four are paired so the route in the opposite
direction reverses the original path.

## Run on the remote Linux server

From the repository root:

```bash
bash run/ring_credit_distance/run.sh
```

The script builds the Opera library and `htsim_xpass_graphTopology` when the
binary is missing, then runs all three distances sequentially. The repository
recommends GCC 7 or GCC 10 on Debian Linux. Do not use parallel `make` for the
Opera library.

Useful shorter or parameterized runs:

```bash
# Fast smoke test
bash run/ring_credit_distance/run.sh --simtime 0.002

# Run one distance with a smaller credit queue
bash run/ring_credit_distance/run.sh --distance 4 --credq 8

# Enable Flare's hop-aware admission shaping
bash run/ring_credit_distance/run.sh --probfile run/pfun_exp2.txt --qshaping 8

# Reuse an existing binary and choose a separate result directory
bash run/ring_credit_distance/run.sh --no-build --output /tmp/ring-results
```

Use `--help` for all options. The default long flow size is 1 TB, so these runs
measure steady-state throughput rather than flow completion time.

## Outputs

The default output directory is `run/ring_credit_distance/results/`:

- `k1.log`, `k2.log`, `k4.log`: simulator standard output and final credit counters.
- `k1.htsim`, `k2.htsim`, `k4.htsim`: native htsim log files.
- `summary.csv`: one row per distance with credit drops, credit pressure,
  aggregate goodput, data queue drops, topology losses, and queue metrics.
- `per_queue.csv`: host NIC and ToR-port credit counters for hotspot analysis.

The final simulator records use this stable format:

```text
CreditStats scope id port received transmitted queued max_queued dropped overflow timeout shaping tentative
DataQueueStats scope id port dropped
```

`summary.csv` uses all queue drop events divided by credits generated at the
receiver NIC as `credit_drop_ratio`. A credit is dropped at most once, so this
is an end-to-end loss ratio. `uplink_arrivals_per_generated` is the observed
number of ToR egress arrivals per generated credit; without early loss it
approaches `k`. `mean_goodput_gbps` is based on delivered host-downlink bytes
after excluding the first 20% of utilization samples. `data_queue_drops` is the
sum of actual data drops at host and ToR queues. `topology_losses` retains the
simulator's separate wrong-destination or unavailable-link counter.

By default, `qshaping` and the tentative threshold equal the credit queue
capacity. Since overflow is checked before admission shaping, this configuration
isolates queue overflow and timeout instead of Flare's probabilistic shaping.
The output still separates all four causes, making it straightforward to enable
and study shaping later with `--probfile` plus a smaller `--qshaping` value.

To re-run analysis after copying or editing logs:

```bash
python3 run/ring_credit_distance/analyze.py run/ring_credit_distance/results
```
