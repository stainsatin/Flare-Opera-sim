#!/usr/bin/env python3
"""Compare FIFO, WRR, priority-admission, and combined summary CSVs."""

import argparse
import csv
from pathlib import Path


CASES = ("fifo", "wrr", "admission", "combined")
METRICS = (
    ("completed_flows", "higher"),
    ("completion_ratio", "higher"),
    ("mean_fct_ms", "lower"),
    ("p95_fct_ms", "lower"),
    ("p99_fct_ms", "lower"),
    ("simulation_throughput_gbps", "higher"),
    ("active_makespan_throughput_gbps", "higher"),
    ("flow_goodput_jain", "higher"),
    ("mean_admitted_credit_path_hops", "lower"),
    ("mean_delivered_credit_path_hops", "lower"),
    ("mean_delivered_actual_credit_hops", "lower"),
    ("regular_admitted_share", "higher"),
    ("regular_delivered_share", "higher"),
    ("tor_queue_credit_drops", "lower"),
    ("path_conditional_credit_drop_ratio", "lower"),
    ("total_credit_network_link_bytes", "lower"),
    ("endpoint_credit_drops", "lower"),
    ("overflow_credit_drops", "lower"),
    ("pushout_credit_drops", "lower"),
    ("shaping_credit_drops", "lower"),
    ("tentative_credit_drops", "lower"),
    ("known_data_drops", "lower"),
)


def read_summary(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"{path} must contain exactly one summary row")
    return rows[0]


def parse_number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def compare_summaries(summaries):
    rows = []
    for metric, preferred in METRICS:
        row = {"metric": metric, "preferred": preferred}
        values = {}
        for case in CASES:
            text = summaries[case].get(metric, "")
            row[case] = text
            values[case] = parse_number(text)

        fifo_value = values["fifo"]
        for case in CASES[1:]:
            value = values[case]
            delta = None if fifo_value is None or value is None else value - fifo_value
            relative = None
            if delta is not None and fifo_value != 0:
                relative = delta / abs(fifo_value)
            row[f"{case}_delta_vs_fifo"] = "" if delta is None else delta
            row[f"{case}_relative_vs_fifo"] = "" if relative is None else relative

        valid = {case: value for case, value in values.items() if value is not None}
        if not valid:
            row["best"] = ""
        else:
            target = max(valid.values()) if preferred == "higher" else min(valid.values())
            row["best"] = "+".join(
                case for case in CASES if values[case] == target
            )
        rows.append(row)
    return rows


def write_comparison(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    for case in CASES:
        parser.add_argument(f"--{case}", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summaries = {
            case: read_summary(getattr(args, case)) for case in CASES
        }
    except (OSError, ValueError) as error:
        parser.error(str(error))

    rows = compare_summaries(summaries)
    write_comparison(args.output, rows)
    for row in rows:
        if row["metric"] in {
            "completion_ratio",
            "mean_fct_ms",
            "mean_admitted_credit_path_hops",
            "mean_delivered_credit_path_hops",
            "mean_delivered_actual_credit_hops",
            "regular_delivered_share",
            "tor_queue_credit_drops",
            "total_credit_network_link_bytes",
        }:
            values = " ".join(f"{case}={row[case]}" for case in CASES)
            print(f"{row['metric']}: {values}; best={row['best']}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
