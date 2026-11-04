"""The copy-typing step: a copy row whose bytes equal a typed place in another version takes that type.

A `copy` leftover row holds the same bytes as a known place (mostly another version of the same cart). Where every
place those bytes sit at in the other ROMs lies whole inside typed rows of one content type, the copy row is a fuzzy
row of that type (state `identified`, as a stale copy of a typed blob is) whose format names the source place, and its
id says the type in place of `leftover`. It is fuzzy, never exact, as its own typed source has not rebuilt it. A
source that is itself untyped (or of a type no draft can take), a place that is not inside a row, or sources of
different types leave the row untyped; the answer groups those by source region. The step runs to a fixed point, so a
copy of a copy takes the type its source row just took."""

from bisect import bisect_right
from pathlib import Path
from typing import Any

from unbake import blobs
from unbake import config as configuration
from unbake.contracts import Config

Json = dict[str, Any]  # the blobs document, edited in place (contracts.Json is read-only)

MAX_PLACES = 32  # a byte string that sits in more places than this in one ROM names no source


def _kind(row: Json) -> str | None:
    """The content type a row gives a copy of it, None when it gives none (untyped, or not a content type)."""
    kind = row.get("draft") if row["type"] == "leftover" else row["type"]
    return kind if kind in blobs.CONTENT else None


def _windows(document: Json, version: str) -> tuple[list[int], list[tuple[int, int, str]]]:
    spans = sorted(
        (a, b, name)
        for name, row in document["blob"].items()
        if "parent" not in row
        for a, b in row.get("rom", {}).get(version, ())
    )
    return [s[0] for s in spans], spans


def _places(rom: bytes, data: bytes) -> list[int] | None:
    found: list[int] = []
    at = rom.find(data)
    while at >= 0:
        found.append(at)
        if len(found) > MAX_PLACES:
            return None
        at = rom.find(data, at + 1)
    return found


def _source(document: Json, roms: dict[str, bytes], version: str, row: Json) -> tuple[str | None, Json]:
    """(content type, note) of a copy row's source: the type every place of its bytes in the other ROMs lies in."""
    data = b"".join(roms[version][a:b] for a, b in row["rom"][version])
    kinds: dict[str, str] = {}
    untyped: list[str] = []
    for other, rom in roms.items():
        if other == version:
            continue
        starts, spans = _windows(document, other)
        places = _places(rom, data)
        if places is None:
            return None, {"why": "ambiguous", "regions": []}
        for at in places:
            i = bisect_right(starts, at) - 1
            held = [s for s in spans[max(i - 8, 0) : i + 1] if s[0] <= at and at + len(data) <= s[1]]
            if not held:
                untyped.append(f"{other}:no-row")
                continue
            source = document["blob"][held[-1][2]]
            kind = _kind(source)
            if kind is None:
                untyped.append(f"{other}:{held[-1][2]}")
            else:
                kinds.setdefault(kind, f"{other} {at:#x} {held[-1][2]}")
    if untyped or len(kinds) != 1:
        return None, {"why": "untyped" if untyped else "none" if not kinds else "mixed", "regions": untyped}
    ((kind, where),) = kinds.items()
    return kind, {"evidence": f"same bytes as {where}, a {kind} row"}


def run(config: Config) -> Json:
    """`unbake blobs type`: type every copy row by its source; write blobs.toml and answer per version the rows typed
    and the untyped rows grouped by source region."""
    path = Path(config.project.root) / blobs.path_of(config)
    document: Json = dict(blobs.parse(config, path.read_bytes()))
    roms: dict[str, bytes] = {}
    for version in config.project.version:
        rom = Path(config.project.root) / configuration.root("roms") / f"baserom.{version}.z64"
        if rom.is_file():
            roms[version] = rom.read_bytes()
    typed: dict[str, dict[str, int]] = {}
    last: dict[str, Json] = {}
    while True:
        changed = False
        last = {}
        for name, row in sorted(document["blob"].items()):  # a rename below rebinds the dict, not this list
            if row["type"] != "leftover" or row.get("format") != "copy" or row.get("draft") or "parent" in row:
                continue
            if len(row.get("rom", {})) != 1:
                continue
            (version,) = row["rom"]
            if version not in roms:
                continue
            kind, note = _source(document, roms, version, row)
            if kind is None:
                last[name] = {"version": version, "bytes": blobs.length(row["rom"][version]), **note}
                continue
            row.update(type=kind, state="identified", format=note["evidence"])
            fresh = name.replace("leftover", kind, 1)
            if fresh != name and fresh not in document["blob"]:
                document["blob"] = {(fresh if n == name else n): r for n, r in document["blob"].items()}
            held = typed.setdefault(version, {"rows": 0, "bytes": 0})
            held["rows"] += 1
            held["bytes"] += blobs.length(row["rom"][version])
            changed = True
        if not changed:
            break
    if typed:
        path.write_bytes(blobs.emit(document))
    regions: dict[str, dict[str, int]] = {}
    for item in last.values():
        for region in sorted(set(item["regions"])) or [f"{item['why']}"]:
            held = regions.setdefault(f"{item['version']} <- {region}", {"rows": 0, "bytes": 0})
            held["rows"] += 1
            held["bytes"] += item["bytes"]
    return {"typed": typed, "untyped_rows": len(last), "untyped_by_source": dict(sorted(regions.items()))}
