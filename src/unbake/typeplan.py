"""Which declared type one name takes, and the source edits that give every declaration of it that type.

A name's candidates are the types it is declared with, the most declared first (a type spelled `void*` after the others
of equal count, then the longer spelling last); a definition with a body or an initialiser decides alone. `rank` picks
the next candidate when the one before it failed to keep an object identical. A file whose declaration changes from a
scalar to a one-field struct of that scalar (or back) also has its reads of the name rewritten to the new type
(`x` as `x.value`, `x.value` as `x`), and one that changes from a scalar to an array of it has its reads of the name
rewritten as `x[0]`, and one that changes between a pointer and an array of the type it points to has its reads of the
name rewritten as the pointer stored at the array (`*(T **)x`) or the address the array stands for (`(T *)&x`), by the
one rule for every name of these shapes. A function whose chosen
declaration takes more parameters than a call in a file that declares it passes has the call completed with the
enclosing function's own parameters at the missing positions (the registers the call leaves untouched, so the bytes
stay), and a rewritten declaration whose parameter list names a struct or union tag is preceded, on its line, by a
declaration of that tag (else the tag is born inside the parameter list and conflicts with the file's own).
The edit of a declaration replaces the
whole source statement that holds it (a multi-declarator statement keeps its other names) by the canonical declaration
text, written from a declaration of the chosen type that is in the sources. A name whose candidates are records (a
struct tag or an anonymous body, or a pointer to or array of one) has each unit's own definition read through cheaders
(offsets and sizes) and the members merged by offset into the chosen candidate's definition: members that overlap
become the alternatives of an unnamed union, so every unit still reads the member it wrote, at its offset. Comments are
blanked before any statement
is cut, so offsets are those of the original text."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import pairwise

from unbake.cheaders import Field, Headers, Struct
from unbake.typecensus import Norm, decimal_bounds, split_top, tkey, ws

EXHAUSTED = "no further candidate type"  # the plan of a rank past the last candidate
Site = tuple[str, str, int]  # (kind, type key, declarators) of one declaration by its loc "file:line"
Edit = tuple[str, int, int]  # (file, start, end) of the source statement to replace
Body = tuple[str, int, int, str]  # (file, start, end, text): a record's `{ .. }` and the merged members that replace it
Read = tuple[str, str, str]  # (file, "into" | "out", field): its reads of the name become `name.field` or `name`


@dataclass(frozen=True)
class Plan:
    name: str
    canon: str  # the chosen type
    why: str
    ctext: str  # the canonical declaration, spelled for a source
    edits: tuple[Edit, ...]
    files: frozenset[str]  # every file the plan reads or writes
    reads: tuple[Read, ...] = ()
    keep: tuple[Edit, ...] = ()  # the declarations of the name in a file of `reads`: not reads
    params: tuple[str, ...] = ()  # the parameters of a function's chosen declaration
    callers: frozenset[str] = frozenset()  # the files whose calls of the name pass fewer arguments than `params`
    bodies: tuple[Body, ...] = ()  # the chosen record's definitions, their members merged with every candidate's
    includes: tuple[
        tuple[str, str], ...
    ] = ()  # (source file, header): the header that defines the chosen tag, which the file's unit does not read


@dataclass
class Sources:
    """The original text of source files, read once through `read`, and the same with comments blanked."""

    read: Callable[[str], str]
    _text: dict[str, tuple[str, str]] = field(default_factory=dict)

    def text(self, path: str) -> tuple[str, str]:
        if path not in self._text:
            t = self.read(path)
            b = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m[0]), t, flags=re.S)
            self._text[path] = (t, re.sub(r"//[^\n]*", lambda m: " " * len(m[0]), b))
        return self._text[path]


def sites_of(per: Mapping[str, Mapping[str, Sequence[tuple[str, str, str, int]]]], name: str) -> dict[str, Site]:
    """loc -> (kind, type key, declarators) over every version that declares the name."""
    return {loc: (kind, tkey(typ), n) for ds in per[name].values() for loc, kind, typ, n in ds}


def candidates(sites: Mapping[str, Site]) -> list[str]:
    """The types the name may take, first choice first; a definition is the only candidate."""
    defs = {key for kind, key, _ in sites.values() if kind in ("fdef", "vdef")}
    if defs:
        return sorted(defs)
    votes = Counter(key for _, key, _ in sites.values())
    return [k for k, _ in sorted(votes.items(), key=lambda kv: (-kv[1], "void*" in kv[0], -len(kv[0])))]


def _span(src: Sources, path: str, line: int, name: str) -> tuple[int, int] | None:
    """Source span of the statement whose first token is on `line`; None when it does not hold `name`."""
    try:
        t, b = src.text(path)
        p = 0
        for _ in range(line - 1):
            p = t.index("\n", p) + 1
    except (OSError, ValueError):
        return None
    n = len(b)
    while p < n and b[p].isspace():
        p += 1
    i, depth = p, 0
    word = re.compile(r"\b" + re.escape(name) + r"\b")
    while i < n:
        c = b[i]
        if c in "\"'":
            i += 1
            while i < n and b[i] != c:
                i += 2 if b[i] == "\\" else 1
        elif c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "{" and depth == 0:
            start, level, i = i, 1, i + 1
            while i < n and level:
                level += (b[i] == "{") - (b[i] == "}")
                i += 1
            e = i
            while e < n and b[e] in " \t":
                e += 1
            if e < n and b[e] == ";":
                return (p, e + 1) if word.search(b[p : e + 1]) else None
            if b[:start].rstrip().endswith(")"):  # a function body ends its statement
                return (p, i) if word.search(b[p:i]) else None
            i -= 1  # a struct or union body or an initializer: the declarators and the `;` follow
        elif c == ";" and depth == 0:
            return (p, i + 1) if word.search(b[p : i + 1]) else None
        elif c == "#" and depth == 0 and (i == 0 or b[i - 1] == "\n"):
            return None
        i += 1
    return None


_TAG_OPEN = re.compile(r"\b(?:struct|union|enum)\s*\w*\s*$")


def _declaration(s: str) -> str:
    """A statement without its function body or initializer; the body of an anonymous `struct { .. }` type stays."""
    depth = 0
    for i, c in enumerate(s):
        if c == "{":
            if depth == 0 and not _TAG_OPEN.search(s[:i]):
                return s[:i].strip()
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "=" and depth == 0:
            return s[:i].strip()
    return s


def _outside_braces(text: str) -> str:
    """`text` with everything inside `{ .. }` dropped."""
    return re.sub(r"\{[^{}]*\}", "", text) if "{" in text else text


def _decl_text(stmt: str, name: str) -> str | None:
    """The single declaration `BASE declarator` of `name` in a statement text (comments blanked)."""
    s = _declaration(re.sub(r"^(extern|static)\s+", "", ws(stmt.rstrip(";").strip())))
    parts = split_top(s)
    m = re.match(r"^(.*?)(\*+\s*)?\b" + re.escape(name) + r"\b", parts[0])
    if m:
        base = m[1].strip()
    else:
        mb = re.match(r"^(.*?)\s*\**\s*\b\w+\b\s*(\[.*)?$", parts[0])
        base = mb[1].strip() if mb else ""
    for i, p in enumerate(parts):
        if re.search(r"\b" + re.escape(name) + r"\b", p):
            return (s if len(parts) == 1 else parts[0].strip()) if i == 0 else ws(base + " " + p.strip())
    return None


def _spell(text: str, norm: Norm) -> str:
    """A tag typedef written as its tag (`Foo` as `struct Foo`) outside a tag position, the first bound opened and every
    other bound in decimal."""
    out, prev = [], ""
    for t in re.split(r"(\b\w+\b)", text):
        full = norm.typedefs.get(t, "")
        out.append(
            full
            if re.match(r"\w+$", t)
            and prev not in ("struct", "union", "enum")
            and full.split(" ")[0] in ("struct", "union", "enum")
            else t
        )
        prev = t.strip() or prev
    spelled = decimal_bounds("".join(out))  # a bound copied from another declaration is decimal, as the audit demands
    if "(" in spelled:
        return spelled
    cut = spelled.rfind("}") + 1  # a member's bound inside a record body stays
    return spelled[:cut] + re.sub(r"\[[^\]]+\]", "[]", spelled[cut:], count=1)


_BOX = re.compile(r"^(?:struct|union)\s*\w*\s*\{\s*([^;{}]+?)\s*\b(\w+)\s*;\s*\}$")
_PRIM = re.compile(r"^(?:unsigned |signed )?(?:char|short|int|long|long long|float|double)$")


def _shape(decl: str, name: str, norm: Norm) -> tuple[str, str, str] | None:
    """("scalar", type, "") for a plain variable of a primitive type, ("array", element type, "") for an unsized array
    of one, ("pointer", pointee type, "") for a pointer to one, ("box", field type, field) for a one-field struct
    variable, ("record", type, "") for any other type."""
    m = re.match(r"^(.*?)\s*\b" + re.escape(name) + r"\s*(\[\s*\])?$", ws(decl))
    base = m[1].strip() if m else ""
    if not m or not base:
        return None
    if box := _BOX.match(base):
        return ("box", norm.prim(box[1]), box[2]) if not m[2] else None
    if base.endswith("*") and not m[2] and not re.search(r"[{}\[\]()*]", base[:-1]):
        pointee = norm.prim(base[:-1].strip())
        return ("pointer", pointee, "") if _PRIM.match(pointee) else None
    if re.search(r"[{}\[\]()*]", base):
        return None
    kind = norm.prim(base)
    if _PRIM.match(kind):
        return ("array" if m[2] else "scalar", kind, "")
    return None if m[2] else ("record", kind, "")


def _direction(canon: tuple[str, str, str] | None, was: tuple[str, str, str] | None) -> tuple[str, str] | None:
    """How reads written for the type `was` are written for `canon`: one is the one-field struct of the other, or
    `canon` is a record that `was` (a primitive or an array of one) views by its address, or `canon` is the unsized
    array of the primitive `was` (its reads of the scalar are reads of the first element), or one of `canon` and `was`
    is an unsized array and the other a pointer (the array's own address is what the pointer holds, so a read of the
    pointer is the pointer stored at the array and a read of the array is the address of the pointer)."""
    if not canon or not was:
        return None
    if (canon[0], was[0]) == ("array", "scalar") and canon[1] == was[1]:
        return ("first", "0")
    if (canon[0], was[0]) == ("array", "pointer"):
        return ("held", was[1])
    if (canon[0], was[0]) == ("pointer", "array"):
        return ("address", canon[1])
    if canon[0] == "record" and was[0] in ("scalar", "array"):
        return ("view" if was[0] == "scalar" else "views", was[1])
    if canon[1] != was[1] or canon[0] == was[0] or "record" in (canon[0], was[0]) or "array" in (canon[0], was[0]):
        return None
    return ("into", canon[2]) if canon[0] == "box" else ("out", was[2])


_RECORD = re.compile(r"^((?:struct|union)\b.*?)\s*(\*+|\[\])?$")
_ANON = re.compile(r"^(?:struct|union)\s*(?:\{|__anonymous$)")  # the census names every anonymous record alike


def _blanked(src: Sources, file: str) -> str:
    """The comment-blanked text of `file`; empty when it cannot be read (a file a unit's markers name only)."""
    try:
        return src.text(file)[1]
    except OSError:
        return ""


def _close(b: str, i: int) -> int:
    """The index after the `}` that closes the `{` at `i` in `b`; -1 when it never closes."""
    depth = 0
    for j in range(i, len(b)):
        depth += (b[j] == "{") - (b[j] == "}")
        if depth == 0:
            return j + 1
    return -1


def _definitions(
    key: str, sites: Mapping[str, Site], name: str, src: Sources, scope: Iterable[str]
) -> list[tuple[str, int, int]]:
    """(file, start, end) of each `{ .. }` that defines the record of `key`: the body written at each site of an
    anonymous record, the body of the tag in the first file of the sites and `scope` (the files a unit that declares
    the name reads) that defines it, headers first."""
    base = _RECORD.match(key)[1]  # type: ignore[index]
    out: list[tuple[str, int, int]] = []
    if _ANON.match(base):
        for loc, (_, k, _) in sorted(sites.items()):
            file, line = loc.rsplit(":", 1)
            span = _span(src, file, int(line), name) if k == key else None
            b = src.text(file)[1]
            i = b.find("{", *span) if span else -1
            if i >= 0 and (j := _close(b, i)) > 0:
                out.append((file, i, j))
        return out
    tag = None if _ANON.match(base) else re.match(r"^(?:struct|union)\s+(\w+)$", base)
    for file in sorted(
        {loc.rsplit(":", 1)[0] for loc in sites} | set(scope), key=lambda f: (not f.startswith("include/"), f)
    ):
        b = _blanked(src, file)
        m = re.search(rf"\b(?:struct|union)\s+{tag[1]}\s*\{{", b) if tag else None
        if m and (j := _close(b, m.end() - 1)) > 0:
            return [(file, m.end() - 1, j)]
    return out


def _layouts(
    key: str, name: str, sites: Mapping[str, Site], src: Sources, base_headers: Headers, scope: Iterable[str]
) -> list[Struct | None]:
    """The struct each definition of the record of `key` is (None when it does not size), read through cheaders: the
    body at every site of an anonymous record, the one body of a tag."""
    base = _RECORD.match(key)[1]  # type: ignore[index]
    tag = None if _ANON.match(base) else re.match(r"^(?:struct|union)\s+(\w+)$", base)
    out: list[Struct | None] = []
    for file, s, e in _definitions(key, sites, name, src, scope):
        h = base_headers.copy()
        h.add(src.text(file)[1], file)
        if not tag:
            h.add(f"{base.split()[0]} __rec {src.text(file)[1][s:e]};")
        got = h.struct(tag[1] if tag else "__rec")
        out.append(got if got and got.complete and not got.union and got.size is not None else None)
    return out or [None]


def _spelled(f: Field) -> str:
    base, _, dims = f.type.text.partition("[")  # cheaders spells tokens apart: `short [ 2 ]`
    return (
        f"{base.strip()}{'' if base.strip().endswith('*') else ' '}{f.name}"
        + ("[" + re.sub(r"\s+", "", dims) if dims else "")
        + ";"
    )


def _alternative(members: Sequence[Field], at: int) -> str:
    """The members of one record laid out from offset `at`: a gap the compiler would not insert is an `unsigned char`
    array; one member at `at` stands alone."""
    parts, pos = [], at
    for f in members:
        align = max(f.type.align, 1)
        if f.offset != (pos + align - 1) // align * align:
            parts.append(f"unsigned char _gap{f.offset}[{f.offset - pos}];")
        parts.append(_spelled(f))
        pos = f.offset + (f.type.size or 0)
    return parts[0] if len(parts) == 1 and members[0].offset == at else "struct { " + " ".join(parts) + " };"


def _union(layouts: Sequence[Struct]) -> str | None:
    """The members of `layouts` merged by offset, the chosen record's first: members of different records that overlap
    are the alternatives of an unnamed union. None when a record's own members overlap (it holds a union already)."""
    owned = [sorted(s.fields, key=lambda f: f.offset) for s in layouts]
    if any(a.offset + (a.type.size or 0) > b.offset for fs in owned for a, b in pairwise(fs)):
        return None
    ends: dict[int, int] = {}
    for fs in owned:
        for f in fs:
            ends[f.offset] = max(ends.get(f.offset, 0), f.offset + max(f.type.size or 0, 1))
    clusters: list[tuple[int, int]] = []
    for at in sorted(ends):
        if clusters and at < clusters[-1][1]:
            clusters[-1] = (clusters[-1][0], max(clusters[-1][1], ends[at]))
        else:
            clusters.append((at, ends[at]))
    out, pos = [], 0
    for start, end in clusters:
        alts: dict[tuple[tuple[str, int, str], ...], str] = {}
        for fs in owned:
            held = [f for f in fs if start <= f.offset < end]
            if held:
                alts.setdefault(tuple((f.name, f.offset, f.type.text) for f in held), _alternative(held, start))
        out.append(next(iter(alts.values())) if len(alts) == 1 else "union { " + " ".join(alts.values()) + " };")
        pos = end
    size = max(s.size or 0 for s in layouts)
    align = max((f.type.align for s in layouts for f in s.fields), default=1)
    if (pos + align - 1) // align * align != size:
        out.append(f"unsigned char _tail[{size - pos}];")
    return " ".join(out)


def _rebody(ctext: str, body: str) -> str:
    """`ctext` with the members of its first `{ .. }` replaced by `body`."""
    i = ctext.index("{")
    return ctext[: i + 1] + " " + body + " " + ctext[_close(ctext, i) - 1 :]


def _merged(
    name: str,
    order: Sequence[str],
    canon: str,
    sites: Mapping[str, Site],
    src: Sources,
    norm: Norm,
    scope: Iterable[str],
) -> tuple[str, list[tuple[str, int, int]]] | None:
    """(merged members, the definitions of the chosen record they replace) when the name's candidates are records of
    one suffix (`*` or `[]`) whose definitions differ in layout and all read; else None."""
    cut = [_RECORD.match(k) for k in order]
    if not all(cut) or len({m[2] for m in cut if m}) != 1:
        return None
    base = Headers(typedefs=dict(norm.typedefs))
    for file in sorted(set(scope)):
        base.add(_blanked(src, file), file)
    found = [s for k in [canon, *(k for k in order if k != canon)] for s in _layouts(k, name, sites, src, base, scope)]
    layouts = {tuple((f.name, f.offset, f.type.text) for f in s.fields): s for s in found if s}
    if not all(found) or len(layouts) < 2:
        return None
    body = _union(list(layouts.values()))
    spans = _definitions(canon, sites, name, src, scope)
    return (body, spans) if body and spans else None


def _includes(
    canon: str,
    sites: Mapping[str, Site],
    name: str,
    src: Sources,
    scope: Iterable[str],
    closures: Iterable[frozenset[str]],
    edited: Iterable[str],
) -> tuple[tuple[str, str], ...]:
    """(source file, header) for each edited source file whose unit does not read the header that defines the chosen
    record's tag: the declaration it is given names a tag it sees only forward-declared, and a member read of an
    incomplete type does not build. The header is included, never the tag redefined; a tag defined in a source file, or
    an anonymous record (written whole at every site), needs nothing."""
    record = _RECORD.match(canon)
    if not record or _ANON.match(record[1]):
        return ()
    held = [f for f, _, _ in _definitions(canon, sites, name, src, scope) if f.startswith("include/")]
    if not held:
        return ()
    units = list(closures)
    return tuple(
        (f, held[0])
        for f in sorted(set(edited))
        if not f.startswith("include/") and any(f in c and held[0] not in c for c in units)
    )


def plan(
    name: str,
    sites: Mapping[str, Site],
    rank: int,
    src: Sources,
    norm: Norm,
    scope: Iterable[str] = (),
    closures: Iterable[frozenset[str]] = (),
) -> Plan | str:
    """The plan of `name` at candidate `rank`, or the reason it has none (a string). `scope` is every file a unit that
    declares the name reads: where the definition of a record tag is looked for."""
    order = candidates(sites)
    defkeys = {key for kind, key, _ in sites.values() if kind in ("fdef", "vdef")}
    if len(defkeys) > 1:
        return "several definitions with different types"
    if rank >= len(order):
        return EXHAUSTED
    canon = order[rank]
    why = "definition" if defkeys else f"candidate {rank + 1} of {len(order)}, most declared first"
    ranked = sorted(
        sites.items(), key=lambda kv: (kv[1][0] not in ("fdef", "vdef"), not kv[0].startswith("include/"), kv[0])
    )
    ctext, cloc = "", ""
    for loc, (_, key, _) in ranked:
        if key != canon:
            continue
        file, line = loc.rsplit(":", 1)
        span = _span(src, file, int(line), name)
        text = _decl_text(src.text(file)[1][span[0] : span[1]], name) if span else None
        if text and ";" not in _outside_braces(text):
            cloc, ctext = loc, _spell(text, norm)
            break
    if not cloc:
        return "no source text for the canonical declaration"
    edits: list[Edit] = []
    how: dict[str, set[tuple[str, str] | None]] = defaultdict(set)
    shape = _shape(ctext, name, norm)
    for loc, (kind, key, _) in sorted(sites.items()):
        if key == canon:
            continue
        if kind in ("fdef", "vdef"):
            return f"a definition has another type than the chosen one ({loc})"
        file, line = loc.rsplit(":", 1)
        span = _span(src, file, int(line), name)
        if not span:
            return f"no source statement at {loc}"
        edits.append((file, *span))
        was = _decl_text(src.text(file)[1][span[0] : span[1]], name)
        how[file].add(_direction(shape, _shape(was, name, norm)) if was else None)
    reads = tuple(
        (file, way[0], way[1]) for file, ways in sorted(how.items()) if len(ways) == 1 and (way := next(iter(ways)))
    )
    keep = tuple(
        (file, *span)
        for loc in sorted(sites)
        for file in [loc.rsplit(":", 1)[0]]
        if any(file == r[0] for r in reads) and (span := _span(src, file, int(loc.rsplit(":", 1)[1]), name))
    )
    bodies: tuple[Body, ...] = ()
    if merged := _merged(name, order, canon, sites, src, norm, scope):
        body, spans = merged
        bodies = tuple((f, s, e, "{ " + body + " }") for f, s, e in spans)
        ctext = _rebody(ctext, body) if _ANON.match(canon) else ctext
    includes = _includes(canon, sites, name, src, scope, closures, {e[0] for e in edits})
    params = _params(ctext, name)
    callers = frozenset(
        file
        for file in {loc.rsplit(":", 1)[0] for loc in sites}
        if params and _calls(src.text(file)[1], name, params, [(s, e) for f, s, e in edits if f == file])
    )
    files = frozenset({e[0] for e in edits} | {cloc.rsplit(":", 1)[0]} | callers | {b[0] for b in bodies})
    files |= {f for f, _ in includes}
    return Plan(name, canon, why, ctext, tuple(edits), files, reads, keep, params, callers, bodies, includes)


def readers(found: Plan, sites: Mapping[str, Site], closures: Iterable[frozenset[str]], src: Sources) -> Plan:
    """`found` with the reads of the files that see a changed declaration through a file that holds it: in a unit whose
    files hold changed declarations of one way, every other file that names the name without declaring it reads as they
    do (a header declares, the sources that include it read)."""
    ways = {f: (w, m) for f, w, m in found.reads}
    declaring = {loc.rsplit(":", 1)[0] for loc in sites}
    seen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for files in closures:
        held = {ways[f] for f in files if f in ways}
        if len(held) != 1:
            continue
        for f in files - declaring:
            if re.search(rf"\b{re.escape(found.name)}\b", src.text(f)[1]):
                seen[f] |= held
    extra = tuple((f, *next(iter(w))) for f, w in sorted(seen.items()) if len(w) == 1)
    return replace(found, reads=found.reads + extra, files=found.files | {f for f, _, _ in extra})


_LITERAL = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'")


def _reads(b: str, path: str, found: Plan) -> list[tuple[int, int, str]]:
    """The reads of the plan's name in `path` (comments blanked in `b`) with their new spelling: not those in a string,
    a declaration of the name, or a member of that name."""
    word = re.escape(found.name)
    out: list[tuple[int, int, str]] = []
    for file, way, member in found.reads:
        if file != path:
            continue
        pattern = (
            rf"(?<![\w.])(?<!->){word}\b(?!\s*\.\s*{member}\b)"
            if way == "into"
            else rf"(?<![\w.])(?<!->){word}\s*\.\s*{member}\b"
            if way == "out"
            else rf"(?<![\w.])(?<!->){word}\b(?!\s*\[)"
            if way == "first"
            else rf"(?<![\w.&])(?<!->){word}\b"
            if way == "address"
            else rf"(?<![\w.])(?<!->){word}\b"
        )
        spelled = {
            "into": f"{found.name}.{member}",
            "out": found.name,
            "first": f"{found.name}[{member}]",
            "held": f"(*({member} **){found.name})",
            "address": f"(({member} *)&{found.name})",
            "view": f"(*({member} *)&{found.name})",
            "views": f"(({member} *)&{found.name})",
        }[way]
        fixed = [m.span() for m in _LITERAL.finditer(b)]
        fixed += [(s, e) for f, s, e in (*found.keep, *found.edits) if f == file]
        out += [
            (m.start(), m.end(), spelled)
            for m in re.finditer(pattern, b)
            if not any(s < m.end() and m.start() < e for s, e in fixed)
        ]
    return out


def _params(ctext: str, name: str) -> tuple[str, ...]:
    """The parameter declarations of the function `name` declared by `ctext`; none for a variable or `(void)`."""
    m = re.search(r"\b" + re.escape(name) + r"\s*\(", ctext)
    if not m:
        return ()
    depth, i = 1, m.end()
    while i < len(ctext) and depth:
        depth += (ctext[i] == "(") - (ctext[i] == ")")
        i += 1
    parts = [ws(p) for p in split_top(ctext[m.end() : i - 1])]
    return () if parts in ([""], ["void"]) else tuple(parts)


def _tags(ctext: str) -> list[str]:
    """`struct Tag` and `union Tag` named in the parameter list of a function declaration, each once."""
    if "(" not in ctext:
        return []
    tags = re.findall(r"\b(?:struct|union)\s+\w+", ctext[ctext.index("(") :])
    return list(dict.fromkeys(ws(t) for t in tags))


_CALL_LEAD = frozenset({"return", "else", "do", "case"})


def _bodies(m: str) -> list[tuple[int, int, tuple[str, ...]]]:
    """(start, end, parameter names) of each function definition in `m`, text with literals blanked: a `{` that
    follows a `)` opens a body, whatever encloses it."""
    out: list[tuple[int, int, tuple[str, ...]]] = []
    i = 0
    while (i := m.find("{", i)) >= 0:
        before = m[:i].rstrip()
        depth, j = 1, i + 1
        while j < len(m) and depth:
            depth += (m[j] == "{") - (m[j] == "}")
            j += 1
        if before.endswith(")"):
            k, d = len(before) - 1, 0
            while k >= 0:
                d += (before[k] == ")") - (before[k] == "(")
                if d == 0:
                    break
                k -= 1
            names = tuple(
                n[1]
                if (n := re.search(r"(\w+)\s*(?:\[[^\]]*\])?\s*$", p)) and "(" not in p and p.strip() != "void"
                else ""
                for p in split_top(before[k + 1 : -1])
            )
            out.append((i, j, names))
            i = j
        else:
            i += 1
    return out


def _calls(b: str, name: str, params: Sequence[str], skip: Sequence[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """The calls of `name` in `b` (comments blanked) that pass fewer arguments than `params`, each as an insertion at
    its closing parenthesis of the enclosing function's parameters at the missing positions; a call whose enclosing
    function lacks one of them is left alone."""
    m = _LITERAL.sub(lambda x: " " * len(x[0]), b)
    bodies = _bodies(m)
    out: list[tuple[int, int, str]] = []
    for hit in re.finditer(r"(?<![\w.>])" + re.escape(name) + r"\s*\(", m):
        lead = re.search(r"(\w+)?\s*$", m[: hit.start()])
        fn = next((f for f in bodies if f[0] < hit.start() < f[1]), None)
        if not fn or (lead and lead[1] and lead[1] not in _CALL_LEAD) or any(s <= hit.start() < e for s, e in skip):
            continue
        depth, i, given = 1, hit.end(), 0
        while i < len(m) and depth:
            depth += (m[i] in "([{") - (m[i] in ")]}")
            given += m[i] == "," and depth == 1
            i += 1
        given += bool(m[hit.end() : i - 1].strip())
        names = [fn[2][k] if k < len(fn[2]) else "" for k in range(given, len(params))]
        if given < len(params) and all(names):
            out.append((i - 1, i - 1, (", " if given else "") + ", ".join(names)))
    return out


def render(path: str, plans: Iterable[Plan], src: Sources) -> str:
    """The text of `path` with the edits of `plans` that fall in it, from the original text."""
    t, b = src.text(path)
    spans: defaultdict[tuple[int, int], list[Plan]] = defaultdict(list)
    for p in plans:
        for file, s, e in p.edits:
            if file == path:
                spans[(s, e)].append(p)
    ops: list[tuple[int, int, list[Plan] | str]] = [(s, e, held) for (s, e), held in spans.items()]
    for p in plans:
        ops += _reads(b, path, p)
        if path in p.callers:
            ops += _calls(b, p.name, p.params, [(s, e) for f, s, e in p.edits if f == path])
    ops += [(s, e, body) for f, s, e, body in (b for p in plans for b in p.bodies) if f == path]
    wanted = sorted({h for p in plans for f, h in p.includes if f == path})
    if wanted:
        first = min((s for p in plans for f, s, _ in p.edits if f == path), default=len(b))
        at = max((m.end() for m in re.finditer(r"^[ \t]*#[ \t]*include\b.*$", b, re.M) if m.end() <= first), default=0)
        have = {m[1] for m in re.finditer(r'#[ \t]*include[ \t]*"([^"]+)"', b)}
        text = "".join(
            f'\n#include "{h.removeprefix("include/")}"' for h in wanted if h.removeprefix("include/") not in have
        )
        if text:
            ops.append((at, at, text if at else text[1:] + "\n"))
    for s, e, held in sorted(ops, key=lambda o: (o[0], o[1]), reverse=True):
        if isinstance(held, str):
            t, b = t[:s] + held + t[e:], b[:s] + held + b[e:]
            continue
        had_extern = bool(re.match(r"\s*extern\b", b[s:e]))
        parts = split_top(re.sub(r"^\s*(extern\s+)?", "", b[s:e].rstrip().rstrip(";"), count=1))
        lead = t[:s].rsplit("\n", 1)[-1]
        indent = re.match(r"\s*", lead)[0] if not lead.strip() else ""  # type: ignore[index]
        pre = "extern " if had_extern else ""
        texts, used = [], []
        for i, part in enumerate(ws(p) for p in parts):
            hit = [p for p in held if re.search(r"\b" + re.escape(p.name) + r"\b", part)]
            if len(parts) == 1 or hit:
                used.append((hit[0] if hit else held[0]).ctext)
                texts.append(pre + used[-1] + ";")
            elif i == 0:
                texts.append(pre + part + ";")
            else:
                mm = re.match(
                    r"^(.*?)\s*\**\s*\b\w+\b\s*(\[.*)?$", ws(parts[0])
                )  # the type before the first declarator
                texts.append(pre + ws((mm[1] if mm else "") + " " + part) + ";")
        new = ("\n" + indent).join(texts)
        if not indent:
            new = "".join(f"{tag}; " for ctext in used for tag in _tags(ctext)) + new
        t = t[:s] + new + t[e:]
        b = b[:s] + " " * len(new) + b[e:]  # offsets of the lower spans, edited next, stay valid
    return t
