"""Which declared type one name takes, and the source edits that give every declaration of it that type.

A name's candidates are the types it is declared with, the most declared first (a type spelled `void*` after the others
of equal count, then the longer spelling last); a definition with a body or an initialiser decides alone. `rank` picks
the next candidate when the one before it failed to keep an object identical. A file whose declaration changes from a
scalar to a one-field struct of that scalar (or back) also has its reads of the name rewritten to the new type
(`x` as `x.value`, `x.value` as `x`), by the one rule for every name of this shape. A function whose chosen
declaration takes more parameters than a call in a file that declares it passes has the call completed with the
enclosing function's own parameters at the missing positions (the registers the call leaves untouched, so the bytes
stay), and a rewritten declaration whose parameter list names a struct or union tag is preceded, on its line, by a
declaration of that tag (else the tag is born inside the parameter list and conflicts with the file's own).
The edit of a declaration replaces the
whole source statement that holds it (a multi-declarator statement keeps its other names) by the canonical declaration
text, written from a declaration of the chosen type that is in the sources. Comments are blanked before any statement
is cut, so offsets are those of the original text."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from unbake.typecensus import Norm, decimal_bounds, split_top, tkey, ws

EXHAUSTED = "no further candidate type"  # the plan of a rank past the last candidate
Site = tuple[str, str, int]  # (kind, type key, declarators) of one declaration by its loc "file:line"
Edit = tuple[str, int, int]  # (file, start, end) of the source statement to replace
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
    return re.sub(r"\[[^\]]+\]", "[]", spelled, count=1) if "(" not in spelled else spelled


_BOX = re.compile(r"^(?:struct|union)\s*\w*\s*\{\s*([^;{}]+?)\s*\b(\w+)\s*;\s*\}$")
_PRIM = re.compile(r"^(?:unsigned |signed )?(?:char|short|int|long|long long|float|double)$")


def _shape(decl: str, name: str, norm: Norm) -> tuple[str, str, str] | None:
    """("scalar", type, "") for a plain variable of a primitive type, ("array", element type, "") for an unsized array
    of one, ("box", field type, field) for a one-field struct variable, ("record", type, "") for any other type."""
    m = re.match(r"^(.*?)\s*\b" + re.escape(name) + r"\s*(\[\s*\])?$", ws(decl))
    base = m[1].strip() if m else ""
    if not m or not base:
        return None
    if box := _BOX.match(base):
        return ("box", norm.prim(box[1]), box[2]) if not m[2] else None
    if re.search(r"[{}\[\]()*]", base):
        return None
    kind = norm.prim(base)
    if _PRIM.match(kind):
        return ("array" if m[2] else "scalar", kind, "")
    return None if m[2] else ("record", kind, "")


def _direction(canon: tuple[str, str, str] | None, was: tuple[str, str, str] | None) -> tuple[str, str] | None:
    """How reads written for the type `was` are written for `canon`: one is the one-field struct of the other, or
    `canon` is a record that `was` (a primitive or an array of one) views by its address."""
    if not canon or not was:
        return None
    if canon[0] == "record" and was[0] in ("scalar", "array"):
        return ("view" if was[0] == "scalar" else "views", was[1])
    if canon[1] != was[1] or canon[0] == was[0] or "record" in (canon[0], was[0]) or "array" in (canon[0], was[0]):
        return None
    return ("into", canon[2]) if canon[0] == "box" else ("out", was[2])


def plan(name: str, sites: Mapping[str, Site], rank: int, src: Sources, norm: Norm) -> Plan | str:
    """The plan of `name` at candidate `rank`, or the reason it has none (a string)."""
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
    params = _params(ctext, name)
    callers = frozenset(
        file
        for file in {loc.rsplit(":", 1)[0] for loc in sites}
        if params and _calls(src.text(file)[1], name, params, [(s, e) for f, s, e in edits if f == file])
    )
    files = frozenset({e[0] for e in edits} | {cloc.rsplit(":", 1)[0]} | callers)
    return Plan(name, canon, why, ctext, tuple(edits), files, reads, keep, params, callers)


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
            else rf"(?<![\w.])(?<!->){word}\b"
        )
        spelled = {
            "into": f"{found.name}.{member}",
            "out": found.name,
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
