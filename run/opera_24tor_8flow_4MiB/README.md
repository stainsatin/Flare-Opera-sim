# 24-ToR receiver-hop-priority experiment

This experiment repeats the 8-flow-per-host FIFO versus `-rxhopprio`
comparison on `topologies/opera_24tor_4host_55us.txt`.
When `-rxhopprio` is enabled, the selected Flow keeps a configurable Credit
quantum (16 Credits by default) before all pending Flows compete again.

## Default workload

```text
24 ToRs, 4 hosts/ToR, 96 hosts
8 outgoing and 8 incoming flows per host
4 MiB per flow, 32 MiB sourced and received per host
768 flows, 3 GiB total data
cwnd = 4 packets
50 ms simulated time
```

Each host communicates with the same host lane at eight remote ToRs. The
destination offsets are reverse-paired and spread around the 24-ToR ring:

```text
[2, 5, 7, 10, 14, 17, 19, 22]
```

Every selected directed ToR pair carries four flows. Every ToR sources and
receives 32 flows, so the workload is balanced at Host and ToR granularity.

The 768 starts are divided evenly across the first two 55 us superslices.
Every receiver gets four new flows per superslice. All 384 timestamps in each
superslice are distinct and remain inside the 54 us optical active window.

## Run

Build and run both comparison cases:

```bash
bash run/opera_24tor_8flow_4MiB/run.sh --build
```

Reuse an existing executable on subsequent runs:

```bash
bash run/opera_24tor_8flow_4MiB/run.sh \
  --no-build \
  --scheduler both \
  --rxhop-quantum 16 \
  --simtime 0.05 \
  --output run/opera_24tor_8flow_4MiB/results_8x4MiB_hostflow_stagger2_q16
```

For a quick setup check, run only FIFO with small flows:

```bash
bash run/opera_24tor_8flow_4MiB/run.sh \
  --no-build \
  --scheduler fifo \
  --flow-size-mib 0.01 \
  --simtime 0.002 \
  --output run/opera_24tor_8flow_4MiB/results_smoke
```

The two cases reuse the exact same traffic trace. The output root contains
`comparison.csv`; each case contains `summary.csv`, `per_flow.csv`,
`per_queue.csv`, `per_tor.csv`, `flow_credit_scheduler.csv`, raw simulator
logs, and the exact command used. `per_credit_hop.csv` reports generated,
NIC-admitted, and Sender-delivered Credits plus delivery probability by hop.

## Tests

```bash
python3 -m unittest discover \
  -s run/opera_24tor_8flow_4MiB \
  -p 'test_*.py'
```
