#!/usr/bin/env python3
"""Generate balanced lane-preserving traffic for the two-host Opera topology."""

import argparse
from collections import Counter
from math import gcd
from pathlib import Path


TORS = 108
HOSTS_PER_TOR = 2
HOSTS = TORS * HOSTS_PER_TOR
REMOTE_TORS = TORS - 1
FLOW_COUNT = HOSTS * REMOTE_TORS
DEFAULT_FLOW_SIZE_BYTES = 1 * 1024 * 1024
DEFAULT_SUPERSLICE_NS = 54_500
DEFAULT_START_STRIDE = 53


def select_destination_offsets(fanout):
    """Select a deterministic, symmetric subset of the 107 remote ToRs."""
    if not 1 <= fanout <= REMOTE_TORS:
        raise ValueError(f"fanout must be between 1 and {REMOTE_TORS}")

    pair_count = fanout // 2
    near_offsets = []
    if pair_count:
        # Pick midpoint samples from offsets 1..53. Adding each reverse offset
        # keeps the directed traffic matrix symmetric without clustering IDs.
        near_offsets = [
            1 + ((2 * index + 1) * (TORS // 2 - 1)) // (2 * pair_count)
            for index in range(pair_count)
        ]

    offsets = set(near_offsets)
    offsets.update(TORS - offset for offset in near_offsets)
    if fanout % 2:
        offsets.add(TORS // 2)

    selected = sorted(offsets)
    if len(selected) != fanout:
        raise RuntimeError(
            f"failed to select {fanout} unique destination offsets: {selected}"
        )
    return selected


def build_flows(
    flow_size_bytes=DEFAULT_FLOW_SIZE_BYTES,
    start_mode="cycle_spread",
    base_start_ns=1_000,
    superslice_ns=DEFAULT_SUPERSLICE_NS,
    fanout=REMOTE_TORS,
    start_stride=DEFAULT_START_STRIDE,
    active_window_ns=None,
    spread_superslices=TORS,
):
    if flow_size_bytes <= 0:
        raise ValueError("flow size must be positive")
    if base_start_ns < 0:
        raise ValueError("base start time must be non-negative")
    if superslice_ns <= 0:
        raise ValueError("superslice duration must be positive")
    if start_mode not in ("cycle_spread", "staggered", "synchronized"):
        raise ValueError(f"unsupported start mode: {start_mode}")
    selected_offsets = select_destination_offsets(fanout)
    if active_window_ns is None:
        active_window_ns = superslice_ns
    if not 0 < active_window_ns <= superslice_ns:
        raise ValueError("active window must be in (0, superslice_ns]")
    if not 1 <= spread_superslices <= TORS:
        raise ValueError(f"spread superslices must be between 1 and {TORS}")
    if HOSTS % spread_superslices != 0:
        raise ValueError(f"spread superslices must divide the {HOSTS} hosts")
    start_phase_ns = base_start_ns % superslice_ns
    stagger_window_ns = active_window_ns - start_phase_ns
    if start_mode == "staggered":
        if gcd(start_stride, spread_superslices) != 1:
            raise ValueError(
                f"start stride must be coprime with {spread_superslices}"
            )
        starts_per_superslice = HOSTS * fanout // spread_superslices
        if stagger_window_ns < starts_per_superslice:
            raise ValueError(
                "active window after the base-start phase is too short "
                "to stagger all starts"
            )

    flows = []
    for source_tor in range(TORS):
        for lane in range(HOSTS_PER_TOR):
            source = source_tor * HOSTS_PER_TOR + lane
            for offset_rank, offset in enumerate(selected_offsets):
                destination_tor = (source_tor + offset) % TORS
                destination = destination_tor * HOSTS_PER_TOR + lane
                if start_mode == "cycle_spread":
                    start_superslice = offset - 1
                    start_ns = base_start_ns + start_superslice * superslice_ns
                elif start_mode == "staggered":
                    # Phase by receiver so each receiver sees at most one new
                    # flow in a superslice. Each superslice has exactly
                    # 2*fanout starts, and those starts use distinct subslots.
                    receiver_residue = destination % spread_superslices
                    receiver_copy = destination // spread_superslices
                    start_superslice = (
                        receiver_residue + start_stride * offset_rank
                    ) % spread_superslices
                    receiver_copies = HOSTS // spread_superslices
                    subslot = receiver_copies * offset_rank + receiver_copy
                    subslot_count = receiver_copies * fanout
                    intra_superslice_ns = (
                        (2 * subslot + 1) * stagger_window_ns
                        // (2 * subslot_count)
                    )
                    start_ns = (
                        base_start_ns
                        + start_superslice * superslice_ns
                        + intra_superslice_ns
                    )
                else:
                    start_superslice = 0
                    start_ns = base_start_ns
                flows.append(
                    {
                        "flow_id": len(flows),
                        "source": source,
                        "destination": destination,
                        "source_tor": source_tor,
                        "destination_tor": destination_tor,
                        "lane": lane,
                        "offset": offset,
                        "bytes": flow_size_bytes,
                        "start_ns": start_ns,
                        "start_superslice": start_superslice,
                    }
                )

    validate_flows(flows, start_mode, selected_offsets, spread_superslices)
    return flows


def validate_flows(flows, start_mode, selected_offsets, spread_superslices):
    fanout = len(selected_offsets)
    expected_flow_count = HOSTS * fanout
    if len(flows) != expected_flow_count:
        raise RuntimeError(f"expected {expected_flow_count} flows, got {len(flows)}")
    if any(flow["source_tor"] == flow["destination_tor"] for flow in flows):
        raise RuntimeError("all flows must cross ToRs")

    sources = Counter(flow["source"] for flow in flows)
    destinations = Counter(flow["destination"] for flow in flows)
    expected_hosts = Counter({host: fanout for host in range(HOSTS)})
    if sources != expected_hosts or destinations != expected_hosts:
        raise RuntimeError("every host must source and receive the configured fanout")

    tor_pairs = Counter(
        (flow["source_tor"], flow["destination_tor"]) for flow in flows
    )
    if len(tor_pairs) != TORS * fanout or set(tor_pairs.values()) != {
        HOSTS_PER_TOR
    }:
        raise RuntimeError("every selected directed ToR pair must carry two flows")

    observed_offsets = {flow["offset"] for flow in flows}
    if observed_offsets != set(selected_offsets):
        raise RuntimeError("generated destination offsets do not match the selection")
    if any((TORS - offset) % TORS not in observed_offsets for offset in observed_offsets):
        raise RuntimeError("selected destination offsets must be symmetric")

    if any(flow["source"] % HOSTS_PER_TOR != flow["destination"] % HOSTS_PER_TOR for flow in flows):
        raise RuntimeError("flows must preserve their host lane")

    if start_mode == "cycle_spread":
        starts = Counter(flow["start_superslice"] for flow in flows)
        expected = Counter(
            {offset - 1: HOSTS for offset in selected_offsets}
        )
        if starts != expected:
            raise RuntimeError("each used superslice must start one flow per host")
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        if set(receiver_starts.values()) != {1}:
            raise RuntimeError("a receiver must see one new flow per used superslice")
    elif start_mode == "staggered":
        starts = Counter(flow["start_superslice"] for flow in flows)
        starts_per_superslice = HOSTS * fanout // spread_superslices
        expected = Counter(
            {
                slice_index: starts_per_superslice
                for slice_index in range(spread_superslices)
            }
        )
        if starts != expected:
            raise RuntimeError("staggered starts must be balanced over the window")
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        max_receiver_starts = (
            fanout + spread_superslices - 1
        ) // spread_superslices
        if max(receiver_starts.values()) > max_receiver_starts:
            raise RuntimeError("receiver starts are not evenly spread")
        if len({flow["start_ns"] for flow in flows}) != len(flows):
            raise RuntimeError("staggered flows must use distinct start timestamps")


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
    parser.add_argument(
        "--flow-size-mib",
        type=float,
        default=DEFAULT_FLOW_SIZE_BYTES / (1024 * 1024),
    )
    parser.add_argument(
        "--start-mode",
        choices=("cycle_spread", "staggered", "synchronized"),
        default="cycle_spread",
    )
    parser.add_argument("--fanout", type=int, default=REMOTE_TORS)
    parser.add_argument("--base-start-ns", type=int, default=1_000)
    parser.add_argument("--superslice-ns", type=int, default=DEFAULT_SUPERSLICE_NS)
    parser.add_argument("--active-window-ns", type=int)
    parser.add_argument("--start-stride", type=int, default=DEFAULT_START_STRIDE)
    parser.add_argument("--spread-superslices", type=int, default=TORS)
    args = parser.parse_args()
    flow_size_bytes = round(args.flow_size_mib * 1024 * 1024)
    try:
        flows = build_flows(
            flow_size_bytes=flow_size_bytes,
            start_mode=args.start_mode,
            base_start_ns=args.base_start_ns,
            superslice_ns=args.superslice_ns,
            fanout=args.fanout,
            start_stride=args.start_stride,
            active_window_ns=args.active_window_ns,
            spread_superslices=args.spread_superslices,
        )
    except ValueError as error:
        parser.error(str(error))

    write_trace(args.output, flows)
    print(f"Wrote {len(flows)} flows to {args.output}")
    print(
        f"Each flow: {flow_size_bytes} bytes "
        f"({flow_size_bytes / (1024 * 1024):.3f} MiB)"
    )
    print(
        f"Every host sources and receives {args.fanout} flows; "
        f"every ToR sources and receives {HOSTS_PER_TOR * args.fanout} flows"
    )
    print(f"Destination ToR offsets: {select_destination_offsets(args.fanout)}")
    print(f"Total offered data: {len(flows) * flow_size_bytes / (1024**3):.3f} GiB")
    if args.start_mode == "cycle_spread":
        print(
            f"Start distribution: {HOSTS} flows on each of "
            f"{args.fanout} selected superslices"
        )
    elif args.start_mode == "staggered":
        release_gbps = (
            args.fanout * flow_size_bytes * 8.0
            / (args.spread_superslices * args.superslice_ns)
        )
        starts_per_superslice = HOSTS * args.fanout // args.spread_superslices
        print(
            f"Staggered distribution: {starts_per_superslice} "
            f"flows/superslice across {args.spread_superslices} superslices"
        )
        print(
            f"Starts use unique timestamps inside each active window; "
            f"per-host offered release rate={release_gbps:.3f} Gbps"
        )
    else:
        print(f"All flows start at {args.base_start_ns} ns")


if __name__ == "__main__":
    main()
