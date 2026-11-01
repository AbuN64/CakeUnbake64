"""Class-wise unit order: the text of a merged module with each section class in its own linked order.

A linker places the text, data and read-only data of one module in whatever order its link script lists them, so the
units of a module can be linked text A, B but data B, A. One source file cannot hold that as a concatenation of its
units, but a compiler emits each section in source order, so the file only has to put the code of A before the code
of B and the data of B before the data of A. `arrange` cuts every unit body into chunks (a top-level statement, or the
whole outermost `#if` block it sits in), tells each chunk's class from the symbols the built object of its unit
defines, and emits the chunks without storage (typedefs, prototypes, externs) first, then the storage chunks in an
order that keeps every class in its own unit order and defines a data name before a chunk uses it. A function used
before its definition gets a prototype and a data name an extern declaration. A class is linked in one
base order (the versions' orders merged); a unit that a version links elsewhere is emitted a second time,
under the guard of the versions that move it, at the place those versions link it, and the original sits
under the negated guard. What cannot be ordered is reported, never guessed."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

Row = dict[str, Any]
CLASSES = ("text", "data", "rodata", "bss")
_STORED = CLASSES[1:3]
_IF = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef)\b")
_ENDIF = re.compile(r"^[ \t]*#[ \t]*endif\b")
_WORD = re.compile(r"[A-Za-z_]\w*")


@dataclass(eq=False)
class Chunk:
    unit: str
    at: int  # position of the chunk in its unit
    text: str
    masked: str
    classes: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    heads: dict[str, str] = field(default_factory=dict)  # function name -> its prototype text
    when: str = ""  # a copy for the versions that move its unit: the condition it is emitted under
    unless: str = ""  # the original of a moved unit: the condition it is left out under
    externs: dict[str, str] = field(default_factory=dict)  # data name -> its extern declaration

    @property
    def cls(self) -> str:
        """The class of the chunk's storage; empty when it holds none."""
        return self.classes[0] if self.classes else ""


def _blocks(masked: str) -> list[tuple[int, int]]:
    """The offsets of the outermost `#if` .. `#endif` blocks of the text."""
    out: list[tuple[int, int]] = []
    depth, start, at = 0, 0, 0
    for line in masked.split("\n"):
        end = at + len(line) + 1
        if _IF.match(line):
            if depth == 0:
                start = at
            depth += 1
        elif _ENDIF.match(line) and depth:
            depth -= 1
            if depth == 0:
                out.append((start, end))
        at = end
    return out


def _fallback(decl: Any, masked: str) -> str:
    """The class of a variable the object does not name: by its text."""
    text = masked[decl.start : decl.end]
    head = text.split("=")[0]
    if "=" not in text:
        return "bss"
    stars = head.rfind("*")
    return "rodata" if re.search(r"\bconst\b", head[stars + 1 :] if stars >= 0 else head) else "data"


def chunks(unit: str, body: str, masked: str, decls: Sequence[Any], classes: Mapping[str, str]) -> list[Chunk]:
    """The chunks of one unit body in order, covering all of it."""
    blocks = _blocks(masked)
    out: list[Chunk] = []
    position, index = 0, 0
    while index < len(decls):
        decl = decls[index]
        end = decl.end + (1 if body[decl.end : decl.end + 1] == "\n" else 0)
        if decl.guard:
            block = next((b for b in blocks if b[0] <= decl.start < b[1]), None)
            if block:
                end = block[1]
        members = [d for d in decls[index:] if d.start < end]
        index += len(members)
        chunk = Chunk(unit, len(out), body[position:end], masked[position:end])
        for d in members:
            _classify(chunk, d, body, masked, classes)
        out.append(chunk)
        position = end
    if body[position:].strip() and out:
        out[-1].text += body[position:]
        out[-1].masked += masked[position:]
    return out


def _classify(chunk: Chunk, decl: Any, body: str, masked: str, classes: Mapping[str, str]) -> None:
    statement = masked[decl.start : decl.end]
    if decl.kind == "def":
        chunk.classes.append("text")
        chunk.defines.append(decl.name)
        chunk.heads[decl.name] = statement[: statement.find("{")].strip() + ";"
    elif decl.kind == "var":
        chunk.classes.append(classes.get(decl.name) or _fallback(decl, masked))
        chunk.defines.append(decl.name)
        if not (decl.static or decl.joint or decl.guard):
            chunk.externs[decl.name] = _extern(body[decl.start : decl.end], statement)
    elif decl.kind == "proto" and (call := _WORD.match(statement.lstrip())) and call.group(0) == decl.name:
        # a macro call that places a symbol (an included data or code block): the symbols named in its arguments
        for word in _WORD.findall(statement[statement.find("(") :]):
            if word in classes:
                chunk.classes.append(classes[word])
                chunk.defines.append(word)


def base_order(orders: Iterable[Sequence[str]]) -> list[str]:
    """One order that holds every unit and keeps the first order whole: a unit only a later order holds goes after the
    unit that precedes it there (the front when it has no earlier neighbour)."""
    base: list[str] = []
    for order in orders:
        anchor = -1
        for unit in order:
            if unit not in base:
                anchor += 1
                base.insert(anchor, unit)
            else:
                anchor = base.index(unit)
    return base


def moved(base: Sequence[str], order: Sequence[str]) -> list[str]:
    """The units of `order` that sit elsewhere than in `base`: those left out of the longest run both keep."""
    held = set(order)
    other = [u for u in base if u in held]
    n, m = len(order), len(other)
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = best[i + 1][j + 1] + 1 if order[i] == other[j] else max(best[i + 1][j], best[i][j + 1])
    out: list[str] = []
    i = j = 0
    while i < n and j < m:
        if order[i] == other[j]:
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            out.append(order[i])
            i += 1
        else:
            j += 1
    return out + list(order[i:])


def _moves(
    bases: Mapping[str, Sequence[str]], orders: Mapping[str, Mapping[str, Sequence[str]]]
) -> dict[tuple[str, str], dict[str | None, tuple[int, list[str]]]]:
    """(class, unit) -> anchor (the unit before it in those versions, None at the front) -> (its place, versions)."""
    out: dict[tuple[str, str], dict[str | None, tuple[int, list[str]]]] = defaultdict(dict)
    for cls, per in orders.items():
        for version, order in per.items():
            gone = set(moved(bases[cls], order))
            for at, unit in enumerate(order):
                if unit in gone:
                    anchor = next((order[k] for k in range(at - 1, -1, -1) if order[k] not in gone), None)
                    out[cls, unit].setdefault(anchor, (at, []))[1].append(version)
    return out


def _condition(versions: Iterable[str], guards: Mapping[str, str]) -> str:
    return " || ".join(f"defined({guards[v]})" for v in sorted(versions))


def _fill(n: int, fill: Row, versions: Sequence[str], guards: Mapping[str, str]) -> Chunk:
    """The array of the stale bytes `fill` names for `versions`; under a condition when only some versions link them
    there."""
    const = "const " if fill["cls"] == "rodata" else ""
    text = f"{const}unsigned char unbake_fill_{n}[] = {{{', '.join(f'0x{b:02X}' for b in fill['data'])}}};"
    chunk = Chunk(fill["after"], 1 << 30, text, text, [fill["cls"]], [f"unbake_fill_{n}"])
    if guards and set(versions) != set(guards):
        chunk.when = _condition(versions, guards)
    return chunk


def _fill_places(
    fill: Row,
    moves: Mapping[tuple[str, str], Mapping[str | None, tuple[int, list[str]]]],
    rank: Mapping[str, Mapping[str, int]],
) -> list[tuple[tuple[int, int, int, int], list[str]]]:
    """Where the stale bytes of `fill` sit in the chain: right after the unit's data as the fill's versions link it. A
    unit some versions link elsewhere is emitted twice (its original and a copy per anchor), so the bytes follow the
    copy of the versions that hold them, never the last copy of the unit. Returns (place, versions) per distinct
    place; the place is the chain key of the unit's copy with the array's position past every statement of it."""
    cls, unit = fill["cls"], fill["after"]
    left = list(fill["versions"])
    out: list[tuple[tuple[int, int, int, int], list[str]]] = []
    for anchor, (at, versions) in moves.get((cls, unit), {}).items():
        here = [v for v in left if v in versions]
        if here:
            out.append(((rank[cls][anchor] if anchor else -1, 1, at, 1 << 30), here))
            left = [v for v in left if v not in here]
    if left:
        out.append(((rank[cls][unit], 0, 0, 1 << 30), left))
    return out


def _extern(text: str, masked: str) -> str:
    """The `extern` declaration of the data defined by a statement: its text up to the initialiser."""
    cut = masked.find("=")
    return "extern " + (text[:cut] if cut >= 0 else text).strip().rstrip(";").rstrip() + ";"


def arrange(
    parts: Sequence[tuple[str, str, str, Sequence[Any]]],
    orders: Mapping[str, Mapping[str, Sequence[str]]],
    classes: Mapping[str, Mapping[str, str]],
    guards: Mapping[str, str] | None = None,
    fills: Sequence[Row] = (),
) -> tuple[str, list[Row]]:
    """The merged body of `parts` ((unit, body, masked body, declarations) in code order) with every class in the base
    order of its `orders` (class -> version -> units in that version's linked order) and each unit a version links
    elsewhere also emitted under `guards` (version -> macro) where that version links it, and the conflicts left.
    `fills` ({after, cls, data, versions}) are the stale link bytes between two units: each is emitted as an array of
    those bytes after the unit's data of that class, for the versions that link them there."""
    conflicts: list[Row] = []
    every = [c for unit, body, masked, decls in parts for c in chunks(unit, body, masked, decls, classes.get(unit, {}))]
    for chunk in every:
        if len(set(chunk.classes)) > 1:
            conflicts.append(
                {
                    "kind": "mixed",
                    "name": chunk.defines[0],
                    "detail": f"{chunk.unit}: one block holds {'+'.join(sorted(set(chunk.classes)))}",
                }
            )
    plain = [c for c in every if not c.cls]
    storage = [c for c in every if c.cls]
    bases = {cls: base_order(per.values()) for cls, per in orders.items()}
    rank = {cls: {u: i for i, u in enumerate(base)} for cls, base in bases.items()}
    for cls in {c.cls for c in storage}:
        unplaced = {c.unit for c in storage if c.cls == cls} - rank.get(cls, {}).keys()
        if unplaced:
            rank.setdefault(cls, {}).update({u: len(rank.get(cls, {})) + i for i, u in enumerate(sorted(unplaced))})
    place: dict[Chunk, tuple[int, int, int, int]] = {c: (rank[c.cls][c.unit], 0, 0, c.at) for c in storage}
    moves = _moves(bases, orders)
    for (cls, unit), anchors in moves.items():
        every_version = [v for _, versions in anchors.values() for v in versions]
        for chunk in [c for c in storage if c.unit == unit and c.cls == cls]:
            chunk.unless = _condition(every_version, guards or {})
            for anchor, (at, versions) in anchors.items():
                copy = Chunk(unit, chunk.at, chunk.text, chunk.masked, list(chunk.classes), list(chunk.defines))
                copy.when = _condition(versions, guards or {})
                storage.append(copy)
                place[copy] = (rank[cls][anchor] if anchor else -1, 1, at, chunk.at)
    kept = [f for f in fills if f["cls"] in _STORED and f["after"] in rank.get(f["cls"], {})]
    for n, fill in enumerate(kept):
        for where, versions in _fill_places(fill, moves, rank):
            chunk = _fill(n, fill, versions, guards or {})
            storage.append(chunk)
            place[chunk] = where
    chain: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in sorted(storage, key=lambda c: place[c]):
        chain[chunk.cls].append(chunk)
    # a unit whose object holds read-only data no statement of it defines (the literals its functions emit) has that
    # data where its functions are compiled, so its functions sit at the unit's place in the read-only data order
    defined = {(c.unit, n) for c in storage for n in c.defines}
    literals = {
        unit: rank["rodata"][unit]
        for unit in {c.unit for c in storage if c.cls == "text"}
        if unit in rank.get("rodata", {})
        and any(cls == "rodata" and (unit, name) not in defined for name, cls in classes.get(unit, {}).items())
    }
    ordered, forward = _sequence(chain, storage, literals, rank.get("rodata", {}))
    functions = {n: c.heads[n] for c in storage for n in c.heads}
    externs = {n: c.externs[n] for c in storage for n in c.externs}
    protos: list[str] = []
    for chunk, names in forward:
        for name in names:
            if name in functions:
                protos.append(functions[name])
            elif name in externs:
                protos.append(externs[name])
            else:
                conflicts.append(
                    {"kind": "forward", "name": name, "detail": f"{chunk.unit}: uses {name} before its definition"}
                )
    out: list[str] = []
    last = ""
    for chunk in plain:
        out.append(chunk.text.strip("\n"))
    if protos:
        out.append("\n".join(dict.fromkeys(protos)))
    for chunk in ordered:
        label = f"/* {chunk.unit.rsplit('/', 1)[-1]} */\n" if chunk.unit != last else ""
        last = chunk.unit
        body = chunk.text.strip("\n")
        if chunk.when:
            body = f"#if {chunk.when}\n{body}\n#endif"
        elif chunk.unless:
            body = f"#if !({chunk.unless})\n{body}\n#endif"
        out.append(label + body)
    return "\n\n".join(p for p in out if p.strip()), conflicts


def _sequence(
    chain: Mapping[str, list[Chunk]],
    storage: Sequence[Chunk],
    literals: Mapping[str, int] | None = None,
    rodata: Mapping[str, int] | None = None,
) -> tuple[list[Chunk], list[tuple[Chunk, list[str]]]]:
    """The storage chunks in one order that keeps each class chain: the next chunk of the class whose head is first in
    the default order (the order of the text chain, then the rest) and whose data names are all defined; when none
    is ready, the first head goes anyway and its early uses are returned. `literals` (unit -> its place in the
    read-only data order) holds the units whose functions emit read-only data: their functions wait for the
    read-only chunks of the units before them, and the read-only chunks of the units after them wait for their
    functions. `rodata` is every unit's place in that order."""
    literals, rodata = literals or {}, rodata or {}

    def waits(head: Chunk) -> bool:
        if head.cls == "text" and head.unit in literals:
            return any(rodata.get(c.unit, 0) < literals[head.unit] for c in heads.get("rodata", [])[:1])
        if head.cls == "rodata":
            return any(literals[c.unit] < rodata.get(head.unit, 0) for c in heads.get("text", []) if c.unit in literals)
        return False

    home = {c: i for i, c in enumerate(c for cls in ("text", *CLASSES) for c in chain.get(cls, ()))}
    owner: dict[str, list[Chunk]] = defaultdict(list)
    for c in storage:
        if c.when:
            continue
        for n in c.defines:
            if n not in c.heads and c not in owner[n]:
                owner[n].append(c)
    heads = {cls: list(chunks) for cls, chunks in chain.items()}
    done: set[int] = set()
    out: list[Chunk] = []
    forward: list[tuple[Chunk, list[str]]] = []
    # definitions of one name under mutually exclusive `#if` blocks are one definition: a chunk that defines the name
    # itself uses none of them, any other chunk uses all of them
    uses = {
        c: {w for w in _WORD.findall(c.masked) if w in owner and c not in owner[w] and w not in c.defines}
        for c in storage
    }
    while any(heads.values()):
        ready = [
            h[0]
            for h in heads.values()
            if h and not waits(h[0]) and all(id(o) in done for w in uses[h[0]] for o in owner[w])
        ]
        pick = min(ready or [h[0] for h in heads.values() if h], key=lambda c: home[c])
        if pick not in ready:
            forward.append((pick, sorted(w for w in uses[pick] if any(id(o) not in done for o in owner[w]))))
        heads[pick.cls].pop(0)
        done.add(id(pick))
        out.append(pick)
    for chunk in out:
        later = {n for c in out[out.index(chunk) + 1 :] for n in c.heads}
        used = sorted(w for w in _WORD.findall(chunk.masked) if w in later and w not in chunk.heads)
        if used:
            forward.append((chunk, list(dict.fromkeys(used))))
    return out, forward
