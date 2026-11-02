"""`unbake census` and `unbake unify` over a toy tree: the seams (units, text, make) are fakes, no build."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pytest

from unbake import typecensus, typeplan, typeunify
from unbake.contracts import Host, Refusal
from unbake.typecensus import Norm
from unbake.typeunify import Seams, Unit

LIKE = lambda f: f.startswith(("src/", "include/"))  # noqa: E731


def marked(root: Path, unit: str) -> str:
    """What a preprocessor makes of the unit: its header's text under a line marker, then its own."""
    head = (root / "include/h.H").read_text() if (root / "include/h.H").exists() else ""
    return f'# 1 "include/h.H"\n{head}# 1 "{unit}"\n{(root / unit).read_text()}'


class World:
    """A tree of one header and some sources. `fail` names the declarations that make a unit fail to build."""

    def __init__(self, root: Path, files: dict[str, str], units: Sequence[str], fail: Sequence[str] = ()) -> None:
        self.root, self.units, self.fail, self.calls = root, list(units), list(fail), []
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        self.seams = Seams(self.found, self.read, self.make)

    def found(self) -> dict[str, list[str]]:
        return {"us": list(self.units), "de": list(self.units)}

    def read(self, items: Sequence[Unit]) -> list[typeunify.Facts]:
        return [typeunify.facts(marked(self.root, u), u, LIKE) for _, u in items]

    def make(self, targets: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(targets))
        log, code = "make\n", 0
        for t in targets:
            version, unit = re.match(r"build/(\w+)/(.*)\.o$", t).groups()  # type: ignore[union-attr]
            text = marked(self.root, unit)
            if any(bad in text for bad in self.fail):
                log, code = log + f"make: *** [Makefile:1: {t}] Error 1\n", 2
            else:
                out = self.root / t
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    re.sub(r"extern[^\n]*\n", "", (self.root / unit).read_text()).replace(".value", "") + version
                )  # no declarations; a one-field struct reads as its field
        return code, log


def tree(tmp_path: Path, fail: Sequence[str] = ()) -> World:
    files = {
        "include/h.H": "extern int g;\nextern int h;\n",
        "src/a.CPP": "extern long g;\nint f(void) { return g; }\n",
        "src/b.CPP": "extern short h;\nint k(void) { return h + g; }\n",
    }
    return World(tmp_path, files, ["src/a.CPP", "src/b.CPP"], fail)


# ---------------------------------------------------------------- census parse
def test_statements_are_typed_and_normalised() -> None:
    text = (
        '# 1 "include/t.H"\ntypedef unsigned char u8;\ntypedef struct Foo { int a; } Foo;\n'
        "extern u8 *p[4];\nextern u8 *p[];\nvoid f(Foo *x, int n);\nint g = 3;\nint (*cb)(int a, Foo *b);\n"
        "int body(int a) { return a; }\n"
    )
    typedefs, stmts, files, _ = typeunify.facts(text, "src/u.CPP", LIKE)
    norm = Norm()
    for t in typedefs:
        norm.define(t)
    found = {n: (d[1], d[2]) for n, d in typecensus.declarations(stmts, norm, LIKE)}
    assert found["p"] == ("ext", "unsigned char*[]")
    assert found["f"] == ("proto", "void(struct Foo*,int)")
    assert found["g"] == ("vdef", "int")
    assert found["cb"] == ("ext", "int(*)(int,struct Foo*)") or found["cb"][1] == "int(*)(int,struct Foo*)"
    assert found["body"] == ("fdef", "int(int)")
    assert files == {"include/t.H", "src/u.CPP"}


def test_census_table_has_the_clash_shape(tmp_path: Path) -> None:
    world = tree(tmp_path)
    census = typeunify.collect(world.found(), world.read)
    assert set(typecensus.clashes(census.per)) == {"g", "h"}
    rows = typecensus.table(census.per, typecensus.index({"src/a.CPP": "g g", "include/h.H": "g h"})).splitlines()
    assert rows[0] == typecensus.HEADER.rstrip("\n")
    cols = rows[1].split("\t")
    assert cols[:2] == ["g", "2"] and "long => src/a.CPP:1 ext [de,us]" in cols[2]
    assert cols[3] == "2: include/h.H src/a.CPP" and cols[4] == "open"


# ---------------------------------------------------------------- closure, plan, batching
def test_closure_is_the_files_a_unit_reads(tmp_path: Path) -> None:
    world = tree(tmp_path)
    census = typeunify.collect(world.found(), world.read)
    assert census.closure[("us", "src/a.CPP")] == {"include/h.H", "src/a.CPP"}


def test_plan_orders_candidates_and_rewrites_the_other_declarations(tmp_path: Path) -> None:
    world = tree(tmp_path)
    census = typeunify.collect(world.found(), world.read)
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    sites = typeplan.sites_of(census.per, "g")
    assert typeplan.candidates(sites) == ["long", "int"]  # equal votes: the longer spelling first
    first = typeplan.plan("g", sites, 0, src, census.norm)
    second = typeplan.plan("g", sites, 1, src, census.norm)
    assert isinstance(first, typeplan.Plan) and isinstance(second, typeplan.Plan)
    assert typeplan.render("include/h.H", [first], src) == "extern long g;\nextern int h;\n"
    assert typeplan.render("src/a.CPP", [second], src).startswith("extern int g;\n")
    assert typeplan.plan("g", sites, 2, src, census.norm) == typeplan.EXHAUSTED


def test_a_definition_decides_alone() -> None:
    sites = {"a.C:1": ("vdef", "int", 1), "b.H:2": ("ext", "short", 1), "c.H:3": ("ext", "short", 1)}
    assert typeplan.candidates(sites) == ["int"]
    voted = {"a.H:1": ("ext", "void*", 1), "b.H:1": ("ext", "int*", 1), "c.H:1": ("ext", "int*", 1)}
    assert typeplan.candidates(voted) == ["int*", "void*"]


def test_multi_declarator_statement_keeps_the_other_name(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "include/m.H").write_text("extern short a, b;\n")
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    mine = typeplan.Plan("a", "int", "", "int a", (("include/m.H", 0, 18),), frozenset({"include/m.H"}))
    assert typeplan.render("include/m.H", [mine], src) == "extern int a;\nextern short b;\n"


def test_batches_group_disjoint_names() -> None:
    units = {
        "a": frozenset({("us", "x")}),
        "b": frozenset({("us", "y")}),
        "c": frozenset({("us", "x"), ("us", "z")}),
        "none": frozenset(),
    }
    assert typeunify.batches(units) == [["b", "c", "none"], ["a"]]  # c is the widest, a meets it


# ---------------------------------------------------------------- pick, rotation, refusal
def test_pick_keeps_the_most_declared_type_and_names_that_share_a_unit_share_one_build(tmp_path: Path) -> None:
    world = tree(tmp_path)
    # g is used by both units, h by b alone: the two share b, and a make repeats the preprocess and record runs
    result = typeunify.run(tmp_path, world.seams)
    assert result["unified"]["g"]["type"] == "long" and result["unified"]["h"]["type"] == "short"
    assert result["unresolved"] == {} and result["batches"] == 1
    assert (tmp_path / "include/h.H").read_text() == "extern long g;\nextern short h;\n"
    assert len(world.calls) == 1 + 1 + 1  # baseline, one build for the round, the verification of every unit touched


def test_disjoint_names_share_one_build(tmp_path: Path) -> None:
    world = World(
        tmp_path,
        {
            "src/a.CPP": "extern long g;\nextern short g;\nint f(void) { return g; }\n",
            "src/b.CPP": "extern long h;\nextern short h;\nint k(void) { return h; }\n",
        },
        ["src/a.CPP", "src/b.CPP"],
    )
    result = typeunify.run(tmp_path, world.seams)
    assert result["batches"] == 1 and sorted(result["unified"]) == ["g", "h"]


def test_a_failing_type_rotates_to_the_next(tmp_path: Path) -> None:
    world = tree(tmp_path, fail=["extern long g;\nextern"])  # a long g in include/h.H is the fault
    result = typeunify.run(tmp_path, world.seams, ["g"])
    assert result["unified"]["g"]["type"] == "int"
    assert (tmp_path / "include/h.H").read_text().startswith("extern int g;")
    assert (tmp_path / "src/a.CPP").read_text().startswith("extern int g;")  # every declaration took the pick


def test_a_name_no_type_satisfies_is_reported_per_type_and_left_alone(tmp_path: Path) -> None:
    world = tree(tmp_path, fail=["extern long g;", "extern int g;"])
    before = {p: (tmp_path / p).read_text() for p in ("include/h.H", "src/a.CPP")}
    # the baseline must build, so the failing texts are armed after it
    armed = world.fail
    world.fail = []
    original = world.make

    def late(targets: Sequence[str]) -> tuple[int, str]:
        world.fail = armed if 1 <= len(world.calls) <= 2 else []  # the two attempts, not the baseline or the proof
        return original(targets)

    seams = Seams(world.found, world.read, late)
    result = typeunify.run(tmp_path, seams, ["g"])
    assert set(result["unresolved"]["g"]) == {"long", "int"}
    assert all("does not build" in w and w.startswith(("us ", "de ")) for w in result["unresolved"]["g"].values())
    assert {p: (tmp_path / p).read_text() for p in before} == before


@pytest.mark.parametrize(
    ("code", "log", "makes", "why"),
    [(2, "make: *** No rule\n", True, "named no unit"), (0, "make\n", False, "neither built")],
)
def test_a_build_that_did_not_run_is_refused(tmp_path: Path, code: int, log: str, makes: bool, why: str) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.CPP").write_text("int f;\n")

    def make(targets: Sequence[str]) -> tuple[int, str]:
        for t in targets if makes else ():
            (tmp_path / t).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / t).write_bytes(b"o")
        return code, log

    seams = Seams(lambda: {"us": ["src/a.CPP"]}, lambda items: [], make)
    with pytest.raises(Refusal) as refused:
        typeunify.run(tmp_path, seams)
    assert refused.value.findings[0].key == "unify.build"
    assert why in refused.value.findings[0].reason


def test_a_tree_that_does_not_build_has_no_baseline(tmp_path: Path) -> None:
    world = tree(tmp_path, fail=["extern short h;"])
    with pytest.raises(Refusal) as refused:
        typeunify.run(tmp_path, world.seams)
    assert refused.value.findings[0].key == "unify.baseline"


def test_no_units_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Refusal) as refused:
        typeunify.run(tmp_path, Seams(lambda: {"us": []}, lambda items: [], lambda targets: (0, "x")))
    assert refused.value.findings[0].key == "unify.units"


# ---------------------------------------------------------------- the seams of the speedup
def test_words_are_what_a_unit_defines_or_uses_never_what_it_only_declares() -> None:
    text = (
        '# 1 "include/h.H"\ntypedef int T;\nextern int only_declared;\nint proto(int);\nextern int used;\n'
        "int (*pointer)(int);\nint defined = 3;\nint holder[] = { initialised };\n"
        "int body(void) { return used + proto(1); }\n"
    )
    words = typeunify.facts(text, "src/u.CPP", LIKE)[3]
    assert {"used", "proto", "pointer", "defined", "holder", "initialised", "body"} <= words
    assert "only_declared" not in words


def test_a_name_touches_the_units_that_define_or_use_it(tmp_path: Path) -> None:
    world = tree(tmp_path)
    census = typeunify.collect(world.found(), world.read)
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    plans = {n: typeplan.plan(n, typeplan.sites_of(census.per, n), 0, src, census.norm) for n in ("g", "h")}
    g, h = plans["g"], plans["h"]
    assert isinstance(g, typeplan.Plan) and isinstance(h, typeplan.Plan)
    assert typeunify.reach(census, h) >= {("us", "src/a.CPP"), ("us", "src/b.CPP")}  # the header is in both
    assert typeunify.touched(census, g) == {(v, u) for v in ("us", "de") for u in ("src/a.CPP", "src/b.CPP")}
    assert typeunify.touched(census, h) == {("us", "src/b.CPP"), ("de", "src/b.CPP")}  # a only declares h


def test_only_the_touched_objects_are_made_and_compared(tmp_path: Path) -> None:
    world = tree(tmp_path)
    result = typeunify.run(tmp_path, world.seams, ["h"])
    assert result["unified"]["h"]["units"] == 2
    built = [t for call in world.calls[1:-1] for t in call]
    assert built == ["build/de/src/b.CPP.o", "build/us/src/b.CPP.o"]  # a does not use h


def test_a_unit_the_finer_reading_missed_refuses_the_run_as_drift(tmp_path: Path) -> None:
    world = tree(tmp_path)
    base = world.make

    def late(targets: Sequence[str]) -> tuple[int, str]:
        code, log = base(targets)
        broken = [t for t in targets if "src/a.CPP" in t and len(world.calls) > 1]  # a only declares h: not touched
        for t in broken:
            (tmp_path / t).unlink()
            log += f"make: *** [Makefile:1: {t}] Error 1\n"
        return (2 if broken else code), log

    with pytest.raises(Refusal) as err:
        typeunify.run(tmp_path, Seams(world.found, world.read, late), ["h"])
    assert err.value.findings[0].key == "unify.drift"


def test_differing_names_the_missing_and_the_changed_objects() -> None:
    base = {("us", "a"): "1", ("us", "b"): "2", ("us", "c"): "3"}
    made = {("us", "a"): "1", ("us", "b"): "9", ("us", "c"): None}
    assert typeunify.differing(base, made, base) == [("us", "b"), ("us", "c")]
    assert typeunify.differing([("us", "a")], made, base) == []


def test_nothing_to_rebuild_runs_no_make(tmp_path: Path) -> None:
    world = tree(tmp_path)
    assert typeunify.rebuild(tmp_path, world.seams, []) == {} and world.calls == []


@pytest.mark.parametrize(("slots", "jobs"), [(16, 16), (1, 1), (0, 1)])
def test_a_build_step_asks_for_every_slot_of_the_host(slots: int, jobs: int) -> None:
    host = Host.__new__(Host)
    object.__setattr__(host, "slots", slots)
    assert typeunify.jobs(host) == jobs


def test_a_statement_every_unit_repeats_is_parsed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = tree(tmp_path)
    parsed: list[str] = []
    original = Norm.statement

    def count(self: Norm, text: str, hint: str) -> list:  # type: ignore[type-arg]
        parsed.append(text)
        return original(self, text, hint)

    monkeypatch.setattr(Norm, "statement", count)
    census = typeunify.collect(world.found(), world.read)
    header = [t for t in parsed if "extern int g" in t]
    assert len(header) == 1  # the header's statement once, not once per unit nor per version
    assert set(typecensus.clashes(census.per)) == {"g", "h"}


# ---------------------------------------------------------------- the real slices of TYPE-CLASH-2
def test_hex_array_bounds_are_written_decimal(tmp_path: Path) -> None:
    """g_videoManagerThreadStack[0x200]: `unbake audit` (source.hex-value) refuses a small hex bound."""
    assert typecensus.decimal_bounds("char g_videoManagerThreadStack[0x200]") == "char g_videoManagerThreadStack[512]"
    assert typecensus.decimal_bounds("u8 m[0x10U][0XfL] = {0x10}") == "u8 m[16][15] = {0x10}"  # only the bounds
    assert typecensus.tkey("char[512]") == typecensus.tkey("char[0x200]") == "char[]"
    files = {
        "include/h.H": "extern unsigned char m[2][0x20];\n",
        "src/a.CPP": "extern char m[2][0x20];\nint f(void) { return m[0][0]; }\n",
        "src/b.CPP": "int k(void) { return m[0][1]; }\n",
    }
    world = World(tmp_path, files, ["src/a.CPP", "src/b.CPP"])
    result = typeunify.run(tmp_path, world.seams, ["m"], say=lambda _text: None)
    assert "m" in result["unified"]
    rewritten = (
        tmp_path / "src/a.CPP"
    ).read_text()  # the declaration unify wrote: the one in h.H is the pick as it was
    assert "m[][32]" in rewritten and "0x" not in rewritten


def test_the_real_fold_only_shapes_read_as_their_types() -> None:
    """The three shapes the census was blind to (TRIPLE.H, RESULTS_TEXT.H, a two-prototype statement)."""
    norm = Norm()

    def types(text: str) -> dict[str, str]:
        return {n: d[2] for n, d in typecensus.declarations(typeunify.scan(f'# 1 "include/x.H"\n{text}'), norm, LIKE)}

    assert types("extern *g_resultsGoalOneFormat;") == {"g_resultsGoalOneFormat": "int*"}  # implicit int
    assert types("extern char *g_resultsGoalOneFormatLocalized[];") == {"g_resultsGoalOneFormatLocalized": "char*[]"}
    assert types("extern char g_pickupHealthDefinitions;") == {"g_pickupHealthDefinitions": "char"}
    assert types("extern struct { int a; } g_anon;")["g_anon"] != types("extern char g_other;")["g_other"]
    both = types("void a(int x), *b(char c);")
    assert both == {"a": "void(int)", "b": "void*(char)"}


FOLD_TREE = {
    "include/TRIPLE.H": "extern *g_resultsGoalOneFormat;\nextern char g_pickupHealthDefinitions;\n",
    "include/RESULTS_TEXT.H": "extern char *g_resultsGoalOneFormat;\nextern int g_unrelated;\n",
    "include/h.H": "extern int g;\n",
    "src/a.CPP": "int f(void) { return g; }\n",
}


def fold_world(tmp_path: Path, include: dict[str, str]) -> World:
    world = World(tmp_path, FOLD_TREE, ["src/a.CPP", "src/b.CPP"])
    world.read = lambda items: [  # type: ignore[method-assign]
        typeunify.facts(
            "".join(f'# 1 "{h}"\n{FOLD_TREE[h]}' for h in include[u]) + f'# 1 "{u}"\n{FOLD_TREE.get(u, "")}', u, LIKE
        )
        for _, u in items
    ]
    return world


def test_the_fold_check_names_what_clashes_in_a_unit_once_headers_are_one(tmp_path: Path) -> None:
    """The 42 fold-only names: TRIPLE.H spells `extern *x;` (implicit int), RESULTS_TEXT.H `extern char *x;`. No unit
    reads both until they fold, so only the fold makes the conflict one a compiler sees."""
    include = {"src/a.CPP": ["include/TRIPLE.H"], "src/b.CPP": ["include/RESULTS_TEXT.H"]}
    world = fold_world(tmp_path, include)
    both = frozenset({"include/TRIPLE.H", "include/RESULTS_TEXT.H"})
    census = typeunify.collect(world.found(), world.read, both)
    assert set(typecensus.clashes(census.per)) == {"g_resultsGoalOneFormat"}  # the parser reads `extern *x;` now
    folds = tmp_path / "fold.tsv"
    folds.write_text("header\ttarget\trule\tevidence\ninclude/TRIPLE.H\tinclude/RESULTS_TEXT.H\tr\te\n")
    found = typeunify.folded(census, typeunify.fold_groups(typeunify.folds_of(folds)))
    assert sorted(found) == ["g_resultsGoalOneFormat"]  # int* against char*; g_unrelated and the others agree
    unrelated = typeunify.folded(census, [frozenset({"include/TRIPLE.H", "include/h.H"})])
    assert unrelated == {}  # RESULTS_TEXT.H is not in the group: no unit reads both types
    rows = typecensus.table(found, {}, {n: "fold-only" for n in found}).splitlines()
    assert rows[1].split("\t")[0] == "g_resultsGoalOneFormat" and rows[1].endswith("fold-only")


def test_fold_groups_join_the_folds_that_share_a_file() -> None:
    groups = typeunify.fold_groups([("a.H", "b.H"), ("c.H", "d.H"), ("b.H", "e.H")])
    assert sorted(sorted(g) for g in groups) == [["a.H", "b.H", "e.H"], ["c.H", "d.H"]]


def test_one_line_per_name_says_the_pick_the_candidates_and_the_failing_unit(tmp_path: Path) -> None:
    world = tree(tmp_path, fail=["extern long g;\nextern"])
    said: list[str] = []
    typeunify.run(tmp_path, world.seams, ["g", "h"], say=said.append)
    assert len(said) == 2 and all(re.fullmatch(r"unify (g|h): .*; \d+\.\ds", s) for s in said)
    g = next(s for s in said if s.startswith("unify g:"))
    assert "unified as int" in g and "2 candidate(s) tried" in g and "failing long" in g and "src/" in g


def test_objects_a_cut_short_build_left_are_made_again(tmp_path: Path) -> None:
    """SRC-ENGINE: make stopped (Dos CPU hold) with 20 objects neither built nor failed; unify builds the rest."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.CPP").write_text("int f;\n")
    units = {"us": ["src/a.CPP", "src/b.CPP", "src/c.CPP"]}
    rounds: list[list[str]] = []

    def make(targets: Sequence[str]) -> tuple[int, str]:
        rounds.append(list(targets))
        for t in targets[: 1 if len(rounds) == 1 else None]:  # the first round builds one object, the next the rest
            (tmp_path / t).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / t).write_bytes(b"o")
        return 0, "make\n"

    made = typeunify.rebuild(tmp_path, Seams(lambda: units, lambda items: [], make), [("us", u) for u in units["us"]])
    assert [len(r) for r in rounds] == [3, 2]
    assert all(made.values())


def test_an_extern_of_an_anonymous_struct_is_planned_as_the_one_definitions_type(tmp_path: Path) -> None:
    """SRC-MENU-1: `extern struct { u8 value; } g;` against the definition `u8 g;` had no plan."""
    text = "#include <x>\nextern struct { u8 value; } g_flags;\nvoid f(void) { g_flags.value = 1; }\nu8 g_other;\n"
    (tmp_path / "a.c").write_text(text)
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    start = text.index("extern")
    end = text.index("g_flags;", start) + len("g_flags;")
    assert typeplan._span(src, "a.c", 2, "g_flags") == (start, end)
    assert typeplan._span(src, "a.c", 3, "f") == (text.index("void f"), text.index("}\nu8") + 1)


def test_a_quiet_make_that_made_its_objects_ran(tmp_path: Path) -> None:
    """SRC-MENU-1 on the rig: the build wrote its objects and an empty log; that is a build, not a refusal."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.CPP").write_text("int f;\n")

    def make(targets: Sequence[str]) -> tuple[int, str]:
        for t in targets:
            (tmp_path / t).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / t).write_bytes(b"o")
        return 0, ""

    seams = Seams(lambda: {"us": ["src/a.CPP"]}, lambda items: [], make)
    made = typeunify.rebuild(tmp_path, seams, [("us", "src/a.CPP")])
    assert made[("us", "src/a.CPP")] is not None


def test_an_anonymous_struct_extern_is_a_candidate_with_a_plan(tmp_path: Path) -> None:
    """g_controllerPakPreviewProfileFlags: two externs spell `u8`, one `struct { u8 value; }` that reads `.value`.
    The most declared type goes first and its unit that read `.value` now reads the scalar; the anonymous struct, the
    next candidate, has a plan too and the reading unit of the scalar reads `.value`."""
    files = {
        "include/h.H": "extern unsigned char g_flags;\n",
        "src/a.CPP": "extern unsigned char g_flags;\nint f(void) { return g_flags; }\n",
        "src/b.CPP": "extern struct { unsigned char value; } g_flags;\nint k(void) { return g_flags.value; }\n",
    }
    world = World(tmp_path, files, ["src/a.CPP", "src/b.CPP"])
    census = typeunify.collect(world.found(), world.read)
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    sites = typeplan.sites_of(census.per, "g_flags")
    first = typeplan.plan("g_flags", sites, 0, src, census.norm)
    second = typeplan.plan("g_flags", sites, 1, src, census.norm)
    assert isinstance(first, typeplan.Plan) and first.ctext == "unsigned char g_flags"
    assert first.reads == (("src/b.CPP", "out", "value"),)
    assert (
        typeplan.render("src/b.CPP", [first], src) == "extern unsigned char g_flags;\nint k(void) { return g_flags; }\n"
    )
    assert isinstance(second, typeplan.Plan) and second.ctext == "struct { unsigned char value; } g_flags"
    expected = "extern struct { unsigned char value; } g_flags;\n"
    assert typeplan.render("include/h.H", [second], src) == expected
    assert typeplan.render("src/a.CPP", [second], src) == expected + "int f(void) { return g_flags.value; }\n"
    result = typeunify.run(tmp_path, world.seams, ["g_flags"], say=lambda _text: None)
    assert result["unified"]["g_flags"]["type"] == first.canon
    assert (tmp_path / "src/b.CPP").read_text().endswith("int k(void) { return g_flags; }\n")


def test_reads_of_a_scalar_become_reads_of_the_definitions_one_field_struct(tmp_path: Path) -> None:
    """g_pakControllerRecord: `struct { u16 value; }` is defined, the reading unit's `extern u16` reads it as a scalar.
    Only the reads change: not a string, a member of that name, the declaration, or a comment."""
    files = {
        "src/a.CPP": "struct { unsigned short value; } g_rec = { 1 };\n",
        "src/b.CPP": (
            "extern unsigned short g_rec;\n"
            'int k(void) { /* g_rec */ return g_rec + s.g_rec + p->g_rec + sizeof("g_rec") + g_rec; }\n'
        ),
    }
    got, src = _plan_of(tmp_path, files, "g_rec")
    assert isinstance(got, typeplan.Plan) and got.reads == (("src/b.CPP", "into", "value"),)
    assert typeplan.render("src/b.CPP", [got], src) == (
        "extern struct { unsigned short value; } g_rec;\n"
        'int k(void) { /* g_rec */ return g_rec.value + s.g_rec + p->g_rec + sizeof("g_rec") + g_rec.value; }\n'
    )


def test_a_type_that_is_not_the_one_field_struct_of_the_other_rewrites_no_reads(tmp_path: Path) -> None:
    files = {
        "src/a.CPP": "struct { unsigned short value; } g_rec = { 1 };\n",
        "src/b.CPP": "extern unsigned char g_rec;\nint k(void) { return g_rec; }\n",
    }
    got, _ = _plan_of(tmp_path, files, "g_rec")
    assert isinstance(got, typeplan.Plan) and got.reads == ()


def test_reads_of_a_scalar_become_reads_of_the_first_element_of_the_array_it_is_unified_to(tmp_path: Path) -> None:
    """g_persistedOptionUnlockFlags: `int g[4]` is defined, a reading unit's `extern int g` reads it as a scalar.
    Only the reads change: not a string, a member of that name, the declaration, a comment or an element read."""
    files = {
        "src/a.CPP": "int g_flags[4] = { 1 };\nint f(void) { return g_flags[2]; }\n",
        "src/b.CPP": (
            "extern int g_flags;\n"
            'int k(void) { /* g_flags */ g_flags = 3; return g_flags + s.g_flags + p->g_flags + sizeof("g_flags"); }\n'
        ),
    }
    got, src = _plan_of(tmp_path, files, "g_flags")
    assert isinstance(got, typeplan.Plan) and got.ctext == "int g_flags[]"
    assert got.reads == (("src/b.CPP", "first", "0"),)
    assert typeplan.render("src/b.CPP", [got], src) == (
        "extern int g_flags[];\n"
        'int k(void) { /* g_flags */ g_flags[0] = 3; return g_flags[0] + s.g_flags + p->g_flags + sizeof("g_flags"); }'
        "\n"
    )
    assert typeplan.render("src/a.CPP", [got], src) == files["src/a.CPP"]


def test_a_source_that_reads_the_scalar_through_a_header_reads_the_first_element_too(tmp_path: Path) -> None:
    """g_frontendSavedVideoModeIndex: the header and PLAYEROPTION.CPP declare `u8`, two sources an array of it. The
    source with no declaration of its own reads what the header declares, so its reads change with the header's."""
    files = {
        "include/h.H": "extern unsigned char g_mode;\n",
        "src/a.CPP": '#include "h.H"\nvoid f(void) { g_mode = 1; }\n',
        "src/b.CPP": "extern unsigned char g_mode[];\nint k(void) { return g_mode[1]; }\n",
        "src/c.CPP": "extern unsigned char g_mode[];\nint m(void) { return g_mode[2]; }\n",
        "src/d.CPP": "int unrelated(void) { return 4; }\n",
    }
    world = World(tmp_path, files, ["src/a.CPP", "src/b.CPP", "src/c.CPP", "src/d.CPP"])
    census = typeunify.collect(world.found(), world.read)
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    sites = typeplan.sites_of(census.per, "g_mode")
    got = typeplan.plan("g_mode", sites, 0, src, census.norm)
    assert isinstance(got, typeplan.Plan) and got.ctext == "unsigned char g_mode[]"
    assert got.reads == (("include/h.H", "first", "0"),)
    closures = [census.closure[u] for u in typeunify.touched(census, got)]
    both = typeplan.readers(got, sites, closures, src)
    assert both.reads == (("include/h.H", "first", "0"), ("src/a.CPP", "first", "0"))
    assert "src/a.CPP" in both.files and "src/d.CPP" not in both.files
    assert typeplan.render("src/a.CPP", [both], src) == '#include "h.H"\nvoid f(void) { g_mode[0] = 1; }\n'
    assert typeplan.render("src/b.CPP", [both], src) == files["src/b.CPP"]


def test_reads_of_an_array_stay_when_the_scalar_is_the_type_it_is_unified_to(tmp_path: Path) -> None:
    files = {
        "src/a.CPP": "int g_flags = 1;\n",
        "src/b.CPP": "extern int g_flags[];\nint k(void) { return g_flags[1]; }\n",
    }
    got, _ = _plan_of(tmp_path, files, "g_flags")
    assert isinstance(got, typeplan.Plan) and got.ctext == "int g_flags" and got.reads == ()


def test_reads_of_a_pointer_become_the_pointer_stored_at_the_array_it_is_unified_to(tmp_path: Path) -> None:
    """g_pakDefaultPreviewTypeLabel: `char g[]` is defined, a reading unit's `extern u8 *g` loads the pointer."""
    files = {
        "src/a.CPP": 'char g_label[] = "ab";\n',
        "src/b.CPP": (
            "extern char *g_other, *g_label;\nvoid* k(void) { return &g_label; }\nchar* m(void) { return g_label; }\n"
        ),
    }
    got, src = _plan_of(tmp_path, files, "g_label")
    assert isinstance(got, typeplan.Plan) and got.ctext == "char g_label[]"
    assert got.reads == (("src/b.CPP", "held", "char"),)
    assert typeplan.render("src/b.CPP", [got], src) == (
        "extern char *g_other;\nextern char g_label[];\n"
        "void* k(void) { return &(*(char **)g_label); }\n"
        "char* m(void) { return (*(char **)g_label); }\n"
    )


def test_reads_of_an_array_become_its_address_when_the_pointer_is_the_type_it_is_unified_to(tmp_path: Path) -> None:
    files = {
        "src/a.CPP": "char *g_label = 0;\n",
        "src/b.CPP": "extern char g_label[];\nvoid* k(void) { return &g_label; }\nchar* m(void) { return g_label; }\n",
    }
    got, src = _plan_of(tmp_path, files, "g_label")
    assert isinstance(got, typeplan.Plan) and got.ctext == "char *g_label"
    assert got.reads == (("src/b.CPP", "address", "char"),)
    assert typeplan.render("src/b.CPP", [got], src) == (
        "extern char *g_label;\nvoid* k(void) { return &g_label; }\nchar* m(void) { return ((char *)&g_label); }\n"
    )


def test_an_anonymous_struct_definition_is_the_type_of_every_extern(tmp_path: Path) -> None:
    """g_pakControllerRecord: the definition is `struct { u16 value; }`, an extern spells a scalar."""
    files = {"include/h.H": "extern int g_rec;\n", "src/a.CPP": "struct { unsigned short value; } g_rec = { 1 };\n"}
    world = World(tmp_path, files, ["src/a.CPP"])
    census = typeunify.collect(world.found(), world.read)
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    got = typeplan.plan("g_rec", typeplan.sites_of(census.per, "g_rec"), 0, src, census.norm)
    assert isinstance(got, typeplan.Plan) and got.why == "definition"
    assert got.ctext == "struct { unsigned short value; } g_rec"
    assert typeplan.render("include/h.H", [got], src) == "extern struct { unsigned short value; } g_rec;\n"


def test_reads_of_a_primitive_or_its_array_view_the_definitions_record_by_address(tmp_path: Path) -> None:
    """g_playerViewRenderContext: `Shared_MenuData` is defined; `char` and `u8[]` externs read its bytes by address."""
    files = {
        "src/a.CPP": "struct Menu { int a; int b; };\nstruct Menu g_ctx;\n",
        "src/b.CPP": "extern unsigned char g_ctx[];\nint k(void) { return g_ctx[4]; }\n",
        "src/c.CPP": "extern char g_ctx;\nchar *k(void) { return &g_ctx; }\n",
    }
    got, src = _plan_of(tmp_path, files, "g_ctx")
    assert isinstance(got, typeplan.Plan) and got.ctext == "struct Menu g_ctx"
    assert got.reads == (("src/b.CPP", "views", "unsigned char"), ("src/c.CPP", "view", "char"))
    assert typeplan.render("src/b.CPP", [got], src) == (
        "extern struct Menu g_ctx;\nint k(void) { return ((unsigned char *)&g_ctx)[4]; }\n"
    )
    assert typeplan.render("src/c.CPP", [got], src) == (
        "extern struct Menu g_ctx;\nchar *k(void) { return &(*(char *)&g_ctx); }\n"
    )


def _plan_of(tmp_path: Path, files: dict[str, str], name: str) -> tuple[typeplan.Plan | str, typeplan.Sources]:
    world = World(tmp_path, files, sorted(f for f in files if f.startswith("src/")))
    census = typeunify.collect(world.found(), world.read)
    src = typeplan.Sources(lambda p: (tmp_path / p).read_text())
    scope = {f for c in census.closure.values() for f in c}
    return typeplan.plan(name, typeplan.sites_of(census.per, name), 0, src, census.norm, scope), src


def test_a_definition_typed_by_a_typedef_array_is_the_type_of_the_char_externs(tmp_path: Path) -> None:
    """SRC-AI-2 WEAPON.CPP g_weaponFireStateCallbackTable: `WEAPON_STATE_TABLE(8)` defined, `char[]` read elsewhere."""
    files = {
        "include/h.H": "extern char g_table[];\n",
        "src/a.CPP": "typedef struct { int fire; } WEAPON_STATE_ENTRY;\nWEAPON_STATE_ENTRY g_table[8] = { { 1 } };\n",
        "src/b.CPP": "extern char g_table[];\nchar *k(void) { return g_table; }\n",
    }
    got, src = _plan_of(tmp_path, files, "g_table")
    assert isinstance(got, typeplan.Plan) and got.why == "definition", got
    assert got.ctext == "WEAPON_STATE_ENTRY g_table[]"
    assert typeplan.render("include/h.H", [got], src) == "extern WEAPON_STATE_ENTRY g_table[];\n"


def test_a_definition_of_a_struct_array_is_the_type_of_the_scalar_extern(tmp_path: Path) -> None:
    """SRC-AI-2 g_localPlayerProfiles: `Profile[4]` defined, an array of another element type read elsewhere."""
    files = {
        "include/h.H": "extern int g_profiles[];\n",
        "src/a.CPP": "typedef struct Profile { int id; } Profile;\nProfile g_profiles[4];\n",
        "src/b.CPP": "extern int g_profiles[];\nint k(void) { return g_profiles[0]; }\n",
    }
    got, src = _plan_of(tmp_path, files, "g_profiles")
    assert isinstance(got, typeplan.Plan) and got.why == "definition", got
    assert got.ctext == "struct Profile g_profiles[]"
    assert typeplan.render("include/h.H", [got], src) == "extern struct Profile g_profiles[];\n"


def test_a_function_definition_is_the_type_of_the_proto_read_elsewhere(tmp_path: Path) -> None:
    """SRC-AI-2 SoundElement_PlaySoundAtPosition: the definition's signature against a differing proto."""
    files = {
        "include/h.H": "void SoundElement_Play(void*, int);\n",
        "src/a.CPP": "void SoundElement_Play(void *e, int x, int y) { }\n",
        "src/b.CPP": "void SoundElement_Play(void*, int);\nvoid k(void) { SoundElement_Play(0, 1); }\n",
    }
    got, _src = _plan_of(tmp_path, files, "SoundElement_Play")
    assert isinstance(got, typeplan.Plan) and got.why == "definition", got
    assert got.ctext == "void SoundElement_Play(void *e, int x, int y)"


def test_a_units_own_extern_of_a_function_is_edited_with_the_header_declaration(tmp_path: Path) -> None:
    """TIMER_FORMATPREVIEWDOTTEDTRIPLE.CPP Menu_CountLineChars: the header and the unit's own extern say
    `struct Field *`, the definition says `struct LayeredText *`; both declarations take the definition's type."""
    files = {
        "include/h.H": "typedef int s32;\nextern s32 Menu_CountLineChars(struct Field *);\n",
        "src/a.CPP": "s32 Menu_CountLineChars(struct LayeredText *t) { return 0; }\n",
        "src/b.CPP": "extern s32 Menu_CountLineChars(struct Field *);\nvoid k(void) { Menu_CountLineChars(0); }\n",
    }
    got, src = _plan_of(tmp_path, files, "Menu_CountLineChars")
    assert isinstance(got, typeplan.Plan) and got.why == "definition", got
    assert {e[0] for e in got.edits} == {"include/h.H", "src/b.CPP"}
    want = "struct LayeredText; extern s32 Menu_CountLineChars(struct LayeredText *t);\n"
    assert typeplan.render("src/b.CPP", [got], src).startswith(want)


def test_a_call_with_fewer_arguments_than_the_definition_takes_the_callers_own_parameters(tmp_path: Path) -> None:
    """SNAPSHOT_RESTORESAVEBLOCK.CPP Snapshot_UpdateScoreTitle calls `Menu_CountLineChars()` where the definition takes
    one parameter: the argument is the register the call leaves untouched, the caller's own first parameter."""
    files = {
        "include/h.H": "typedef int s32;\n",
        "src/a.CPP": "s32 Menu_CountLineChars(struct LayeredText *t) { return 0; }\n",
        "src/b.CPP": "extern s32 Menu_CountLineChars(void);\n"
        "s32 k(struct Entry *item, int n) { return n + Menu_CountLineChars() - 11; }\n"
        "s32 j(void) { return Menu_CountLineChars(); }\n"
        'static char *s = "Menu_CountLineChars()";\n',
    }
    got, src = _plan_of(tmp_path, files, "Menu_CountLineChars")
    assert isinstance(got, typeplan.Plan), got
    assert got.params == ("struct LayeredText *t",) and got.callers == {"src/b.CPP"}
    assert typeplan.render("src/b.CPP", [got], src) == (
        "struct LayeredText; extern s32 Menu_CountLineChars(struct LayeredText *t);\n"
        "s32 k(struct Entry *item, int n) { return n + Menu_CountLineChars(item) - 11; }\n"
        "s32 j(void) { return Menu_CountLineChars(); }\n"  # no parameter to pass: the build reports it
        'static char *s = "Menu_CountLineChars()";\n'
    )


def test_missing_trailing_arguments_are_the_callers_parameters_at_the_same_positions(tmp_path: Path) -> None:
    files = {
        "include/h.H": "typedef int s32;\n",
        "src/a.CPP": "s32 Snapshot_Check(struct Menu *m, s32 ch, s32 q) { return 0; }\n",
        "src/b.CPP": "s32 Snapshot_Check(void *, s32, s32);\n"
        "s32 k(struct Menu *m, s32 ch, s32 q) {\n    if (Snapshot_Check(m)) return Snapshot_Check(m, ch);\n"
        "    return Snapshot_Check(m, ch, 0) + Snapshot_Check(m, Snapshot_Check(m, ch, q), q);\n}\n",
    }
    got, src = _plan_of(tmp_path, files, "Snapshot_Check")
    assert isinstance(got, typeplan.Plan), got
    body = typeplan.render("src/b.CPP", [got], src).split("\n", 1)[1]
    assert "if (Snapshot_Check(m, ch, q)) return Snapshot_Check(m, ch, q);" in body
    assert "Snapshot_Check(m, ch, 0) + Snapshot_Check(m, Snapshot_Check(m, ch, q), q);" in body


def test_a_struct_tag_in_a_rewritten_prototype_is_declared_before_it_on_its_line(tmp_path: Path) -> None:
    """PAK.CPP Snapshot_CheckStatusPrompt: three prototypes take the definition's struct, whose tag the file never
    declares; each would otherwise open its own tag inside the parameter list and conflict with the others."""
    files = {
        "include/h.H": "typedef int s32;\n",
        "src/a.CPP": "s32 Snapshot_Check(struct Begin *m, s32 ch) { return 0; }\n",
        "src/b.CPP": "extern s32 Snapshot_Check(struct Menu *, s32);\n"
        "extern s32 Snapshot_Check(struct Menu *x, s32 c);\n",
    }
    got, src = _plan_of(tmp_path, files, "Snapshot_Check")
    assert isinstance(got, typeplan.Plan), got
    assert typeplan.render("src/b.CPP", [got], src) == (
        "struct Begin; extern s32 Snapshot_Check(struct Begin *m, s32 ch);\n"
        "struct Begin; extern s32 Snapshot_Check(struct Begin *m, s32 ch);\n"
    )


class SameCodeWorld(World):
    """Every rewrite of a read compiles to the code it had (the real compiler's verdict), so an object is its unit."""

    armed: Sequence[str] = ()

    def make(self, targets: Sequence[str]) -> tuple[int, str]:
        self.fail = list(self.armed) if self.calls else []  # the baseline builds, the attempts after it may not
        code, log = super().make(targets)
        for t in targets:
            if (self.root / t).exists():
                (self.root / t).write_text(t)
        return code, log


def _label_world(tmp_path: Path, fail: Sequence[str] = ()) -> World:
    """g_pakDefaultPreviewTypeLabel: no definition, the header and one unit say `u8 *`, one unit says `char[]`."""
    files = {
        "include/h.H": "extern unsigned char *g_label;\n",
        "src/a.CPP": "extern char g_label[];\nvoid *f(void) { return (char **)g_label; }\n",
        "src/b.CPP": "extern char *g_label;\nvoid *k(void) { return &g_label; }\nchar *m(void) { return g_label; }\n",
    }
    world = SameCodeWorld(tmp_path, files, ["src/a.CPP", "src/b.CPP"])
    world.armed = fail
    return world


def test_the_array_stays_and_the_pointer_readers_load_through_it_when_its_objects_match(tmp_path: Path) -> None:
    world = _label_world(
        tmp_path, fail=["extern unsigned char *g_label;", "extern char *g_label;"]
    )  # pointer objects differ
    result = typeunify.run(tmp_path, world.seams, ["g_label"])
    assert result["unified"]["g_label"]["type"] == "char[]" and result["unresolved"] == {}
    assert "(*(char **)g_label)" in (tmp_path / "src/b.CPP").read_text()
    assert (tmp_path / "src/a.CPP").read_text().startswith("extern char g_label[];")


def test_the_pointer_wins_and_the_array_readers_take_its_address_when_the_array_objects_differ(tmp_path: Path) -> None:
    world = _label_world(tmp_path, fail=["extern char g_label[];"])  # the array objects differ
    result = typeunify.run(tmp_path, world.seams, ["g_label"])
    assert result["unified"]["g_label"]["type"].endswith("*") and result["unresolved"] == {}
    assert "g_label[]" not in (tmp_path / "src/a.CPP").read_text()
    assert "(char **)((" in (tmp_path / "src/a.CPP").read_text()  # the array read is the address of the pointer


# ---------------------------------------------------------------- UNIFY-FAST: the TIMER names (rig NAMES.tsv rows)
TIMER_NAMES = [
    "g_controllerPakSnapshotPayload",
    "g_gameSettings",
    "g_matchEndingTeamWheelClose",
    "g_matchRoundCounters",
    "g_pakControllerRecord",
    "g_playerViewRenderContext",
]


def timer_world(tmp_path: Path, fail: Sequence[str] = ()) -> World:
    """Every timer name is declared twice in each of two units, the units all names share as they share the header."""
    body = "".join(f"extern long {n};\nextern short {n};\n" for n in TIMER_NAMES)
    use = "".join(f"  s += {n};\n" for n in TIMER_NAMES)
    files = {f"src/{u}.CPP": f"{body}int {u}(void) {{\n  int s = 0;\n{use}  return s;\n}}\n" for u in ("m", "n")}
    return World(tmp_path, files, ["src/m.CPP", "src/n.CPP"], fail)


def test_names_that_share_every_unit_settle_in_one_build(tmp_path: Path) -> None:
    world = timer_world(tmp_path)
    result = typeunify.run(tmp_path, world.seams)
    assert sorted(result["unified"]) == sorted(TIMER_NAMES) and result["unresolved"] == {}
    assert result["batches"] == 1 and result["rounds"] == 1
    assert len(world.calls) == 1 + 1 + 1  # baseline, one build for all six names, the verification


def test_a_failing_name_among_names_that_share_units_rotates_alone(tmp_path: Path) -> None:
    pick = timer_world(tmp_path / "pick")
    typeunify.run(tmp_path / "pick", pick.seams, ["g_gameSettings"])
    first = (
        "long"
        if "extern long g_gameSettings;\nextern long g_gameSettings;" in (tmp_path / "pick/src/m.CPP").read_text()
        else "short"
    )
    world = timer_world(tmp_path / "real", fail=[f"extern {first} g_gameSettings;\nextern {first} g_gameSettings;"])
    armed, world.fail = world.fail, []
    original = world.make

    def late(targets: Sequence[str]) -> tuple[int, str]:
        world.fail = armed if len(world.calls) >= 1 else []  # the baseline builds; every later make meets the fault
        return original(targets)

    result = typeunify.run(tmp_path / "real", Seams(world.found, world.read, late))
    assert sorted(result["unified"]) == sorted(TIMER_NAMES) and result["unresolved"] == {}
    assert result["unified"]["g_gameSettings"]["type"] != first  # rotated to the next candidate
    assert result["rounds"] >= 2 and result["batches"] >= 3  # the round, the suspects one by one, the rotation

def test_records_that_overlap_by_offset_are_merged_into_the_chosen_ones_unnamed_unions(tmp_path: Path) -> None:
    """g_pakControllerRecord: one unit's `struct PakRecord { Saved values[4]; }`, another's `struct { u16 value; }`."""
    files = {
        "include/h.H": "typedef unsigned short Saved;\nstruct PakRecord { Saved values[4]; };\n",
        "src/a.CPP": "extern struct PakRecord g_rec;\nint f(void) { return g_rec.values[1]; }\n",
        "src/b.CPP": "extern struct { unsigned short value; } g_rec;\nint k(void) { return g_rec.value; }\n",
        "src/c.CPP": "extern struct PakRecord g_rec;\nint m(void) { return g_rec.values[0]; }\n",
    }
    got, src = _plan_of(tmp_path, files, "g_rec")
    assert isinstance(got, typeplan.Plan), got
    assert got.ctext == "struct PakRecord g_rec"
    head = typeplan.render("include/h.H", [got], src)
    assert head.endswith("struct PakRecord { union { Saved values[4]; unsigned short value; }; };\n")
    # the unit that wrote the anonymous record now declares the tag, and still reads `value`
    assert (
        typeplan.render("src/b.CPP", [got], src)
        == "extern struct PakRecord g_rec;\nint k(void) { return g_rec.value; }\n"
    )


def test_members_at_other_offsets_stay_beside_the_union_of_the_overlapping_ones(tmp_path: Path) -> None:
    files = {
        "include/h.H": "struct Rec { int head; short a[2]; };\n",
        "src/a.CPP": "extern struct Rec g_rec;\nint f(void) { return g_rec.a[0]; }\n",
        "src/b.CPP": "extern struct { int head; int whole; } g_rec;\nint k(void) { return g_rec.whole; }\n",
        "src/c.CPP": "extern struct Rec g_rec;\n",
    }
    got, src = _plan_of(tmp_path, files, "g_rec")
    assert isinstance(got, typeplan.Plan), got
    assert typeplan.render("include/h.H", [got], src) == (
        "struct Rec { int head; union { short a[2]; int whole; }; };\n"
    )


def test_an_anonymous_chosen_record_takes_the_merged_body_at_every_site(tmp_path: Path) -> None:
    files = {
        "include/h.H": "",
        "src/a.CPP": "extern struct { short a[2]; } g_rec;\n",
        "src/b.CPP": "extern struct { int whole; } g_rec;\n",
        "src/c.CPP": "extern struct { short a[2]; } g_rec;\n",
    }
    got, _src = _plan_of(tmp_path, files, "g_rec")
    assert isinstance(got, typeplan.Plan), got
    assert got.ctext == "struct { union { short a[2]; int whole; }; } g_rec"


def test_records_that_do_not_read_are_left_to_the_plain_plan(tmp_path: Path) -> None:
    files = {
        "include/h.H": "struct Rec { int v; };\n",
        "src/a.CPP": "extern struct Rec g_rec;\n",
        "src/b.CPP": "extern struct { Unknown u[2]; } g_rec;\n",
    }
    got, _src = _plan_of(tmp_path, files, "g_rec")
    assert isinstance(got, typeplan.Plan) and got.bodies == ()
