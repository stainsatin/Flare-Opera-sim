# 108-ToR receiver-hop-priority experiment

This experiment compares the original per-Flow Credit generation with a
host-level Flow-aware generator (`-rxhopprio`) on the paper reproduction's
native Opera topology. Before a Credit Packet exists, the receiver NIC selects
one pending Flow by `(current_path_hops, request_sequence)` and lets only that
Flow generate Credits. The selected Flow keeps a configurable quantum (16
Credits by default), then all pending Flows compete again. Generated Credits
still use the original NIC FIFO.

## Default workload

```text
108 ToRs, 6 hosts/ToR, 648 hosts
8 outgoing and 8 incoming flows per host
4 MiB per flow, 32 MiB sourced and received per host
5,184 flows, 20.25 GiB total data
cwnd = 4 packets
50 ms simulated time
```

Each host uses the same host lane at eight remote ToRs. The destination ToR
offsets are reverse-paired and spread around the ring:

```text
[7, 20, 34, 47, 61, 74, 88, 101]
```

Thus every selected directed ToR pair carries six flows, and every ToR sends
and receives 48 flows. The traffic matrix is byte-balanced at Host and ToR
granularity.

The 5,184 starts are split evenly across the first two 54.5 us superslices.
Each receiver gets four new flows per superslice. Within each superslice, the
2,592 starts have distinct timestamps spread across the 44.5 us optical active
window; none starts during the 10 us reconfiguration interval. Both scheduler
cases reuse the exact same generated trace.

## Run

Clean-build once after pulling the `-rxhopprio` implementation, then run both
cases:

```bash
bash run/opera_108tor_8flow_4MiB/run.sh --build
```

Later runs can reuse the executable:

```bash
bash run/opera_108tor_8flow_4MiB/run.sh \
  --no-build \
  --scheduler both \
  --rxhop-quantum 16 \
  --simtime 0.05 \
  --output run/opera_108tor_8flow_4MiB/results_8x4MiB_hostflow_stagger2_q16
```

For a quick setup check, run only FIFO with smaller flows. This is not the
main experiment:

```bash
bash run/opera_108tor_8flow_4MiB/run.sh \
  --no-build \
  --scheduler fifo \
  --flow-size-mib 0.01 \
  --simtime 0.002 \
  --output run/opera_108tor_8flow_4MiB/results_smoke
```

The root output contains the shared workload and `comparison.csv`. Each of
`fifo/` and `rxhopprio/` contains `summary.csv`, `per_flow.csv`,
`per_queue.csv`, `per_tor.csv`, `flow_credit_scheduler.csv`, simulator logs,
`per_credit_hop.csv`, and the exact command used. The FIFO scheduler CSV contains only its header;
the `rxhopprio` file records every pre-generation Flow selection.

## Tests

```bash
python3 -m unittest discover \
  -s run/opera_108tor_8flow_4MiB \
  -p 'test_*.py'
```
