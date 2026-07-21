#!/usr/bin/env python3
"""Generate short/long flow pairs for the multi-bottleneck topology."""

import argparse
from pathlib import Path


FLOW_PAIRS = (
    (
        {"receiver": 12, "sender": 3, "flow_class": "short", "path_hops": 1},
        {"receiver": 12, "sender": 4, "flow_class": "long", "path_hops": 4},
    ),
    (
        {"receiver": 10, "sender": 2, "flow_class": "short", "path_hops": 2},
        {"receiver": 10, "sender": 12, "flow_class": "long", "path_hops": 4},
    ),
    (
        {"receiver": 0, "sender": 7, "flow_class": "short", "path_hops": 1},
        {"receiver": 0, "sender": 4, "flow_class": "long", "path_hops": 3},
    ),
    (
        {"receiver": 1, "sender": 7, "flow_class": "short", "path_hops": 1},
        {"receiver": 1, "sender": 5, "flow_class": "long", "path_hops": 4},
    ),
    (
        {"receiver": 2, "sender": 6, "flow_class": "short", "path_hops": 1},
        {"receiver": 2, "sender": 1, "flow_class": "long", "path_hops": 3},
    ),
)

CASES = ("short_first", "long_first", "simultaneous")


def generate_rows(case, flow_size, rounds, interval_ns, order_gap_ns):
    if case not in CASES:
        raise ValueError(f"unknown case: {case}")
    rows = []
    for round_index in range(rounds):
        base_start = round_index * interval_ns
        for short_flow, long_flow in FLOW_PAIRS:
            if case == "short_first":
                ordered = ((short_flow, base_start), (long_flow, base_start + order_gap_ns))
            elif case == "long_first":
                ordered = ((short_flow, base_start + order_gap_ns), (long_flow, base_start))
            elif round_index % 2 == 0:
                ordered = ((short_flow, base_start), (long_flow, base_start))
            else:
                ordered = ((long_flow, base_start), (short_flow, base_start))
            for flow, start_ns in ordered:
                rows.append(
                    {
                        **flow,
                        "bytes": flow_size,
                        "start_ns": start_ns,
                        "round": round_index,
                    }
                )
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=CASES, required=True)
    parser.add_argument("--flow-size", type=int, default=1_000_000)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--interval-ns", type=int, default=10_000_000)
    parser.add_argument("--order-gap-ns", type=int, default=1_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.flow_size <= 0:
        parser.error("--flow-size must be positive")
    if args.rounds <= 0:
        parser.error("--rounds must be positive")
    if args.interval_ns <= 0:
        parser.error("--interval-ns must be positive")
    if args.order_gap_ns < 0 or args.order_gap_ns >= args.interval_ns:
        parser.error("--order-gap-ns must be in [0, interval-ns)")

    rows = generate_rows(
        args.case,
        args.flow_size,
        args.rounds,
        args.interval_ns,
        args.order_gap_ns,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="ascii", newline="\n") as stream:
        for row in rows:
            stream.write(
                f"{row['sender']} {row['receiver']} {row['bytes']} "
                f"{row['start_ns']}\n"
            )


if __name__ == "__main__":
    main()
