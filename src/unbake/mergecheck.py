"""Which merges must stay separate files: a module map (unit, module) read against the link order and the built objects.

A module of several units can become one source file only when one object can take the units' place in the link
without moving a byte. `verdicts` is the pure rule over plain facts; `facts_of` reads them from the link script and the
objects of each version. The hard findings (the module must stay separate files):
  flags       the units do not share one toolchain and flag set (a translation-unit boundary)
  sections    one class of the module sits in several output sections; the parts (units that share the section in every
              version where both are placed) are printed, each with its section per version, so a map row can name one
  gap         two entries of one class are not adjacent in the link order (another module's bytes sit between)
  interleave  one output section links a unit of another class between two units of one class of the module: one
              merged object emits a class whole, so it cannot reproduce data, pool, data
  lma         one merged object would be larger than the span the units fill in an output section (their pieces, each
              aligned as linked, packed again): it would overlap the next piece
The soft findings (the merge can go ahead, and the source must reproduce them):
  order       a version links some units of a section class elsewhere than the base order (the versions' orders
              merged): merge emits each moved unit's chunks again under the guard of the versions that move it
  fill        one merged object is smaller than that span: merge regenerates the linker fill record for the difference
  align       an ALIGN directive pads between two entries of the module
  statics     two units define a local symbol of the same name (the merged file needs one renamed)
  literals    two or more units each hold read-only data (a merged file lets the compiler pool and reorder it)"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection

from unbake import classwise

Row = dict[str, Any]
CLASS_OF = {".text": "text", ".data": "data", ".rodata": "rodata", ".rdata": "rodata", ".bss": "bss", ".sbss": "bss"}
RODATA = "rodata"
CLASSES = ("text", "data", "rodata", "bss")
_ENTRY = re.compile(r"^\s*(\S+?)\.o\(([^)]*)\);")
_ALIGN = re.compile(r"^\s*\.\s*=\s*ALIGN\((0x[0-9A-Fa-f]+|\d+)\)")
_MINIMUM = re.compile(r"^\s*\.\s*=\s*MAX\(\s*\.\s*,\s*(0x[0-9A-Fa-f]+|\d+)\s*\)")
_CONDITIONAL_ALIGN = re.compile(r"^\s*\.\s*=\s*DEFINED\((\w+)\)\s*\?\s*ALIGN\((\d+)\)\s*:\s*\.")
_FILL = re.compile(r"^\s*\.\s*=\s*0x[0-9A-Fa-f]+;\s*((?:BYTE\(0x[0-9A-Fa-f]{2}\);?\s*)+)")
_OUTSEC = re.compile(r"^\s*(\.[A-Za-z0-9_.]+)\s+(?:0x[0-9A-Fa-f]+\s*)?(?:\(NOLOAD\)\s*)?:")


def _section_class(name: str) -> str | None:
    return CLASS_OF.get("." + name.split(".", 2)[1]) if name.startswith(".") else None


def read_modules(text: str) -> list[tuple[str, str]]:
    """(unit, module) rows of a TSV with a header row holding `unit` and `module` columns."""
    rows = list(csv.reader(text.splitlines(), delimiter="\t"))
    if not rows or "unit" not in rows[0] or "module" not in rows[0]:
        raise ValueError("a module map needs a header row with `unit` and `module` columns")
    unit, module = rows[0].index("unit"), rows[0].index("module")
    return [(r[unit], r[module]) for r in rows[1:] if r and not r[0].startswith("#") and len(r) > max(unit, module)]


def read_targets(text: str) -> dict[str, str]:
    """module -> the path of its merged file, from the optional `target` column of a module map; a module without one
    sits at `merged_path`."""
    rows = list(csv.reader(text.splitlines(), delimiter="\t"))
    if not rows or "target" not in rows[0] or "module" not in rows[0]:
        return {}
    module, target = rows[0].index("module"), rows[0].index("target")
    found: dict[str, str] = {}
    for r in rows[1:]:
        if not r or r[0].startswith("#") or len(r) <= max(module, target) or not r[target]:
            continue
        if found.setdefault(r[module], r[target]) != r[target]:
            raise ValueError(f"module {r[module]} names two targets: {found[r[module]]} and {r[target]}")
    return found


def merged_path(module: str, target: str | None = None, units: Sequence[str] = ()) -> str:
    """Where a module's merged file sits: the `target` the module map names, else in the folder named by the module,
    under the module file name, the way every unit sits in the folder of its module; a single unit already at its
    module path stays there. The form follows the units' kind: a module of assembler units only gives an `.s` file."""
    path = PurePosixPath(target or module)
    if not target:
        if list(units) == [module]:
            return module
        path = path.with_suffix("") / path.name
    if units and path.suffix.lower() == ".c" and all(u.lower().endswith(".s") for u in units):
        path = path.with_suffix(".s")
    return path.as_posix()


def merged_modules(text: str, mapping: Mapping[str, str]) -> str:
    """Rewrite all selected units in one pass, keeping one row per destination and all other rows/comments."""
    lines = text.splitlines(keepends=True)
    col = lines[0].rstrip("\r\n").split("\t").index("unit")
    out = [lines[0]]
    done: set[str] = set()
    for line in lines[1:]:
        cells = line.rstrip("\r\n").split("\t")
        if not line.startswith("#") and len(cells) > col and cells[col] in mapping:
            merged = mapping[cells[col]]
            if merged not in done:
                cells[col] = merged
                out.append("\t".join(cells) + "\n")
                done.add(merged)
            continue
        out.append(line)
    return "".join(out)


def split_modules(text: str, unit: str, pieces: Mapping[str, str]) -> str:
    """A module map with the row of `unit` replaced by one row per piece (target -> path): the unit column holds the
    piece's path and the module column its target; every other cell and row stays."""
    lines = text.splitlines(keepends=True)
    head = lines[0].rstrip("\r\n").split("\t")
    col, module = head.index("unit"), head.index("module") if "module" in head else -1
    out = [lines[0]]
    for line in lines[1:]:
        cells = line.rstrip("\r\n").split("\t")
        if line.startswith("#") or len(cells) <= col or cells[col] != unit:
            out.append(line)
            continue
        for target, path in pieces.items():
            row = list(cells)
            row[col] = path
            if 0 <= module < len(row):
                row[module] = target
            out.append("\t".join(row) + "\n")
    return "".join(out)


def parse_link(text: str) -> list[Row]:
    """The ordered entries, alignment and minimum output offsets emitted by the linker writer."""
    rows: list[Row] = []
    outsec = ""
    subalign = 0
    for line in text.splitlines():
        found = _OUTSEC.match(line)
        if found and "{" not in line:
            outsec = found.group(1)
            sub = re.search(r"SUBALIGN\((\d+)\)", line)
            subalign = int(sub.group(1)) if sub else 0
            continue
        align = _ALIGN.match(line)
        if align:
            rows.append({"o": outsec, "k": "align", "a": int(align.group(1), 0)})
            continue
        fill = _FILL.match(line)
        if fill:
            data = bytes(int(b, 16) for b in re.findall(r"BYTE\(0x([0-9A-Fa-f]{2})\)", fill.group(1)))
            rows.append({"o": outsec, "k": "fill", "data": data})
            continue
        minimum = _MINIMUM.match(line)
        if minimum:
            rows.append({"o": outsec, "k": "minimum", "offset": int(minimum.group(1), 0)})
            continue
        conditional = _CONDITIONAL_ALIGN.match(line)
        if conditional:
            rows.append({"o": outsec, "k": "align", "a": int(conditional.group(2)), "symbol": conditional.group(1)})
            continue
        entry = _ENTRY.match(line)
        if entry:
            sections = tuple(s.strip('"') for s in entry.group(2).split())
            classes = tuple(sorted({c for s in sections if (c := _section_class(s)) is not None}))
            rows.append(
                {
                    "o": outsec,
                    "k": "entry",
                    "u": entry.group(1),
                    "cls": classes,
                    "sections": sections,
                    "subalign": subalign,
                }
            )
    return rows


def object_facts(path: Path) -> tuple[dict[str, int], set[str], dict[str, list[tuple[int, int]]], set[str]]:
    """Read exact section sizes, local names, pieces and defined alignment markers once."""
    sizes: dict[str, int] = defaultdict(int)
    local: set[str] = set()
    pieces: dict[str, list[tuple[int, int]]] = defaultdict(list)
    defined: set[str] = set()
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        for section in elf.iter_sections():
            cls = _section_class(section.name)
            if cls is not None:
                sizes[section.name] += section["sh_size"]
            if cls is not None and section["sh_size"]:
                pieces[section.name].append((section["sh_size"], max(section["sh_addralign"], 1)))
        table = elf.get_section_by_name(".symtab")
        if isinstance(table, SymbolTableSection):
            for symbol in table.iter_symbols():
                if symbol["st_shndx"] != "SHN_UNDEF":
                    defined.add(symbol.name)
                kind = symbol["st_info"]["type"]
                if symbol["st_info"]["bind"] == "STB_LOCAL" and kind in ("STT_FUNC", "STT_OBJECT") and symbol.name:
                    local.add(symbol.name)
    return dict(sizes), local, dict(pieces), defined


def fills(path: Path, symbol: str) -> bool | None:
    """Whether the symbol's bytes only fill its section out to the section alignment (the section rounded up is the
    same with the symbol cut out); None when the object has no such symbol."""
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        table = elf.get_section_by_name(".symtab")
        if not isinstance(table, SymbolTableSection):
            return None
        for found in table.get_symbol_by_name(symbol) or ():
            index = found["st_shndx"]
            if not isinstance(index, int):
                continue
            section = elf.get_section(index)
            size, align = section["sh_size"], max(section["sh_addralign"], 1)
            return _up(size - found["st_size"], align) == size
    return None


def _up(position: int, align: int) -> int:
    return -(-position // align) * align


def _linked_align(at: int, align: int) -> int:
    """The alignment a piece kept where the link placed it: the object's section alignment, or less when the link
    (SUBALIGN, a pinned offset) put it at an offset that is not a multiple of that. The compiler packs the piece the
    same way inside one object, so the merged span does not pad it up to the section alignment the link ignored."""
    return min(align, at & -at) if at else align


def _pad(
    inside: bool,
    size: int,
    last: Row | None,
    section: str,
    classes: dict[str, list[tuple[int, int, int]]],
    pads: list[tuple[str, str, str, int]] | None,
) -> None:
    """A pad of `size` bytes the link script makes behind `last` inside the span: the merged source holds it as a zero
    array of the unit's data (`interior_fills`), one byte aligned, so the merged span counts it as a piece of that
    class. Only a unit whose data is one class that holds storage can carry it."""
    if not inside or size <= 0 or last is None or last["cls"] not in classwise.STORED:
        return
    classes[last["cls"]].append((size, 1, last.get("subalign") or 0))
    if pads is not None:
        pads.append((section, last["u"], last["cls"], size))


def spans(
    seq: Sequence[Row],
    units: set[str],
    built: Mapping[str, Sequence[tuple[int, int]]] | None = None,
    holes: list[tuple[str, int]] | None = None,
    pads: list[tuple[str, str, str, int]] | None = None,
) -> list[tuple[str, int, int, int]]:
    """Per output section holding entries of `units`: (section, bytes the linked entries span, bytes one merged object
    spans, offset in the section where the module starts). The linked span walks the section from the module's first
    entry to its last (the module's pieces aligned as linked, ALIGN directives and `MAX(., offset)` pins between
    them); the bytes of other modules between two entries are the `gap` finding's, so they are not walked and the
    pins after them are not read. The merged span packs the module's pieces of each class again, each at the alignment
    the link kept for it (`_linked_align`), class after class as the link places the sections; a class the link pinned
    (`MAX(., offset)` right before its first piece) starts no earlier than that offset, as the merged object's does.
    `built` holds the merged object's own sections (name -> (size, alignment) pieces, `object_facts`): a class it
    holds is measured on those pieces, so the in-object padding the model cannot see is part of the span. `holes`, when
    given, collects (section, offset in the section) for each place the link padded inside the span, the offset where
    the padding ended: the module's bytes after it sit where the merged object, which packs tighter, cannot put them.
    A pad the link script makes inside the span (ALIGN, `MAX(., offset)`) behind a piece of data or read-only data is a
    zero array of the merged source (`interior_fills`), so the merged span counts it; `pads`, when given, collects
    (section, unit before, class, bytes) of each."""
    out: list[tuple[str, int, int, int]] = []
    for section in dict.fromkeys(e["o"] for e in seq if e["k"] == "entry" and e["u"] in units):
        rows = [(i, e) for i, e in enumerate(seq) if e["o"] == section]
        mine = [i for i, e in rows if e["k"] == "entry" and e["u"] in units]
        position, start, foreign = 0, 0, False
        last: Row | None = None  # the module's last entry before the row in hand
        classes: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
        pin, pins = None, {}  # the `MAX(., offset)` before an entry, and per class the pin before its first piece
        for i, row in rows:
            if i > mine[-1]:
                break
            if i == mine[0]:
                start = position
            if row["k"] == "entry" and row["u"] not in units:
                foreign = foreign or (i > mine[0] and any(row.get("parts", {}).values()))
                if i < mine[0]:
                    for cls in row.get("parts", {}):
                        for size, align in row["parts"][cls]:
                            position = _up(position, row.get("subalign") or align) + size
                continue
            if row["k"] == "align":
                before, position = position, _up(position, row["a"])
                if holes is not None and i > mine[0] and position > before:
                    holes.append((section, position))
                _pad(i > mine[0], position - before, last, section, classes, pads)
                continue
            if row["k"] == "minimum":
                before = position
                position = position if foreign else max(position, row["offset"])
                pin = None if foreign else row["offset"]
                if holes is not None and i > mine[0] and position > before:
                    holes.append((section, position))
                _pad(i > mine[0], position - before, last, section, classes, pads)
                continue
            if row["k"] == "fill":  # the stale bytes of an older link: the merged source holds them as an array too
                position += len(row["data"])
                _pad(i > mine[0], len(row["data"]), last, section, classes, None)
                continue
            if row["k"] == "entry" and row.get("n", 0) > 0:
                last = row
            for cls in row.get("parts", {}):
                for size, align in row["parts"][cls]:
                    at = _up(position, row.get("subalign") or align)
                    if holes is not None and i >= mine[0] and at > position and (i > mine[0] or classes):
                        holes.append((section, at))
                    position = at + size
                    if i >= mine[0]:
                        if cls not in pins:
                            pins[cls] = pin
                        classes[cls].append((size, _linked_align(at, align), row.get("subalign") or 0))
        merged = 0
        for cls, pieces in classes.items():
            if built is not None:
                own = [p for name, found in built.items() if _section_class(name) == cls for p in found]
                if own:
                    pieces = [(size, align, pieces[0][2]) for size, align in own]
            pin = pins.get(cls)
            if pin is not None:  # the link script pins each class where it linked: the merged object keeps it
                merged = max(merged, pin - start)
            # the object's section starts at an address that is a multiple of its alignment (the link counts from the
            # output section's start, not from the module's first byte), after the pin as the script orders them
            merged = _up(start + merged, pieces[0][2] or max(a for _, a, _ in pieces)) - start
            packed = 0
            for size, align, _ in pieces:
                packed = _up(packed, align) + size
            merged += packed
        out.append((section, position - start, merged, start))
    return out


def facts_of(
    root: Path, versions: Sequence[str], ld: str, obj: str
) -> tuple[dict[str, list[Row]], dict[str, set[str]]]:
    """Per version the link rows with each entry's byte count, and per unit its local symbol names."""
    seqs: dict[str, list[Row]] = {}
    statics: dict[str, set[str]] = {}
    cache: dict[tuple[str, str], dict[str, int] | None] = {}
    pieces: dict[tuple[str, str], dict[str, list[tuple[int, int]]]] = {}
    for version in versions:
        seq = parse_link((root / ld.format(version=version)).read_text())
        defined: set[str] = set()
        for row in seq:
            if row["k"] != "entry":
                continue
            key = (version, row["u"])
            if key not in cache:
                path = root / obj.format(version=version, unit=row["u"])
                if path.is_file():
                    cache[key], local, pieces[key], names = object_facts(path)
                    defined.update(names)
                    statics.setdefault(row["u"], set()).update(local)
                else:
                    cache[key] = None
            sizes = cache[key] or {}
            row["n"] = sum(sizes.get(s, 0) for s in row["sections"])
            parts: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for section in row["sections"]:
                if (cls := _section_class(section)) is not None:
                    parts[cls].extend(pieces.get(key, {}).get(section, ()))
            row["parts"] = dict(parts)
            row["missing"] = cache[key] is None
            row["cls"] = "+".join(row["cls"])
        seqs[version] = [r for r in seq if "symbol" not in r or r["symbol"] in defined]
    return seqs, statics


def _linked(units: Sequence[str], seq: Sequence[Row], cls: str) -> list[str]:
    """The units of `units` that hold bytes of `cls` in one version, in the order that version links them."""
    wanted = set(units)
    return list(
        dict.fromkeys(
            row["u"]
            for row in seq
            if row["k"] == "entry" and row["u"] in wanted and row.get("n", 0) > 0 and cls in row["cls"].split("+")
        )
    )


def code_order(units: Sequence[str], seqs: Mapping[str, Sequence[Row]], cls: str = "text") -> list[str]:
    """The units in the base order of a class: the versions' orders merged (a unit only a later version places goes
    after its neighbour there); a unit without that class follows the others, in the order given."""
    base = classwise.base_order(_linked(units, seq, cls) for seq in seqs.values())
    return base + [u for u in units if u not in set(base)]


def class_orders(units: Sequence[str], seqs: Mapping[str, Sequence[Row]]) -> dict[str, dict[str, list[str]]]:
    """Per section class, per version, the units that hold bytes of it in the order that version links them."""
    out: dict[str, dict[str, list[str]]] = {}
    for cls in CLASSES:
        per = {version: order for version, seq in seqs.items() if (order := _linked(units, seq, cls))}
        if per:
            out[cls] = per
    return out


def interior_fills(units: Sequence[str], seqs: Mapping[str, Sequence[Row]]) -> list[Row]:
    """The stale link bytes (`fill` rows of the link script) and the zero pads (ALIGN and `MAX(., offset)` of the link
    script behind a piece of data) that lie between two entries of `units` in some version:
    a merged object keeps them only as data of its own. Per (unit before the bytes, class, bytes) one row
    {"after", "cls", "data", "versions"}; the class is the one the entry before them holds."""
    wanted = set(units)
    found: dict[tuple[str, str, bytes], list[str]] = {}
    for version, seq in seqs.items():
        pads: list[tuple[str, str, str, int]] = []
        spans(seq, wanted, None, None, pads)
        for _, unit, cls, size in pads:
            found.setdefault((unit, cls, bytes(size)), []).append(version)
        for i, row in enumerate(seq):
            if row["k"] != "fill":
                continue
            before = next((e for e in reversed(seq[:i]) if e["k"] == "entry" and e.get("n", 0) > 0), None)
            after = next((e for e in seq[i + 1 :] if e["k"] == "entry" and e.get("n", 0) > 0), None)
            if before is None or after is None:
                continue
            if before["u"] in wanted and after["u"] in wanted and len(before["cls"].split("+")) == 1:
                found.setdefault((before["u"], before["cls"], row["data"]), []).append(version)
    return [{"after": u, "cls": c, "data": d, "versions": v} for (u, c, d), v in found.items()]


def object_classes(path: Path) -> dict[str, str]:
    """symbol -> section class of every symbol an object defines in a section of a known class."""
    out: dict[str, str] = {}
    with path.open("rb") as handle:
        elf = ELFFile(handle)
        table = elf.get_section_by_name(".symtab")
        if isinstance(table, SymbolTableSection):
            for symbol in table.iter_symbols():
                index = symbol["st_shndx"]
                if symbol.name and isinstance(index, int) and (cls := _section_class(elf.get_section(index).name)):
                    out[symbol.name] = cls
    return out


def _section_parts(placed: Mapping[str, Mapping[str, str]], order: Sequence[str]) -> list[Row]:
    """The parts of one class of a module: units that share their output section in every version where both are
    placed (and are placed together in one), joined through such pairs. `placed` is version -> unit -> section; each
    part is {"sections": {version: section}, "units": [...]}, units in `order`, parts by their first unit."""
    units = [u for u in order if any(u in per for per in placed.values())]
    parent = {u: u for u in units}

    def find(u: str) -> str:
        while parent[u] != u:
            parent[u] = parent[parent[u]]
            u = parent[u]
        return u

    for i, one in enumerate(units):
        for two in units[i + 1 :]:
            common = [per for per in placed.values() if one in per and two in per]
            if common and all(per[one] == per[two] for per in common):
                parent[find(two)] = find(one)
    groups: dict[str, list[str]] = {}
    for u in units:
        groups.setdefault(find(u), []).append(u)
    return [
        {"sections": {v: per[members[0]] for v, per in placed.items() if members[0] in per}, "units": members}
        for members in groups.values()
    ]


def _part_label(sections: Mapping[str, str]) -> str:
    """`.text` when every version links the part in that section, else `us .text, eu .boot`."""
    if len(set(sections.values())) == 1:
        return next(iter(sections.values()))
    return ", ".join(f"{v} {s}" for v, s in sections.items())


def verdicts(
    rows: Sequence[tuple[str, str]],
    seqs: Mapping[str, Sequence[Row]],
    statics: Mapping[str, set[str]],
    identity: Callable[[str], Any],
) -> list[Row]:
    """One verdict per module of two or more units: {"module", "units", "verdict": merge|separate, "hard", "soft"}."""
    modules: dict[str, list[str]] = defaultdict(list)
    for unit, module in rows:
        modules[module].append(unit)
    multi = {m: us for m, us in modules.items() if len(us) > 1}
    owner = {u: m for m, us in multi.items() for u in us}
    hard: dict[str, list[str]] = defaultdict(list)
    soft: dict[str, list[str]] = defaultdict(list)
    placed = {row["u"] for seq in seqs.values() for row in seq if row["k"] == "entry"}
    for module, units in multi.items():
        for unit in units:
            if unit not in placed:
                hard[module].append(f"unplaced: {unit} has no linker input in any version")
        if len({identity(u) for u in units}) > 1:
            hard[module].append("flags: the units differ in toolchain or flags")
        names: dict[str, str] = {}
        for unit in units:
            for name in statics.get(unit, ()):
                if name in names and names[name] != unit:
                    soft[module].append(f"statics: {name} is local to {names[name]} and {unit}")
                names.setdefault(name, unit)
    layout: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(dict)
    placed_in: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    split: set[tuple[str, str]] = set()
    gapped: set[tuple[str, str]] = set()
    for version, seq in seqs.items():
        for row in seq:
            if row.get("missing") and (missing_owner := owner.get(row["u"])):
                hard[missing_owner].append(f"object: {version} {row['u']} has no built object")
        live = [i for i, e in enumerate(seq) if e["k"] == "entry" and e.get("n", 0) > 0]
        rodata = {e["u"] for e in seq if e["k"] == "entry" and e["cls"] == RODATA and e["n"] > 0}
        following = dict(pairwise(live))
        where: dict[tuple[str, str], list[tuple[int, str, str]]] = defaultdict(list)
        for i in live:
            if home := owner.get(seq[i]["u"]):
                for part in seq[i]["cls"].split("+"):
                    where[(home, part)].append((i, seq[i]["o"], seq[i]["u"]))
        for (module, cls), hits in where.items():
            for _, o, u in hits:
                placed_in[module, cls][version][u] = o
            if len({o for _, o, _ in hits}) > 1:
                split.add((module, cls))
                hard[module].append(f"sections: {version} {cls} sits in several output sections")
            layout[module, cls][version] = list(dict.fromkeys(u for _, _, u in hits))
            for (i, _, one), (j, _, two) in pairwise(hits):
                if following.get(i) != j:
                    between = list(
                        dict.fromkeys(seq[k]["u"] for k in live if i < k < j and owner.get(seq[k]["u"]) != module)
                    )
                    if between:
                        gapped.add((module, version))
                        hard[module].append(
                            f"gap: {version} {cls} {one} .. {two} has {len(between)} between: {', '.join(between)}"
                        )
                for k in range(i + 1, j):
                    if seq[k]["k"] == "align":
                        soft[module].append(f"align: {version} {cls} ALIGN({seq[k]['a']}) before {two}")
        for module in multi:
            classruns: dict[str, list[tuple[str, str]]] = defaultdict(list)
            for i in live:
                if owner.get(seq[i]["u"]) == module:
                    for part in seq[i]["cls"].split("+"):
                        if not classruns[seq[i]["o"]] or classruns[seq[i]["o"]][-1][0] != part:
                            classruns[seq[i]["o"]].append((part, seq[i]["u"]))
            for section, chain in classruns.items():
                again = next((cls for n, (cls, _) in enumerate(chain) if cls in {c for c, _ in chain[:n]}), None)
                if again:
                    order = " ".join(f"{cls}({unit})" for cls, unit in chain)
                    hard[module].append(
                        f"interleave: {version} {section} links {again} on both sides of another class: {order}"
                    )
        for module, units in multi.items():
            # another module's bytes inside the span make the gap the finding: the span is not one run to compare
            for section, linked, merged, _ in [] if (module, version) in gapped else spans(seq, set(units)):
                shape = f"{version} {section} merged {merged} B against linked {linked} B"
                if merged > linked:
                    hard[module].append(f"lma: {shape}: overlaps the next section")
                elif merged < linked:
                    soft[module].append(f"fill: {shape}: the link fill of the {linked - merged} B is regenerated")
            held = [u for u in units if u in rodata]
            if len(held) > 1:
                soft[module].append(f"literals: {version} rodata in {len(held)} units")
    parts: dict[str, dict[str, list[Row]]] = defaultdict(dict)
    for module, cls in sorted(split):
        base = classwise.base_order(layout[module, cls].values())
        parts[module][cls] = found = _section_parts(placed_in[module, cls], base)
        shown = "; ".join(f"{_part_label(p['sections'])} [{', '.join(p['units'])}]" for p in found)
        hard[module].append(f"sections: {cls} parts: {len(found)}: {shown}")
    for (module, cls), per in layout.items():
        base = classwise.base_order(per.values())
        for version, seen in per.items():
            if gone := classwise.moved(base, seen):
                soft[module].append(f"order: {version} {cls} links {', '.join(gone)} elsewhere than the other versions")
    return [
        {
            "module": m,
            "units": len(us),
            "verdict": "separate" if hard[m] else "merge",
            "hard": sorted(set(hard[m])),
            "soft": sorted(set(soft[m])),
            "parts": parts.get(m, {}),
        }
        for m, us in multi.items()
    ]


def _between(seqs: Mapping[str, Sequence[Row]], members: set[str]) -> set[str]:
    """The units outside `members` that sit between two entries of `members` of one class in one output section of a
    version: the units a `gap` finding of the group would name."""
    found: set[str] = set()
    for seq in seqs.values():
        live = [i for i, e in enumerate(seq) if e["k"] == "entry" and e.get("n", 0) > 0]
        hits: dict[tuple[str, str], list[int]] = defaultdict(list)
        for n, i in enumerate(live):
            if seq[i]["u"] in members:
                for cls in seq[i]["cls"].split("+"):
                    hits[cls, seq[i]["o"]].append(n)
        for places in hits.values():
            for one, two in pairwise(places):
                found.update(u for k in live[one + 1 : two] if (u := seq[k]["u"]) not in members)
    return found


def _grow(seed: Sequence[str], seqs: Mapping[str, Sequence[Row]], modules: Mapping[str, Sequence[str]]) -> set[str]:
    """The units that must merge together with `seed`: every unit between two entries of the set joins it, with the
    whole module of the map it belongs to, until no unit is between. `modules`: a unit to the units of its module."""
    members = set(seed)
    while added := _between(seqs, members) - members:
        for unit in added:
            members.update(modules.get(unit, (unit,)))
    return members


def _sections_of(units: Sequence[str], seqs: Mapping[str, Sequence[Row]]) -> dict[str, dict[tuple[str, str], str]]:
    """Per unit, the output section of each (class, version) it holds bytes in."""
    wanted = set(units)
    placed: dict[str, dict[tuple[str, str], str]] = {u: {} for u in units}
    for version, seq in seqs.items():
        for row in seq:
            if row["k"] == "entry" and row["u"] in wanted and row.get("n", 0) > 0:
                for cls in row["cls"].split("+"):
                    placed[row["u"]][cls, version] = row["o"]
    return placed


def _one_section(units: Sequence[str], placed: Mapping[str, Mapping[tuple[str, str], str]]) -> bool:
    seen: dict[tuple[str, str], str] = {}
    return all(seen.setdefault(key, o) == o for u in units for key, o in placed[u].items())


def _parts_of(units: Sequence[str], seqs: Mapping[str, Sequence[Row]]) -> tuple[list[list[str]], bool]:
    """The units of an unbounded group in parts that each hold every class in one output section per version and
    leave no unit of another part between theirs (so neither `sections` nor `gap` arises for a part), units in
    link order. A unit joins the first part it agrees with on every (class, version) both hold, one it shares a key
    with first; parts a gap ties together become one. False when a part left holds a class in several sections."""
    placed = _sections_of(units, seqs)
    found: list[tuple[list[str], dict[tuple[str, str], str]]] = []
    for unit in units:
        fits = [p for p in found if all(p[1].get(k, o) == o for k, o in placed[unit].items())]
        shared = [p for p in fits if p[1].keys() & placed[unit].keys()]
        if not fits:
            found.append(([], {}))
        part = (shared or fits or found[-1:])[0]
        part[0].append(unit)
        part[1].update(placed[unit])
    parts = [p[0] for p in found]
    while True:
        for one in parts:
            tied = _between(seqs, set(one)) & set(units)
            if tied:
                break
        else:
            return parts, all(_one_section(p, placed) for p in parts)
        joined = [p for p in parts if p is one or tied & set(p)]
        parts = [p for p in parts if p not in joined]
        parts.append([u for u in units if any(u in p for p in joined)])


def _part_sections(part: Sequence[str], placed: Mapping[str, Mapping[tuple[str, str], str]]) -> dict[str, str]:
    """Per class the part holds, its output section (`_part_label`)."""
    seen: dict[str, dict[str, str]] = defaultdict(dict)
    for unit in part:
        for (cls, version), section in placed[unit].items():
            seen[cls].setdefault(version, section)
    return {cls: _part_label(per) for cls, per in seen.items()}


def _conflicts(part: Sequence[str], placed: Mapping[str, Mapping[tuple[str, str], str]]) -> dict[str, dict[str, int]]:
    """Per `class version` the part holds in several output sections, the units each section holds: what keeps a part
    from being sound, so the owner sees what to split."""
    seen: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
    for unit in part:
        for key, section in placed[unit].items():
            seen[key][section] = seen[key].get(section, 0) + 1
    return {f"{cls} {version}": per for (cls, version), per in sorted(seen.items()) if len(per) > 1}


def _blockers(units: Sequence[str], seqs: Mapping[str, Sequence[Row]], top: int = 5) -> list[Row]:
    """The widest runs of units outside `units` that sit between two of their entries of one class in one output
    section of a version: what a merge of `units` as one object cannot reproduce, widest first."""
    members = set(units)
    found: list[Row] = []
    for version, seq in seqs.items():
        live = [i for i, e in enumerate(seq) if e["k"] == "entry" and e.get("n", 0) > 0]
        hits: dict[tuple[str, str], list[int]] = defaultdict(list)
        for n, i in enumerate(live):
            if seq[i]["u"] in members:
                for cls in seq[i]["cls"].split("+"):
                    hits[cls, seq[i]["o"]].append(n)
        for (cls, section), places in hits.items():
            for one, two in pairwise(places):
                if width := sum(1 for k in live[one + 1 : two] if seq[k]["u"] not in members):
                    found.append(
                        {
                            "class": cls,
                            "section": section,
                            "version": version,
                            "width": width,
                            "between": [seq[live[one]]["u"], seq[live[two]]["u"]],
                        }
                    )
    return sorted(found, key=lambda b: -b["width"])[:top]


def unplaced(rows: Sequence[tuple[str, str]], seqs: Mapping[str, Sequence[Row]]) -> list[str]:
    """The units of a module map that no version's link names: a closure over them would see no gap, so the map and the
    built tree disagree (a stale build, another source layout) and the answer must be refused, not read as bounded."""
    placed = {row["u"] for seq in seqs.values() for row in seq if row["k"] == "entry"}
    return [u for u, _ in rows if u not in placed]


def closures(rows: Sequence[tuple[str, str]], seqs: Mapping[str, Sequence[Row]]) -> list[Row]:
    """The groups of a module map that must merge together: modules (and the loose units) tied by units between their
    entries, each `{"modules", "units", "added", "bounded", "parts"}`. A group is bounded when its closure holds
    every class in one output section per version; then it is one part. Otherwise a merge would be refused for
    `sections`, and the parts (`_parts_of`) are the modules to merge instead. A lone module is left out."""
    members: dict[str, list[str]] = defaultdict(list)
    for unit, module in rows:
        members[module].append(unit)
    siblings = {u: us for us in members.values() for u in us}
    owner = {u: m for m, us in members.items() for u in us}
    groups: list[set[str]] = []
    for units in members.values():
        if len(units) < 2 or any(set(units) <= g for g in groups):
            continue
        found = _grow(units, seqs, siblings)
        tied = [g for g in groups if g & found]
        if len(found) == len(units) and not tied:
            continue
        groups = [g for g in groups if g not in tied]
        groups.append(_grow([*found, *(u for g in tied for u in g)], seqs, siblings))
    out: list[Row] = []
    for group in groups:
        order = code_order(sorted(group), seqs)
        seeds = [m for m in dict.fromkeys(owner[u] for u in order if u in owner) if len(members[m]) > 1]
        placed = _sections_of(order, seqs)
        bounded = _one_section(order, placed)
        carried = order if bounded else [u for u in order if owner.get(u) in seeds]
        parts, sound = ([order], True) if bounded else _parts_of(carried, seqs)
        walls = [[] if bounded else _blockers(part, seqs) for part in parts]
        sound = sound and not any(walls)
        first = PurePosixPath(seeds[0])
        out.append(
            {
                "modules": seeds,
                "units": len(order),
                "added": [u for u in order if owner.get(u) not in seeds],
                "bounded": bounded,
                "sound": sound,
                "parts": [
                    {
                        "module": seeds[0]
                        if bounded
                        else first.with_name(f"{first.stem}_PART{n}{first.suffix}").as_posix(),
                        "units": part,
                        "sections": _part_sections(part, placed),
                        "conflicts": _conflicts(part, placed),
                        "blockers": walls[n - 1],
                    }
                    for n, part in enumerate(parts, 1)
                ],
            }
        )
    return out
