# Sparse paired 24-ToR Credit experiment

This experiment keeps the same 24-ToR Opera topology, Flare feedback,
`cwnd=4`, queue thresholds, and four scheduler/admission cases as the denser
4-flow x 2-MiB experiment. Only workload pressure changes.

## Default workload

```text
24 ToRs, 4 Hosts/ToR, 96 Hosts
2 outgoing and 2 incoming flows per Host
1 MiB per flow, 2 MiB per Host
192 flows, 192 MiB total data
starts spread over 16 x 55-us superslices
12 starts per superslice (previously 48)
```

Every start slice activates six receiver Hosts. Each receiver gets a local
two-flow burst with a 1-us gap, while the 12 flows come from 12 distinct source
ToRs. This keeps receiver-NIC flow competition while reducing global offered
bytes and release density by 75% relative to the 4 x 2-MiB experiment.

Lane 0/2 use reverse-paired offsets `(3, 21)` and lane 1/3 use `(9, 15)`.
For the committed topology and release phases, 63 of the 96 receiver pairs
start with different Credit route hop counts. Both starts stay inside the same
stable internal routing slice.

## Run

Build once and run FIFO, WRR, Admission, and Combined:

```bash
bash run/opera_24tor_sparse_2flow_1MiB/run.sh --build
```

Reuse the executable:

```bash
bash run/opera_24tor_sparse_2flow_1MiB/run.sh \
  --no-build \
  --output run/opera_24tor_sparse_2flow_1MiB/results_2x1MiB_pair16_w821_admission
```

The output contains a shared workload, four case directories, and
`comparison.csv`. To isolate flow size while retaining the same sparse matrix,
override `--flow-size-mib`, for example `--flow-size-mib 2`.
