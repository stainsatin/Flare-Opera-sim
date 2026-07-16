#!/usr/bin/env python3
"""Generate repeated finite-flow waves for the 8-ToR ring experiment."""

import argparse
from pathlib import Path


def generate_rows(distance, flow_size, rounds, interval_ns, nodes=8):
    rows = []
    for round_index in range(rounds):
        start_ns = round_index * interval_ns
        for src in range(nodes):
            dst = (src + distance) % nodes
            rows.append((src, dst, flow_size, start_ns))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--distance", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--flow-size", type=int, default=1_000_000)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--interval-ns", type=int, default=100_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.flow_size <= 0:
        parser.error("--flow-size must be positive")
    if args.rounds <= 0:
        parser.error("--rounds must be positive")
    if args.interval_ns < 0:
        parser.error("--interval-ns must be non-negative")

    rows = generate_rows(
        args.distance, args.flow_size, args.rounds, args.interval_ns
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="ascii", newline="\n") as stream:
        for row in rows:
            stream.write(" ".join(map(str, row)) + "\n")


if __name__ == "__main__":
    main()
