"""Units split into the fewest groups that mergecheck's layout rules accept in every version.

A group is sound when, over the live entries (bytes > 0) of every version, no foreign unit sits between two of its
entries of one class (gap), each class sits in one output section, and in each output section every class forms one run
(interleave). `partition` cuts a group wherever one of those fails, in link order, until every group is sound; a unit
whose own entries straddle a foreign unit stays alone."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from unbake.mergecheck import CLASSES

Row = dict[str, Any]
Signature = dict[tuple[str, str], int]


def _live(seqs: Mapping[str, Sequence[Row]]) -> dict[str, list[Row]]:
    return {v: [r for r in seq if r["k"] == "entry" and r.get("n", 0) > 0] for v, seq in seqs.items()}


def signatures(group: Collection[str], live: Mapping[str, Sequence[Row]]) -> tuple[dict[str, Signature], set[str]]:
    """Per unit, {(class or section, version): segment}; two units may share a group only where their segments agree.
    The bridges are the units that sit in two segments of one key: no group can hold them with a neighbour."""
    sig: dict[str, Signature] = defaultdict(dict)
    bridges: set[str] = set()

    def place(unit: str, key: tuple[str, str], seg: int) -> None:
        if sig[unit].setdefault(key, seg) != seg:
            bridges.add(unit)

    for version, seq in live.items():
        for cls in CLASSES:
            seg, section, started = -1, None, False
            for row in seq:
                if row["u"] not in group:
                    started = False
                elif cls in row["cls"].split("+"):
                    if not started or row["o"] != section:
                        seg, section, started = seg + 1, row["o"], True
                    place(row["u"], (cls, version), seg)
        chains: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
        for row in seq:
            if row["u"] in group:
                for cls in row["cls"].split("+"):
                    chain = chains[row["o"]]
                    if not chain or chain[-1][0] != cls:
                        chain.append((cls, []))
                    chain[-1][1].append(row["u"])
        for section, chain in chains.items():
            seen: set[str] = set()
            seg = 0
            for cls, units in chain:
                if cls in seen:
                    seg, seen = seg + 1, set()
                seen.add(cls)
                for unit in units:
                    place(unit, (section, version), seg)
    return sig, bridges


def _compatible(a: Signature, b: Signature) -> bool:
    return all(a[k] == b[k] for k in a.keys() & b.keys())


def _refine(group: set[str], live: Mapping[str, Sequence[Row]], order: Mapping[str, int]) -> list[set[str]]:
    sig, bridges = signatures(group, live)
    if bridges:
        rest = group - bridges
        return ([rest] if rest else []) + [{b} for b in sorted(bridges)]
    clusters: list[tuple[set[str], Signature]] = []
    for unit in sorted(group, key=lambda u: order.get(u, len(order))):
        for members, merged in clusters:
            if _compatible(sig[unit], merged):
                members.add(unit)
                merged.update(sig[unit])
                break
        else:
            clusters.append(({unit}, dict(sig[unit])))
    return [members for members, _ in clusters]


def partition(units: Collection[str], seqs: Mapping[str, Sequence[Row]], order: Mapping[str, int]) -> list[set[str]]:
    """The groups of `units` (a unit alone is a group of one), each sound in every version of `seqs`. `order` ranks
    the units in the base link order so the groups form from the first unit onward."""
    live = _live(seqs)
    groups = [set(units)]
    while True:
        parts = [p for g in groups for p in (_refine(g, live, order) if len(g) > 1 else [g])]
        if len(parts) == len(groups):
            return parts
        groups = parts
