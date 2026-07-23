#!/usr/bin/env python3
"""Compare FIFO and receiver-hop-priority summary CSV files."""

import argparse
import csv
from pathlib import Path


METRICS = (
    ("completed_flows", "higher"),
    ("completion_ratio", "higher"),
    ("mean_fct_ms", "lower"),
    ("p95_fct_ms", "lower"),
    ("p99_fct_ms", "lower"),
    ("simulation_throughput_gbps", "higher"),
    ("active_makespan_throughput_gbps", "higher"),
    ("mean_sampled_goodput_gbps", "higher"),
    ("flow_goodput_jain", "higher"),
    ("tor_goodput_jain", "higher"),
    ("credit_drop_ratio", "lower"),
    ("endpoint_credit_drops", "lower"),
    ("tor_queue_credit_drops", "lower"),
    ("topology_credit_drops", "lower"),
    ("path_conditional_credit_drop_ratio", "lower"),
    ("credit_delivery_ratio", "higher"),
    ("overflow_credit_drops", "lower"),
    ("timeout_credit_drops", "lower"),
    ("shaping_credit_drops", "lower"),
    ("tentative_credit_drops", "lower"),
    ("credit_waste_hops_per_generated", "lower"),
    ("known_data_drops", "lower"),
    ("max_credit_queue_packets", "lower"),
    ("max_uplink_credit_queue_packets", "lower"),
    ("max_data_queue_packets", "lower"),
)


def read_summary(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"{path} must contain exactly one summary row")
    return rows[0]


def parse_number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def compare_summaries(fifo, priority):
    rows = []
    for metric, direction in METRICS:
        fifo_text = fifo.get(metric, "")
        priority_text = priority.get(metric, "")
        fifo_value = parse_number(fifo_text)
        priority_value = parse_number(priority_text)
        delta = None
        relative = None
        winner = ""
        if fifo_value is not None and priority_value is not None:
            delta = priority_value - fifo_value
            if fifo_value != 0:
                relative = delta / abs(fifo_value)
            if priority_value == fifo_value:
                winner = "tie"
            elif direction == "higher":
                winner = "rxhopprio" if priority_value > fifo_value else "fifo"
            else:
                winner = "rxhopprio" if priority_value < fifo_value else "fifo"
        rows.append(
            {
                "metric": metric,
                "preferred": direction,
                "fifo": fifo_text,
                "rxhopprio": priority_text,
                "absolute_delta": "" if delta is None else delta,
                "relative_change": "" if relative is None else relative,
                "winner": winner,
            }
        )
    return rows


def write_comparison(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fifo", type=Path, required=True)
    parser.add_argument("--rxhopprio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        fifo = read_summary(args.fifo)
        priority = read_summary(args.rxhopprio)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    rows = compare_summaries(fifo, priority)
    write_comparison(args.output, rows)
    selected = {
        row["metric"]: row
        for row in rows
        if row["metric"]
        in {
            "completion_ratio",
            "mean_fct_ms",
            "p99_fct_ms",
            "simulation_throughput_gbps",
            "credit_drop_ratio",
            "endpoint_credit_drops",
            "tor_queue_credit_drops",
            "path_conditional_credit_drop_ratio",
        }
    }
    for metric, _ in METRICS:
        if metric not in selected:
            continue
        row = selected[metric]
        print(
            f"{metric}: fifo={row['fifo']} rxhopprio={row['rxhopprio']} "
            f"winner={row['winner']}"
        )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
