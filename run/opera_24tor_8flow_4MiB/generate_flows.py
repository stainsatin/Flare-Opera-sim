#!/usr/bin/env python3
"""Generate the balanced 8-flow-per-host workload for the 24-ToR Opera topology."""

import argparse
from collections import Counter
from pathlib import Path


TORS = 24
HOSTS_PER_TOR = 4
HOSTS = TORS * HOSTS_PER_TOR
REMOTE_TORS = TORS - 1
DEFAULT_FLOWS_PER_HOST = 8
DEFAULT_FLOW_SIZE_BYTES = 4 * 1024 * 1024
DEFAULT_SUPERSLICE_NS = 55_000
DEFAULT_ACTIVE_WINDOW_NS = 54_000
DEFAULT_START_SUPERSLICES = 2


def select_destination_offsets(flow_count):
    """Select deterministic reverse-paired offsets spread across remote ToRs."""
    if not 1 <= flow_count <= REMOTE_TORS:
        raise ValueError(f"flows per host must be between 1 and {REMOTE_TORS}")

    pair_count = flow_count // 2
    near_offsets = (
        [
            1 + ((2 * index + 1) * (TORS // 2 - 1)) // (2 * pair_count)
            for index in range(pair_count)
        ]
        if pair_count
        else []
    )
    offsets = set(near_offsets)
    offsets.update(TORS - offset for offset in near_offsets)
    if flow_count % 2:
        offsets.add(TORS // 2)

    selected = sorted(offsets)
    if len(selected) != flow_count:
        raise RuntimeError(
            f"failed to select {flow_count} unique destination offsets: {selected}"
        )
    return selected


def build_flows(
    flow_size_bytes=DEFAULT_FLOW_SIZE_BYTES,
    flows_per_host=DEFAULT_FLOWS_PER_HOST,
    base_start_ns=1_000,
    superslice_ns=DEFAULT_SUPERSLICE_NS,
    active_window_ns=DEFAULT_ACTIVE_WINDOW_NS,
    start_superslices=DEFAULT_START_SUPERSLICES,
):
    if flow_size_bytes <= 0:
        raise ValueError("flow size must be positive")
    if base_start_ns < 0:
        raise ValueError("base start time must be non-negative")
    if superslice_ns <= 0:
        raise ValueError("superslice duration must be positive")
    if not 0 < active_window_ns <= superslice_ns:
        raise ValueError("active window must be in (0, superslice_ns]")
    if start_superslices not in (1, 2):
        raise ValueError("start superslices must be 1 or 2")
    if flows_per_host % start_superslices:
        raise ValueError("flows per host must be divisible by start superslices")

    start_phase_ns = base_start_ns % superslice_ns
    usable_window_ns = active_window_ns - start_phase_ns
    if usable_window_ns <= 0:
        raise ValueError("base-start phase must fall before the active-window end")

    offsets = select_destination_offsets(flows_per_host)
    flows_per_receiver_slice = flows_per_host // start_superslices
    slots_per_slice = HOSTS * flows_per_receiver_slice
    if usable_window_ns < slots_per_slice:
        raise ValueError("active window is too short to assign unique start times")

    flows = []
    for source_tor in range(TORS):
        for lane in range(HOSTS_PER_TOR):
            source = source_tor * HOSTS_PER_TOR + lane
            for offset_rank, offset in enumerate(offsets):
                destination_tor = (source_tor + offset) % TORS
                destination = destination_tor * HOSTS_PER_TOR + lane
                start_superslice = offset_rank % start_superslices
                receiver_local_rank = offset_rank // start_superslices

                subslot = receiver_local_rank * HOSTS + destination
                intra_superslice_ns = (
                    (2 * subslot + 1) * usable_window_ns // (2 * slots_per_slice)
                )
                start_ns = (
                    base_start_ns
                    + start_superslice * superslice_ns
                    + intra_superslice_ns
                )
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
                        "start_ns": start_ns,
                        "start_superslice": start_superslice,
                    }
                )

    validate_flows(
        flows, offsets, start_superslices, active_window_ns, superslice_ns
    )
    return flows


def validate_flows(
    flows, offsets, start_superslices, active_window_ns, superslice_ns
):
    flows_per_host = len(offsets)
    expected_count = HOSTS * flows_per_host
    if len(flows) != expected_count:
        raise RuntimeError(f"expected {expected_count} flows, got {len(flows)}")

    expected_hosts = Counter({host: flows_per_host for host in range(HOSTS)})
    if Counter(flow["source"] for flow in flows) != expected_hosts:
        raise RuntimeError("every host must source the configured flow count")
    if Counter(flow["destination"] for flow in flows) != expected_hosts:
        raise RuntimeError("every host must receive the configured flow count")
    if any(flow["source_tor"] == flow["destination_tor"] for flow in flows):
        raise RuntimeError("all flows must cross ToRs")
    if any(
        flow["source"] % HOSTS_PER_TOR != flow["destination"] % HOSTS_PER_TOR
        for flow in flows
    ):
        raise RuntimeError("flows must preserve their host lane")

    tor_pairs = Counter(
        (flow["source_tor"], flow["destination_tor"]) for flow in flows
    )
    if len(tor_pairs) != TORS * flows_per_host:
        raise RuntimeError("the selected directed ToR pairs are incomplete")
    if set(tor_pairs.values()) != {HOSTS_PER_TOR}:
        raise RuntimeError("every selected ToR pair must carry four flows")
    if any((TORS - offset) % TORS not in offsets for offset in offsets):
        raise RuntimeError("destination offsets must be reverse-paired")

    expected_starts = HOSTS * flows_per_host // start_superslices
    starts = Counter(flow["start_superslice"] for flow in flows)
    if starts != Counter(
        {slice_index: expected_starts for slice_index in range(start_superslices)}
    ):
        raise RuntimeError("starts must be balanced across the release slices")
    receiver_starts = Counter(
        (flow["destination"], flow["start_superslice"]) for flow in flows
    )
    expected_receiver_starts = flows_per_host // start_superslices
    if set(receiver_starts.values()) != {expected_receiver_starts}:
        raise RuntimeError("each receiver must get an equal start count per slice")
    if len({flow["start_ns"] for flow in flows}) != len(flows):
        raise RuntimeError("all flows must have distinct start timestamps")
    if any(
        flow["start_ns"] % superslice_ns >= active_window_ns for flow in flows
    ):
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
    parser.add_argument("--flow-size-mib", type=float, default=4)
    parser.add_argument("--flows-per-host", type=int, default=8)
    parser.add_argument("--start-superslices", type=int, choices=(1, 2), default=2)
    parser.add_argument("--base-start-ns", type=int, default=1_000)
    parser.add_argument("--superslice-ns", type=int, default=DEFAULT_SUPERSLICE_NS)
    parser.add_argument(
        "--active-window-ns", type=int, default=DEFAULT_ACTIVE_WINDOW_NS
    )
    args = parser.parse_args()

    try:
        flows = build_flows(
            flow_size_bytes=round(args.flow_size_mib * 1024 * 1024),
            flows_per_host=args.flows_per_host,
            base_start_ns=args.base_start_ns,
            superslice_ns=args.superslice_ns,
            active_window_ns=args.active_window_ns,
            start_superslices=args.start_superslices,
        )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))

    write_trace(args.output, flows)
    offsets = select_destination_offsets(args.flows_per_host)
    flow_size_bytes = round(args.flow_size_mib * 1024 * 1024)
    print(f"Wrote {len(flows)} flows to {args.output}")
    print(
        f"Each flow: {flow_size_bytes} bytes "
        f"({flow_size_bytes / (1024 * 1024):.3f} MiB)"
    )
    print(
        f"Every host sources and receives {args.flows_per_host} flows "
        f"({args.flows_per_host * args.flow_size_mib:.3f} MiB per host)"
    )
    print(
        f"Every ToR sources and receives "
        f"{HOSTS_PER_TOR * args.flows_per_host} flows"
    )
    print(f"Destination ToR offsets: {offsets}")
    print(
        f"Starts: {len(flows) // args.start_superslices} flows/superslice "
        f"across {args.start_superslices} superslices"
    )
    print("All starts are uniquely staggered inside optical active windows")


if __name__ == "__main__":
    main()
