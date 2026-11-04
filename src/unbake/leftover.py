"""Generic cutover reductions any decomp needs once its source tree reads like an original one.

Five rules, each a pure function over text a pass already holds, with project facts (the unused root, the source
roots, the region gap) read from the project's `[layout]`:

1. `readers` / `unread` / `home`: data nothing reads keeps its C, moved to the configured unused root.
2. `placeholder` / `findings`: no address, number or ordinal placeholder names, except under the unused root.
3. `literals` / `retire`: an address-only symbol (no bytes, no reader as data) becomes its address literal.
4. `tables`: adjacent rows of one shape (equal once their names and differing numbers are set aside) become one table.
5. `regions`: interleaved rows are owned by their contiguous address region, not by their name pattern.
6. `with_c` / `keep_c`: bytes that have C keep it (under the unused root when unread), never a bin segment.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

Json = dict[str, Any]

_ORDINALS = [
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
    "Twenty",
    "First",
    "Second",
    "Third",
    "Fourth",
    "Fifth",
    "Sixth",
    "Seventh",
    "Eighth",
    "Ninth",
    "Tenth",
    "Last",
]
_ORDINAL = "|".join(sorted(_ORDINALS, key=len, reverse=True))
_ADDRESS = re.compile(r"(?:^|_)(?:0x)?[0-9A-Fa-f]{5,8}(?=$|_)")
_NUMBER = re.compile(r"(?<=[A-Za-z_])\d+$")
_ORDINAL_END = re.compile(rf"(?<=[a-z0-9_])(?:{_ORDINAL})$")
_IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
_SYMBOL_ROW = re.compile(r"^\s*(\w+)\s*=\s*(0x[0-9A-Fa-f]+)\s*;")


def unused_root(layout: Mapping[str, Any]) -> str:
    """The folder that keeps the C of data nothing reads (layout `unused_root`); fails fast when the layout lacks it."""
    root = str(layout["unused_root"])
    if not root or "/" in root.strip("/"):
        raise ValueError(f"layout unused_root must be one folder name, not {root!r}")
    return root.strip("/")


def marker(layout: Mapping[str, Any]) -> str:
    """The word every generated name of the unused tree carries (unused_root `leftover` -> `Leftover`): the place says
    older build, so no name says `earlier`, and a number or address is allowed only beside this word."""
    return re.sub(r"[^A-Za-z0-9]", "", unused_root(layout)).capitalize()


def in_unused(path: str, layout: Mapping[str, Any]) -> bool:
    return path.startswith(unused_root(layout) + "/")


# 1. reader analysis and the unused home


def readers(symbols: Collection[str], texts: Mapping[str, str]) -> dict[str, set[str]]:
    """Each symbol's reading files: files that name it other than in its own definition or declaration line."""
    out: dict[str, set[str]] = {s: set() for s in symbols}
    for path, text in texts.items():
        for line in text.splitlines():
            names = set(_IDENT.findall(line)) & out.keys()
            declared = _declared(line)
            for name in names - declared:
                out[name].add(path)
    return out


def _declared(line: str) -> set[str]:
    """The names a line defines or declares: `extern T x[];`, `T x[n] = {`, `x = 0x...;`."""
    if m := _SYMBOL_ROW.match(line):
        return {m[1]}
    m = re.match(
        r"^\s*(?:extern\s+|static\s+|const\s+)*[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*(?:=|;)", line
    )
    return {m[1]} if m else set()


def unread(symbols: Collection[str], texts: Mapping[str, str]) -> list[str]:
    """The symbols no file reads, sorted."""
    return sorted(s for s, files in readers(symbols, texts).items() if not files)


def row_pattern(pattern: str, layout: Mapping[str, Any]) -> str:
    """The project's per-object Makefile row pattern widened to the unused tree: a unit moved there keeps its
    `FILE.`, `CPPFLAGS.` and `CFLAGS.` rows (and the other per-file kinds), now spelled under the unused root."""
    kinds = "FILE|CPPFLAGS|CFLAGS|OMIT|HILO|UNRELOC"
    return rf"(?:{pattern})|^(?:{kinds})\.{re.escape(unused_root(layout))}/\S* :?="


def home(path: str, layout: Mapping[str, Any]) -> str:
    """Where a unit whose data nothing reads lives: its path with the source root swapped for the unused root
    (SOURCE/ENGINE/X.CPP -> leftover/ENGINE/X.CPP); a header root keeps its name under the unused root."""
    root = unused_root(layout)
    if in_unused(path, layout):
        return path
    for source in layout.get("source_roots", ()):
        if path.startswith(source + "/"):
            rest = path[len(source) + 1 :]
            return f"{root}/{rest}" if source not in layout.get("header_roots", ()) else f"{root}/{source}/{rest}"
    raise ValueError(f"{path}: not under a source root {list(layout.get('source_roots', ()))}")


# 2. placeholder names


def placeholder(name: str) -> str | None:
    """Why a name is a placeholder (an address, a trailing number or ordinal word), or None for a content name."""
    if _ADDRESS.search(name):
        return "address"
    if _NUMBER.search(name):
        return "number"
    if _ORDINAL_END.search(name):
        return "ordinal"
    return None


_ORDINAL_WORD = re.compile(rf"(?<=[a-z0-9_])(?:{_ORDINAL})(?=[A-Z_]|$)|(?<=[A-Za-z_])\d+(?=[A-Z_]|$)")


def told_by_number(name: str) -> bool:
    """Whether a name tells its row apart by a number or ordinal word anywhere in it (`PartTwoTrailingWords`)."""
    return placeholder(name) is not None or bool(_ORDINAL_WORD.search(name))


def findings(names: Mapping[str, str], layout: Mapping[str, Any]) -> list[str]:
    """`name -> defining path` audited: a placeholder name is a finding except under the unused root."""
    return [
        f"{path}: {name}: {kind} placeholder name"
        for name, path in sorted(names.items())
        if (kind := placeholder(name)) and not in_unused(path, layout)
    ]


# 3. address-only symbols become literals


def symbol_rows(text: str) -> dict[str, int]:
    """`name = 0xADDR;` rows of a linker symbols file."""
    return {m[1]: int(m[2], 16) for line in text.splitlines() if (m := _SYMBOL_ROW.match(line))}


def literals(addresses: Mapping[str, int], with_bytes: Collection[str]) -> dict[str, str]:
    """`name -> literal` for every symbol that only names an address: no unit gives it bytes."""
    return {n: f"0x{a:08X}" for n, a in sorted(addresses.items()) if n not in with_bytes}


def retire(text: str, retired: Mapping[str, str]) -> str:
    """The text with each retired symbol's declaration dropped and every use (`&x`, `(void *)&x`, `x`) its literal."""
    if not retired:
        return text
    kept = [line for line in text.splitlines(keepends=True) if not (_declared(line) & retired.keys())]
    names = "|".join(map(re.escape, sorted(retired, key=len, reverse=True)))
    return re.sub(rf"&?\b({names})\b", lambda m: retired[m[1]], "".join(kept))


def forget(symbols: str, retired: Collection[str]) -> str:
    """A linker symbols file without the rows of retired symbols: an address literal needs no linker name."""
    return "".join(
        line for line in symbols.splitlines(keepends=True) if not ((m := _SYMBOL_ROW.match(line)) and m[1] in retired)
    )


def rename_map(retired: Mapping[str, str], merged: Mapping[str, Sequence[str]]) -> Json:
    """The rename-proof record of a reduction: `Retired {old: literal}` and `Merged {table: [old rows]}`."""
    return {"Retired": dict(sorted(retired.items())), "Merged": {t: list(rows) for t, rows in sorted(merged.items())}}


# 4. rows of one shape become one table


@dataclass(frozen=True)
class Row:
    name: str
    type: str
    body: str
    before: str = ""  # the text between the previous definition and this one


_DEFINITION = re.compile(r"^([A-Za-z_][\w \*]*?)\s+([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*=\s*(\{.*?\});", re.S | re.M)


def rows(text: str) -> list[Row]:
    """The initialized global definitions of a unit, in order."""
    out, end = [], 0
    for m in _DEFINITION.finditer(text):
        out.append(Row(m[2], m[1].strip() + (m[3] or ""), m[4], text[end : m.start()]))
        end = m.end()
    return out


def _adjacent(row: Row) -> bool:
    """Nothing but blanks and comments sits before the row: no directive, declaration or other file between."""
    return not re.sub(r"/\*.*?\*/|//[^\n]*", "", row.before, flags=re.S).strip()


def _shape(row: Row) -> str:
    return " ".join(re.sub(r"\b\d+\b", "#", row.type + row.body).split())


def stem(names: Sequence[str]) -> str:
    """The content name rows share: the name with its placeholder suffix (number, ordinal, address) removed."""
    out = names[0]
    for _ in range(3):
        out = _NUMBER.sub("", _ORDINAL_END.sub("", _ADDRESS.sub("", out))).rstrip("_")
    return out


def tables(units: Sequence[Row]) -> dict[str, list[str]]:
    """`table -> [rows]` for each run of two or more adjacent rows of one shape and one stem."""
    out: dict[str, list[str]] = {}
    run: list[Row] = []
    for row in [*units, None]:
        if (
            row is not None
            and run
            and _shape(row) == _shape(run[0])
            and stem([row.name]) == stem([run[0].name])
            and _adjacent(row)
        ):
            run.append(row)
            continue
        if len(run) > 1:
            out[stem([run[0].name]) + "Table"] = [r.name for r in run]
        run = [row] if row is not None else []
    return out


# 5. ownership by region


@dataclass
class Region:
    start: int
    end: int
    names: list[str] = field(default_factory=list)

    @property
    def families(self) -> list[str]:
        return sorted({stem([n]) for n in self.names})


def regions(addresses: Mapping[str, int], sizes: Mapping[str, int], gap: int) -> list[Region]:
    """Contiguous address regions: rows sorted by address join one region while the next starts at most `gap` bytes
    after the previous ends; each region lists every name inside it whatever its pattern."""
    out: list[Region] = []
    for name, at in sorted(addresses.items(), key=lambda kv: (kv[1], kv[0])):
        end = at + sizes.get(name, 4)
        if out and at <= out[-1].end + gap:
            out[-1].end = max(out[-1].end, end)
            out[-1].names.append(name)
        else:
            out.append(Region(at, end, [name]))
    return out


def owners(found: Sequence[Region]) -> dict[str, str]:
    """`name -> region` owner key: a region with interleaved families is owned as one unit named by its address,
    which only the unused tree may carry (rule 2)."""
    return {n: f"{r.start:08X}" for r in found for n in r.names}


# 6. the reduction record replayed


def merge(text: str, table: str, names: Sequence[str]) -> str:
    """The text with the adjacent rows `names` (one type) written as one table `table[len(names)]`, each old row its
    cell at the same offset, and every use of an old row spelled `table[i]`. Refuses rows that are not adjacent or
    not of one type, since either would move a byte."""
    found = {m[2]: m for m in _DEFINITION.finditer(text)}
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"{table}: no definition of {missing}")
    first, last = found[names[0]], found[names[-1]]
    between = text[first.start() : last.end()]
    for n in names:
        between = between.replace(found[n][0], "")
    if (
        re.sub(r"/\*.*?\*/|//[^\n]*", "", between, flags=re.S).strip()
        or len({(found[n][1].strip(), found[n][3]) for n in names}) != 1
    ):
        raise ValueError(f"{table}: rows {list(names)} are not adjacent rows of one type")
    cells = ",\n".join(found[n][4] for n in names)
    head = f"{first[1].strip()} {table}[{len(names)}]{first[3] or ''} = {{\n{cells}\n}};"
    out = text[: first.start()] + head + text[last.end() :]
    index = {n: i for i, n in enumerate(names)}
    return re.sub(rf"\b({'|'.join(map(re.escape, names))})\b", lambda m: f"{table}[{index[m[1]]}]", out)


def replay(before: str, after: str, record: Mapping[str, Any]) -> list[str]:
    """Why `after` is not `before` reduced by the rename-proof record (`Retired` literals, then `Merged` tables), or
    [] when replaying the record over `before` gives `after` token for token."""
    try:
        got = retire(before, record.get("Retired", {}))
        for table, names in record.get("Merged", {}).items():
            got = merge(got, table, names)
    except ValueError as error:
        return [str(error)]
    return (
        [] if got.split() == after.split() else ["the text is not its record replayed: a change the map does not name"]
    )


Extent = tuple[int, int, int]  # (ROM start, ROM end, address of the ROM start) of one older-build window


def rom_range(region: Region, extents: Sequence[Extent]) -> tuple[int, int] | None:
    """The ROM range of a region: its addresses mapped through the window that holds its start, clipped to that
    window; None when no window holds it (the region has no bytes in this ROM)."""
    for start, end, vram in extents:
        if vram <= region.start < vram + (end - start):
            return start + region.start - vram, min(end, start + region.end - vram)
    return None


def census(texts: Mapping[str, str], gap: int, extents: Mapping[str, Sequence[Extent]] | None = None) -> Json:
    """`version -> symbols file text` as the region census: each version's contiguous regions, one address range
    and (through the version's window `extents`) one ROM range each, with every name and family inside it whatever
    its pattern; a region of two or more families is interleaved and owned as one unit (rule 5)."""
    out: Json = {}
    for version, text in sorted(texts.items()):
        rows = []
        for r in regions(symbol_rows(text), {}, gap):
            row: Json = {"start": f"0x{r.start:08X}", "end": f"0x{r.end:08X}", "names": r.names, "families": r.families}
            if (rom := rom_range(r, (extents or {}).get(version, ()))) is not None:
                row["rom"] = [f"0x{rom[0]:X}", f"0x{rom[1]:X}"]
            rows.append(row)
        out[version] = rows
    return {"versions": out, "interleaved": sum(len(r["families"]) > 1 for rs in out.values() for r in rs)}


def extents_of(segments: Sequence[Any], size: int) -> list[Extent]:
    """The window extents of one version's segments: each window's ROM range and the address its first byte has."""
    from unbake import windows

    ranges = dict(windows.spans(segments, size))
    return [
        (s.rom, ranges[s.rom], s.vram) for s in windows.declared(segments) if s.vram is not None and s.rom in ranges
    ]


def command(cfg: Any, params: Json) -> Json:
    """`region-census --gap N`: read only; the region census of every version's older-build symbols file, each
    region with its ROM range from the version's window extents."""
    from unbake import windows

    root = cfg.project.root
    texts = {v: p.read_text() for v in cfg.project.versions if (p := root / windows.symbols_path(v)).is_file()}
    extents = {v: extents_of(s.segments, s.size) for v, s in cfg.project.version.items()}
    return census(texts, int(str(params["gap"]), 0), extents)


# 6. bytes that have C keep it


def with_c(
    rows: Mapping[str, Mapping[str, Sequence[tuple[int, int]]]], c: Mapping[str, Sequence[tuple[int, int]]]
) -> dict[str, list[str]]:
    """Per bin row, the versions whose ROM ranges overlap bytes the C builds (`c`, per version); rows clear of C are
    absent. Such bytes are proven older-build data that has C: the C stays, the bin is refused."""
    found: dict[str, list[str]] = {}
    for name, rom in rows.items():
        vs = [v for v, spans in rom.items() if any(a < d and c_ < b for a, b in spans for c_, d in c.get(v, ()))]
        if vs:
            found[name] = sorted(vs)
    return found


def keep_c(deleted: Sequence[str], layout: Mapping[str, Any]) -> dict[str, str]:
    """The plan for source units a bin would replace: each moves to its unused home with its bytes, none is deleted."""
    return {path: home(path, layout) for path in deleted}
