# 24-ToR RCDCP experiment

This experiment evaluates Regular-Centric Dynamic Credit Priority (RCDCP) on
`topologies/opera_24tor_4host_55us.txt`. All modes share one physical `credq`,
the same traffic trace, and the same Flare parameters.

| Mode | Tentative threshold occupancy | NIC service | Push-out |
|---|---|---|---|
| `fifo_original` | Original global occupancy | FIFO | Off |
| `fifo_global` | Explicit global occupancy | FIFO | Off |
| `wrr421` | Global occupancy | Smooth WRR `4:2:1` | Off |
| `wrr821` | Global occupancy | Smooth WRR `8:2:1` | Off |

`fifo_original` and `fifo_global` are expected to match: original FIFO already
uses total shared occupancy. This pair is a Phase-1 regression check. The WRR
modes add dynamic High/Medium/Low service after that check. Pending Credits are
reclassified whenever `time_to_slice()` changes; an enqueue sequence preserves
FIFO order inside the resulting class.

## Workload

```text
24 ToRs, 4 Hosts/ToR, 96 Hosts
4 outgoing and 4 incoming flows per Host
2 MiB per flow, 384 flows total
cwnd = 4 packets
starts spread across 8 x 55 us superslices
20 ms simulated time (more than 15 complete 1.32 ms cycles)
```

The analyzer reports `flows_spanning_3_cycles_ratio`. Extending `simtime` does
not itself lengthen a completed 2-MiB flow, so this column must be checked rather
than assuming every flow spans three cycles.

## Run

Build once and run all four modes for seeds 1 through 5:

```bash
bash run/opera_24tor_4flow_2MiB/run.sh --build
```

Run phases separately:

```bash
bash run/opera_24tor_4flow_2MiB/run.sh --no-build --mode fifo_original
bash run/opera_24tor_4flow_2MiB/run.sh --no-build --mode fifo_global
bash run/opera_24tor_4flow_2MiB/run.sh --no-build --mode wrr421
bash run/opera_24tor_4flow_2MiB/run.sh --no-build --mode wrr821
```

Use a smaller seed subset while checking a server build:

```bash
bash run/opera_24tor_4flow_2MiB/run.sh \
  --no-build --mode all --seeds 1 \
  --output run/opera_24tor_4flow_2MiB/results_rcdcp_smoke
```

The full run writes `multi_seed_summary.csv` with every seed plus mean,
standard deviation, and 95% confidence interval. Each case also writes:

- `summary.csv`, `per_flow.csv`, `per_queue.csv`, `per_tor.csv`
- `credit_lifecycle.csv`, `per_credit_hop.csv`
- `per_nic_credit_slot.csv`, `tentative_admission.csv`
- `per_tor_uplink_credit.csv`, `per_rotor_credit.csv`

The primary decision metrics are `regular_delivered_per_nic_slot` and
`regular_delivered_per_credit_hop`. Mean hop count is supporting evidence, not
the success criterion.

In the new lifecycle counters, `admitted` means accepted into the receiver NIC
buffer and `sent` means actually serialized out of that NIC. Therefore
`regular_delivered_per_sent` is the pure post-NIC path success ratio, while the
required `regular_delivered_per_admitted` also includes later endpoint timeout
or push-out losses.

Detailed slot traces can be large. `--no-slot-trace` leaves the two event-level
CSVs with headers only while retaining aggregate lifecycle and queue metrics.

## FIFO Credit time series

To validate whether Regular loss is followed by a larger Tentative fraction,
run the original FIFO mode with compact per-superslice statistics:

```bash
bash run/opera_24tor_4flow_2MiB/run.sh \
  --no-build --mode fifo_original --seeds 1,2,3,4,5 \
  --credit-timeseries --no-slot-trace \
  --output run/opera_24tor_4flow_2MiB/results_fifo_timeseries
```

`credit_timeseries.csv` contains one row per active 55-us superslice. It
reports the generated Tentative share, the generation-weighted
`_crt_rate/_max_rate`, endpoint/path Drop events, and the exact Regular loss
windows consumed by `feedbackControl2()`. Each Credit also retains its
generation superslice, so the `*_cohort_*` columns give the final outcome of
Credits generated in that window. Event Drop ratios use events observed in the
superslice and must not be confused with these generation-cohort probabilities.

`credit_timeseries_lag.csv` correlates Regular feedback loss in superslice `t`
with Tentative generation share in `t + k` for lags 0 through 8. Positive
feedback-loss correlation at a future lag, together with a negative
post-feedback-rate correlation, is the expected signature of the feedback
chain. The same file also correlates Tentative share with the final Regular
cohort Drop ratio; a negative lag-0 value is the expected signature of Regular
protection. The simulator option is default-off and the run-script option is
applied only to FIFO modes.
