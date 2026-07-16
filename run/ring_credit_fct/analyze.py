#!/usr/bin/env python3
"""Analyze drop, throughput, and FCT for the finite-flow ring experiment."""

import argparse
import csv
import importlib.util
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISTANCE_ANALYZER = ROOT / "run/ring_credit_distance/analyze.py"


def load_distance_analyzer():
    spec = importlib.util.spec_from_file_location(
        "ring_credit_distance_analyze", DISTANCE_ANALYZER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_distance_analyzer()


def parse_trace(path):
    rows = []
    with path.open(encoding="ascii") as stream:
        for line_number, line in enumerate(stream, start=1):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 4:
                raise RuntimeError(f"{path}:{line_number}: expected 4 fields")
            src, dst, flow_size, start_ns = map(int, fields)
            rows.append(
                {
                    "src": src,
                    "dst": dst,
                    "bytes": flow_size,
                    "start_ns": start_ns,
                }
            )
    if not rows:
        raise RuntimeError(f"{path} contains no flows")
    return rows


def parse_fct_records(path, distance):
    records = []
    unfinished_markers = 0
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "FCT" and len(fields) >= 8:
                flow_size = int(fields[3])
                fct_ms = float(fields[4])
                start_ms = float(fields[5])
                records.append(
                    {
                        "distance": distance,
                        "src": int(fields[1]),
                        "dst": int(fields[2]),
                        "bytes": flow_size,
                        "fct_ms": fct_ms,
                        "start_ms": start_ms,
                        "finish_ms": start_ms + fct_ms,
                        "total_packet_hops": int(fields[6]),
                        "flow_id": int(fields[7]),
                        "flow_goodput_gbps": (
                            flow_size * 8.0 / (fct_ms * 1_000_000.0)
                            if fct_ms > 0
                            else ""
                        ),
                    }
                )
            elif fields[0] == "UNFINISHED":
                unfinished_markers += 1
    return records, unfinished_markers


def infer_offered_load_gbps(trace):
    starts_ms = sorted({row["start_ns"] / 1_000_000.0 for row in trace})
    if len(starts_ms) < 2:
        return ""
    intervals = [b - a for a, b in zip(starts_ms, starts_ms[1:]) if b > a]
    if not intervals:
        return ""
    interval_ms = statistics.median(intervals)
    arrival_window_ms = starts_ms[-1] - starts_ms[0] + interval_ms
    total_bytes = sum(row["bytes"] for row in trace)
    return total_bytes * 8.0 / (arrival_window_ms * 1_000_000.0)


def analyze_case(log_path, trace_path, distance, simtime_s):
    base_summary, queues = BASE.parse_log(log_path, distance, warmup_fraction=0.0)
    trace = parse_trace(trace_path)
    flows, unfinished_markers = parse_fct_records(log_path, distance)
    flows.sort(key=lambda row: (row["start_ms"], row["src"], row["flow_id"]))

    fcts = [row["fct_ms"] for row in flows]
    offered_flows = len(trace)
    completed_flows = len(flows)
    completed_bytes = sum(row["bytes"] for row in flows)
    first_start_ms = min(row["start_ns"] for row in trace) / 1_000_000.0
    last_finish_ms = max((row["finish_ms"] for row in flows), default=first_start_ms)
    makespan_ms = last_finish_ms - first_start_ms
    flow_sizes = {row["bytes"] for row in trace}

    summary = {
        "distance": distance,
        "flow_size_bytes": next(iter(flow_sizes)) if len(flow_sizes) == 1 else "",
        "offered_flows": offered_flows,
        "completed_flows": completed_flows,
        "incomplete_flows": max(offered_flows - completed_flows, 0),
        "completion_ratio": completed_flows / offered_flows if offered_flows else 0.0,
        "unfinished_markers": unfinished_markers,
        "offered_bytes": sum(row["bytes"] for row in trace),
        "completed_bytes": completed_bytes,
        "offered_load_gbps": infer_offered_load_gbps(trace),
        "simulation_time_s": simtime_s,
        "simulation_throughput_gbps": (
            completed_bytes * 8.0 / (simtime_s * 1_000_000_000.0)
            if simtime_s > 0
            else ""
        ),
        "active_makespan_throughput_gbps": (
            completed_bytes * 8.0 / (makespan_ms * 1_000_000.0)
            if makespan_ms > 0
            else ""
        ),
        "makespan_ms": makespan_ms,
        "mean_fct_ms": statistics.fmean(fcts) if fcts else "",
        "median_fct_ms": BASE.percentile(fcts, 0.50),
        "p95_fct_ms": BASE.percentile(fcts, 0.95),
        "p99_fct_ms": BASE.percentile(fcts, 0.99),
        "max_fct_ms": max(fcts) if fcts else "",
        "generated_credits": base_summary["generated_credits"],
        "delivered_credits": base_summary["delivered_credits"],
        "credit_drops": base_summary["credit_drops"],
        "credit_drop_ratio": base_summary["credit_drop_ratio"],
        "overflow_drops": base_summary["overflow_drops"],
        "timeout_drops": base_summary["timeout_drops"],
        "shaping_drops": base_summary["shaping_drops"],
        "tentative_drops": base_summary["tentative_drops"],
        "shaping_checks": base_summary["shaping_checks"],
        "shaping_admitted": base_summary["shaping_admitted"],
        "shaping_admission_ratio": base_summary["shaping_admission_ratio"],
        "uplink_arrivals_per_generated": base_summary[
            "uplink_arrivals_per_generated"
        ],
        "sim_sampled_goodput_gbps": base_summary["mean_goodput_gbps"],
        "max_credit_queue_packets": base_summary["max_credit_queue_packets"],
        "max_uplink_credit_queue_packets": base_summary[
            "max_uplink_credit_queue_packets"
        ],
        "max_data_queue_packets": base_summary["max_data_queue_packets"],
        "data_queue_drops": base_summary["data_queue_drops"],
        "topology_losses": base_summary["topology_losses"],
    }
    return summary, queues, flows


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_metric(value):
    return f"{value:.3f}" if value != "" else "n/a"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--distances", type=int, nargs="+", choices=(1, 2, 3), default=(1, 2, 3)
    )
    parser.add_argument(
        "--simtime", type=float, required=True, help="simulation duration in seconds"
    )
    args = parser.parse_args()
    if args.simtime <= 0:
        parser.error("--simtime must be positive")

    summaries = []
    queue_rows = []
    flow_rows = []
    for distance in args.distances:
        log_path = args.results / f"k{distance}.log"
        trace_path = args.results / "traffic" / f"k{distance}.htsim"
        if not log_path.is_file():
            parser.error(f"missing simulator log: {log_path}")
        if not trace_path.is_file():
            parser.error(f"missing traffic trace: {trace_path}")
        summary, queues, flows = analyze_case(
            log_path, trace_path, distance, args.simtime
        )
        summaries.append(summary)
        queue_rows.extend(queues)
        flow_rows.extend(flows)

    summary_path = args.results / "summary.csv"
    queue_path = args.results / "per_queue.csv"
    flow_path = args.results / "per_flow.csv"
    write_csv(summary_path, summaries, list(summaries[0].keys()))
    write_csv(
        queue_path,
        queue_rows,
        [
            "distance",
            "scope",
            "id",
            "port",
            *BASE.CREDIT_FIELDS,
            "drop_ratio",
            "data_drops",
        ],
    )
    write_csv(
        flow_path,
        flow_rows,
        [
            "distance",
            "src",
            "dst",
            "bytes",
            "fct_ms",
            "start_ms",
            "finish_ms",
            "total_packet_hops",
            "flow_id",
            "flow_goodput_gbps",
        ],
    )

    print(
        "k  done       drop_ratio  overflow  shaping  "
        "throughput_Gbps  active_Gbps  mean_FCT_ms  p99_FCT_ms"
    )
    for row in summaries:
        print(
            f"{row['distance']:<2} "
            f"{row['completed_flows']:>3}/{row['offered_flows']:<3}  "
            f"{row['credit_drop_ratio']:<11.6f} "
            f"{row['overflow_drops']:<9} "
            f"{row['shaping_drops']:<8} "
            f"{format_metric(row['simulation_throughput_gbps']):<16} "
            f"{format_metric(row['active_makespan_throughput_gbps']):<12} "
            f"{format_metric(row['mean_fct_ms']):<12} "
            f"{format_metric(row['p99_fct_ms'])}"
        )
    print(f"Wrote {summary_path}")
    print(f"Wrote {queue_path}")
    print(f"Wrote {flow_path}")


if __name__ == "__main__":
    main()
