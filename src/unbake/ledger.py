"""The unknown-bytes ledger: every ROM span no class holds gets exactly one cause class, the evidence the bytes show and
the reader that would prove it. A class is a cause read from the bytes (never a guess that retypes them); the first rule
that matches wins, in `RULES` order. A span no rule matches is `unclassified` with the hypotheses already tried."""

from __future__ import annotations

import re
import struct
from bisect import bisect_right
from collections import defaultdict
from importlib import import_module
from pathlib import Path
from typing import Any

from unbake import blob_stale
from unbake.contracts import Json

Spans = list[tuple[int, int]]
UNCLASSIFIED = "unclassified"
MIN_COPY = 8  # the fewest bytes of a copy of accounted bytes
MIN_DISTINCT = 4  # a copy of fewer distinct byte values names no source
MIN_TEXT = 4  # printable characters of a C string, three of them letters
TINY = 4  # at most this many bytes between proven neighbours
_TEXT = re.compile(rb"[\x20-\x7e\t\r\n]+")
# the hypotheses already measured on the unclassified rest (UNKNOWN-SPANS 11-01): kept with the ledger so a reader card
# does not repeat them
TRIED = (
    "copy: span equals bytes of another version at any offset (accounted or not): refuted",
    "ram-pointer words equal symbols of any version's link, or one constant delta: refuted",
    "span equals ROM[p + k*0x1000] (page-shifted payload): refuted",
    "tiled by SN sym/obj or PsyqSym64 LNK records: refuted",
)

# class -> (what proves it, the open card that owns it); the card column is the Split.md contract
READERS: dict[str, tuple[str, str]] = {
    "alignment-pad": ("blobs.fill at the alignment end of the neighbouring object", "STALE-DE-B 32LTxT0XsIbA"),
    "fill-run": ("blobs.fill once a layout rule names the run's owner", "STALE-DE-B 32LTxT0XsIbA"),
    "copy-of-known": ("blobs prove kind copy against the accounted bytes it equals", "STALE-EUX I49SI-jdoNA-"),
    "c-string": ("stale-data/c-string row: NUL-terminated printable text", "EU2-STRINGS-2 OmpuSJ-JvE_i"),
    "text-fragment": (
        "a string cut by its neighbours: the string reader on the joined bytes",
        "EU2-STRINGS-2 OmpuSJ-JvE_i",
    ),
    "font-asset": ("an asset reader for the font/asset segment (header, then body)", "EU2-FONT dUhzZaWoDzH5"),
    "asset-data": ("an asset reader for the assets segment", "EU2-ASSETS i2UfaSAId6mB"),
    "pointer-run": (
        "earlier-link pointer table: the older build's symbol layout names each target",
        "EU2-CODEPTR yVgG7cL6fF4T",
    ),
    "float-run": ("type-pool float run in a typed record", "EU2-RECORDS H5tPF50PxoVO"),
    "packed-record": (
        "type-pool integer run / packed record whose layout an older object owns",
        "EU2-RECORDS H5tPF50PxoVO",
    ),
    "tiny-fragment": ("the neighbouring row's extent (<= 4 bytes between proven rows)", "STALE-EUX I49SI-jdoNA-"),
    UNCLASSIFIED: ("no reader known; hypotheses tried are listed with the ledger", "older-link yZlYpcK3bvGD"),
}


def _words(d: bytes) -> tuple[int, ...]:
    return struct.unpack(f">{len(d) // 4}I", d[: len(d) // 4 * 4])


def _is_float(w: int) -> bool:
    return 0x30000000 < w < 0x50000000 or 0xB0000000 < w < 0xD0000000


def _covered(held: Spans, starts: list[int], a: int, b: int) -> bool:
    i = bisect_right(starts, a) - 1
    return i >= 0 and held[i][0] <= a and b <= held[i][1]


def _copy(roms: dict[str, bytes], held: dict[str, tuple[Spans, list[int]]], v: str, a: int, b: int) -> Json | None:
    """The first place any version holds these bytes inside its accounted bytes (not the span itself)."""
    d = roms[v][a:b]
    for w in sorted(roms, reverse=True):
        at = roms[w].find(d)
        while at >= 0:
            if _covered(*held[w], at, at + len(d)) and not (w == v and at < b and a < at + len(d)):
                return {"version": w, "at": at}
            at = roms[w].find(d, at + 1)
    return None


def classify(d: bytes, a: int, b: int, segment: str, copy: Json | None) -> tuple[str, str]:
    """(class, evidence) of one span: the bytes `d` at ROM [a, b) in `segment`; `copy` where accounted bytes equal them."""
    n = len(d)
    if len(set(d)) == 1:
        return (
            "alignment-pad" if b % 4 == 0 and n < 16 else "fill-run"
        ), f"all bytes 0x{d[0]:02x}, end 0x{b:x} % 4 = {b % 4}"
    if n <= TINY:
        return "tiny-fragment", f"{n} bytes {d.hex()}"
    if copy:
        return "copy-of-known", f"equals {copy['version']} 0x{copy['at']:x}+{n}"
    body = d.rstrip(b"\0")
    if _TEXT.fullmatch(body) and len(body) >= MIN_TEXT and sum(chr(c).isalpha() for c in body) >= 3 and len(body) < n:
        return "c-string", f"{body.decode('ascii')!r} + {n - len(body)} NUL"
    printable = sum(32 <= c < 127 for c in d)
    if printable * 2 >= n and max(map(len, _TEXT.findall(d)), default=0) >= MIN_TEXT:
        return "text-fragment", f"{printable} of {n} bytes printable, no terminating NUL inside"
    words = _words(d)
    if words:
        ptr = sum(0x80000000 <= w < 0x80800000 or 0xA0000000 <= w < 0xA0800000 for w in words)
        flt = sum(_is_float(w) for w in words)
        small = sum(w < 0x10000 for w in words)
        if ptr * 2 >= len(words):
            return "pointer-run", f"{ptr} of {len(words)} words 0x80xxxxxx, none a symbol of this link"
        if flt * 2 >= len(words) and n >= 8:
            return "float-run", f"{flt} of {len(words)} words in float range"
        if small * 2 >= len(words) and n >= 8:
            return "packed-record", f"{small} of {len(words)} words below 0x10000"
    if "font" in segment:
        return "font-asset", f"segment {segment}, no feature rule matches"
    if "asset" in segment:
        return "asset-data", f"segment {segment}, no feature rule matches"
    return UNCLASSIFIED, f"head {d[:16].hex()}"


def ledger(config: Any, tree: Any, spans: Json) -> Json:
    """`spans` as `report.unknown_spans` answers (`{version: {total, spans: [[low, high, size, segment]]}}`) with a cause
    class and evidence per span, the byte table per class and version, and the unclassified total."""
    parts = import_module("unbake.report").partitions(config, tree)
    root = Path(config.project.root)
    roms = {v: (root / "roms" / f"baserom.{v}.z64").read_bytes() for v in spans}
    held = {}
    for v in roms:
        held[v] = (acc := blob_stale.accounted(parts[v]), [s[0] for s in acc])
    rows: dict[str, list[Json]] = {}
    table: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for v, x in spans.items():
        out = []
        for low, high, size, segment in x["spans"]:
            d = roms[v][low:high]
            copy = _copy(roms, held, v, low, high) if size >= MIN_COPY and len(set(d)) >= MIN_DISTINCT else None
            cause, evidence = classify(d, low, high, segment, copy)
            out.append(
                {"low": low, "high": high, "size": size, "segment": segment, "class": cause, "evidence": evidence}
            )
            table[cause][v] += size
        rows[v] = out
    return {
        "spans": rows,
        "bytes": {c: dict(sorted(t.items())) for c, t in sorted(table.items())},
        "unclassified": sum(table[UNCLASSIFIED].values()),
        "tried": list(TRIED),
        "readers": {c: {"reader": r, "card": k} for c, (r, k) in READERS.items()},
    }
