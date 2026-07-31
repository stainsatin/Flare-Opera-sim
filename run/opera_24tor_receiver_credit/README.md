# Receiver-side Credit service experiments

## Lightweight FIFO versus NEW comparison

For the normal performance comparison, run only two cases with one seed:

```bash
bash run/opera_24tor_receiver_credit/compare.sh --build
```

This executes:

- `fifo`: original FIFO behavior with audit instrumentation.
- `new`: Regular-first, one Tentative probe per 16 Regular-backlogged slots,
  and Regular-only dynamic Hop WRR 4:2:1.

Both cases use the same 384-flow, 2 MiB workload and original feedback. The
comparison deliberately leaves feedback grace and push-out disabled so the
measured difference comes from receiver Credit service. Results are written
to `results_fifo_vs_new/comparison_summary.csv` and
`results_fifo_vs_new/per_seed_comparison.csv`. Use `--seeds 1,2,3` only when a
small robustness check is needed; that runs six simulations.

## Full mechanism matrix

This experiment keeps Flare Credit generation, Opera routing, ToR shaping,
Data queues, and the shared `credq=60` capacity unchanged. It separates four
questions into opt-in modes:

- `A0/A1`: original FIFO, without/with audit instrumentation.
- `B0/B1`: global Tentative admission, then strict Regular-first FIFO.
- `C1/C2/C3`: one Tentative probe per 32/16/8 Regular-backlogged slots.
- `D1/D2`: Regular-only dynamic Hop WRR with 4:2:1 and 8:2:1 weights.
- `E1/E2/E3`: Tentative-loss diagnostic and/or Regular feedback grace.
- `F1`: oldest-Tentative push-out by an incoming Regular Credit.

Build and run the five-seed medium workload:

```bash
bash run/opera_24tor_receiver_credit/run.sh --build --workload medium
```

With `--mode all`, the script first runs A0 through C3, selects the probe
interval with the highest five-seed mean total Delivered Credit per used NIC
slot, runs D1/D2, and carries 4:2:1 Hop WRR into E/F only when D1 improves
Regular Delivered per slot without reducing Total Delivered per slot. Pass
`--best-probe-interval` and `--best-regular-hop` (or
`--no-best-regular-hop`) to override those selections. The long workload also
stops if fewer than 80% of flows span three complete 1.32 ms Opera cycles.

Run both the 2 MiB workload and the 16 MiB multi-cycle workload without
rebuilding:

```bash
bash run/opera_24tor_receiver_credit/run.sh --no-build --workload both \
  --output run/opera_24tor_receiver_credit/results_receiver_credit
```

Use `--mode C2 --seeds 1` for a smoke run. Every audited case emits
`feedback_window_trace.csv`, `per_nic_credit_slot.csv`,
`regular_wrr_trace.csv`, `credit_funnel.csv`, and the existing queue/flow
CSVs. `multi_seed_summary.csv` contains per-seed rows plus mean, standard
deviation, and 95% confidence-interval half widths.
