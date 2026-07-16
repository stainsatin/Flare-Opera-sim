# 8-ToR ring finite-flow FCT experiment

This experiment complements `run/ring_credit_distance/` with finite flows. It
reports credit drops, completed-workload throughput, and flow completion time
for the same `k=1`, `k=2`, and `k=3` ring paths.

## Default workload

Each arrival wave starts eight rotationally symmetric flows:

```text
source n -> destination (n + k) mod 8, for every n in [0, 7]
```

The default workload uses 20 waves, 1 MB per flow, and 0.1 ms between waves.
That is 160 flows and 640 Gbit/s of offered load during the 2 ms arrival
window. The 20 ms simulation leaves time for the backlog to drain in all three
distance cases.

## Run on the remote Linux server

From the repository root:

```bash
bash run/ring_credit_fct/run.sh
```

The script builds the graph-topology simulator, generates deterministic traces
under the selected output directory, runs all distances, and writes the CSV
summaries. Useful variants:

```bash
# Keep an overflow-only control in a separate directory
bash run/ring_credit_fct/run.sh --no-build --no-shaping \
  --output run/ring_credit_fct/results_no_shaping

# Change the short-flow workload
bash run/ring_credit_fct/run.sh --no-build \
  --flow-size 2000000 --rounds 30 --interval-ns 200000 \
  --simtime 0.04 --output run/ring_credit_fct/results_2mb
```

If `incomplete_flows` is nonzero, increase `--simtime` and rerun. Changing the
flow size, rounds, or interval changes the offered load, so use identical
workload arguments for comparisons.

## Outputs

The default output directory is `run/ring_credit_fct/results/`:

- `summary.csv`: completion ratio, workload throughput, mean/median/P95/P99/max
  FCT, and all credit/data drop counters.
- `per_flow.csv`: one row per completed flow with start, finish, FCT, and
  per-flow goodput.
- `per_queue.csv`: host NIC and ToR-port credit counters.
- `traffic/k1.htsim`, `traffic/k2.htsim`, `traffic/k3.htsim`: exact generated
  workloads used by the run.
- `k1.log`, `k2.log`, `k3.log`: simulator stdout including `FCT` records.

`workload_throughput_gbps` is completed bytes divided by the interval from the
first flow start to the last observed completion. It is the primary throughput
metric for this finite workload. `sim_sampled_goodput_gbps` averages utilization
over the full simulation and therefore includes the idle tail after all flows
finish.

FCT is measured from each scheduled flow start until cumulative acknowledgement
of its final data packet. Credit drops do not directly mark a flow failed; they
increase FCT and reduce throughput by delaying useful data transmission.
