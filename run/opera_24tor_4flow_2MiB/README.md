# 24-ToR lighter receiver-NIC priority experiment

This experiment keeps `topologies/opera_24tor_4host_55us.txt` and compares
FIFO against the shared receiver-NIC Credit queues with WRR weights `8:2:1`.
It is intentionally lighter than the earlier 8-flow x 4-MiB workload.

## Default workload

```text
24 ToRs, 4 Hosts/ToR, 96 Hosts
4 outgoing and 4 incoming flows per Host
2 MiB per flow, 8 MiB sourced and received per Host
384 flows, 768 MiB total data
cwnd = 4 packets
starts spread across 8 x 55 us superslices
20 ms simulated time
```

The ToR offsets are `[3, 9, 15, 21]`, so every selected direction has its
reverse direction. Each superslice starts exactly 48 flows. Every ToR starts
two outgoing and two incoming flows per superslice, every receiver Host gets
at most one new flow in a superslice, and every timestamp stays inside the
54 us optical active window.

Compared with the previous default, this halves the flow count, halves each
flow size, reduces total offered bytes by 75%, and expands the release window
from two to eight superslices. Queue capacities and Flare feedback parameters
remain unchanged for a controlled comparison.

## Run

Build and run FIFO plus `8:2:1` priority:

```bash
bash run/opera_24tor_4flow_2MiB/run.sh --build
```

Reuse an existing executable:

```bash
bash run/opera_24tor_4flow_2MiB/run.sh \
  --no-build \
  --scheduler both \
  --rxhop-weights 8:2:1 \
  --simtime 0.02 \
  --output run/opera_24tor_4flow_2MiB/results_4x2MiB_stagger8_w821
```

For a still lighter release rate without changing bytes or the traffic matrix:

```bash
bash run/opera_24tor_4flow_2MiB/run.sh \
  --no-build \
  --scheduler both \
  --start-superslices 16 \
  --output run/opera_24tor_4flow_2MiB/results_4x2MiB_stagger16_w821
```

Each case produces `summary.csv`, `per_flow.csv`, `per_queue.csv`,
`per_priority_queue.csv`, `per_tor.csv`, and `per_credit_hop.csv`. The output
root also contains the shared workload and `comparison.csv`.

## Tests

```bash
python3 -m unittest discover \
  -s run/opera_24tor_4flow_2MiB \
  -p 'test_*.py'
```
