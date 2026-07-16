# Multi-bottleneck short/long credit-order experiment

This experiment tests whether the arrival order of short- and long-credit-path
flows changes credit drops, wasted credit-link work, per-flow throughput, and
FCT on a fixed 13-ToR topology.

## Fixed credit paths

Each receiver has one short and one long flow. Data uses the exact reverse path.

```text
receiver 12 short: 12 -> 3
receiver 12 long:  12 -> 3 -> 7 -> 8 -> 4

receiver 10 short: 10 -> 6 -> 2
receiver 10 long:  10 -> 6 -> 7 -> 8 -> 9

receiver 11 short: 11 -> 9
receiver 11 long:  11 -> 9 -> 8 -> 7

receiver 2 short:   2 -> 6
receiver 2 long:    2 -> 6 -> 7 -> 8 -> 9 -> 11
```

The repeated `8 -> 8` in the original receiver-2 path is treated as a typo and
replaced with the `8 -> 9 -> 11` path shown by the topology diagram.

The graph is intentionally irregular. `GraphTopology` allocates the maximum
uplink count and leaves nonexistent ports empty; routing still follows only the
explicit paths in `topologies/multibottleneck_13tor_graph.txt`.

## Ordering cases

Each wave contains the eight flows above:

- `short_first`: short flow starts 1 microsecond before its paired long flow.
- `long_first`: long flow starts 1 microsecond before its paired short flow.
- `simultaneous`: both start together; declaration order alternates by round.

Defaults are 1 MB per flow, 20 waves, 10 ms between waves, a 1 microsecond
within-pair gap, and a 250 ms simulation. The inter-wave gap keeps each receiver
at two intentional competing flows instead of accumulating flows across waves.

All credits currently use the same FIFO priority class. This experiment measures
arrival-order sensitivity and hop-dependent shaping; it does not yet install an
explicit short/long priority queue policy.

## Run

From the repository root on the Linux server:

```bash
bash run/multibottleneck_credit_order/run.sh
```

Useful variants:

```bash
# One ordering case
bash run/multibottleneck_credit_order/run.sh --no-build \
  --case short_first --output run/multibottleneck_credit_order/results_short

# Overflow-only control
bash run/multibottleneck_credit_order/run.sh --no-build --no-shaping \
  --output run/multibottleneck_credit_order/results_no_shaping

# Increase the explicit ordering gap
bash run/multibottleneck_credit_order/run.sh --no-build \
  --order-gap-ns 10000 --output run/multibottleneck_credit_order/results_gap10us
```

Because this change adds C++ counters and irregular-graph support, the first run
must rebuild the simulator. Use `--no-build` only after that build succeeds.

## Outputs

- `summary.csv`: one row per ordering case and `short`, `long`, or `all` class.
- `order_comparison.csv`: direct short-first versus long-first relative changes.
- `per_receiver.csv`: class metrics split across receivers 12, 10, 11, and 2.
- `per_flow.csv`: FCT, per-flow goodput, credit reasons, and waste for every flow.
- `per_queue.csv`: queue hotspots for every ordering case.

`credit_waste_hops` sums the number of ToR-to-ToR links already traversed by
credits that are eventually dropped. `credit_waste_link_bytes` multiplies that
count by the 64-byte credit size. A first-ToR drop has zero wasted ToR-link hops;
a later drop contributes the links consumed before the drop point.

The primary ordering comparison is class-specific: short-flow performance under
`short_first` should be compared with short-flow performance under `long_first`,
and likewise for long flows. Aggregate metrics can hide one class benefiting at
the expense of the other.
