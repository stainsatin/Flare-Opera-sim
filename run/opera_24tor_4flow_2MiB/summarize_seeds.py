#!/usr/bin/env python3
"""Aggregate per-seed RCDCP summaries with standard deviation and 95% CI."""

import argparse
import csv
import math
import statistics
from pathlib import Path


METRICS = {
    "regular_generated": "regular_generated",
    "regular_endpoint_drop": "regular_endpoint_drop",
    "regular_admitted": "regular_admitted",
    "regular_sent": "regular_sent",
    "regular_path_drop": "regular_path_drop",
    "regular_delivered": "regular_delivered",
    "regular_delivered_rate": "regular_delivered_rate",
    "regular_delivered_per_nic_slot": "regular_delivered_per_nic_slot",
    "total_delivered_per_used_nic_slot": "total_delivered_per_used_nic_slot",
    "total_nic_credit_slots": "total_nic_credit_slots",
    "used_nic_credit_slots": "used_nic_credit_slots",
    "idle_slots": "idle_slots",
    "tentative_stealing_ratio": "tentative_stealing_ratio",
    "slots_with_regular_pending": "slots_with_regular_pending",
    "tentative_probe_slots": "tentative_probe_slots",
    "regular_delivered_per_credit_hop": "regular_delivered_per_credit_hop",
    "tentative_admitted": "tentative_admitted",
    "tentative_endpoint_drop": "tentative_endpoint_drop",
    "tentative_sent": "tentative_sent",
    "tentative_path_drop": "tentative_path_drop",
    "tentative_delivered": "tentative_delivered",
    "tentative_slot_share": "tentative_nic_slot_share",
    "mean_fct": "mean_fct_ms",
    "p95_fct": "p95_fct_ms",
    "active_throughput": "active_makespan_throughput_gbps",
    "credit_timeout": "timeout_credit_drops",
    "medium_low_timeout": "medium_low_endpoint_timeout",
    "tor_uplink_tentative_drops": "tor_uplink_tentative_drops",
    "tor_uplink_regular_drop": "tor_uplink_regular_drop",
    "regular_path_success_ratio": "regular_path_success_ratio",
    "mean_feedback_loss": "mean_feedback_loss",
    "mean_final_regular_loss": "mean_final_regular_loss",
    "feedback_false_loss_ratio": "feedback_false_loss_ratio",
    "feedback_unresolved": "feedback_regular_unresolved_at_sim_end",
    "mean_regular_feedback_sample_size": "mean_regular_feedback_sample_size",
    "mean_admitted_hops": "mean_admitted_credit_path_hops",
    "mean_delivered_hops": "mean_delivered_actual_credit_hops",
    "regular_generated_mean_hops": "regular_generated_mean_hops",
    "regular_admitted_mean_hops": "regular_admitted_mean_hops",
    "regular_delivered_mean_hops": "regular_delivered_mean_hops",
    "tentative_generated_mean_hops": "tentative_generated_mean_hops",
    "tentative_admitted_mean_hops": "tentative_admitted_mean_hops",
    "tentative_delivered_mean_hops": "tentative_delivered_mean_hops",
    "tor_uplink_tentative_drop": "tor_uplink_tentative_drop",
    "tor_downlink_regular_drop": "tor_downlink_regular_drop",
    "tor_downlink_tentative_drop": "tor_downlink_tentative_drop",
    "data_drop": "data_drop",
    "flows_spanning_3_cycles_ratio": "flows_spanning_3_cycles_ratio",
}
T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
}


def read_one(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 1:
        raise ValueError(f"{path} must contain exactly one summary row")
    return rows[0]


def number(value):
    if value in (None, ""):
        return math.nan
    return float(value)


def aggregate(values, kind):
    valid = [value for value in values if not math.isnan(value)]
    if not valid:
        return ""
    if kind == "mean":
        return statistics.fmean(valid)
    if len(valid) < 2:
        return 0.0
    stdev = statistics.stdev(valid)
    if kind == "stddev":
        return stdev
    critical = T_CRITICAL_95.get(len(valid) - 1, 1.96)
    return critical * stdev / math.sqrt(len(valid))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--seeds", required=True)
    parser.add_argument("--modes", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    seeds = [int(value) for value in args.seeds.split(",")]
    modes = [value for value in args.modes.split(",") if value]
    rows = []
    by_mode = {mode: [] for mode in modes}
    for seed in seeds:
        for mode in modes:
            source = args.results / f"seed_{seed}" / mode / "summary.csv"
            summary = read_one(source)
            row = {"seed": seed, "mode": mode, "statistic": "seed"}
            row.update(
                {output: summary.get(source_name, "") for output, source_name in METRICS.items()}
            )
            rows.append(row)
            by_mode[mode].append(row)

    for mode in modes:
        for statistic_name in ("mean", "stddev", "ci95"):
            row = {"seed": "", "mode": mode, "statistic": statistic_name}
            for metric in METRICS:
                row[metric] = aggregate(
                    [number(source[metric]) for source in by_mode[mode]],
                    statistic_name,
                )
            rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
