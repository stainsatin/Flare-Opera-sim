#!/usr/bin/env python3
"""Analyze short-only and long-only multi-bottleneck experiments."""

import argparse
import importlib.util
from pathlib import Path


HERE = Path(__file__).resolve().parent
COMMON_SPEC = importlib.util.spec_from_file_location(
    "multibottleneck_order_analyzer", HERE / "analyze.py"
)
COMMON = importlib.util.module_from_spec(COMMON_SPEC)
COMMON_SPEC.loader.exec_module(COMMON)

CASES = ("short_only", "long_only")
CLASS_BY_CASE = {"short_only": "short", "long_only": "long"}
FLOWS_PER_ROUND = 5


def parse_trace(path, case):
    expected_class = CLASS_BY_CASE[case]
    rows = []
    with path.open(encoding="ascii") as stream:
        for line_number, line in enumerate(stream, start=1):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 4:
                raise RuntimeError(f"{path}:{line_number}: expected 4 fields")
            sender, receiver, flow_size, start_ns = map(int, fields)
            flow_class, path_hops = COMMON.classify(sender, receiver)
            if flow_class != expected_class:
                raise RuntimeError(
                    f"{path}:{line_number}: {case} contains a {flow_class} flow"
                )
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
    if len(rows) % FLOWS_PER_ROUND:
        raise RuntimeError(
            f"{path} contains {len(rows)} flows, not complete five-flow rounds"
        )
    return rows


def build_comparison(summaries):
    lookup = {row["case"]: row for row in summaries}
    row = {}
    for metric in (
        "mean_fct_ms",
        "p95_fct_ms",
        "p99_fct_ms",
        "mean_flow_goodput_gbps",
        "credit_drop_ratio",
        "credit_delivery_ratio",
        "waste_hops_per_generated",
        "waste_hops_per_drop",
    ):
        short_value = lookup["short_only"][metric]
        long_value = lookup["long_only"][metric]
        row[f"short_only_{metric}"] = short_value
        row[f"long_only_{metric}"] = long_value
        row[f"long_vs_short_{metric}_pct"] = COMMON.pct_change(
            long_value, short_value
        )
    return [row]


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
        trace = parse_trace(trace_path, case)
        queues, flow_credits, fcts, data_drops, topology_losses = COMMON.parse_log(
            log_path
        )
        flow_rows = COMMON.build_flow_rows(case, trace, flow_credits, fcts)
        all_flows.extend(flow_rows)
        for queue in queues:
            all_queues.append({"case": case, **queue})

        flow_class = CLASS_BY_CASE[case]
        summaries.append(
            COMMON.summarize(
                flow_rows,
                case,
                flow_class,
                args.simtime,
                data_drops,
                topology_losses,
            )
        )
        for receiver in COMMON.RECEIVERS:
            receiver_flows = [
                row for row in flow_rows if row["receiver"] == receiver
            ]
            receiver_rows.append(
                {
                    "receiver": receiver,
                    **COMMON.summarize(
                        receiver_flows, case, flow_class, args.simtime
                    ),
                }
            )

    COMMON.write_csv(args.results / "summary.csv", summaries)
    COMMON.write_csv(args.results / "per_receiver.csv", receiver_rows)
    COMMON.write_csv(args.results / "per_flow.csv", all_flows)
    COMMON.write_csv(
        args.results / "per_queue.csv",
        all_queues,
        ["case", "scope", "id", "port", *COMMON.CREDIT_FIELDS, "drop_ratio"],
    )
    if set(args.cases) == set(CASES):
        COMMON.write_csv(
            args.results / "isolation_comparison.csv",
            build_comparison(summaries),
        )

    print(
        "case        class  done      mean_FCT_ms  p99_FCT_ms  "
        "mean_flow_Gbps  drop_ratio  waste/drop"
    )
    for row in summaries:
        print(
            f"{row['case']:<11} {row['flow_class']:<6} "
            f"{row['completed_flows']:>3}/{row['offered_flows']:<3} "
            f"{row['mean_fct_ms']:<12.6f} {row['p99_fct_ms']:<11.6f} "
            f"{row['mean_flow_goodput_gbps']:<15.3f} "
            f"{row['credit_drop_ratio']:<11.6f} "
            f"{row['waste_hops_per_drop']:.4f}"
        )


if __name__ == "__main__":
    main()
