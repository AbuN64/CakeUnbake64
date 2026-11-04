"""Unify clashes at their single physical definition, with object proof and one final drift check."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from abucache import pool, stages

from unbake import build, compilerrors, hoist, mergecheck, process, store, tree
from unbake import config as configuration
from unbake.contracts import Config, Finding, Host, Json, Refusal
from unbake.typecensus import Decl, Norm, Stmt, clashes, declarations, files_of, index, scan, table, tkey
from unbake.typeplan import Plan, Sources, plan, readers, render, sites_of

Unit = tuple[str, str]  # (version, source path)
Facts = tuple[list[str], list[Stmt], frozenset[str], frozenset[str]]  # (typedefs, project statements, files, words)
_WORD = re.compile(r"[A-Za-z_]\w*")
_POINTER = re.compile(r"\(\s*\*")
WORK = ("batches", "rounds", "rebuilt")  # counts of the builds one run made: not part of what it decided
# An isolate trial builds its objects in chunks of FIRST_CHUNK, then GROWTH times more each make, most suspect first,
# and is decided by the first chunk holding an object that is not the baseline's: a failing trial costs one small
# make, a passing one still builds every object it touches (at most log(n) makes).
FIRST_CHUNK = 16
GROWTH = 4


@dataclass(frozen=True)
class Seams:
    """What touches the real build: tests replace all three."""

    units: Callable[[], Mapping[str, Sequence[str]]]  # version -> compiled source paths
    read: Callable[[Sequence[Unit]], list[Facts]]  # the facts of each unit's preprocessed text
    make: Callable[[Sequence[str]], tuple[int, str]]  # object paths -> (exit code, log)
    # lane number, the files a trial writes -> (a root of its own holding the tree, the seams building in it): the
    # isolate waves of one pass run each in a lane, `width` at a time. None: the waves take turns in the one tree.
    lane: Callable[[int, Collection[str]], tuple[Path, Seams]] | None = None
    width: int = 1
    # the jobs the makes of the lanes of a pass share, as one jobserver (0: each make takes its own, as one at a time)
    grant: int = 0


@dataclass
class Census:
    per: dict[str, dict[str, list[Decl]]] = field(default_factory=dict)
    closure: dict[Unit, frozenset[str]] = field(default_factory=dict)
    words: dict[Unit, frozenset[str]] = field(default_factory=dict)  # per unit: what it defines or uses
    norm: Norm = field(default_factory=Norm)
    held: dict[Unit, list[Stmt]] = field(default_factory=dict)  # the statements of the units that read a `watch` file
    by_file: dict[str, dict[str, set[Stmt]]] = field(default_factory=dict)  # version -> watched file -> statements
    named: dict[Stmt, list[tuple[str, Decl]]] = field(default_factory=dict)  # what each statement declares, read once


def _declares_only(stmt: Stmt) -> bool:
    """Whether a statement only declares what it names (typedef, extern, prototype): its words are no use of them."""
    text = stmt[2].lstrip()
    if stmt[3] != "stmt" or "=" in text:
        return False
    return text.startswith(("typedef ", "extern ")) or ("(" in text and not _POINTER.search(text))


def spelled(text: str, stmts: Sequence[Stmt]) -> frozenset[str]:
    """The words a unit defines or uses: those of every statement that defines something, and those of the text outside
    its depth-0 statements (function bodies and initializers). A word only a declaration spells is not in it."""
    inside = Counter(w for s in stmts for w in _WORD.findall(s[2]))
    total = Counter(_WORD.findall("\n".join(ln for ln in text.split("\n") if not ln.startswith("# "))))
    defined = {w for s in stmts if not _declares_only(s) for w in _WORD.findall(s[2])}
    return frozenset(defined | {w for w, n in total.items() if n > inside[w]})


def facts(text: str, unit: str, keep: Callable[[str], bool]) -> Facts:
    """One unit's preprocessed text reduced to what the census reads: it is all a worker sends back."""
    stmts = scan(text)
    return (
        [s[2] for s in stmts if s[2].lstrip().startswith("typedef ")],
        [s for s in stmts if keep(s[0]) and not s[2].lstrip().startswith("typedef ")],
        frozenset(f for f in files_of(text, unit) if keep(f)),
        spelled(text, stmts),
    )


def collect(
    units: Mapping[str, Sequence[str]],
    read: Callable[[Sequence[Unit]], list[Facts]],
    watch: frozenset[str] = frozenset(),
) -> Census:
    """The declarations of every name per version, and the files each unit reads. The statements of the units that
    read a `watch` file are kept (the fold-check composes them)."""
    items = [(v, u) for v in sorted(units) for u in sorted(units[v])]
    held = read(items)
    out = Census()
    spelled_out: set[str] = set()  # a typedef or a statement of a shared header is in every unit: read it once
    for typedefs, _, _, _ in held:
        for typedef in typedefs:
            if typedef not in spelled_out:
                spelled_out.add(typedef)
                out.norm.define(typedef)
    seen: dict[str, dict[str, dict[str, Decl]]] = defaultdict(lambda: defaultdict(dict))
    parsed: dict[str, set[Stmt]] = defaultdict(set)
    for (version, unit), (_, stmts, files, words) in zip(items, held, strict=True):
        out.closure[(version, unit)] = files
        out.words[(version, unit)] = words
        fresh = [s for s in stmts if s not in parsed[version]]
        parsed[version].update(fresh)
        for stmt in fresh:
            if stmt not in out.named:  # a statement of a shared header reads the same in every version
                out.named[stmt] = declarations([stmt], out.norm, lambda _f: True)
            for name, decl in out.named[stmt]:
                seen[name][version][f"{decl[0]}\t{decl[1]}\t{decl[2]}"] = decl
        if files & watch:
            out.held[(version, unit)] = stmts
            of_version = out.by_file.setdefault(version, {})
            for stmt in stmts:
                if stmt[0] in watch:
                    of_version.setdefault(stmt[0], set()).add(stmt)
    out.per = {n: {v: sorted(d.values()) for v, d in vers.items()} for n, vers in seen.items()}
    return out


Fold = tuple[str, str]  # (header folded away, target header it folds into)


def folds_of(path: Path) -> list[Fold]:
    """The (header, target) rows of a fold list: the first two columns of each line (a header row is skipped)."""
    rows = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [(r[0].strip(), r[1].strip()) for r in rows if len(r) > 1 and r[0] != "header"]


def fold_groups(folds: Iterable[Fold]) -> list[frozenset[str]]:
    """The files that become one header: folds that share a file are one group."""
    groups: list[set[str]] = []
    for pair in folds:
        joined = [g for g in groups if g.intersection(pair)]
        merged = set(pair).union(*joined)
        groups = [g for g in groups if g not in joined] + [merged]
    return [frozenset(g) for g in groups]


def folded(census: Census, groups: Sequence[frozenset[str]]) -> dict[str, dict[str, list[Decl]]]:
    """The declarations of every name that two types meet in one unit once `groups` are one header each: a unit that
    reads a file of a group reads the statements of the whole group. name -> version -> declarations. A unit of the
    built tree has no such name, so each is a conflict the fold makes."""
    found: dict[str, dict[str, dict[str, Decl]]] = defaultdict(lambda: defaultdict(dict))
    for (version, unit), stmts in census.held.items():
        files = census.closure[(version, unit)]
        seen = set(stmts)
        for group in groups:
            if files & group:
                for file in group:
                    seen |= census.by_file.get(version, {}).get(file, set())
        local: dict[str, dict[str, Decl]] = defaultdict(dict)
        for stmt in seen:
            for name, decl in census.named.get(stmt, ()):
                local[name][f"{decl[0]}\t{decl[1]}\t{decl[2]}"] = decl
        for name, decls in local.items():
            if len({tkey(d[2]) for d in decls.values()}) > 1:
                found[name][version].update(decls)
    return {n: {v: sorted(d.values()) for v, d in vers.items()} for n, vers in found.items()}


def reach(census: Census, found: Plan) -> frozenset[Unit]:
    """The units whose text holds a file the plan reads or writes."""
    return frozenset(unit for unit, files in census.closure.items() if files & found.files)


def touched(census: Census, found: Plan) -> frozenset[Unit]:
    """The units of its reach that define or use the name: the only objects the plan can change."""
    return frozenset(u for u in reach(census, found) if found.name in census.words[u])


def batches(units: Mapping[str, frozenset[Unit]]) -> list[list[str]]:
    """Names grouped so no two of a group touch one unit: the largest first into the first group that has room."""
    groups: list[tuple[set[Unit], list[str]]] = []
    for name in sorted(units, key=lambda n: (-len(units[n]), n)):
        for held, names in groups:
            if held.isdisjoint(units[name]):
                held |= units[name]
                names.append(name)
                break
        else:
            groups.append((set(units[name]), [name]))
    return [sorted(names) for _, names in groups]


def target(unit: Unit) -> str:
    return f"{configuration.root('build')}/{unit[0]}/{unit[1]}.o"


def _sha(path: Path) -> str | None:
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.is_file() else None


def rebuild(
    root: Path, seams: Seams, units: Iterable[Unit], errors: dict[str, str] | None = None
) -> dict[Unit, str | None]:
    """Delete the objects of `units`, make them, and answer unit -> object hash, None for a unit whose build failed
    (`errors` takes the first compiler error of each such object).
    An object make neither built nor named in a failure (the build was cut short) is made again, the rest being done,
    for as long as a round makes one more. A build that did not run is a Refusal (exit naming no unit, a round that
    made no object); a quiet make with an empty log that made its objects ran."""
    wanted = {target(u): u for u in units}
    if not wanted:
        return {}
    for path in wanted:
        (root / path).unlink(missing_ok=True)
    named: set[str] = set()
    pending = sorted(wanted)
    while pending:
        code, log = seams.make(pending)
        failed = {m for m in compilerrors.FAILED.findall(log) if m in pending}
        stamps = {f"{m}.o" for m in compilerrors.STAMP.findall(log) if f"{m}.o" in pending} - failed
        if code != 0 and not failed and not stamps:
            tail = " | ".join(line.strip() for line in log.splitlines()[-6:] if line.strip())
            raise Refusal(
                Finding(
                    "unify.build",
                    f"make exited {code} and named no unit that failed: it did not build. Its log ends: {tail}"[:900],
                )
            )
        named |= failed | stamps
        if errors is not None:
            errors.update(compilerrors.first_errors(log, failed))
            errors.update(compilerrors.stamp_errors(log, stamps))
        absent = [p for p in pending if p not in named and not (root / p).is_file()]
        if len(absent) == len(pending):
            raise Refusal(
                Finding("unify.build", "make neither built nor failed these objects", missing=tuple(absent[:20]))
            )
        pending = absent
    return {u: None if p in named else _sha(root / p) for p, u in wanted.items()}


def _decide(
    root: Path,
    seams: Seams,
    trials: Sequence[Sequence[Unit]],
    base: Mapping[Unit, str],
    errors: dict[str, str] | None = None,
    known: Mapping[Unit, str | None] | None = None,
) -> tuple[dict[Unit, str | None], int]:
    """Build the objects of several trials (each in the order to try them) until each trial has met an object that is
    not the baseline's or has built all of its own. Answers unit -> hash (None: did not build) for the objects built,
    and the number of makes. An object a trial did not reach stays undecided; one it did not need is not built.
    `known` is the hash of objects already built in the very state the trials give them: they count as built and cost
    no make, so a trial holding a known failing object is decided without one."""
    made: dict[Unit, str | None] = {u: h for u, h in (known or {}).items() if any(u in t for t in trials)}
    seen = [0] * len(trials)
    live = set(range(len(trials)))
    size, makes = FIRST_CHUNK, 0
    while live:
        taken = {i: trials[i][seen[i] : seen[i] + size] for i in live}
        chunk = sorted({u for units in taken.values() for u in units if u not in made})
        if chunk:
            made.update(rebuild(root, seams, chunk, errors))
            makes += 1
        for i, units in taken.items():
            seen[i] += len(units)
            if seen[i] >= len(trials[i]) or any(made[u] != base[u] for u in units):
                live.discard(i)
        size *= GROWTH
    return made, makes


def _closing(
    root: Path, seams: Seams, census: Census, tried: set[Unit], accepted: Mapping[str, Plan], errors: dict[str, str]
) -> dict[Unit, str | None]:
    """The closing check: every object a rewrite reaches or a trial built, built once with the accepted rewrites."""
    reached = set().union(tried, *(reach(census, p) for p in accepted.values()))
    with stages.stage("unify.verify"):
        return rebuild(root, seams, reached, errors)


def differing(units: Iterable[Unit], made: Mapping[Unit, str | None], base: Mapping[Unit, str]) -> list[Unit]:
    """The units of `units`, in order, whose new object is missing (the build failed) or not the baseline's."""
    return sorted(u for u in units if made[u] != base[u])


def _object_groups(bad: set[Unit], units_of: Mapping[str, frozenset[Unit]]) -> list[tuple[tuple[str, ...], set[Unit]]]:
    """Connected names sharing a bad object: independent groups share one build per round."""
    groups: list[tuple[set[str], set[Unit]]] = []
    for unit in sorted(bad):
        names = {n for n, units in units_of.items() if unit in units}
        if not names:
            raise Refusal(Finding("unify.build", f"no remaining rewrite reaches {target(unit)}"))
        units = {unit}
        rest = []
        for held, objects in groups:
            if names & held:
                names |= held
                units |= objects
            else:
                rest.append((held, objects))
        groups = [*rest, (names, units)]
    return [(tuple(sorted(names)), units) for names, units in groups]


@dataclass
class Wave:
    """One isolate wave: the trials it decides, what it writes, and what its builds answered."""

    picks: list[int]
    held: list[Plan]  # the rewrites it writes
    ordered: list[list[Unit]]  # the objects of each trial, in the order to try them
    states: dict[Unit, tuple[Unit, frozenset[str]]]  # object -> the memo key of the state it is built in
    seen: dict[Unit, str | None]  # objects already built in that state
    made: dict[Unit, str | None] = field(default_factory=dict)
    makes: int = 0
    seconds: float = 0.0
    errors: dict[str, str] = field(default_factory=dict)


Node = tuple[tuple[str, ...], dict[Unit, str | None], frozenset[str]]  # (names, bad object -> its hash, names left out)


def _broken(
    results: Mapping[int, Mapping[Unit, str | None]], trial: int | None, base: Mapping[Unit, str]
) -> dict[Unit, str | None]:
    """The objects a trial decided that are not the baseline's, with what it built (None: it did not build)."""
    return {} if trial is None or trial not in results else {u: h for u, h in results[trial].items() if h != base[u]}


def _waves(
    trials: Sequence[tuple[frozenset[str], frozenset[Unit]]],
    touchers: Mapping[Unit, set[str]],
    required: Sequence[int],
    optional: Sequence[int] = (),
) -> tuple[list[list[int]], list[int]]:
    """The `required` trials grouped into waves that share one write and one make, and the `optional` ones that fit a
    wave. A trial is (the rewrites it writes, the objects it decides); it joins a wave when no rewrite the wave writes
    beyond its own touches an object it decides, so each object is built with exactly the rewrites that can change it.
    Trials on separate objects run in one make; trials that meet on an object (different rewrites reaching it) take
    turns. An optional trial that fits no wave is answered back, never given a make of its own."""
    waves: list[tuple[set[str], list[int]]] = []
    blocking = {i: set().union(*(touchers[u] for u in trials[i][1])) for i in (*required, *optional)}

    def join(i: int) -> bool:
        for written, picks in waves:
            union = written | trials[i][0]
            if all(not (union - trials[j][0]) & blocking[j] for j in (*picks, i)):
                written |= trials[i][0]
                picks.append(i)
                return True
        return False

    for i in required:
        if not join(i):
            waves.append((set(trials[i][0]), [i]))
    return [picks for _, picks in waves], [i for i in optional if not join(i)]


def _write(
    root: Path,
    files: Iterable[str],
    held: Iterable[Plan],
    src: Sources,
    written: dict[str, frozenset[str]] | None = None,
    rendered: dict[tuple[str, frozenset[str]], bytes] | None = None,
) -> None:
    """Write the files as the held rewrites render them: its own stage line, as time between trials is not make time.
    With `written` (path -> the held rewrites its text was last rendered with, kept by the caller for a tree only it
    writes) a file already as asked is left alone, so a wave pays for what differs from the one before it; `rendered`
    keeps the bytes of a (path, rewrites) the trees share."""
    held = list(held)
    by_path: dict[str, set[str]] = defaultdict(set)
    for p in held:
        for f in p.files:
            by_path[f].add(p.name)
        if p.header:
            by_path[p.header].add(p.name)
    with stages.stage("unify.write"):
        for path in sorted(files):
            key = frozenset(by_path.get(path, ()))
            if written is not None and written.get(path) == key:
                continue
            if path in src.missing and not any(p.header == path for p in held):
                (root / path).unlink(missing_ok=True)
            else:
                if rendered is None or (path, key) not in rendered:
                    data = render(path, [p for p in held if p.name in key], src).encode("latin-1")
                    if rendered is not None:
                        rendered[path, key] = data
                else:
                    data = rendered[path, key]
                store.write(root / path, data)
            if written is not None:
                written[path] = key


def _farm(root: Path, home: Path, files: Collection[str], skip: Collection[str] = ()) -> None:
    """Make `home` a tree that reads as `root` but takes the writes of `files`: every entry of `root` is a symlink to
    it, except `skip` (top-level names the lane owns) and the directories on the way to `files`, which are real
    directories mirrored the same way; a file of `files` is left absent for the writer. Rebuilt whole each time."""
    shutil.rmtree(home, ignore_errors=True)
    wanted = set(files)
    dirs = {""} | {parent.as_posix() for f in wanted for parent in Path(f).parents if parent.as_posix() != "."}

    def mirror(rel: str) -> None:
        (home / rel).mkdir(parents=True, exist_ok=True)
        with os.scandir(root / rel if rel else root) as entries:
            for entry in entries:
                path = f"{rel}/{entry.name}" if rel else entry.name
                if (not rel and entry.name in skip) or path in wanted:
                    continue
                if path in dirs and entry.is_dir(follow_symlinks=False):
                    mirror(path)
                else:
                    (home / path).symlink_to(root / path)

    mirror("")


def baseline(root: Path, seams: Seams, units: Mapping[str, Sequence[str]]) -> dict[Unit, str]:
    """The object hash of every unit built from the tree as it is: a unit that does not build refuses the run."""
    errors: dict[str, str] = {}
    made = rebuild(root, seams, [(v, u) for v in units for u in units[v]], errors)
    broken = sorted(target(u) for u, h in made.items() if h is None)
    if broken:
        lines = tuple(f"{path}: {errors.get(path, compilerrors.NO_ERROR)}" for path in broken)
        raise Refusal(Finding("unify.baseline", "the tree does not build before any rewrite", missing=lines))
    return {u: h for u, h in made.items() if h is not None}


def _compile_findings(
    broken: Sequence[Unit], errors: Mapping[str, str], census: Census, settled: Mapping[str, Plan]
) -> list[Finding]:
    """One finding per source file whose object the kept rewrites stopped building (not a drift: the object is missing):
    the file, the first compiler error lines of its first version, the versions, and the rewrites that touched it."""
    by_file: dict[str, list[Unit]] = defaultdict(list)
    for unit in broken:
        by_file[unit[1]].append(unit)
    out: list[Finding] = []
    for path, units in sorted(by_file.items())[:20]:
        why = errors.get(target(units[0]), compilerrors.NO_ERROR)
        rewrites = "; ".join(
            f"{n} as {p.canon} ({', '.join(sorted(p.files))})"
            for n, p in sorted(settled.items())
            if any(u in reach(census, p) for u in units)
        )
        out.append(
            Finding(
                "unify.compile",
                f"{path} does not compile after the rewrites: {why}; rewrites that touched it: "
                + (rewrites or "none of the kept ones reaches it"),
                path=path,
                unit=path,
                versions=tuple(v for v, _ in units),
                missing=tuple(target(u) for u in units),
            )
        )
    return out


def line(name: str, got: Plan | None, failed: Mapping[str, str], seconds: float) -> str:
    """The progress line of a name that is done: the type picked (or none), how many candidates were tried, the failing
    unit of each that failed, the seconds its builds took."""
    tried = sum(1 for canon in failed if canon) + (got is not None)
    why = "; ".join(f"{canon or 'no plan'} => {reason}" for canon, reason in failed.items())
    return (
        f"unify {name}: {'unified as ' + got.canon if got else 'unresolved'}; {tried} candidate(s) tried"
        + (f"; failing {why}" if why else "")
        + f"; {seconds:.1f}s"
    )


def run(
    root: Path,
    seams: Seams,
    names: Sequence[str] | None = None,
    say: Callable[[str], None] = stages.say,
    keep: Callable[[Json], None] | None = None,
    resume: Json | None = None,
) -> Json:
    """Settle one type for each clashing name (or each of `names`); the tree keeps the rewrites that held. `say` takes
    one line per name as soon as it is settled or out of candidates. `keep` takes the result as far as it is decided
    (`complete` false) after every isolate round, so a run cut short leaves its rounds; `resume` is such a result of a
    cut run on the same tree: the names it rejected stay rejected and are not tried again."""
    units = seams.units()
    if not any(units.values()):
        raise Refusal(Finding("unify.units", "no compiled unit found: build the tree first (unbake setup)"))
    with stages.stage("unify.baseline"):
        base = baseline(root, seams, units)
    with stages.stage("unify.census"):
        census = collect(units, seams.read)
        found = clashes(census.per)
    src = Sources(lambda p: (root / p).read_bytes().decode("latin-1"))
    todo = sorted(found if names is None else [n for n in names if n in found])
    signature = hashlib.sha1(
        json.dumps([sorted(f"{v}\t{u}\t{h}" for (v, u), h in base.items()), todo]).encode()
    ).hexdigest()
    if resume is not None and resume["baseline"] != signature:
        raise Refusal(
            Finding(
                "unify.resume",
                "the partial result was made from another baseline or another set of names: restore the tree to the"
                " state the cut run started from and give the same names, or remove the partial result",
            )
        )
    settled: dict[str, Plan] = {}
    failed: dict[str, dict[str, str]] = defaultdict(dict)
    spent: dict[str, float] = defaultdict(float)
    tried: set[Unit] = set()
    errors: dict[str, str] = {}
    objects: dict[str, Json] = {}
    held_objects: dict[str, dict[str, str]] = defaultdict(dict)
    plans: dict[str, Plan] = {}
    built, rebuilt = 0, 0
    lanes: dict[int, tuple[Path, Seams]] = {}  # the isolate lanes, made when a pass first needs them
    lane_written: dict[Path, dict[str, frozenset[str]]] = {}
    rendered: dict[tuple[str, frozenset[str]], bytes] = {}
    # (object, the rewrites that reach it and were written) -> what it built to: the same state is never built twice
    memo: dict[tuple[Unit, frozenset[str]], str | None] = {}
    passed: dict[
        Unit, tuple[frozenset[str], str]
    ] = {}  # unit -> (rewrites reaching it, object) of its last passing trial
    # Read project texts once: users without a local declaration still need the owning header.
    with stages.stage("unify.setup"):
        paths = sorted(
            {f for files in census.closure.values() for f in files}
            | {
                p.relative_to(root).as_posix()
                for folder in (*configuration.source_roots(), *configuration.header_roots())
                for p in (root / folder).rglob("*")
                if p.is_file() and configuration.is_text(p.as_posix())
            }
        )
        texts = {f: src.text(f)[0] for f in paths if (root / f).is_file()}
        users_of = index(texts)
        headers = sorted(f for f in texts if configuration.is_header(f))
        include_root = hoist.include_root(texts, headers) or configuration.header_roots()[0] + "/"
        module_map = configuration.module_map_at(root)
        owners: dict[str, str] = {}
        if module_map:
            mapping = (root / module_map).read_text()
            targets = mergecheck.read_targets(mapping)
            rows = mergecheck.read_modules(mapping)
            members: dict[str, list[str]] = defaultdict(list)
            for unit, module in rows:
                members[module].append(unit)
            owners = {u: mergecheck.merged_path(m, targets.get(m), members[m]) for u, m in rows}
    for name in todo:
        held = sites_of(census.per, name)
        definitions = sorted(loc for loc, (kind, _, _) in held.items() if kind in ("fdef", "vdef"))
        if len(definitions) != 1:
            failed[name][""] = "no definition" if not definitions else "multiple definitions"
            say(line(name, None, failed[name], 0))
            continue
        keys = {tkey(typ) for ds in census.per[name].values() for loc, kind, typ, _ in ds if kind in ("fdef", "vdef")}
        if len(keys) != 1:
            failed[name][""] = "definition type differs across versions"
            say(line(name, None, failed[name], 0))
            continue
        defining = definitions[0].rsplit(":", 1)[0]
        owner = owners.get(defining, defining)
        header = src.header(owner, texts.get(owner, texts[defining]), headers, include_root)
        users = set(users_of.get(name, ()))
        with stages.stage("unify.plan"):
            got = plan(name, held, 0, src, census.norm, paths, census.closure.values())
            if isinstance(got, Plan):
                got = got.owned(held, src, census.norm, header, users)
        if isinstance(got, Plan):
            closures = [census.closure[u] for u in touched(census, got)] if got.reads else []
            plans[name] = readers(got, held, closures, src) if closures else got
        else:
            failed[name][""] = got
            say(line(name, None, failed[name], 0))

    def why_of(name: str) -> dict[str, str]:
        out = dict(failed[name])
        if name in held_objects and name in plans:
            out[plans[name].canon] = "; ".join(f"{p}: {e}" for p, e in sorted(held_objects[name].items()))
        return out

    def report(complete: bool, accepted: Mapping[str, Plan], verified: int) -> Json:
        unresolved = {n: why for n in todo if n not in accepted and (why := why_of(n))}
        return {
            "complete": complete,
            "baseline": signature,
            "names": len(todo),
            "clashing": len(found),
            "units": sum(len(v) for v in units.values()),
            "unified": {
                n: {
                    "type": p.canon,
                    "why": p.why,
                    "header": p.header,
                    "units": len(touched(census, p)),
                    "seconds": round(spent[n], 3),
                }
                for n, p in sorted(accepted.items())
            },
            "unresolved": unresolved,
            "seconds": {n: round(spent[n], 3) for n in todo if n in accepted or n in unresolved},
            "batches": built,
            "rounds": built,
            "objects": {p: {**o, "names": sorted(o["names"])} for p, o in sorted(objects.items())},
            "held": {n: dict(sorted(v.items())) for n, v in sorted(held_objects.items())},
            "verified": verified,
            "rebuilt": rebuilt,
            "files": sorted({f for p in accepted.values() for f in p.files}),
        }

    def save() -> None:
        if keep:
            with stages.stage("unify.save"):
                keep(report(False, {}, 0))

    cut: Json = resume or {"held": {}, "objects": {}, "seconds": {}}
    for name, paths in cut["held"].items():
        if name not in plans:
            raise Refusal(Finding("unify.resume", f"{name} was rejected by the cut run but has no plan now"))
        held_objects[name].update(paths)
        spent[name] = cut["seconds"][name]
        tried |= reach(census, plans[name])  # the final check still reads what its rewrite touched
        failed[name].update(why_of(name))
        say(line(name, None, failed[name], spent[name]))
    objects.update(cut["objects"])
    fresh = {n: p for n, p in plans.items() if n not in held_objects}
    back: dict[Unit, str | None] | None = None  # the closing check, when the search made it
    verify_errors: dict[str, str] = {}
    if fresh:
        started = time.monotonic()
        files = {f for p in fresh.values() for f in p.files}
        _write(root, files, fresh.values(), src)
        units_of = {n: reach(census, p) for n, p in fresh.items()}
        touches = {n: touched(census, p) for n, p in fresh.items()}
        span = set().union(*units_of.values())
        tried |= span
        built, rebuilt = 1, len(span)
        with stages.stage("unify.rebuild"):
            made = rebuild(root, seams, span, errors)
        for u, object_hash in made.items():
            memo[u, frozenset(n for n in fresh if u in units_of[n])] = object_hash
        bad = set(differing(span, made, base))
        active = dict(fresh)
        touchers: dict[Unit, set[str]] = defaultdict(set)  # object -> the rewrites that touch it
        for n, touching in touches.items():
            for u in touching:
                touchers[u].add(n)
        unknown: set[Unit] = set()  # failed in an earlier state, not rebuilt since a rejection changed its text
        owed: dict[Unit, set[str]] = defaultdict(set)  # object -> the rejected names that may have cleared it
        while True:
            if not bad:
                # The one closing check: every object reached, built once with the rewrites that remain. It finds a
                # culprit that an earlier culprit masked, and those only: the rounds trust an explained object.
                back = _closing(root, seams, census, tried, {**settled, **active}, verify_errors)
                for u, object_hash in back.items():
                    memo[u, frozenset(n for n in active if u in units_of[n])] = object_hash
                masked = {u for u, h in back.items() if h != base[u] and any(u in touches[n] for n in active)}
                if not masked:
                    break
                bad, made = masked, back
            for u in sorted(bad):
                why = (
                    "does not build: " + errors.get(target(u), compilerrors.NO_ERROR)
                    if made[u] is None
                    else "object differs"
                )
                objects.setdefault(target(u), {"error": why, "names": []})
            # One search finds every culprit: a node is the names that may hold one, the bad objects they explain and
            # the names left out of its trials (the suspect siblings of the branches above it).
            nodes: list[Node] = [
                (names, {u: made[u] for u in objs}, frozenset())
                for names, objs in _object_groups(bad, {n: units_of[n] for n in active})
            ]
            rejected: set[str] = set()
            explained: set[Unit] = set()  # bad objects whose culprit this round found
            results: dict[int, dict[Unit, str | None]] = {}
            suspect = Counter(bad)  # object -> how many builds of it were not the baseline's

            def prepare(
                picks: Sequence[int], trials: Sequence[tuple[frozenset[str], frozenset[Unit]]], suspect: Counter[Unit]
            ) -> Wave:
                written = set().union(*(trials[i][0] for i in picks))
                # the objects that failed most often in earlier trials first: a trial holding a culprit stops early
                ordered = [sorted(trials[i][1], key=lambda u: (-suspect[u], u)) for i in picks]
                states = {u: (u, frozenset(n for n in written if u in units_of[n])) for objs in ordered for u in objs}
                seen = {u: memo[k] for u, k in states.items() if k in memo}
                return Wave(list(picks), [p for n, p in active.items() if n in written], ordered, states, seen)

            def lane_tree(lane_no: int) -> tuple[Path, Seams]:
                """The tree of a lane, made once for the whole search: it keeps its build and, in `lane_written`, the
                rewrites each of its files was last rendered with, so a wave writes only the files that differ."""
                if lane_no not in lanes:
                    with stages.stage("unify.lane"):
                        assert seams.lane is not None
                        lanes[lane_no] = seams.lane(lane_no, files)
                    lane_written[lanes[lane_no][0]] = {}
                return lanes[lane_no]

            def execute(wave: Wave, at: Path, under: Seams) -> None:
                """The write and the builds of one wave, in the tree `at`: touches nothing the other waves share."""
                _write(at, files, wave.held, src, None if at == root else lane_written[at], rendered)
                start = time.monotonic()
                with stages.stage("unify.isolate"):
                    wave.made, wave.makes = _decide(at, under, wave.ordered, base, wave.errors, wave.seen)
                wave.seconds = time.monotonic() - start

            def apply(
                wave: Wave,
                trials: Sequence[tuple[frozenset[str], frozenset[Unit]]],
                results: dict[int, dict[Unit, str | None]],
                suspect: Counter[Unit],
            ) -> None:
                nonlocal built, rebuilt
                errors.update(wave.errors)
                memo.update({wave.states[u]: h for u, h in wave.made.items() if u not in wave.seen})
                built += wave.makes
                rebuilt += len(wave.made) - len(wave.seen.keys() & wave.made.keys())
                for i in wave.picks:
                    include, objs = trials[i]
                    results[i] = {u: wave.made[u] for u in objs if u in wave.made}  # the others stay undecided
                    for u, object_hash in results[i].items():
                        if object_hash is None or object_hash != base[u]:
                            passed.pop(u, None)
                            suspect[u] += 1
                        else:  # an object is fixed by the rewrites that reach it: what it was built with is its state
                            passed[u] = (frozenset(n for n in include if u in units_of[n]), object_hash)

            def run(
                picked: Sequence[Sequence[int]],
                trials: Sequence[tuple[frozenset[str], frozenset[Unit]]],
                results: dict[int, dict[Unit, str | None]],
                suspect: Counter[Unit],
            ) -> None:
                """The waves of one pass. With lanes they run together, `seams.width` at a time, each in the tree of
                its lane (all prepared first, so the order of a lane's objects is the pass's, not a finished wave's);
                without, they take turns in the one tree and each sees what the one before it built."""
                width = min(seams.width, len(picked))
                if seams.lane is None or width < 2:
                    for picks in picked:
                        wave = prepare(picks, trials, suspect)
                        execute(wave, root, seams)
                        apply(wave, trials, results, suspect)
                    return
                with stages.stage("unify.isolate.book"):  # the serial work around a round's lanes: its own stage line
                    waves = [prepare(picks, trials, suspect) for picks in picked]
                started = time.monotonic()
                process.together(waves, width, lane_tree, lambda w, lane: execute(w, *lane), seams.grant)
                with stages.stage("unify.isolate.book"):
                    for wave in waves:
                        apply(wave, trials, results, suspect)
                wall = time.monotonic() - started
                stages.say(
                    f"unify.isolate round: {len(waves)} waves in {width} lanes, {wall:.1f}s wall for"
                    f" {sum(w.seconds for w in waves):.1f}s of trials"
                )

            while nodes:
                save()
                # idle lanes split a node further: with few nodes a pass of halves leaves most of the width unused
                splittable = sum(len(names) > 1 for names, _, _ in nodes)
                spread = max(2, seams.width // max(splittable, 1)) if seams.lane is not None else 2
                trials: list[tuple[frozenset[str], frozenset[Unit]]] = []  # (rewrites written, objects it decides)
                splits = []
                for candidates, affected, out in nodes:
                    if len(candidates) == 1:
                        name = candidates[0]
                        rejected.add(name)
                        explained.update(affected)
                        for u, object_hash in sorted(affected.items()):
                            path = target(u)
                            why = (
                                "does not build: " + errors.get(path, compilerrors.NO_ERROR)
                                if object_hash is None
                                else "object differs"
                            )
                            held_objects[name][path] = why
                            objects[path]["names"].append(name)
                        continue
                    fan = min(spread, len(candidates))
                    cuts = [len(candidates) * k // fan for k in range(fan + 1)]
                    parts = [candidates[cuts[k] : cuts[k + 1]] for k in range(fan)]
                    ids: list[int | None] = []
                    # Each part is tried alone, the rest of the node out. Two parts: the left decides first and the
                    # right is tried as well only when the left fails, its trial given a make only if it fits one.
                    # More parts (idle lanes): all are tried in the same pass.
                    for tested in parts:
                        deciding = frozenset(u for u in affected if any(u in touches[n] for n in tested))
                        omit = out.union(n for n in candidates if n not in tested)
                        if deciding and (fan > 2 or not ids or ids[0] is not None):
                            trials.append((frozenset(n for n in active if n not in omit), deciding))
                            ids.append(len(trials) - 1)
                        else:
                            ids.append(None)
                    splits.append((candidates, parts, ids, affected, out))
                if not splits:
                    break
                results.clear()
                lefts, rights = [], {}
                for _, parts, ids, _, _ in splits:
                    if len(parts) > 2:
                        lefts += [i for i in ids if i is not None]
                    elif ids[0] is not None:
                        lefts.append(ids[0])
                        if ids[1] is not None:
                            rights[ids[1]] = ids[0]
                waves, later = _waves(trials, touchers, lefts, list(rights))
                run(waves, trials, results, suspect)
                # a right half whose own left half failed and which fitted no wave takes turns now
                waves, _ = _waves(trials, touchers, [j for j in later if _broken(results, rights[j], base)])
                run(waves, trials, results, suspect)
                nodes = []
                for candidates, parts, ids, affected, out in splits:
                    differ = [(part, _broken(results, i, base)) for part, i in zip(parts, ids, strict=True)]
                    if not any(objs for _, objs in differ):
                        nodes.append((parts[-1], affected, out))  # the others hold none: the culprits are in the last
                        continue
                    for part, objs in differ:
                        if objs:
                            nodes.append((part, objs, out.union(n for n in candidates if n not in part)))
            save()
            for name in sorted(rejected):
                del active[name]
            _write(root, files, active.values(), src)
            # What the round left to know. An explained object is trusted until the closing check. Another bad object
            # keeps its failure when no rejected rewrite reaches it (its text is the one it failed with); the rest
            # (and every object still unknown) is rebuilt in suspect order and stops at its first failing chunk, which
            # the next round searches: a clean build of all of them is the one full verify, the unbuilt stay unknown.
            dirty = set().union(*(units_of[n] for n in rejected))
            carried = {u for u in bad - explained if u not in dirty}
            for u in (bad | unknown) - carried:
                owed[u] |= rejected
            for u in carried:
                owed.pop(u, None)
            testing = {u for u in (bad | unknown) - explained - carried if any(u in units_of[n] for n in active)}
            # An object a passing trial built with exactly the rewrites that remain is already verified.
            known = {
                u: passed[u][1]
                for u in testing
                if u in passed and passed[u][0] == frozenset(n for n in active if u in units_of[n])
            }
            fresh_units = sorted(testing - known.keys(), key=lambda u: (-suspect[u], u))
            states = {u: (u, frozenset(n for n in active if u in units_of[n])) for u in fresh_units}
            seen = {u: memo[k] for u, k in states.items() if k in memo}
            with stages.stage("unify.isolate.verify"):
                decided, makes = _decide(root, seams, [fresh_units], base, errors, seen)
            memo.update({states[u]: h for u, h in decided.items() if u not in seen})
            made = {**{u: made[u] for u in carried}, **known, **decided}
            built += makes
            rebuilt += len(decided) - len(seen.keys() & decided.keys())
            unknown = testing - made.keys()
            remaining_bad = carried | set(differing(testing & made.keys(), made, base))
            # Removing one culprit can also clear another object's interacting
            # failure, even when that object passed an earlier half-trial.
            for u in sorted((bad | testing) - explained - remaining_bad - unknown):
                path = target(u)
                for name in sorted(owed.pop(u, set())):
                    if u in touches[name] and name not in objects[path]["names"]:
                        objects[path]["names"].append(name)
                        held_objects[name][path] = objects[path]["error"]
            for u in remaining_bad:
                owed.pop(u, None)
            bad = remaining_bad
            save()
        settled.update(active)
        for name in fresh:
            spent[name] = (time.monotonic() - started) / len(fresh)
            failed[name].update(why_of(name))
            say(line(name, settled.get(name), failed[name], spent[name]))
        _write(root, files, settled.values(), src)
    if back is None:
        back = _closing(root, seams, census, tried, settled, verify_errors)
    broken = sorted(u for u, h in back.items() if h is None)
    if broken:
        raise Refusal(*_compile_findings(broken, verify_errors, census, settled))
    drift = sorted(target(u) for u, h in back.items() if h != base[u])
    if drift:
        raise Refusal(
            Finding("unify.drift", "after the rewrites an object differs from the baseline", missing=tuple(drift[:20]))
        )
    return report(True, settled, len(back))


READ_CHUNK = 16  # units one worker reads per job: the statements of their shared headers travel once
READ_WINDOW = 4  # jobs per host slot in flight at once: the parent holds one window of results, never all of them


def _shared(got: Facts, same: dict[object, object]) -> Facts:
    """The facts of a unit with every statement, typedef, file and word being the one object the parent already holds:
    a worker's chunk arrives as its own copy of the shared headers' statements, whatever its worker interned."""
    typedefs, stmts, files, words = got
    return (
        [same.setdefault(t, t) for t in typedefs],  # type: ignore[misc]
        [same.setdefault(t, t) for t in stmts],  # type: ignore[misc]
        frozenset(same.setdefault(f, f) for f in files),  # type: ignore[misc]
        frozenset(same.setdefault(w, w) for w in words),  # type: ignore[misc]
    )


def _read_chunk(items: list[tuple[Config, str, str]]) -> list[Facts]:
    """The facts of several units, equal statements and words being one object: pickled once, held once in the parent
    (every unit of a version repeats the statements of the headers it shares)."""
    same: dict[object, object] = {}
    out: list[Facts] = []
    for cfg, version, unit in items:
        typedefs, stmts, files, words = facts(
            tree.preprocess(cfg, unit, version, markers=True), unit, configuration.under_tree
        )
        out.append(_shared((typedefs, stmts, files, words), same))
    return out


def jobs(host: Host) -> int:
    """The job count of a build step: every slot the host grants this command, whatever number of objects it names (the
    steps of one object are many: preprocess, compile, stamps), so the step never starts below what the host can run."""
    return max(1, host.slots)


def seams(cfg: Config) -> Seams:
    """The real build: the project's own units, preprocessor and make. Its lanes are trees of symlinks to the project's
    with a build directory of their own, so waves building in different rewrite states never meet on a file."""
    root = cfg.project.root

    def units() -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for v in cfg.project.versions:
            home = root / configuration.root("build") / v
            rel = (o.relative_to(home).with_suffix("").as_posix() for o in sorted(home.rglob("*.o")) if home.is_dir())
            found[v] = [
                u
                for u in rel
                if configuration.is_source(u) and not configuration.is_assembly(u) and (root / u).is_file()
            ]
        return found

    def read(items: Sequence[Unit]) -> list[Facts]:
        chunks = [[(cfg, v, u) for v, u in items[at : at + READ_CHUNK]] for at in range(0, len(items), READ_CHUNK)]
        window, same = max(1, cfg.host.slots) * READ_WINDOW, {}  # type: ignore[var-annotated]
        out: list[Facts] = []
        for at in range(0, len(chunks), window):
            for chunk in pool.map("unify.read", _read_chunk, chunks[at : at + window]):
                out.extend(_shared(got, same) for got in chunk)
        return out

    def make(targets: Sequence[str]) -> tuple[int, str]:
        store.write(root / "local.mk", build.local_mk(cfg))
        ran = process.make(cfg, list(targets), jobs(cfg.host))
        return (-1 if ran.exit is None else ran.exit), (ran.stdout + ran.stderr).decode(errors="replace")

    def lane(index: int, files: Collection[str]) -> tuple[Path, Seams]:
        home = process.state(root) / "lanes" / str(index)
        _farm(root, home, files, skip=(process.state(root).name, "build", "local.mk"))
        (home / configuration.root("build")).mkdir()
        own = replace(cfg, project=replace(cfg.project, root=home))

        def make_in(targets: Sequence[str]) -> tuple[int, str]:
            store.write(home / "local.mk", build.local_mk(own))
            ran = process.make(own, list(targets), jobs(own.host))
            return (-1 if ran.exit is None else ran.exit), (ran.stdout + ran.stderr).decode(errors="replace")

        return home, Seams(units, read, make_in)

    return Seams(units, read, make, lane, jobs(cfg.host), jobs(cfg.host))


def _names(path: Path | None) -> list[str] | None:
    """The names of a file: the first column of each line (a name per line, or a table in the CLASH.tsv shape)."""
    if path is None:
        return None
    rows = [line.split("\t")[0].strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r and r != "name"]


def census_command(cfg: Config, params: Json) -> Json:
    """`unbake census`: the clashes of the tree in the CLASH.tsv shape, to --out or to the count only. With --folds
    (a fold list: header and target per line) the table is the fold-check instead: the names two types meet at in a
    unit once each group of folded headers is one header, `fold-only` where the tree as it is has no clash of the name;
    nothing is written but --out."""
    real = seams(cfg)
    units = real.units()
    if not any(units.values()):
        raise Refusal(Finding("unify.units", "no compiled unit found: build the tree first (unbake setup)"))
    folds = folds_of(Path(params["folds"])) if params.get("folds") else []
    groups = fold_groups(folds)
    census = collect(units, real.read, frozenset().union(*groups))
    root = cfg.project.root
    texts = {
        p: (root / p).read_bytes().decode("latin-1")
        for r in (*configuration.source_roots(), *configuration.header_roots())
        for p in sorted(x.relative_to(root).as_posix() for x in (root / r).rglob("*") if x.is_file())
        if configuration.is_text(p)
    }
    users = index(texts)
    found = clashes(census.per)
    made = folded(census, groups) if folds else {}
    out = params.get("out")
    if out:
        rows = (
            table(made, users, {n: "open" if n in found else "fold-only" for n in made})
            if folds
            else table(census.per, users)
        )
        store.write(Path(out), rows.encode())
    return {
        "units": sum(len(v) for v in units.values()),
        "names": len(census.per),
        "clashing": len(found),
        "declarations": sum(len(d) for vers in census.per.values() for d in vers.values()),
        "written": str(out) if out else "",
        **(
            {"folds": len(folds), "folded": len(made), "fold_only": sorted(n for n in made if n not in found)}
            if folds
            else {}
        ),
    }


def _rows(result: Json) -> str:
    """The --out table: a row per decided name. What the run decided only: the seconds a name took differ per run (they
    stay in the result file), so two runs over one tree write the same table."""
    rows = ["name\tstatus\ttype\tfailing per candidate type\n"]
    for name in sorted(result["seconds"]):
        got = result["unified"].get(name)
        bad = "; ".join(f"{t or 'no plan'} => {w}" for t, w in result["unresolved"].get(name, {}).items())
        rows.append(f"{name}\t{'unified' if got else 'unresolved'}\t{got['type'] if got else ''}\t{bad}\n")
    return "".join(rows)


def _keeper(result: Path | None, out: Path | None) -> Callable[[Json], None]:
    """What writes the result (as JSON) and the --out table: each write replaces the file whole, so a kill leaves the
    last one."""

    def keep(document: Json) -> None:
        if (
            result
        ):  # the counts of builds are the run's own work: a resumed run's file equals a whole run's without them
            decided = {k: v for k, v in document.items() if k not in WORK}
            store.write(result, (json.dumps(decided, indent=1, sort_keys=True) + "\n").encode())
        if out:
            store.write(out, _rows(document).encode())

    return keep


def _partial(path: Path | None) -> Json | None:
    """The result a cut run left at `path`, or none: no file, an empty one or a finished result start afresh."""
    if path is None or not path.is_file() or not path.stat().st_size:
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        complete = document["complete"]
    except (ValueError, KeyError, TypeError) as exc:
        raise Refusal(Finding("unify.resume", f"{path} is not a unify result: {exc}")) from exc
    return None if complete else document


def unify_command(cfg: Config, params: Json) -> Json:
    """`unbake unify`: the rewrite itself; --names restricts it, --out writes one row per name, --result keeps the
    result as it is decided and resumes from it when a run was cut short."""
    result_path = Path(params["result"]) if params.get("result") else None
    out = Path(params["out"]) if params.get("out") else None
    keep = _keeper(result_path, out)
    result = run(cfg.project.root, seams(cfg), _names(params.get("names")), keep=keep, resume=_partial(result_path))
    with stages.stage("unify.save"):
        keep(result)
    notes = [
        Finding(
            "unify.unresolved",
            f"{n}: definition-owned declaration unresolved: "
            + "; ".join(f"{t or 'no plan'} => {w}" for t, w in why.items()),
            blocking=False,
        )
        for n, why in result["unresolved"].items()
    ]
    return {**result, "notes": [asdict(f) for f in notes]}
