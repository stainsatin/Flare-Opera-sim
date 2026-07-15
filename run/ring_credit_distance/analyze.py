#!/usr/bin/env python3
"""Summarize the 8-ToR ring credit-distance experiment logs."""

import argparse
import csv
import math
import re
import statistics
from pathlib import Path


CREDIT_FIELDS = (
    "received",
    "transmitted",
    "queued",
    "max_queued",
    "dropped",
    "overflow",
    "timeout",
    "shaping",
    "tentative",
)


def percentile(values, probability):
    if not values:
        return ""
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def mean_after_warmup(samples, warmup_fraction):
    if not samples:
        return ""
    last_time = max(timestamp for timestamp, _ in samples)
    threshold = last_time * warmup_fraction
    values = [value for timestamp, value in samples if timestamp >= threshold]
    return statistics.fmean(values) if values else ""


def parse_log(path, distance, warmup_fraction):
    credit_stats = []
    utilization = []
    input_load = []
    fcts = []
    total_packets = 0
    total_packet_losses = 0
    max_data_queue_bytes = 0
    data_queue_drops = {}

    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "CreditStats" and len(fields) >= 13:
                record = {
                    "distance": distance,
                    "scope": fields[1],
                    "id": int(fields[2]),
                    "port": int(fields[3]),
                }
                record.update(
                    {name: int(value) for name, value in zip(CREDIT_FIELDS, fields[4:13])}
                )
                record["drop_ratio"] = (
                    record["dropped"] / record["received"]
                    if record["received"]
                    else 0.0
                )
                credit_stats.append(record)
            elif fields[0] == "DataQueueStats" and len(fields) >= 5:
                data_queue_drops[(fields[1], int(fields[2]), int(fields[3]))] = int(
                    fields[4]
                )
            elif fields[0] == "Util" and len(fields) >= 3:
                utilization.append((float(fields[2]), float(fields[1])))
            elif fields[0] == "Input" and len(fields) >= 5:
                input_load.append((float(fields[4]), float(fields[1])))
            elif fields[0] == "Packetloss" and len(fields) >= 5:
                total_packets = int(fields[2])
                total_packet_losses = int(fields[4])
            elif fields[0] == "Queue" and len(fields) >= 5:
                max_data_queue_bytes = max(max_data_queue_bytes, int(fields[3]))
            elif fields[0] == "FCT" and len(fields) >= 5:
                fcts.append(float(fields[4]))

    if not credit_stats:
        raise RuntimeError(
            f"{path} has no final CreditStats records; the simulation likely did not finish"
        )

    for record in credit_stats:
        record["data_drops"] = data_queue_drops.get(
            (record["scope"], record["id"], record["port"]), 0
        )

    host_stats = [record for record in credit_stats if record["scope"] == "host"]
    tor_stats = [record for record in credit_stats if record["scope"] == "tor"]
    uplink_stats = [record for record in tor_stats if record["port"] >= 1]
    downlink_stats = [record for record in tor_stats if record["port"] == 0]
    all_drops = sum(record["dropped"] for record in credit_stats)
    generated = sum(record["received"] for record in host_stats)
    delivered = sum(record["transmitted"] for record in downlink_stats)
    mean_util = mean_after_warmup(utilization, warmup_fraction)
    mean_input = mean_after_warmup(input_load, warmup_fraction)

    summary = {
        "distance": distance,
        "generated_credits": generated,
        "delivered_credits": delivered,
        "credit_drops": all_drops,
        "credit_drop_ratio": all_drops / generated if generated else 0.0,
        "overflow_drops": sum(record["overflow"] for record in credit_stats),
        "timeout_drops": sum(record["timeout"] for record in credit_stats),
        "shaping_drops": sum(record["shaping"] for record in credit_stats),
        "tentative_drops": sum(record["tentative"] for record in credit_stats),
        "tor_credit_arrivals": sum(record["received"] for record in tor_stats),
        "uplink_credit_arrivals": sum(record["received"] for record in uplink_stats),
        "uplink_arrivals_per_generated": (
            sum(record["received"] for record in uplink_stats) / generated
            if generated
            else 0.0
        ),
        "max_credit_queue_packets": max(
            (record["max_queued"] for record in credit_stats), default=0
        ),
        "max_uplink_credit_queue_packets": max(
            (record["max_queued"] for record in uplink_stats), default=0
        ),
        "mean_goodput_fraction": mean_util,
        "mean_goodput_gbps": mean_util * 800.0 if mean_util != "" else "",
        "mean_input_fraction": mean_input,
        "mean_input_gbps": mean_input * 800.0 if mean_input != "" else "",
        "max_data_queue_packets": max_data_queue_bytes / 1500.0,
        "data_queue_drops": sum(data_queue_drops.values()),
        "topology_packets": total_packets,
        "topology_losses": total_packet_losses,
        "topology_loss_ratio": (
            total_packet_losses / total_packets if total_packets else 0.0
        ),
        "completed_flows": len(fcts),
        "mean_fct_ms": statistics.fmean(fcts) if fcts else "",
        "p99_fct_ms": percentile(fcts, 0.99),
    }
    return summary, credit_stats


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="directory containing k1.log, k2.log, k4.log")
    parser.add_argument(
        "--warmup-fraction",
        type=float,
        default=0.2,
        help="fraction of early utilization samples to exclude (default: 0.2)",
    )
    parser.add_argument(
        "--distances",
        type=int,
        nargs="+",
        choices=(1, 2, 4),
        help="only analyze the selected distances",
    )
    args = parser.parse_args()
    if not 0.0 <= args.warmup_fraction < 1.0:
        parser.error("--warmup-fraction must be in [0, 1)")

    summaries = []
    queue_rows = []
    log_pattern = re.compile(r"^k([124])\.log$")
    for log_path in sorted(args.results.glob("k*.log")):
        match = log_pattern.match(log_path.name)
        if not match:
            continue
        distance = int(match.group(1))
        if args.distances and distance not in args.distances:
            continue
        summary, stats = parse_log(log_path, distance, args.warmup_fraction)
        summaries.append(summary)
        queue_rows.extend(stats)

    if not summaries:
        parser.error(f"no completed k1.log, k2.log, or k4.log found in {args.results}")

    summaries.sort(key=lambda row: row["distance"])
    summary_path = args.results / "summary.csv"
    queue_path = args.results / "per_queue.csv"
    write_csv(summary_path, summaries, list(summaries[0].keys()))
    write_csv(
        queue_path,
        queue_rows,
        [
            "distance",
            "scope",
            "id",
            "port",
            *CREDIT_FIELDS,
            "drop_ratio",
            "data_drops",
        ],
    )

    print("k  credit_drop  drop_ratio  goodput_Gbps  uplink_arrivals/credit  max_credit_q")
    for row in summaries:
        goodput = row["mean_goodput_gbps"]
        goodput_text = f"{goodput:.3f}" if goodput != "" else "n/a"
        print(
            f"{row['distance']:<2} {row['credit_drops']:<12} "
            f"{row['credit_drop_ratio']:<11.6f} {goodput_text:<13} "
            f"{row['uplink_arrivals_per_generated']:<23.3f} "
            f"{row['max_uplink_credit_queue_packets']}"
        )
    print(f"Wrote {summary_path}")
    print(f"Wrote {queue_path}")


if __name__ == "__main__":
    main()
