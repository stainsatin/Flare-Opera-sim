#!/usr/bin/env python3
"""Generate a balanced one-flow-per-host workload for the 108-ToR Opera topology."""

import argparse
import math
from collections import Counter
from pathlib import Path


TORS = 108
HOSTS_PER_TOR = 6
HOSTS = TORS * HOSTS_PER_TOR
DEFAULT_OFFSETS = (1, 19, 37, 55, 73, 91)
DEFAULT_FLOW_SIZE_BYTES = 32 * 1024 * 1024
DEFAULT_SUPERSLICE_NS = 54_500
DEFAULT_START_STRIDE = 9


def build_flows(
    flow_size_bytes=DEFAULT_FLOW_SIZE_BYTES,
    start_mode="cycle_spread",
    base_start_ns=1_000,
    superslice_ns=DEFAULT_SUPERSLICE_NS,
    offsets=DEFAULT_OFFSETS,
    start_stride=DEFAULT_START_STRIDE,
):
    offsets = tuple(offset % TORS for offset in offsets)
    if flow_size_bytes <= 0:
        raise ValueError("flow size must be positive")
    if base_start_ns < 0:
        raise ValueError("base start time must be non-negative")
    if superslice_ns <= 0:
        raise ValueError("superslice duration must be positive")
    if len(offsets) != HOSTS_PER_TOR or len(set(offsets)) != HOSTS_PER_TOR:
        raise ValueError("exactly six distinct ToR offsets are required")
    if 0 in offsets:
        raise ValueError("offset zero would create an intra-ToR flow")
    if start_mode not in ("cycle_spread", "synchronized"):
        raise ValueError(f"unsupported start mode: {start_mode}")
    if start_stride <= 0:
        raise ValueError("start stride must be positive")
    stride_cycle = TORS // math.gcd(start_stride % TORS, TORS)
    if stride_cycle < HOSTS_PER_TOR:
        raise ValueError("start stride does not spread the six lanes across the cycle")

    flows = []
    for source_tor in range(TORS):
        for lane in range(HOSTS_PER_TOR):
            source = source_tor * HOSTS_PER_TOR + lane
            destination_tor = (source_tor + offsets[lane]) % TORS
            destination = destination_tor * HOSTS_PER_TOR + lane
            if start_mode == "cycle_spread":
                start_slice = (source_tor + start_stride * lane) % TORS
                start_ns = base_start_ns + start_slice * superslice_ns
            else:
                start_slice = 0
                start_ns = base_start_ns
            flows.append(
                {
                    "flow_id": len(flows),
                    "source": source,
                    "destination": destination,
                    "source_tor": source_tor,
                    "destination_tor": destination_tor,
                    "lane": lane,
                    "offset": offsets[lane],
                    "bytes": flow_size_bytes,
                    "start_ns": start_ns,
                    "start_superslice": start_slice,
                }
            )
    validate_flows(flows, start_mode)
    return flows


def validate_flows(flows, start_mode):
    if len(flows) != HOSTS:
        raise RuntimeError(f"expected {HOSTS} flows, got {len(flows)}")
    if {flow["source"] for flow in flows} != set(range(HOSTS)):
        raise RuntimeError("every host must be used exactly once as a source")
    if {flow["destination"] for flow in flows} != set(range(HOSTS)):
        raise RuntimeError("every host must be used exactly once as a destination")
    if any(flow["source_tor"] == flow["destination_tor"] for flow in flows):
        raise RuntimeError("all flows must cross ToRs")

    source_tors = Counter(flow["source_tor"] for flow in flows)
    destination_tors = Counter(flow["destination_tor"] for flow in flows)
    if source_tors != Counter({tor: HOSTS_PER_TOR for tor in range(TORS)}):
        raise RuntimeError("each ToR must source exactly six flows")
    if destination_tors != Counter({tor: HOSTS_PER_TOR for tor in range(TORS)}):
        raise RuntimeError("each ToR must receive exactly six flows")

    tor_pairs = Counter(
        (flow["source_tor"], flow["destination_tor"]) for flow in flows
    )
    if set(tor_pairs.values()) != {1}:
        raise RuntimeError("selected ordered ToR pairs must carry equal flow counts")

    if start_mode == "cycle_spread":
        starts = Counter(flow["start_superslice"] for flow in flows)
        expected = Counter({slice_index: HOSTS_PER_TOR for slice_index in range(TORS)})
        if starts != expected:
            raise RuntimeError("cycle-spread mode must start six flows per superslice")
        for slice_index in range(TORS):
            selected = [
                flow for flow in flows if flow["start_superslice"] == slice_index
            ]
            if len({flow["source_tor"] for flow in selected}) != HOSTS_PER_TOR:
                raise RuntimeError("a superslice contains duplicate source ToRs")
            if len({flow["destination_tor"] for flow in selected}) != HOSTS_PER_TOR:
                raise RuntimeError("a superslice contains duplicate destination ToRs")


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
        choices=("cycle_spread", "synchronized"),
        default="cycle_spread",
    )
    parser.add_argument("--base-start-ns", type=int, default=1_000)
    parser.add_argument("--superslice-ns", type=int, default=DEFAULT_SUPERSLICE_NS)
    parser.add_argument("--start-stride", type=int, default=DEFAULT_START_STRIDE)
    parser.add_argument(
        "--offsets",
        type=int,
        nargs=HOSTS_PER_TOR,
        default=DEFAULT_OFFSETS,
        metavar=("O0", "O1", "O2", "O3", "O4", "O5"),
    )
    args = parser.parse_args()
    flow_size_bytes = round(args.flow_size_mib * 1024 * 1024)
    try:
        flows = build_flows(
            flow_size_bytes=flow_size_bytes,
            start_mode=args.start_mode,
            base_start_ns=args.base_start_ns,
            superslice_ns=args.superslice_ns,
            offsets=args.offsets,
            start_stride=args.start_stride,
        )
    except ValueError as error:
        parser.error(str(error))

    write_trace(args.output, flows)
    starts = Counter(flow["start_superslice"] for flow in flows)
    print(f"Wrote {len(flows)} flows to {args.output}")
    print(
        f"Each flow: {flow_size_bytes} bytes "
        f"({flow_size_bytes / (1024 * 1024):.3f} MiB)"
    )
    print("Every host sources one flow and receives one flow")
    print("Every ToR sources six flows and receives six flows")
    print(f"Destination ToR offsets by host lane: {tuple(args.offsets)}")
    if args.start_mode == "cycle_spread":
        print(
            f"Every superslice starts {HOSTS_PER_TOR} flows; "
            f"start stride={args.start_stride}; slices={len(starts)}"
        )
    else:
        print(f"All flows start at {args.base_start_ns} ns")


if __name__ == "__main__":
    main()
