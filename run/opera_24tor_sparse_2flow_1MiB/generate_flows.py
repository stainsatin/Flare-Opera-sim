#!/usr/bin/env python3
"""Generate sparse paired traffic for receiver-NIC Credit experiments."""

import argparse
from collections import Counter
from pathlib import Path


TORS = 24
HOSTS_PER_TOR = 4
HOSTS = TORS * HOSTS_PER_TOR
LANE_OFFSETS = {
    0: (3, 21),
    1: (9, 15),
    2: (3, 21),
    3: (9, 15),
}
DEFAULT_FLOW_SIZE_BYTES = 1 * 1024 * 1024
DEFAULT_SUPERSLICE_NS = 55_000
DEFAULT_ACTIVE_WINDOW_NS = 54_000
DEFAULT_STABLE_WINDOW_NS = 53_000
DEFAULT_START_SUPERSLICES = 16
DEFAULT_PAIR_GAP_NS = 1_000
SUPPORTED_START_SUPERSLICES = (8, 16)


def receiver_start_superslice(destination_tor, lane, start_superslices):
    # This phase assignment balances global starts and keeps each source ToR
    # to at most one new flow per slice in the default 16-slice profile.
    return (10 * destination_tor + 11 * lane + 14) % start_superslices


def build_flows(
    flow_size_bytes=DEFAULT_FLOW_SIZE_BYTES,
    base_start_ns=1_000,
    superslice_ns=DEFAULT_SUPERSLICE_NS,
    active_window_ns=DEFAULT_ACTIVE_WINDOW_NS,
    stable_window_ns=DEFAULT_STABLE_WINDOW_NS,
    start_superslices=DEFAULT_START_SUPERSLICES,
    pair_gap_ns=DEFAULT_PAIR_GAP_NS,
):
    if flow_size_bytes <= 0:
        raise ValueError("flow size must be positive")
    if superslice_ns <= 0:
        raise ValueError("superslice duration must be positive")
    if start_superslices not in SUPPORTED_START_SUPERSLICES:
        raise ValueError("start superslices must be 8 or 16")
    if not 0 < active_window_ns <= superslice_ns:
        raise ValueError("active window must be in (0, superslice_ns]")
    if not 0 < stable_window_ns <= active_window_ns:
        raise ValueError("stable window must be in (0, active_window_ns]")
    if not 0 <= base_start_ns < stable_window_ns:
        raise ValueError("base-start phase must fall inside the stable window")
    if pair_gap_ns <= 0:
        raise ValueError("pair gap must be positive")

    flows = []
    for destination_tor in range(TORS):
        for lane in range(HOSTS_PER_TOR):
            destination = destination_tor * HOSTS_PER_TOR + lane
            start_superslice = receiver_start_superslice(
                destination_tor, lane, start_superslices
            )
            offsets = list(LANE_OFFSETS[lane])
            if destination % 2:
                offsets.reverse()
            for pair_rank, offset in enumerate(offsets):
                source_tor = (destination_tor - offset) % TORS
                source = source_tor * HOSTS_PER_TOR + lane
                flows.append(
                    {
                        "flow_id": len(flows),
                        "source": source,
                        "destination": destination,
                        "source_tor": source_tor,
                        "destination_tor": destination_tor,
                        "lane": lane,
                        "offset": offset,
                        "pair_rank": pair_rank,
                        "bytes": flow_size_bytes,
                        "start_superslice": start_superslice,
                    }
                )

    usable_window_ns = stable_window_ns - base_start_ns - pair_gap_ns
    for slice_index in range(start_superslices):
        receivers = sorted(
            {
                flow["destination"]
                for flow in flows
                if flow["start_superslice"] == slice_index
            }
        )
        if usable_window_ns < len(receivers):
            raise ValueError("stable window is too short for receiver bursts")
        for slot, receiver in enumerate(receivers):
            pair_start_ns = (
                base_start_ns
                + (2 * slot + 1) * usable_window_ns // (2 * len(receivers))
            )
            pair = sorted(
                (
                    flow
                    for flow in flows
                    if flow["start_superslice"] == slice_index
                    and flow["destination"] == receiver
                ),
                key=lambda flow: flow["pair_rank"],
            )
            for flow in pair:
                flow["start_ns"] = (
                    slice_index * superslice_ns
                    + pair_start_ns
                    + flow["pair_rank"] * pair_gap_ns
                )

    validate_flows(
        flows,
        start_superslices,
        superslice_ns,
        active_window_ns,
        stable_window_ns,
        pair_gap_ns,
    )
    return sorted(flows, key=lambda flow: flow["flow_id"])


def validate_flows(
    flows,
    start_superslices,
    superslice_ns,
    active_window_ns,
    stable_window_ns,
    pair_gap_ns,
):
    if len(flows) != HOSTS * 2:
        raise RuntimeError("unexpected flow count")

    expected_hosts = Counter({host: 2 for host in range(HOSTS)})
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
    if len(tor_pairs) != TORS * 4 or set(tor_pairs.values()) != {2}:
        raise RuntimeError("selected ToR pairs are not balanced")
    for lane, offsets in LANE_OFFSETS.items():
        if any((TORS - offset) % TORS not in offsets for offset in offsets):
            raise RuntimeError(f"lane {lane} offsets are not reverse-paired")

    expected_per_slice = len(flows) // start_superslices
    starts = Counter(flow["start_superslice"] for flow in flows)
    if starts != Counter(
        {slice_index: expected_per_slice for slice_index in range(start_superslices)}
    ):
        raise RuntimeError("global starts are not balanced across slices")

    receiver_starts = Counter(
        (flow["destination"], flow["start_superslice"]) for flow in flows
    )
    if set(receiver_starts.values()) != {2}:
        raise RuntimeError("each receiver burst must contain exactly two flows")
    if start_superslices == 16:
        source_tor_starts = Counter(
            (flow["source_tor"], flow["start_superslice"]) for flow in flows
        )
        if set(source_tor_starts.values()) != {1}:
            raise RuntimeError("a source ToR starts multiple flows in one slice")

    starts_by_receiver = {}
    for flow in flows:
        starts_by_receiver.setdefault(flow["destination"], []).append(
            flow["start_ns"]
        )
    if any(
        max(starts_ns) - min(starts_ns) != pair_gap_ns
        for starts_ns in starts_by_receiver.values()
    ):
        raise RuntimeError("receiver flow pairs do not use the configured gap")
    if len({flow["start_ns"] for flow in flows}) != len(flows):
        raise RuntimeError("flow start timestamps must be unique")
    if any(flow["start_ns"] % superslice_ns >= stable_window_ns for flow in flows):
        raise RuntimeError("a flow starts outside the stable routing window")
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
    parser.add_argument("--flow-size-mib", type=float, default=1)
    parser.add_argument("--base-start-ns", type=int, default=1_000)
    parser.add_argument("--superslice-ns", type=int, default=55_000)
    parser.add_argument("--active-window-ns", type=int, default=54_000)
    parser.add_argument(
        "--start-superslices",
        type=int,
        choices=SUPPORTED_START_SUPERSLICES,
        default=16,
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
    print(f"Wrote {len(flows)} flows to {args.output}")
    print(f"Each flow: {flow_size_bytes} bytes ({args.flow_size_mib:.3f} MiB)")
    print("Every Host sources two flows and receives one two-flow burst")
    print("Every ToR sources eight flows and receives eight flows")
    print(f"Starts: {len(flows) // args.start_superslices} flows/superslice")
    print("Each receiver burst contains two flows separated by 1000 ns")
    print("All starts are unique and inside stable optical windows")


if __name__ == "__main__":
    main()
