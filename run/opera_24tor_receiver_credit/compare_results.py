#!/usr/bin/env python3
"""Build paired FIFO-versus-NEW summaries from simulator summary.csv files."""

import argparse
import csv
import math
import statistics
from pathlib import Path


METRICS = {
    "completed_flows": "higher",
    "mean_fct_ms": "lower",
    "p95_fct_ms": "lower",
    "active_makespan_throughput_gbps": "higher",
    "regular_delivered": "higher",
    "tentative_delivered": "higher",
    "regular_delivered_per_used_nic_slot": "higher",
    "total_delivered_per_used_nic_slot": "higher",
    "tentative_stealing_ratio": "lower",
    "regular_path_success_ratio": "higher",
    "credit_drop_ratio": "lower",
    "endpoint_credit_drops": "lower",
    "tor_queue_credit_drops": "lower",
    "tor_uplink_regular_drop": "lower",
    "tor_uplink_tentative_drop": "lower",
    "mean_delivered_actual_credit_hops": "lower",
    "total_credit_network_hops": "lower",
    "known_data_drops": "lower",
}
T_CRITICAL_95 = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776,
                 6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262}


def read_summary(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"{path} must contain exactly one row")
    return rows[0]


def number(row, metric):
    value = row.get(metric, "")
    if value == "":
        return math.nan
    return float(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--seeds", required=True)
    args = parser.parse_args()
    seeds = [int(value) for value in args.seeds.split(",")]

    paired_rows = []
    values = {metric: [] for metric in METRICS}
    for seed in seeds:
        fifo = read_summary(args.results / f"seed_{seed}" / "fifo" / "summary.csv")
        new = read_summary(args.results / f"seed_{seed}" / "new" / "summary.csv")
        row = {"seed": seed}
        for metric, direction in METRICS.items():
            fifo_value = number(fifo, metric)
            new_value = number(new, metric)
            delta = new_value - fifo_value
            improvement = delta if direction == "higher" else -delta
            improvement_pct = (
                improvement / abs(fifo_value) * 100.0 if fifo_value else math.nan
            )
            row[f"fifo_{metric}"] = fifo_value
            row[f"new_{metric}"] = new_value
            row[f"delta_{metric}"] = delta
            row[f"improvement_pct_{metric}"] = improvement_pct
            values[metric].append((fifo_value, new_value, delta, improvement_pct))
        paired_rows.append(row)

    with (args.results / "per_seed_comparison.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(paired_rows[0]))
        writer.writeheader()
        writer.writerows(paired_rows)

    summary_rows = []
    for metric, direction in METRICS.items():
        records = values[metric]
        deltas = [record[2] for record in records if not math.isnan(record[2])]
        stddev = statistics.stdev(deltas) if len(deltas) > 1 else ""
        critical = T_CRITICAL_95.get(len(deltas), 1.96)
        ci95 = (
            critical * stddev / math.sqrt(len(deltas))
            if len(deltas) > 1
            else ""
        )
        summary_rows.append(
            {
                "metric": metric,
                "better_direction": direction,
                "fifo_mean": statistics.fmean(record[0] for record in records),
                "new_mean": statistics.fmean(record[1] for record in records),
                "new_minus_fifo": statistics.fmean(deltas),
                "improvement_pct": statistics.fmean(record[3] for record in records),
                "paired_delta_stddev": stddev,
                "paired_delta_ci95": ci95,
                "seeds": len(records),
            }
        )

    with (args.results / "comparison_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)


if __name__ == "__main__":
    main()
