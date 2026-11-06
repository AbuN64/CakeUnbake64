"""The width census: data symbols whose definition element width differs from the width their readers use.

A data symbol is defined by a variable definition in the preprocessed units (`vdef`) or by a label of an extracted data
assembly file followed by data directives. Its readers are every `extern` declaration of it, the load and store widths
of the extracted assembly that reads its address, and the relocations of the built objects against it. Names at one
address in a version's symbol files are one symbol. The element width is the normalised type with the first array bound
dropped, sized by the target C ABI; a struct, union or enum element the tool cannot size is written by its tag and
differs from every scalar. No project, version or symbol name is special."""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from unbake import accesses, symbols, versions
from unbake import config as configuration
from unbake.contracts import Config
from unbake.typecensus import Decl

Width = int | str  # bytes, or the tag of an element the tool cannot size
Symbol = tuple[int, int | None, int | None, str]  # vram, rom, size, type ("func" or the data type)

HEADER = "name\tdef_width\tdefinition\treaders\tloads\tversions\tstatus\n"
STATUSES = ("other-width", "unread", "same-width")
_SCALAR = {"char": 1, "short": 2, "int": 4, "long": 4, "float": 4, "double": 8, "long long": 8}
_QUALIFIER = re.compile(r"\b(const|volatile|signed|unsigned|register)\b")
_FIRST_BOUND = re.compile(r"\[[^\]]*\]")
_DIRECTIVE = {"word": 4, "4byte": 4, "float": 4, "half": 2, "short": 2, "hword": 2, "2byte": 2, "byte": 1}
_DIRECTIVE |= {"double": 8, "quad": 8, "8byte": 8}
_DATA = re.compile(r"^\s*(?:/\*.*?\*/)?\s*\.(" + "|".join(_DIRECTIVE) + r")\b")
_LABEL = re.compile(r"^\s*(?:(?:glabel|dlabel|\.global|\.globl)\s+([A-Za-z_]\w*)|([A-Za-z_]\w*):)\s*$")
_ARM = re.compile(r"^\s*(?:/\*.*?\*/)?\s*[a-z]")
_OPCODE = {  # primary opcode -> (width, store) of a memory instruction
    0x20: (1, False), 0x24: (1, False), 0x28: (1, True), 0x21: (2, False), 0x25: (2, False), 0x29: (2, True),
    0x23: (4, False), 0x27: (4, False), 0x2B: (4, True), 0x31: (4, False), 0x39: (4, True),
    0x37: (8, False), 0x3F: (8, True), 0x35: (8, False), 0x3D: (8, True),
}  # fmt: skip
_LO16, _GPREL16 = 6, 7


def element_width(typ: str) -> tuple[str, Width]:
    """(element type, width) of a normalised type: the first array bound dropped, sized by the target C ABI (char 1,
    short 2, int, long, float and pointer 4, long long and double 8). A sub-array multiplies by its bounds; a type the
    tool cannot size has its spelling for the width."""
    elem = _FIRST_BOUND.sub("", typ, count=1) if "[" in typ else typ
    count = 1
    while m := re.search(r"\[(\d+)\]$", elem):
        count *= int(m[1])
        elem = elem[: m.start()]
    if "[" in elem or ("(" in elem and "(*)" not in elem):
        return elem, elem
    if "*" in elem or "(*)" in elem:
        return elem, 4 * count
    base = " ".join(_QUALIFIER.sub(" ", elem).split()) or "int"
    base = {"short int": "short", "long int": "long", "long long int": "long long"}.get(base, base)
    if base in _SCALAR:
        return elem, _SCALAR[base] * count
    return elem, elem


def data_defs(text: str) -> list[tuple[str, int, int]]:
    """(label, element width, line) of every label of an assembly file that is followed by a data directive before any
    instruction: the element width is that of the first directive."""
    out: list[tuple[str, int, int]] = []
    pending: list[tuple[str, int]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if m := _LABEL.match(line):
            name = m[1] or m[2]
            if not name.startswith(".L"):
                pending.append((name, number))
        elif m := _DATA.match(line):
            out.extend((n, _DIRECTIVE[m[1]], at) for n, at in pending)
            pending = []
        elif _ARM.match(line) and not line.lstrip().startswith("."):
            pending = []
    return out


def _fields(data: bytes) -> tuple[str, list[tuple[int, int, int, int, int]]] | None:
    """(byte order, sections as (type, offset, size, link, info)) of an ELF32 object; None for anything else."""
    if data[:4] != b"\x7fELF" or data[4] != 1 or len(data) < 52:
        return None
    end = "<" if data[5] == 1 else ">"
    shoff, shentsize, shnum = struct.unpack_from(end + "I", data, 0x20)[0], *struct.unpack_from(end + "HH", data, 0x2E)
    sections = []
    for at in range(shnum):
        _n, kind, _f, _a, offset, size, link, info = struct.unpack_from(end + "8I", data, shoff + at * shentsize)
        sections.append((kind, offset, size, link, info))
    return end, sections


def object_loads(data: bytes) -> list[tuple[str, int, str]]:
    """(symbol, width, function) of every relocation of an object whose instruction is a load or a store: the low half
    of an address (`%lo`, `%gp_rel`) applied to a memory opcode is that access's width, whatever the C type said."""
    got = _fields(data)
    if got is None:
        return []
    end, sections = got
    symtab = next((i for i, s in enumerate(sections) if s[0] == 2), None)
    if symtab is None:
        return []
    _k, offset, size, strtab, _i = sections[symtab]
    names = sections[strtab]
    syms = []
    for at in range(offset, offset + size, 16):
        name, value, extent, info, _o, shndx = struct.unpack_from(end + "3IBBH", data, at)
        start = names[1] + name
        label = data[start : data.index(b"\0", start)].decode("latin-1")
        syms.append((label, value, extent, info & 0xF, shndx))
    out: list[tuple[str, int, str]] = []
    for kind, roff, rsize, link, target in sections:
        if kind not in (9, 4) or link != symtab or target >= len(sections):
            continue
        code = sections[target]
        step = 8 if kind == 9 else 12
        for at in range(roff, roff + rsize, step):
            where, info = struct.unpack_from(end + "II", data, at)
            if info & 0xFF not in (_LO16, _GPREL16) or (info >> 8) >= len(syms):
                continue
            word = struct.unpack_from(end + "I", data, code[1] + where)[0] if where + 4 <= code[2] else 0
            if (word >> 26) not in _OPCODE:
                continue
            owner = next((s[0] for s in syms if s[3] == 2 and s[4] == target and s[1] <= where < s[1] + s[2]), "")
            out.append((syms[info >> 8][0], _OPCODE[word >> 26][0], owner))
    return out


@dataclass(frozen=True)
class Evidence:
    """What the census reads beside the preprocessed units; tests replace all three."""

    symbols: Callable[[str], Mapping[str, Symbol]]  # version -> its symbol files, aliases included, name -> Symbol
    assembly: Callable[[str], Iterable[tuple[str, str]]]  # version -> (path, text) of each extracted assembly file
    objects: Callable[[str], Iterable[bytes]]  # version -> the built objects (empty where the tree has none)


def _root(base: accesses.Base) -> str | None:
    while base and base[0] == "idx":
        base = base[1]
    return str(base[1]) if base and base[0] == "sym" else None


def _loads(
    evidence: Evidence, version: str
) -> tuple[dict[str, list[tuple[int, str]]], dict[str, list[tuple[int, str, int]]]]:
    """(name -> [(width, function)] of every access, name -> [(width, path, line)] of every label with data)."""
    loads: dict[str, list[tuple[int, str]]] = defaultdict(list)
    defs: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
    for path, text in evidence.assembly(version):
        for label, width, line in data_defs(text):
            defs[label].append((width, path, line))
        for function in accesses.functions(text):
            for access in function.accesses:
                if (root := _root(access.base)) is not None:
                    loads[root].append((access.width, function.name))
    for data in evidence.objects(version):
        for symbol, width, owner in object_loads(data):
            loads[symbol].append((width, owner))
    return loads, defs


def _groups(table: Mapping[str, Symbol]) -> dict[str, frozenset[str]]:
    """name -> the names at its address (data symbols only): one symbol."""
    at: dict[int, set[str]] = defaultdict(set)
    for name, (vram, _rom, _size, kind) in table.items():
        if kind != "func":
            at[vram].add(name)
    return {n: frozenset(g) for g in at.values() for n in g}


def _label(group: Iterable[str], readers: Mapping[str, int]) -> str:
    """The name a symbol is listed by: the one its code reads by, then the shortest, then the first."""
    return min(group, key=lambda n: (-readers.get(n, 0), len(n), n))


def rows(
    per: Mapping[str, Mapping[str, Sequence[Decl]]], evidence: Evidence, versions: Iterable[str]
) -> list[dict[str, str]]:
    """One record per data symbol of every version, merged by the name it is listed by: name, def_width, definition,
    readers, loads, versions, status (`other-width`, `unread` or `same-width`)."""
    merged: dict[str, dict[str, Any]] = {}
    for version in sorted(versions):
        loads, asm = _loads(evidence, version)
        table = _groups(evidence.symbols(version))
        names = {n for n, vers in per.items() if version in vers}
        seen: set[frozenset[str]] = set()
        for name in sorted(names | set(asm)):
            group = table.get(name, frozenset({name}))
            if group in seen:
                continue
            seen.add(group)
            decls = [(n, d) for n in sorted(group) for d in per.get(n, {}).get(version, ())]
            defs: dict[str, Width] = {}
            for _n, (loc, kind, typ, _c) in decls:
                if kind == "vdef":
                    elem, width = element_width(typ)
                    defs[f"{elem} {width} => {loc}"] = width
            for n in sorted(group):
                for width, path, line in asm.get(n, ()):
                    defs[f"asm {width} => {path}:{line}"] = width
            if not defs:
                continue
            reads: dict[str, Width] = {}
            for _n, (loc, kind, typ, _c) in decls:
                if kind == "ext":
                    elem, width = element_width(typ)
                    reads[f"{elem} {width} => {loc} ext"] = width
            used = sorted({(w, f) for n in group for w, f in loads.get(n, ())})
            widths = set(defs.values())
            wrong = any(w not in widths for w in reads.values()) or (
                all(isinstance(w, int) for w in widths) and any(w not in widths for w, _f in used)
            )
            status = "other-width" if wrong else "same-width" if reads or used else "unread"
            counts = {n: sum(1 for m, d in decls if m == n and d[1] == "ext") for n in group}
            row = merged.setdefault(
                _label(group, counts), {"def": set(), "defs": [], "reads": [], "loads": [], "ver": set(), "st": set()}
            )
            row["def"].update(str(w) for w in widths)
            row["defs"].extend(k for k in defs if k not in row["defs"])
            row["reads"].extend(k for k in reads if k not in row["reads"])
            row["loads"].extend(f"{w} => {f}" for w, f in used if f"{w} => {f}" not in row["loads"])
            row["ver"].add(version)
            row["st"].add(status)
    out = []
    for name, row in sorted(merged.items()):
        states = row["st"]
        status = "other-width" if "other-width" in states else "same-width" if "same-width" in states else "unread"
        out.append(
            {
                "name": name,
                "def_width": ",".join(sorted(row["def"])),
                "definition": "; ".join(row["defs"]),
                "readers": "; ".join(row["reads"]),
                "loads": "; ".join(row["loads"]),
                "versions": ",".join(sorted(row["ver"])),
                "status": status,
            }
        )
    return out


def table(records: Sequence[Mapping[str, str]]) -> str:
    """The WIDTH.tsv text: a row per `other-width` and `unread` symbol; same-width symbols are not rows."""
    keys = HEADER.rstrip("\n").split("\t")
    lines = ["\t".join(r[k].replace("\t", " ").replace("\n", " ") for k in keys) + "\n" for r in records]
    return HEADER + "".join(line for r, line in zip(records, lines, strict=True) if r["status"] != "same-width")


def counts(records: Sequence[Mapping[str, str]]) -> dict[str, int]:
    """The symbols per status."""
    return {s: sum(1 for r in records if r["status"] == s) for s in STATUSES}


def real(cfg: Config) -> Evidence:
    """The project's own symbol files, extracted assembly and built objects."""
    root = cfg.project.root

    def assembly(version: str) -> Iterable[tuple[str, str]]:
        for path in versions.asm_files(root, version):
            yield path.relative_to(root).as_posix(), path.read_text(errors="replace")

    def objects(version: str) -> Iterable[bytes]:
        home = root / configuration.root("build") / version
        for path in sorted(home.rglob("*.o")) if home.is_dir() else ():
            yield path.read_bytes()

    return Evidence(lambda v: symbols.load(cfg, v), assembly, objects)
