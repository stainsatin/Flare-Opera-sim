#!/usr/bin/env python3
"""Derive a two-host-per-ToR topology from the paper's 108-ToR topology."""

import argparse
from pathlib import Path


DEFAULT_HOSTS_PER_TOR = 2


def parse_ints(line, context):
    try:
        return [int(value) for value in line.split()]
    except ValueError as error:
        raise ValueError(f"invalid integer in {context}") from error


def transform_topology(source, output, hosts_per_tor=DEFAULT_HOSTS_PER_TOR):
    if hosts_per_tor <= 0:
        raise ValueError("hosts per ToR must be positive")
    if source.resolve() == output.resolve():
        raise ValueError("source and output topology must differ")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    route_count = 0
    max_hops = 0

    try:
        with source.open(encoding="ascii") as reader, temporary.open(
            "w", encoding="ascii", newline="\n"
        ) as writer:
            header = parse_ints(reader.readline(), "topology header")
            if len(header) != 4:
                raise ValueError("topology header must contain four integers")
            old_hosts, old_hosts_per_tor, uplinks, tors = header
            if old_hosts != old_hosts_per_tor * tors:
                raise ValueError("source host count does not match its ToR layout")
            if hosts_per_tor > old_hosts_per_tor:
                raise ValueError("this converter only removes host downlinks")

            timing = parse_ints(reader.readline(), "topology timing header")
            if len(timing) != 4:
                raise ValueError("topology timing header must contain four integers")
            slices = timing[0]
            if slices <= 0 or tors <= 1 or uplinks <= 0:
                raise ValueError("invalid topology dimensions")

            hosts = hosts_per_tor * tors
            writer.write(f"{hosts} {hosts_per_tor} {uplinks} {tors}\n")
            writer.write(" ".join(map(str, timing)) + "\n")

            adjacency_width = tors * uplinks
            for slice_index in range(slices):
                adjacency = parse_ints(
                    reader.readline(), f"adjacency slice {slice_index}"
                )
                if len(adjacency) != adjacency_width:
                    raise ValueError(
                        f"adjacency slice {slice_index} has {len(adjacency)} "
                        f"entries, expected {adjacency_width}"
                    )
                writer.write(" ".join(map(str, adjacency)) + "\n")

            for line_number, line in enumerate(reader, start=slices + 3):
                values = parse_ints(line, f"route line {line_number}")
                if not values:
                    continue
                if len(values) == 1:
                    if values[0] < 0 or values[0] >= slices:
                        raise ValueError(f"invalid slice marker on line {line_number}")
                    writer.write(f"{values[0]}\n")
                    continue
                if len(values) < 2:
                    raise ValueError(f"invalid route on line {line_number}")

                source_tor, destination_tor = values[:2]
                if not (0 <= source_tor < tors and 0 <= destination_tor < tors):
                    raise ValueError(f"invalid ToR id on line {line_number}")

                ports = values[2:]
                shifted_ports = []
                for port in ports:
                    if port < old_hosts_per_tor or port >= old_hosts_per_tor + uplinks:
                        raise ValueError(
                            f"route line {line_number} contains non-uplink port {port}"
                        )
                    shifted_ports.append(port - old_hosts_per_tor + hosts_per_tor)

                writer.write(
                    " ".join(
                        map(str, [source_tor, destination_tor, *shifted_ports])
                    )
                    + "\n"
                )
                route_count += 1
                max_hops = max(max_hops, len(shifted_ports))

        temporary.replace(output)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    return {
        "hosts": hosts,
        "hosts_per_tor": hosts_per_tor,
        "uplinks": uplinks,
        "tors": tors,
        "slices": slices,
        "routes": route_count,
        "max_hops": max_hops,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--hosts-per-tor", type=int, default=DEFAULT_HOSTS_PER_TOR
    )
    args = parser.parse_args()
    try:
        summary = transform_topology(
            args.source, args.output, hosts_per_tor=args.hosts_per_tor
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))

    print(f"Wrote {args.output}")
    print(
        "Topology: "
        f"{summary['tors']} ToRs, {summary['hosts_per_tor']} hosts/ToR, "
        f"{summary['uplinks']} uplinks/ToR, {summary['hosts']} hosts"
    )
    print(
        f"Slices={summary['slices']}, routes={summary['routes']}, "
        f"max route hops={summary['max_hops']}"
    )


if __name__ == "__main__":
    main()
