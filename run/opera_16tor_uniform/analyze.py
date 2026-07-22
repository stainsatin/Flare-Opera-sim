#!/usr/bin/env python3
"""Analyze the balanced 64-flow experiment on the dynamic 16-ToR Opera fabric."""

import argparse
import csv
import math
import statistics
from collections import defaultdict
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
FLOW_CREDIT_EXTRA_FIELDS = (
    "topology",
    "path_hops_min",
    "path_hops_max",
    "path_hops_sum",
)
MSS_BYTES = 1436
HOST_RATE_GBPS = 100.0


def percentile(values, probability):
    if not values:
        return ""
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def jain_fairness(values):
    values = [value for value in values if value >= 0]
    if not values or sum(value * value for value in values) == 0:
        return ""
    return sum(values) ** 2 / (len(values) * sum(value * value for value in values))


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_trace(path, hosts_per_tor, superslice_ns):
    rows = []
    with path.open(encoding="ascii") as stream:
        for flow_id, line in enumerate(stream):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != 4:
                raise RuntimeError(f"{path}:{flow_id + 1}: expected 4 fields")
            source, destination, flow_size, start_ns = map(int, fields)
            rows.append(
                {
                    "flow_id": len(rows),
                    "source": source,
                    "destination": destination,
                    "source_tor": source // hosts_per_tor,
                    "destination_tor": destination // hosts_per_tor,
                    "lane": source % hosts_per_tor,
                    "tor_offset": (
                        destination // hosts_per_tor - source // hosts_per_tor
                    )
                    % (64 // hosts_per_tor),
                    "bytes": flow_size,
                    "start_ns": start_ns,
                    "start_ms": start_ns / 1_000_000.0,
                    "start_superslice": (start_ns // superslice_ns)
                    % (64 // hosts_per_tor),
                }
            )
    if not rows:
        raise RuntimeError(f"{path} contains no flows")
    return rows


def parse_log(path):
    queue_rows = []
    data_stats = {}
    flow_credits = {}
    fcts = {}
    utilization = []
    input_load = []
    unfinished_markers = set()
    topology_clip = {"credit": 0, "data": 0, "control": 0, "other": 0}
    topology_wrong_dst = {"credit": 0, "data": 0, "control": 0, "other": 0}

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
                record["credit_drop_ratio"] = (
                    record["dropped"] / record["received"]
                    if record["received"]
                    else 0.0
                )
                queue_rows.append(record)
            elif fields[0] == "DataQueueStats" and len(fields) >= 5:
                data_stats[(fields[1], int(fields[2]), int(fields[3]))] = {
                    "data_drops": int(fields[4]),
                    "max_data_queue_bytes": int(fields[5]) if len(fields) >= 6 else 0,
                    "current_data_queue_bytes": int(fields[6]) if len(fields) >= 7 else 0,
                }
            elif fields[0] == "FlowCreditStats" and len(fields) >= 17:
                flow_id = int(fields[1])
                record = {
                    "sender": int(fields[2]),
                    "receiver": int(fields[3]),
                    "last_credit_path_hops": int(fields[4]),
                }
                record.update(
                    {
                        name: int(value)
                        for name, value in zip(FLOW_CREDIT_FIELDS, fields[5:17])
                    }
                )
                if len(fields) >= 21:
                    record.update(
                        {
                            name: int(value)
                            for name, value in zip(
                                FLOW_CREDIT_EXTRA_FIELDS, fields[17:21]
                            )
                        }
                    )
                else:
                    record.update(
                        {
                            "topology": 0,
                            "path_hops_min": record["last_credit_path_hops"],
                            "path_hops_max": record["last_credit_path_hops"],
                            "path_hops_sum": (
                                record["last_credit_path_hops"] * record["generated"]
                            ),
                        }
                    )
                flow_credits[flow_id] = record
            elif fields[0] == "FCT" and len(fields) >= 8:
                flow_id = int(fields[7])
                fct_ms = float(fields[4])
                start_ms = float(fields[5])
                fcts[flow_id] = {
                    "source": int(fields[1]),
                    "destination": int(fields[2]),
                    "bytes": int(fields[3]),
                    "fct_ms": fct_ms,
                    "start_ms": start_ms,
                    "finish_ms": start_ms + fct_ms,
                    "total_data_hops": int(fields[6]),
                }
            elif fields[0] == "UNFINISHED" and len(fields) >= 2:
                unfinished_markers.add(int(fields[1]))
            elif fields[0] == "Util" and len(fields) >= 3:
                utilization.append((float(fields[2]), float(fields[1])))
            elif fields[0] == "Input" and len(fields) >= 5:
                input_load.append((float(fields[4]), float(fields[1])))
            elif fields[0] == "TopologyClipStats" and len(fields) >= 5:
                topology_clip = dict(
                    zip(topology_clip, (int(value) for value in fields[1:5]))
                )
            elif fields[0] == "TopologyWrongDstStats" and len(fields) >= 5:
                topology_wrong_dst = dict(
                    zip(topology_wrong_dst, (int(value) for value in fields[1:5]))
                )

    if not queue_rows:
        raise RuntimeError(
            f"{path} has no final CreditStats records; simulation likely did not finish"
        )
    for row in queue_rows:
        key = (row["scope"], row["id"], row["port"])
        row.update(
            data_stats.get(
                key,
                {
                    "data_drops": 0,
                    "max_data_queue_bytes": 0,
                    "current_data_queue_bytes": 0,
                },
            )
        )
    return {
        "queues": queue_rows,
        "flow_credits": flow_credits,
        "fcts": fcts,
        "utilization": utilization,
        "input_load": input_load,
        "unfinished_markers": unfinished_markers,
        "topology_clip": topology_clip,
        "topology_wrong_dst": topology_wrong_dst,
    }


def build_flow_rows(trace, parsed):
    rows = []
    for expected in trace:
        flow_id = expected["flow_id"]
        credit = parsed["flow_credits"].get(flow_id, {})
        fct = parsed["fcts"].get(flow_id, {})
        if credit and (
            credit["sender"] != expected["source"]
            or credit["receiver"] != expected["destination"]
        ):
            raise RuntimeError(f"flow {flow_id}: credit endpoints do not match trace")
        if fct and (
            fct["source"] != expected["source"]
            or fct["destination"] != expected["destination"]
            or fct["bytes"] != expected["bytes"]
        ):
            raise RuntimeError(f"flow {flow_id}: FCT record does not match trace")

        row = {**expected, "completed": bool(fct)}
        row.update(
            {
                "finish_ms": fct.get("finish_ms", ""),
                "fct_ms": fct.get("fct_ms", ""),
                "flow_goodput_gbps": (
                    expected["bytes"] * 8.0 / (fct["fct_ms"] * 1_000_000.0)
                    if fct and fct["fct_ms"] > 0
                    else ""
                ),
                "total_data_hops": fct.get("total_data_hops", ""),
                "data_hops_per_nominal_packet": (
                    fct["total_data_hops"]
                    / math.ceil(expected["bytes"] / MSS_BYTES)
                    if fct
                    else ""
                ),
                "unfinished_marker": flow_id in parsed["unfinished_markers"],
            }
        )
        for name in (*FLOW_CREDIT_FIELDS, *FLOW_CREDIT_EXTRA_FIELDS):
            row[name] = credit.get(name, 0)
        row["last_credit_path_hops"] = credit.get("last_credit_path_hops", "")
        row["mean_credit_path_hops"] = (
            row["path_hops_sum"] / row["generated"] if row["generated"] else ""
        )
        row["credit_drop_ratio"] = (
            row["dropped"] / row["generated"] if row["generated"] else 0.0
        )
        row["credit_delivery_ratio"] = (
            row["delivered"] / row["generated"] if row["generated"] else 0.0
        )
        row["waste_link_bytes"] = row["waste_hops"] * 64
        rows.append(row)
    return rows


def mean_sample(samples, first_ms, last_ms, aggregate_capacity_gbps):
    values = [value for timestamp, value in samples if first_ms <= timestamp <= last_ms]
    return (
        statistics.fmean(values) * aggregate_capacity_gbps if values else ""
    )


def build_summary(flow_rows, queue_rows, parsed, simtime_s, hosts_per_tor, cycle_us):
    completed = [row for row in flow_rows if row["completed"]]
    fcts = [row["fct_ms"] for row in completed]
    goodputs = [row["flow_goodput_gbps"] for row in completed]
    first_start_ms = min(row["start_ms"] for row in flow_rows)
    last_finish_ms = max(
        (row["finish_ms"] for row in completed), default=first_start_ms
    )
    makespan_ms = max(last_finish_ms - first_start_ms, 0.0)
    completed_bytes = sum(row["bytes"] for row in completed)
    generated = sum(row["generated"] for row in flow_rows)
    delivered = sum(row["delivered"] for row in flow_rows)
    dropped = sum(row["dropped"] for row in flow_rows)
    topology_credit_drops = sum(row["topology"] for row in flow_rows)
    host_queues = [row for row in queue_rows if row["scope"] == "host"]
    tor_queues = [row for row in queue_rows if row["scope"] == "tor"]
    uplink_queues = [row for row in tor_queues if row["port"] >= hosts_per_tor]
    host_transmitted = sum(row["transmitted"] for row in host_queues)
    tor_queue_drops = sum(row["dropped"] for row in tor_queues)
    aggregate_capacity_gbps = len(host_queues) * HOST_RATE_GBPS

    tor_active_goodputs = []
    for tor in sorted({row["source_tor"] for row in flow_rows}):
        selected = [row for row in completed if row["source_tor"] == tor]
        if not selected:
            tor_active_goodputs.append(0.0)
            continue
        start = min(row["start_ms"] for row in selected)
        finish = max(row["finish_ms"] for row in selected)
        tor_active_goodputs.append(
            sum(row["bytes"] for row in selected) * 8.0
            / ((finish - start) * 1_000_000.0)
            if finish > start
            else 0.0
        )

    known_data_drops = (
        sum(row["data_drops"] for row in queue_rows)
        + parsed["topology_clip"]["data"]
        + parsed["topology_wrong_dst"]["data"]
    )
    nominal_data_packets = sum(
        math.ceil(row["bytes"] / MSS_BYTES) for row in flow_rows
    )
    start_span_ms = max(row["start_ms"] for row in flow_rows) - first_start_ms

    return {
        "offered_flows": len(flow_rows),
        "completed_flows": len(completed),
        "incomplete_flows": len(flow_rows) - len(completed),
        "completion_ratio": len(completed) / len(flow_rows),
        "unfinished_markers": sum(row["unfinished_marker"] for row in flow_rows),
        "flow_size_bytes": flow_rows[0]["bytes"] if len({row["bytes"] for row in flow_rows}) == 1 else "",
        "offered_bytes": sum(row["bytes"] for row in flow_rows),
        "completed_bytes": completed_bytes,
        "simulation_time_s": simtime_s,
        "start_span_ms": start_span_ms,
        "active_makespan_ms": makespan_ms,
        "topology_cycles_in_makespan": makespan_ms * 1000.0 / cycle_us if cycle_us else "",
        "simulation_throughput_gbps": completed_bytes * 8.0 / (simtime_s * 1e9),
        "active_makespan_throughput_gbps": (
            completed_bytes * 8.0 / (makespan_ms * 1e6) if makespan_ms else ""
        ),
        "mean_sampled_goodput_gbps": mean_sample(
            parsed["utilization"], first_start_ms, last_finish_ms, aggregate_capacity_gbps
        ),
        "mean_sampled_input_gbps": mean_sample(
            parsed["input_load"], first_start_ms, last_finish_ms, aggregate_capacity_gbps
        ),
        "mean_fct_ms": statistics.fmean(fcts) if fcts else "",
        "median_fct_ms": percentile(fcts, 0.50),
        "p95_fct_ms": percentile(fcts, 0.95),
        "p99_fct_ms": percentile(fcts, 0.99),
        "max_fct_ms": max(fcts) if fcts else "",
        "flow_goodput_jain": jain_fairness(goodputs),
        "tor_goodput_jain": jain_fairness(tor_active_goodputs),
        "mean_credit_path_hops": (
            sum(row["path_hops_sum"] for row in flow_rows) / generated
            if generated
            else ""
        ),
        "min_credit_path_hops": min(
            (row["path_hops_min"] for row in flow_rows if row["generated"]), default=""
        ),
        "max_credit_path_hops": max(
            (row["path_hops_max"] for row in flow_rows if row["generated"]), default=""
        ),
        "generated_credits": generated,
        "credits_left_endpoints": host_transmitted,
        "delivered_credits": delivered,
        "credit_drops": dropped,
        "credit_drop_ratio": dropped / generated if generated else 0.0,
        "residual_or_unaccounted_credits": generated - delivered - dropped,
        "endpoint_credit_drops": sum(row["dropped"] for row in host_queues),
        "tor_queue_credit_drops": tor_queue_drops,
        "topology_credit_drops": topology_credit_drops,
        "queue_drop_counter_difference": (
            dropped - topology_credit_drops - sum(row["dropped"] for row in queue_rows)
        ),
        "topology_drop_counter_difference": topology_credit_drops
        - parsed["topology_clip"]["credit"]
        - parsed["topology_wrong_dst"]["credit"],
        "path_conditional_credit_drop_ratio": (
            (tor_queue_drops + topology_credit_drops) / host_transmitted
            if host_transmitted
            else 0.0
        ),
        "credit_delivery_ratio": delivered / generated if generated else 0.0,
        "overflow_credit_drops": sum(row["overflow"] for row in flow_rows),
        "timeout_credit_drops": sum(row["timeout"] for row in flow_rows),
        "shaping_credit_drops": sum(row["shaping"] for row in flow_rows),
        "tentative_credit_drops": sum(row["tentative"] for row in flow_rows),
        "topology_clipped_credits": parsed["topology_clip"]["credit"],
        "topology_wrong_dst_credits": parsed["topology_wrong_dst"]["credit"],
        "shaping_checks": sum(row["shaping_checks"] for row in flow_rows),
        "shaping_admitted": sum(row["shaping_admitted"] for row in flow_rows),
        "shaping_admission_ratio": (
            sum(row["shaping_admitted"] for row in flow_rows)
            / sum(row["shaping_checks"] for row in flow_rows)
            if sum(row["shaping_checks"] for row in flow_rows)
            else ""
        ),
        "credit_waste_hops": sum(row["waste_hops"] for row in flow_rows),
        "credit_waste_hops_per_generated": (
            sum(row["waste_hops"] for row in flow_rows) / generated if generated else 0.0
        ),
        "max_credit_queue_packets": max(
            (row["max_queued"] for row in queue_rows), default=0
        ),
        "max_uplink_credit_queue_packets": max(
            (row["max_queued"] for row in uplink_queues), default=0
        ),
        "data_queue_drops": sum(row["data_drops"] for row in queue_rows),
        "topology_clipped_data": parsed["topology_clip"]["data"],
        "topology_wrong_dst_data": parsed["topology_wrong_dst"]["data"],
        "known_data_drops": known_data_drops,
        "known_data_drops_per_nominal_packet": (
            known_data_drops / nominal_data_packets if nominal_data_packets else 0.0
        ),
        "max_data_queue_packets": max(
            (row["max_data_queue_bytes"] / 1500.0 for row in queue_rows), default=0.0
        ),
    }


def build_tor_rows(flow_rows, queue_rows, hosts_per_tor):
    tor_count = max(row["source_tor"] for row in flow_rows) + 1
    queues_by_tor = defaultdict(list)
    for queue in queue_rows:
        tor = queue["id"] // hosts_per_tor if queue["scope"] == "host" else queue["id"]
        queues_by_tor[tor].append(queue)

    rows = []
    for tor in range(tor_count):
        outgoing = [row for row in flow_rows if row["source_tor"] == tor]
        incoming = [row for row in flow_rows if row["destination_tor"] == tor]
        outgoing_done = [row for row in outgoing if row["completed"]]
        incoming_done = [row for row in incoming if row["completed"]]
        queues = queues_by_tor[tor]
        host_queues = [row for row in queues if row["scope"] == "host"]
        tor_queues = [row for row in queues if row["scope"] == "tor"]
        rows.append(
            {
                "tor": tor,
                "outgoing_flows": len(outgoing),
                "incoming_flows": len(incoming),
                "completed_outgoing_flows": len(outgoing_done),
                "completed_incoming_flows": len(incoming_done),
                "mean_outgoing_fct_ms": (
                    statistics.fmean(row["fct_ms"] for row in outgoing_done)
                    if outgoing_done
                    else ""
                ),
                "mean_incoming_fct_ms": (
                    statistics.fmean(row["fct_ms"] for row in incoming_done)
                    if incoming_done
                    else ""
                ),
                "receiver_generated_credits": sum(row["generated"] for row in incoming),
                "receiver_delivered_credits": sum(row["delivered"] for row in incoming),
                "receiver_credit_drops": sum(row["dropped"] for row in incoming),
                "receiver_credit_drop_ratio": (
                    sum(row["dropped"] for row in incoming)
                    / sum(row["generated"] for row in incoming)
                    if sum(row["generated"] for row in incoming)
                    else 0.0
                ),
                "endpoint_credit_drops": sum(row["dropped"] for row in host_queues),
                "tor_queue_credit_drops": sum(row["dropped"] for row in tor_queues),
                "tor_queue_data_drops": sum(row["data_drops"] for row in queues),
                "max_credit_queue_packets": max(
                    (row["max_queued"] for row in queues), default=0
                ),
                "max_data_queue_packets": max(
                    (row["max_data_queue_bytes"] / 1500.0 for row in queues),
                    default=0.0,
                ),
            }
        )
    return rows


def add_queue_labels(queue_rows, hosts_per_tor):
    for row in queue_rows:
        if row["scope"] == "host":
            row["tor"] = row["id"] // hosts_per_tor
            row["role"] = "host_nic"
            row["rotor"] = ""
        else:
            row["tor"] = row["id"]
            if row["port"] < hosts_per_tor:
                row["role"] = "tor_downlink"
                row["rotor"] = ""
            else:
                row["role"] = "tor_uplink"
                row["rotor"] = row["port"] - hosts_per_tor
        row["max_data_queue_packets"] = row["max_data_queue_bytes"] / 1500.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("--simtime", type=float, required=True)
    parser.add_argument("--hosts-per-tor", type=int, default=4)
    timing = parser.add_mutually_exclusive_group()
    timing.add_argument("--superslice-us", type=float)
    timing.add_argument("--superslice-ns", type=int)
    parser.add_argument("--cycle-superslices", type=int, default=16)
    args = parser.parse_args()
    if args.simtime <= 0:
        parser.error("--simtime must be positive")
    if args.superslice_ns is not None and args.superslice_ns <= 0:
        parser.error("--superslice-ns must be positive")
    if args.superslice_us is not None and args.superslice_us <= 0:
        parser.error("--superslice-us must be positive")

    if args.superslice_ns is not None:
        superslice_ns = args.superslice_ns
    else:
        superslice_ns = round(
            (args.superslice_us if args.superslice_us is not None else 14.5) * 1000
        )

    log_path = args.results / "uniform.log"
    trace_path = args.results / "traffic" / "uniform.htsim"
    if not log_path.is_file():
        parser.error(f"missing simulator log: {log_path}")
    if not trace_path.is_file():
        parser.error(f"missing traffic trace: {trace_path}")

    trace = parse_trace(
        trace_path,
        args.hosts_per_tor,
        superslice_ns,
    )
    parsed = parse_log(log_path)
    flow_rows = build_flow_rows(trace, parsed)
    add_queue_labels(parsed["queues"], args.hosts_per_tor)
    summary = build_summary(
        flow_rows,
        parsed["queues"],
        parsed,
        args.simtime,
        args.hosts_per_tor,
        superslice_ns / 1000.0 * args.cycle_superslices,
    )
    tor_rows = build_tor_rows(flow_rows, parsed["queues"], args.hosts_per_tor)

    write_csv(args.results / "summary.csv", [summary], list(summary))
    write_csv(args.results / "per_flow.csv", flow_rows, list(flow_rows[0]))
    write_csv(args.results / "per_queue.csv", parsed["queues"], list(parsed["queues"][0]))
    write_csv(args.results / "per_tor.csv", tor_rows, list(tor_rows[0]))

    print(
        f"Completed: {summary['completed_flows']}/{summary['offered_flows']} "
        f"({summary['completion_ratio']:.2%})"
    )
    print(
        f"FCT ms: mean={summary['mean_fct_ms']} "
        f"p95={summary['p95_fct_ms']} p99={summary['p99_fct_ms']}"
    )
    print(
        f"Throughput: active={summary['active_makespan_throughput_gbps']} Gbps, "
        f"sampled={summary['mean_sampled_goodput_gbps']} Gbps"
    )
    print(
        f"Credit drop: {summary['credit_drops']}/{summary['generated_credits']} "
        f"({summary['credit_drop_ratio']:.2%}); "
        f"path conditional={summary['path_conditional_credit_drop_ratio']:.2%}"
    )
    print(
        f"Known data drops: {summary['known_data_drops']} "
        f"(queue={summary['data_queue_drops']}, "
        f"clip={summary['topology_clipped_data']}, "
        f"wrong-dst={summary['topology_wrong_dst_data']})"
    )
    print(f"Wrote {args.results / 'summary.csv'}")
    print(f"Wrote {args.results / 'per_flow.csv'}")
    print(f"Wrote {args.results / 'per_queue.csv'}")
    print(f"Wrote {args.results / 'per_tor.csv'}")


if __name__ == "__main__":
    main()
