#!/usr/bin/env python3
"""Generate a deterministic small Opera dynamic topology for htsim."""

import argparse
import random
from collections import deque
from pathlib import Path


def one_factorization(size):
    if size < 4 or size % 2:
        raise ValueError("the number of ToRs must be even and at least four")

    matchings = [tuple(range(size))]
    ring = list(range(size))
    for _ in range(size - 1):
        partner = [-1] * size
        for index in range(size // 2):
            left = ring[index]
            right = ring[-1 - index]
            partner[left] = right
            partner[right] = left
        matchings.append(tuple(partner))
        ring = [ring[0], ring[-1], *ring[1:-1]]
    return matchings


def rotor_schedule(tors, uplinks):
    matchings_per_rotor = tors // uplinks
    base = []
    for matching_index in range(matchings_per_rotor):
        base.extend([matching_index] * (uplinks - 1))
        base.append(None)
    return [base[-shift:] + base[:-shift] if shift else list(base) for shift in range(uplinks)]


def active_graph(tors, assignment, schedule, superslice):
    graph = [[] for _ in range(tors)]
    for rotor, rotor_schedule_row in enumerate(schedule):
        slot = rotor_schedule_row[superslice]
        if slot is None:
            continue
        matching = assignment[rotor][slot]
        for source, destination in enumerate(matching):
            if source != destination:
                graph[source].append((destination, rotor))
    for neighbors in graph:
        neighbors.sort()
    return graph


def graph_score(graph):
    max_hops = 0
    total_hops = 0
    pair_count = 0
    for source in range(len(graph)):
        distances = [-1] * len(graph)
        distances[source] = 0
        pending = deque([source])
        while pending:
            current = pending.popleft()
            for neighbor, _ in graph[current]:
                if distances[neighbor] == -1:
                    distances[neighbor] = distances[current] + 1
                    pending.append(neighbor)
        if any(distance < 0 for distance in distances):
            return None
        for destination, distance in enumerate(distances):
            if destination == source:
                continue
            max_hops = max(max_hops, distance)
            total_hops += distance
            pair_count += 1
    return max_hops, total_hops / pair_count


def assignment_score(tors, assignment, schedule):
    max_hops = 0
    total_mean = 0.0
    for superslice in range(tors):
        score = graph_score(active_graph(tors, assignment, schedule, superslice))
        if score is None:
            return None
        max_hops = max(max_hops, score[0])
        total_mean += score[1]
    return max_hops, total_mean / tors


def choose_assignment(tors, uplinks, seed, trials):
    matchings = one_factorization(tors)
    schedule = rotor_schedule(tors, uplinks)
    matching_ids = list(range(tors))
    random_generator = random.Random(seed)
    matchings_per_rotor = tors // uplinks
    best = None

    for _ in range(trials):
        random_generator.shuffle(matching_ids)
        assignment = []
        for rotor in range(uplinks):
            start = rotor * matchings_per_rotor
            assignment.append(
                [matchings[index] for index in matching_ids[start : start + matchings_per_rotor]]
            )
        score = assignment_score(tors, assignment, schedule)
        if score is not None and (best is None or score < best[0]):
            best = (score, assignment)

    if best is None:
        raise RuntimeError(
            f"no connected assignment found in {trials} trials; increase --trials"
        )
    return best[1], schedule, best[0]


def shortest_path(graph, source, destination):
    previous = [None] * len(graph)
    previous_rotor = [None] * len(graph)
    previous[source] = source
    pending = deque([source])
    while pending and previous[destination] is None:
        current = pending.popleft()
        for neighbor, rotor in graph[current]:
            if previous[neighbor] is None:
                previous[neighbor] = current
                previous_rotor[neighbor] = rotor
                pending.append(neighbor)
    if previous[destination] is None:
        raise RuntimeError(f"no path from ToR {source} to ToR {destination}")

    rotors = []
    current = destination
    while current != source:
        rotors.append(previous_rotor[current])
        current = previous[current]
    rotors.reverse()
    return rotors


def previous_slot(schedule_row, superslice):
    for offset in range(1, len(schedule_row) + 1):
        slot = schedule_row[(superslice - offset) % len(schedule_row)]
        if slot is not None:
            return slot
    raise RuntimeError("rotor schedule contains no configured matching")


def adjacency_row(tors, assignment, schedule, superslice, reconfig):
    row = []
    for source in range(tors):
        for rotor, schedule_row in enumerate(schedule):
            slot = schedule_row[superslice]
            if slot is None:
                if reconfig:
                    row.append(-1)
                    continue
                slot = previous_slot(schedule_row, superslice)
            row.append(assignment[rotor][slot][source])
    return row


def generate_topology(
    output,
    tors=16,
    radix=8,
    epsilon_ps=12_880_000,
    delta_ps=620_000,
    reconfig_ps=1_000_000,
    seed=13,
    trials=5_000,
):
    if radix % 2:
        raise ValueError("radix must be even")
    downlinks = radix // 2
    uplinks = radix // 2
    if tors % uplinks:
        raise ValueError("the number of ToRs must be divisible by radix/2")
    if uplinks < 3:
        raise ValueError("at least three rotor uplinks are required")

    assignment, schedule, score = choose_assignment(tors, uplinks, seed, trials)
    lines = [
        f"{tors * downlinks} {downlinks} {uplinks} {tors}",
        f"{3 * tors} {epsilon_ps} {delta_ps} {reconfig_ps}",
    ]

    for superslice in range(tors):
        stable = adjacency_row(tors, assignment, schedule, superslice, False)
        reconfig = adjacency_row(tors, assignment, schedule, superslice, True)
        lines.append(" ".join(map(str, stable)))
        lines.append(" ".join(map(str, stable)))
        lines.append(" ".join(map(str, reconfig)))

    for superslice in range(tors):
        graph = active_graph(tors, assignment, schedule, superslice)
        routes = {}
        for source in range(tors):
            for destination in range(source + 1, tors):
                rotor_path = shortest_path(graph, source, destination)
                ports = [downlinks + rotor for rotor in rotor_path]
                routes[source, destination] = ports
                routes[destination, source] = list(reversed(ports))
        for phase in range(3):
            lines.append(str(3 * superslice + phase))
            for source in range(tors):
                for destination in range(tors):
                    if source == destination:
                        continue
                    ports = " ".join(map(str, routes[source, destination]))
                    lines.append(f"{source} {destination} {ports}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    validate_topology(output)
    return score


def validate_topology(path):
    lines = path.read_text(encoding="ascii").splitlines()
    hosts, downlinks, uplinks, tors = map(int, lines[0].split())
    slices, _, _, _ = map(int, lines[1].split())
    if hosts != downlinks * tors or slices != 3 * tors:
        raise RuntimeError("invalid small Opera topology header")

    adjacency = [list(map(int, line.split())) for line in lines[2 : 2 + slices]]
    expected_width = tors * uplinks
    if any(len(row) != expected_width for row in adjacency):
        raise RuntimeError("invalid Opera adjacency row width")

    for slice_index, row in enumerate(adjacency):
        for source in range(tors):
            for rotor in range(uplinks):
                destination = row[source * uplinks + rotor]
                if destination < 0:
                    continue
                if row[destination * uplinks + rotor] != source:
                    raise RuntimeError(
                        f"slice {slice_index} rotor {rotor} is not a symmetric matching"
                    )

    cursor = 2 + slices
    for slice_index in range(slices):
        if int(lines[cursor]) != slice_index:
            raise RuntimeError(f"missing route section for slice {slice_index}")
        cursor += 1
        seen = set()
        routes = {}
        for _ in range(tors * (tors - 1)):
            fields = list(map(int, lines[cursor].split()))
            cursor += 1
            source, destination, *ports = fields
            if (source, destination) in seen or source == destination or not ports:
                raise RuntimeError(f"invalid route in slice {slice_index}")
            seen.add((source, destination))
            routes[source, destination] = ports
            current = source
            for port in ports:
                rotor = port - downlinks
                if rotor < 0 or rotor >= uplinks:
                    raise RuntimeError(f"invalid output port {port}")
                current = adjacency[slice_index][current * uplinks + rotor]
                if current < 0:
                    raise RuntimeError(f"route uses a disconnected rotor in slice {slice_index}")
            if current != destination:
                raise RuntimeError(
                    f"route {source}->{destination} ends at {current} in slice {slice_index}"
                )
        for source in range(tors):
            for destination in range(source + 1, tors):
                if routes[source, destination] != list(
                    reversed(routes[destination, source])
                ):
                    raise RuntimeError(
                        f"routes {source}->{destination} and {destination}->{source} "
                        f"are not symmetric in slice {slice_index}"
                    )
    if cursor != len(lines):
        raise RuntimeError("unexpected trailing topology records")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tors", type=int, default=16)
    parser.add_argument("--radix", type=int, default=8)
    parser.add_argument("--epsilon-ps", type=int, default=12_880_000)
    parser.add_argument("--delta-ps", type=int, default=620_000)
    parser.add_argument("--reconfig-ps", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--trials", type=int, default=5_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.epsilon_ps, args.delta_ps, args.reconfig_ps) < 0:
        parser.error("slice times must be non-negative")
    if args.trials <= 0:
        parser.error("--trials must be positive")

    max_hops, mean_hops = generate_topology(
        output=args.output,
        tors=args.tors,
        radix=args.radix,
        epsilon_ps=args.epsilon_ps,
        delta_ps=args.delta_ps,
        reconfig_ps=args.reconfig_ps,
        seed=args.seed,
        trials=args.trials,
    )
    print(f"Wrote {args.output}")
    print(f"Maximum ToR hops: {max_hops}")
    print(f"Mean ToR hops: {mean_hops:.3f}")


if __name__ == "__main__":
    main()
