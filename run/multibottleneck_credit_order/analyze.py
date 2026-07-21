#!/usr/bin/env python3
"""Analyze ordering sensitivity in the multi-bottleneck credit experiment."""

import argparse
import csv
import math
import statistics
from pathlib import Path


CASES = ("short_first", "long_first", "simultaneous")
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
    "shaping_checks",
    "shaping_admitted",
)
FLOW_CREDIT_FIELDS = (
    "generated",
    "delivered",
    "queue_arrivals",
    "queue_transmissions",
    "dropped",
    "overflow",
    "timeout",
    "shaping",
    "tentative",
    "shaping_checks",
    "shaping_admitted",
    "waste_hops",
)
FLOW_CLASSES = {
    (3, 12): ("short", 1),
    (4, 12): ("long", 4),
    (2, 10): ("short", 2),
    (12, 10): ("long", 4),
    (7, 0): ("short", 1),
    (4, 0): ("long", 3),
    (7, 1): ("short", 1),
    (5, 1): ("long", 4),
    (6, 2): ("short", 1),
    (1, 2): ("long", 3),
}
FLOWS_PER_ROUND = len(FLOW_CLASSES)
RECEIVERS = tuple(sorted({receiver for _, receiver in FLOW_CLASSES}))


def percentile(values, probability):
    if not values:
        return ""
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def classify(sender, receiver):
    try:
        return FLOW_CLASSES[(sender, receiver)]
    except KeyError as error:
        raise RuntimeError(
            f"unexpected flow endpoints sender={sender}, receiver={receiver}"
        ) from error


def parse_trace(path):
    rows = []
    with path.open(encoding="ascii") as stream:
        for line_number, line in enumerate(stream, start=1):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 4:
                raise RuntimeError(f"{path}:{line_number}: expected 4 fields")
            sender, receiver, flow_size, start_ns = map(int, fields)
            flow_class, path_hops = classify(sender, receiver)
            rows.append(
                {
                    "flow_id": len(rows),
                    "sender": sender,
                    "receiver": receiver,
                    "flow_class": flow_class,
                    "path_hops": path_hops,
                    "bytes": flow_size,
                    "start_ns": start_ns,
                    "round": len(rows) // FLOWS_PER_ROUND,
                }
            )
    if not rows:
        raise RuntimeError(f"{path} contains no flows")
    return rows


def parse_log(path):
    queue_rows = []
    flow_credits = {}
    fcts = {}
    data_queue_drops = 0
    topology_losses = 0
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "CreditStats" and len(fields) >= 15:
                record = {
                    "scope": fields[1],
                    "id": int(fields[2]),
                    "port": int(fields[3]),
                }
                record.update(
                    {name: int(value) for name, value in zip(CREDIT_FIELDS, fields[4:15])}
                )
                record["drop_ratio"] = (
                    record["dropped"] / record["received"]
                    if record["received"]
                    else 0.0
                )
                queue_rows.append(record)
            elif fields[0] == "DataQueueStats" and len(fields) >= 5:
                data_queue_drops += int(fields[4])
            elif fields[0] == "Packetloss" and len(fields) >= 5:
                topology_losses = int(fields[4])
            elif fields[0] == "FCT" and len(fields) >= 8:
                flow_id = int(fields[7])
                fct_ms = float(fields[4])
                start_ms = float(fields[5])
                flow_size = int(fields[3])
                fcts[flow_id] = {
                    "sender": int(fields[1]),
                    "receiver": int(fields[2]),
                    "bytes": flow_size,
                    "fct_ms": fct_ms,
                    "start_ms": start_ms,
                    "finish_ms": start_ms + fct_ms,
                    "total_packet_hops": int(fields[6]),
                    "flow_goodput_gbps": (
                        flow_size * 8.0 / (fct_ms * 1_000_000.0)
                        if fct_ms > 0
                        else ""
                    ),
                }
            elif fields[0] == "FlowCreditStats" and len(fields) >= 17:
                flow_id = int(fields[1])
                record = {
                    "sender": int(fields[2]),
                    "receiver": int(fields[3]),
                    "path_hops": int(fields[4]),
                }
                record.update(
                    {
                        name: int(value)
                        for name, value in zip(FLOW_CREDIT_FIELDS, fields[5:17])
                    }
                )
                flow_credits[flow_id] = record
    if not queue_rows:
        raise RuntimeError(f"{path} has no final CreditStats records")
    return queue_rows, flow_credits, fcts, data_queue_drops, topology_losses


def build_flow_rows(case, trace, flow_credits, fcts):
    rows = []
    for expected in trace:
        flow_id = expected["flow_id"]
        credit = flow_credits.get(flow_id, {})
        fct = fcts.get(flow_id, {})
        if credit and (
            credit["sender"] != expected["sender"]
            or credit["receiver"] != expected["receiver"]
        ):
            raise RuntimeError(f"flow {flow_id} credit endpoints do not match trace")
        if credit and credit["path_hops"] != expected["path_hops"]:
            raise RuntimeError(
                f"flow {flow_id} credit path has {credit['path_hops']} hops; "
                f"expected {expected['path_hops']}"
            )
        if fct and (
            fct["sender"] != expected["sender"]
            or fct["receiver"] != expected["receiver"]
        ):
            raise RuntimeError(f"flow {flow_id} FCT endpoints do not match trace")
        row = {"case": case, **expected, "completed": bool(fct)}
        row.update(
            {
                "start_ms": fct.get("start_ms", expected["start_ns"] / 1_000_000.0),
                "finish_ms": fct.get("finish_ms", ""),
                "fct_ms": fct.get("fct_ms", ""),
                "flow_goodput_gbps": fct.get("flow_goodput_gbps", ""),
                "total_packet_hops": fct.get("total_packet_hops", ""),
            }
        )
        for name in FLOW_CREDIT_FIELDS:
            row[name] = credit.get(name, 0)
        row["credit_drop_ratio"] = (
            row["dropped"] / row["generated"] if row["generated"] else 0.0
        )
        row["credit_delivery_ratio"] = (
            row["delivered"] / row["generated"] if row["generated"] else 0.0
        )
        row["waste_link_bytes"] = row["waste_hops"] * 64
        rows.append(row)
    return rows


def summarize(rows, case, flow_class, simtime_s, data_drops="", topology_losses=""):
    selected = [row for row in rows if flow_class == "all" or row["flow_class"] == flow_class]
    completed = [row for row in selected if row["completed"]]
    fcts = [row["fct_ms"] for row in completed]
    goodputs = [row["flow_goodput_gbps"] for row in completed]
    generated = sum(row["generated"] for row in selected)
    delivered = sum(row["delivered"] for row in selected)
    dropped = sum(row["dropped"] for row in selected)
    waste_hops = sum(row["waste_hops"] for row in selected)
    completed_bytes = sum(row["bytes"] for row in completed)
    starts = [row["start_ns"] / 1_000_000.0 for row in selected]
    finishes = [row["finish_ms"] for row in completed]
    makespan_ms = max(finishes) - min(starts) if finishes else ""
    result = {
        "case": case,
        "flow_class": flow_class,
        "offered_flows": len(selected),
        "completed_flows": len(completed),
        "completion_ratio": len(completed) / len(selected) if selected else 0.0,
        "completed_bytes": completed_bytes,
        "simulation_throughput_gbps": (
            completed_bytes * 8.0 / (simtime_s * 1_000_000_000.0)
            if simtime_s > 0
            else ""
        ),
        "active_makespan_throughput_gbps": (
            completed_bytes * 8.0 / (makespan_ms * 1_000_000.0)
            if makespan_ms != "" and makespan_ms > 0
            else ""
        ),
        "mean_flow_goodput_gbps": statistics.fmean(goodputs) if goodputs else "",
        "mean_fct_ms": statistics.fmean(fcts) if fcts else "",
        "median_fct_ms": percentile(fcts, 0.50),
        "p95_fct_ms": percentile(fcts, 0.95),
        "p99_fct_ms": percentile(fcts, 0.99),
        "max_fct_ms": max(fcts) if fcts else "",
        "generated_credits": generated,
        "delivered_credits": delivered,
        "credit_delivery_ratio": delivered / generated if generated else 0.0,
        "credit_drops": dropped,
        "credit_drop_ratio": dropped / generated if generated else 0.0,
        "overflow_drops": sum(row["overflow"] for row in selected),
        "timeout_drops": sum(row["timeout"] for row in selected),
        "shaping_drops": sum(row["shaping"] for row in selected),
        "tentative_drops": sum(row["tentative"] for row in selected),
        "shaping_checks": sum(row["shaping_checks"] for row in selected),
        "shaping_admitted": sum(row["shaping_admitted"] for row in selected),
        "queue_arrivals": sum(row["queue_arrivals"] for row in selected),
        "queue_transmissions": sum(row["queue_transmissions"] for row in selected),
        "credit_waste_hops": waste_hops,
        "waste_hops_per_generated": waste_hops / generated if generated else 0.0,
        "waste_hops_per_drop": waste_hops / dropped if dropped else 0.0,
        "credit_waste_link_bytes": waste_hops * 64,
        "data_queue_drops": data_drops,
        "topology_losses": topology_losses,
    }
    return result


def write_csv(path, rows, fieldnames=None):
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def pct_change(value, baseline):
    if value == "" or baseline in ("", 0):
        return ""
    return 100.0 * (value / baseline - 1.0)


def build_order_comparison(summaries):
    lookup = {(row["case"], row["flow_class"]): row for row in summaries}
    rows = []
    for flow_class in ("short", "long"):
        short_first = lookup[("short_first", flow_class)]
        long_first = lookup[("long_first", flow_class)]
        simultaneous = lookup[("simultaneous", flow_class)]
        row = {"flow_class": flow_class}
        for metric in (
            "mean_fct_ms",
            "p99_fct_ms",
            "mean_flow_goodput_gbps",
            "credit_drop_ratio",
            "credit_delivery_ratio",
            "waste_hops_per_generated",
        ):
            row[f"short_first_{metric}"] = short_first[metric]
            row[f"long_first_{metric}"] = long_first[metric]
            row[f"simultaneous_{metric}"] = simultaneous[metric]
            row[f"short_first_vs_long_first_{metric}_pct"] = pct_change(
                short_first[metric], long_first[metric]
            )
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--simtime", type=float, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    args = parser.parse_args()
    if args.simtime <= 0:
        parser.error("--simtime must be positive")

    all_flows = []
    all_queues = []
    summaries = []
    receiver_rows = []
    for case in args.cases:
        trace_path = args.results / "traffic" / f"{case}.htsim"
        log_path = args.results / f"{case}.log"
        if not trace_path.is_file() or not log_path.is_file():
            parser.error(f"missing trace or log for case {case}")
        trace = parse_trace(trace_path)
        queues, flow_credits, fcts, data_drops, topology_losses = parse_log(log_path)
        flow_rows = build_flow_rows(case, trace, flow_credits, fcts)
        all_flows.extend(flow_rows)
        for queue in queues:
            all_queues.append({"case": case, **queue})
        for flow_class in ("short", "long", "all"):
            summaries.append(
                summarize(
                    flow_rows,
                    case,
                    flow_class,
                    args.simtime,
                    data_drops if flow_class == "all" else "",
                    topology_losses if flow_class == "all" else "",
                )
            )
        for receiver in RECEIVERS:
            receiver_flows = [row for row in flow_rows if row["receiver"] == receiver]
            for flow_class in ("short", "long"):
                row = summarize(receiver_flows, case, flow_class, args.simtime)
                receiver_rows.append({"receiver": receiver, **row})

    write_csv(args.results / "summary.csv", summaries)
    write_csv(args.results / "per_receiver.csv", receiver_rows)
    write_csv(args.results / "per_flow.csv", all_flows)
    write_csv(
        args.results / "per_queue.csv",
        all_queues,
        ["case", "scope", "id", "port", *CREDIT_FIELDS, "drop_ratio"],
    )
    wrote_comparison = set(args.cases) == set(CASES)
    if wrote_comparison:
        write_csv(
            args.results / "order_comparison.csv",
            build_order_comparison(summaries),
        )

    print(
        "case          class  done    drop_ratio  waste/gen  "
        "mean_FCT_ms  p99_FCT_ms  mean_flow_Gbps"
    )
    for row in summaries:
        if row["flow_class"] == "all":
            continue
        goodput = row["mean_flow_goodput_gbps"]
        print(
            f"{row['case']:<13} {row['flow_class']:<6} "
            f"{row['completed_flows']:>3}/{row['offered_flows']:<3} "
            f"{row['credit_drop_ratio']:<11.6f} "
            f"{row['waste_hops_per_generated']:<10.4f} "
            f"{row['mean_fct_ms'] if row['mean_fct_ms'] != '' else float('nan'):<12.6f} "
            f"{row['p99_fct_ms'] if row['p99_fct_ms'] != '' else float('nan'):<11.6f} "
            f"{goodput if goodput != '' else float('nan'):.3f}"
        )
    print(f"Wrote {args.results / 'summary.csv'}")
    if wrote_comparison:
        print(f"Wrote {args.results / 'order_comparison.csv'}")


if __name__ == "__main__":
    main()
