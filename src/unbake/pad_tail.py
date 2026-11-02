"""Stale tails in an alignment pad: the bytes between a record's end and the next alignment, proven by a twin version.

A c-string or a copy row ends where its record ends; the builder aligns the next object, and the pad after the record
holds whatever the previous contents of that memory were. Under SHORT_COPY bytes no copy reader proves such a tail. A
twin version proves it when it holds the same record end (CONTEXT accounted bytes with MIN_DISTINCT values or more,
equal in both versions), the same tail bytes right after it, and the same alignment (both tails end on a multiple of
ALIGN). The row names the twin and the tail's offset there; the twin's tail is not required to be accounted, because
the shared accounted record end is the witness, and the twin's own tail is proven by the same rule in the other
direction. A tail whose twin differs in a byte, in the record end or in the alignment is refused and listed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from unbake import blobs
from unbake.contracts import Json

ALIGN = 4  # a pad runs to a multiple of this
MAX_TAIL = 7  # the longest pad tail: under SHORT_COPY, where the copy readers stop
CONTEXT = 8  # the accounted bytes before the tail that name the record end
MIN_DISTINCT = 4  # a record end of fewer distinct byte values (fill, zeros) names no record
Spans = list[tuple[int, int]]


class TailError(ValueError):
    """Bytes that are not a pad tail proven by a twin."""


def check(rom: bytes, other: bytes, start: int, end: int, source: int, held: Spans, held_other: Spans) -> None:
    """Raise TailError naming what fails for rom[start:end] as the pad tail of the twin's bytes at `source`."""
    size = end - start
    if not 0 < size <= MAX_TAIL:
        raise TailError(f"a pad tail is 1 to {MAX_TAIL} bytes, not {size}")
    if end % ALIGN or (source + size) % ALIGN:
        raise TailError(f"alignment differs: tail ends at {end:#x}, twin tail at {source + size:#x}")
    if start < CONTEXT or source < CONTEXT or source + size > len(other):
        raise TailError("record end outside ROM")
    context = rom[start - CONTEXT : start]
    if len(set(context)) < MIN_DISTINCT:
        raise TailError("record end holds too few distinct values")
    if other[source - CONTEXT : source] != context:
        raise TailError("record end differs from the twin's")
    if blobs.subtract([(start - CONTEXT, start)], held) or blobs.subtract([(source - CONTEXT, source)], held_other):
        raise TailError("record end is not accounted in both versions")
    if other[source : source + size] != rom[start:end]:
        raise TailError("tail differs from the twin's")


def prove(rom: bytes, other: bytes, start: int, end: int, source: int, held: Spans, held_other: Spans) -> Json:
    """The receipt of rom[start:end] as a pad tail: the record end and the tail, both equal in the twin."""
    check(rom, other, start, end, source, held, held_other)
    return {
        "source": [source, source + end - start],
        "record_end": rom[start - CONTEXT : start].hex(),
        "hex": rom[start:end].hex(),
        "alignment": ALIGN,
    }


def find(
    roms: Mapping[str, bytes], spans: Mapping[str, Spans], version: str, targets: Spans, order: Sequence[str] = ()
) -> tuple[list[Json], list[Json]]:
    """The rows for the pad tails among `targets` of `version` (stretches of 1 to MAX_TAIL bytes that end on an
    ALIGN multiple right after accounted bytes), and the tails refused with the reason; the newest twin wins."""
    rom = roms[version]
    twins = [v for v in sorted(roms, reverse=True) if v != version]
    twins.sort(key=lambda v: order.index(v) if v in order else len(order))
    rows: list[Json] = []
    refused: list[Json] = []
    for start, end in targets:
        if not 0 < end - start <= MAX_TAIL or end % ALIGN or blobs.subtract([(start - CONTEXT, start)], spans[version]):
            continue
        context = rom[start - CONTEXT : start]
        reasons: list[str] = []
        for twin in twins:
            other = roms[twin]
            at = other.find(context)
            while at >= 0:
                try:
                    check(rom, other, start, end, at + CONTEXT, spans[version], spans[twin])
                except TailError as error:
                    reasons.append(f"{twin} {at + CONTEXT:#x}: {error}")
                else:
                    rows.append(
                        {
                            "version": version,
                            "region": [start, end],
                            "reader": "pad-tail",
                            "source": at + CONTEXT,
                            "source_version": twin,
                        }
                    )
                    break
                at = other.find(context, at + 1)
            else:
                continue
            break
        else:
            reason = "; ".join(reasons[:3]) or "no twin holds the record end"
            refused.append({"version": version, "region": [start, end], "reason": reason})
    return rows, refused
