#!/usr/bin/env python3
"""Generate a lighter balanced workload for the 24-ToR Opera topology."""

import argparse
from collections import Counter
from pathlib import Path


TORS = 24
HOSTS_PER_TOR = 4
HOSTS = TORS * HOSTS_PER_TOR
FLOW_OFFSETS = (3, 9, 15, 21)
DEFAULT_FLOW_SIZE_BYTES = 2 * 1024 * 1024
DEFAULT_SUPERSLICE_NS = 55_000
DEFAULT_ACTIVE_WINDOW_NS = 54_000
DEFAULT_START_SUPERSLICES = 8
SUPPORTED_START_SUPERSLICES = (4, 8, 16)


def build_flows(
    flow_size_bytes=DEFAULT_FLOW_SIZE_BYTES,
    base_start_ns=1_000,
    superslice_ns=DEFAULT_SUPERSLICE_NS,
    active_window_ns=DEFAULT_ACTIVE_WINDOW_NS,
    start_superslices=DEFAULT_START_SUPERSLICES,
):
    if flow_size_bytes <= 0:
        raise ValueError("flow size must be positive")
    if superslice_ns <= 0:
        raise ValueError("superslice duration must be positive")
    if not 0 < active_window_ns <= superslice_ns:
        raise ValueError("active window must be in (0, superslice_ns]")
    if start_superslices not in SUPPORTED_START_SUPERSLICES:
        raise ValueError("start superslices must be 4, 8, or 16")
    if not 0 <= base_start_ns < active_window_ns:
        raise ValueError("base-start phase must fall inside the active window")

    release_stride = start_superslices // len(FLOW_OFFSETS)
    flows = []
    for source_tor in range(TORS):
        for lane in range(HOSTS_PER_TOR):
            source = source_tor * HOSTS_PER_TOR + lane
            for offset_rank, offset in enumerate(FLOW_OFFSETS):
                destination_tor = (source_tor + offset) % TORS
                destination = destination_tor * HOSTS_PER_TOR + lane
                start_superslice = (
                    release_stride * offset_rank + lane
                ) % start_superslices
                flows.append(
                    {
                        "flow_id": len(flows),
                        "source": source,
                        "destination": destination,
                        "source_tor": source_tor,
                        "destination_tor": destination_tor,
                        "lane": lane,
                        "offset": offset,
                        "offset_rank": offset_rank,
                        "bytes": flow_size_bytes,
                        "start_superslice": start_superslice,
                    }
                )

    usable_window_ns = active_window_ns - base_start_ns
    for slice_index in range(start_superslices):
        released = sorted(
            (flow for flow in flows if flow["start_superslice"] == slice_index),
            key=lambda flow: (flow["destination"], flow["source"]),
        )
        if usable_window_ns < len(released):
            raise ValueError("active window is too short for unique timestamps")
        for slot, flow in enumerate(released):
            intra_slice_ns = (
                (2 * slot + 1) * usable_window_ns // (2 * len(released))
            )
            flow["start_ns"] = (
                base_start_ns
                + slice_index * superslice_ns
                + intra_slice_ns
            )

    validate_flows(
        flows, start_superslices, superslice_ns, active_window_ns
    )
    return sorted(flows, key=lambda flow: flow["flow_id"])


def validate_flows(flows, start_superslices, superslice_ns, active_window_ns):
    flows_per_host = len(FLOW_OFFSETS)
    if len(flows) != HOSTS * flows_per_host:
        raise RuntimeError("unexpected flow count")

    expected_hosts = Counter({host: flows_per_host for host in range(HOSTS)})
    if Counter(flow["source"] for flow in flows) != expected_hosts:
        raise RuntimeError("source Host load is not balanced")
    if Counter(flow["destination"] for flow in flows) != expected_hosts:
        raise RuntimeError("receiver Host load is not balanced")
    if any(flow["source_tor"] == flow["destination_tor"] for flow in flows):
        raise RuntimeError("all flows must cross ToRs")
    if any(
        flow["source"] % HOSTS_PER_TOR != flow["destination"] % HOSTS_PER_TOR
        for flow in flows
    ):
        raise RuntimeError("flows must preserve their Host lane")

    tor_pairs = Counter(
        (flow["source_tor"], flow["destination_tor"]) for flow in flows
    )
    if len(tor_pairs) != TORS * flows_per_host:
        raise RuntimeError("directed ToR pairs are incomplete")
    if set(tor_pairs.values()) != {HOSTS_PER_TOR}:
        raise RuntimeError("every selected ToR pair must carry four flows")
    if any((TORS - offset) % TORS not in FLOW_OFFSETS for offset in FLOW_OFFSETS):
        raise RuntimeError("destination offsets must be reverse-paired")

    expected_per_slice = len(flows) // start_superslices
    starts = Counter(flow["start_superslice"] for flow in flows)
    if starts != Counter(
        {slice_index: expected_per_slice for slice_index in range(start_superslices)}
    ):
        raise RuntimeError("global starts are not balanced across slices")

    expected_per_tor_slice = HOSTS_PER_TOR * flows_per_host // start_superslices
    source_tor_starts = Counter(
        (flow["source_tor"], flow["start_superslice"]) for flow in flows
    )
    destination_tor_starts = Counter(
        (flow["destination_tor"], flow["start_superslice"]) for flow in flows
    )
    expected_tor_starts = Counter(
        {
            (tor, slice_index): expected_per_tor_slice
            for tor in range(TORS)
            for slice_index in range(start_superslices)
        }
    )
    if source_tor_starts != expected_tor_starts:
        raise RuntimeError("source ToR starts are not balanced")
    if destination_tor_starts != expected_tor_starts:
        raise RuntimeError("receiver ToR starts are not balanced")

    receiver_host_starts = Counter(
        (flow["destination"], flow["start_superslice"]) for flow in flows
    )
    if max(receiver_host_starts.values()) != 1:
        raise RuntimeError("a receiver Host gets multiple new flows in one slice")
    if len({flow["start_ns"] for flow in flows}) != len(flows):
        raise RuntimeError("flow start timestamps must be unique")
    if any(flow["start_ns"] % superslice_ns >= active_window_ns for flow in flows):
        raise RuntimeError("a flow starts during optical reconfiguration")


def write_trace(path, flows):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        f"{flow['source']} {flow['destination']} {flow['bytes']} {flow['start_ns']}"
        for flow in flows
    ]
    path.write_text("\n".join(rows) + "\n", encoding="ascii")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--flow-size-mib", type=float, default=2)
    parser.add_argument("--base-start-ns", type=int, default=1_000)
    parser.add_argument("--superslice-ns", type=int, default=DEFAULT_SUPERSLICE_NS)
    parser.add_argument(
        "--active-window-ns", type=int, default=DEFAULT_ACTIVE_WINDOW_NS
    )
    parser.add_argument(
        "--start-superslices",
        type=int,
        choices=SUPPORTED_START_SUPERSLICES,
        default=DEFAULT_START_SUPERSLICES,
    )
    args = parser.parse_args()

    try:
        flow_size_bytes = round(args.flow_size_mib * 1024 * 1024)
        flows = build_flows(
            flow_size_bytes=flow_size_bytes,
            base_start_ns=args.base_start_ns,
            superslice_ns=args.superslice_ns,
            active_window_ns=args.active_window_ns,
            start_superslices=args.start_superslices,
        )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))

    write_trace(args.output, flows)
    per_slice = len(flows) // args.start_superslices
    per_tor_slice = HOSTS_PER_TOR * len(FLOW_OFFSETS) // args.start_superslices
    print(f"Wrote {len(flows)} flows to {args.output}")
    print(f"Each flow: {flow_size_bytes} bytes ({args.flow_size_mib:.3f} MiB)")
    print("Every Host sources four flows and receives four flows")
    print("Every ToR sources 16 flows and receives 16 flows")
    print(f"Destination ToR offsets: {FLOW_OFFSETS}")
    print(
        f"Starts: {per_slice} flows/superslice across "
        f"{args.start_superslices} superslices"
    )
    print(
        f"Each ToR starts {per_tor_slice} outgoing and "
        f"{per_tor_slice} incoming flows per superslice"
    )
    print("Each receiver Host gets at most one new flow per superslice")
    print("All starts are unique and inside optical active windows")


if __name__ == "__main__":
    main()
