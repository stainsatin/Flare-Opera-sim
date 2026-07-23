#!/usr/bin/env python3
"""Generate two lane-preserving flows per ordered pair of distinct ToRs."""

import argparse
from collections import Counter
from pathlib import Path


TORS = 108
HOSTS_PER_TOR = 2
HOSTS = TORS * HOSTS_PER_TOR
REMOTE_TORS = TORS - 1
FLOW_COUNT = HOSTS * REMOTE_TORS
DEFAULT_FLOW_SIZE_BYTES = 1 * 1024 * 1024
DEFAULT_SUPERSLICE_NS = 54_500


def build_flows(
    flow_size_bytes=DEFAULT_FLOW_SIZE_BYTES,
    start_mode="cycle_spread",
    base_start_ns=1_000,
    superslice_ns=DEFAULT_SUPERSLICE_NS,
):
    if flow_size_bytes <= 0:
        raise ValueError("flow size must be positive")
    if base_start_ns < 0:
        raise ValueError("base start time must be non-negative")
    if superslice_ns <= 0:
        raise ValueError("superslice duration must be positive")
    if start_mode not in ("cycle_spread", "synchronized"):
        raise ValueError(f"unsupported start mode: {start_mode}")

    flows = []
    for source_tor in range(TORS):
        for lane in range(HOSTS_PER_TOR):
            source = source_tor * HOSTS_PER_TOR + lane
            for offset in range(1, TORS):
                destination_tor = (source_tor + offset) % TORS
                destination = destination_tor * HOSTS_PER_TOR + lane
                if start_mode == "cycle_spread":
                    start_superslice = offset - 1
                    start_ns = base_start_ns + start_superslice * superslice_ns
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

    validate_flows(flows, start_mode)
    return flows


def validate_flows(flows, start_mode):
    if len(flows) != FLOW_COUNT:
        raise RuntimeError(f"expected {FLOW_COUNT} flows, got {len(flows)}")
    if any(flow["source_tor"] == flow["destination_tor"] for flow in flows):
        raise RuntimeError("all flows must cross ToRs")

    sources = Counter(flow["source"] for flow in flows)
    destinations = Counter(flow["destination"] for flow in flows)
    expected_hosts = Counter({host: REMOTE_TORS for host in range(HOSTS)})
    if sources != expected_hosts or destinations != expected_hosts:
        raise RuntimeError("every host must source and receive one flow per remote ToR")

    tor_pairs = Counter(
        (flow["source_tor"], flow["destination_tor"]) for flow in flows
    )
    if len(tor_pairs) != TORS * REMOTE_TORS or set(tor_pairs.values()) != {
        HOSTS_PER_TOR
    }:
        raise RuntimeError("every ordered remote ToR pair must carry two flows")

    if any(flow["source"] % HOSTS_PER_TOR != flow["destination"] % HOSTS_PER_TOR for flow in flows):
        raise RuntimeError("flows must preserve their host lane")

    if start_mode == "cycle_spread":
        starts = Counter(flow["start_superslice"] for flow in flows)
        expected = Counter(
            {slice_index: HOSTS for slice_index in range(REMOTE_TORS)}
        )
        if starts != expected:
            raise RuntimeError("each used superslice must start one flow per host")
        receiver_starts = Counter(
            (flow["destination"], flow["start_superslice"]) for flow in flows
        )
        if set(receiver_starts.values()) != {1}:
            raise RuntimeError("a receiver must see one new flow per used superslice")


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
    args = parser.parse_args()
    flow_size_bytes = round(args.flow_size_mib * 1024 * 1024)
    try:
        flows = build_flows(
            flow_size_bytes=flow_size_bytes,
            start_mode=args.start_mode,
            base_start_ns=args.base_start_ns,
            superslice_ns=args.superslice_ns,
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
        f"Every host sources and receives {REMOTE_TORS} flows; "
        f"every ToR sources and receives {HOSTS_PER_TOR * REMOTE_TORS} flows"
    )
    print(f"Total offered data: {len(flows) * flow_size_bytes / (1024**3):.3f} GiB")
    if args.start_mode == "cycle_spread":
        print(
            f"Start distribution: {HOSTS} flows/superslice for "
            f"superslices 0..{REMOTE_TORS - 1}"
        )
    else:
        print(f"All flows start at {args.base_start_ns} ns")


if __name__ == "__main__":
    main()
