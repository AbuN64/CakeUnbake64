"""The generic cutover reductions (unbake.leftover) on real RageWars slices (f626ced59), tests/data/ragewars_cutover."""

import tomllib
from pathlib import Path

import pytest

from unbake import leftover as L
from unbake.config import load_resource

DATA = Path(__file__).parent / "data" / "ragewars_cutover"
UNIT = "SOURCE/DATA/OVERLAYS/NINE/RESOURCES/G_ARENACOMPATIBILITYTABLE.CPP"


@pytest.fixture
def layout():
    return {**load_resource("repo.toml")["layout"], **tomllib.loads((DATA / "layout.toml").read_text())["layout"]}


@pytest.fixture
def unit():
    return (DATA / "G_ARENACOMPATIBILITYTABLE.CPP").read_text()


@pytest.fixture
def symbols():
    return L.symbol_rows((DATA / "eu_earlier_build_symbols.txt").read_text())


def test_unused_root_is_project_config(layout):
    assert load_resource("repo.toml")["layout"]["unused_root"] == "unused"
    assert L.unused_root(layout) == "leftover"
    with pytest.raises(ValueError):
        L.unused_root({"unused_root": "a/b"})


def test_unread_data_moves_to_unused_tree_keeping_c(layout, unit):
    found = L.readers(["g_arenaCompatibilityTable", "g_MenuEventHandlersPointerWords4"], {UNIT: unit})
    assert found == {"g_arenaCompatibilityTable": set(), "g_MenuEventHandlersPointerWords4": set()}
    assert L.home(UNIT, layout) == "leftover/DATA/OVERLAYS/NINE/RESOURCES/G_ARENACOMPATIBILITYTABLE.CPP"
    assert L.home("include/DATA/SCENARIO_SETUP/G_ARENACOMPATIBILITYTABLE.H", layout).startswith("leftover/include/")
    assert L.home("leftover/ENGINE/ADDRTABLE_EU_1CA224.CPP", layout) == "leftover/ENGINE/ADDRTABLE_EU_1CA224.CPP"


def test_a_reader_keeps_data_in_source(unit):
    used = L.readers(["g_CTrialCatalogue_SelectNeighborTwoPackedAddress242"], {UNIT: unit})
    assert used["g_CTrialCatalogue_SelectNeighborTwoPackedAddress242"] == {UNIT}


def test_placeholder_names_are_findings_outside_unused_tree(layout, symbols):
    assert L.placeholder("g_arenaCompatibilityTable") is None
    assert L.placeholder("g_earlierMenuPaged2") == "number"
    assert L.placeholder("g_CTrialCatalogue_SelectNeighborTwo") == "ordinal"
    assert L.placeholder("ADDRTABLE_EU_1CA224") == "address"
    names = {n: UNIT for n in symbols}
    assert len(L.findings(names, layout)) == len(symbols)
    assert L.findings({n: "leftover/MENU/PAGED.CPP" for n in symbols}, layout) == []


def test_address_only_symbols_become_literals(unit):
    externs = {n for n in L._IDENT.findall(unit) if n.endswith("PackedAddress242")}
    assert len(externs) == 15
    addresses = {n: 0x80240000 + 4 * i for i, n in enumerate(sorted(externs))}
    retired = L.literals(addresses, with_bytes=())
    out = L.retire(unit, retired)
    assert "PackedAddress242" not in out
    assert "(ResidentEventHandler)(void *)0x80240" in out
    assert "g_arenaCompatibilityTable[64]" in out
    assert L.rename_map(retired, {})["Retired"] == retired
    assert L.literals(addresses, with_bytes=externs) == {}


def _adjacent_rows(unit):
    """The real us-rev1 handler rows 4 and 5 laid out adjacent, as one unit's .data holds them."""
    four, five = (r for r in L.rows(unit) if r.name.startswith("g_MenuEventHandlersPointerWords"))
    return "".join(f"{r.type.split('[')[0]} {r.name}[13] = {r.body};\n/* next row */\n" for r in (four, five))


def test_rows_of_one_shape_become_one_table(unit):
    assert L.tables(L.rows(unit)) == {}  # rows 4 and 5 sit in two #if blocks of two files: never one table
    merged = L.tables(L.rows(_adjacent_rows(unit)))
    assert merged == {
        "g_MenuEventHandlersPointerWordsTable": ["g_MenuEventHandlersPointerWords4", "g_MenuEventHandlersPointerWords5"]
    }
    assert L.placeholder(next(iter(merged))) is None
    assert L.rename_map({}, merged)["Merged"] == merged


def test_interleaved_rows_are_owned_by_region(symbols):
    found = L.regions(symbols, {"g_earlierOptionBits2": 0xC0}, gap=16)
    assert sum(len(r.names) for r in found) == len(symbols)
    mixed = [r for r in found if len(r.families) > 1]
    assert [(r.start, r.end, r.families) for r in mixed] == [
        (0x45020C, 0x4502D0, ["g_earlierMenuWords", "g_earlierOptionBits"])
    ]
    owned = L.owners(found)
    assert owned["g_earlierMenuWords1"] == owned["g_earlierOptionBits2"] == "0045020C"


def test_generated_names_carry_the_unused_marker_never_earlier(layout):
    from unbake import pointertype

    mark = L.marker(layout)
    name = pointertype.label(0x801CA224, mark)
    assert mark == "Leftover" and name == "g_addrTableLeftover_1CA224" and "earlier" not in name.lower()
    assert L.findings({name: "leftover/ENGINE/ADDRTABLE_1CA224.CPP"}, layout) == []
    assert L.findings({name: "SOURCE/ENGINE/ADDRTABLE_1CA224.CPP"}, layout) != []


def test_an_unread_unit_moves_to_its_unused_home_with_its_makefile_row(layout):
    from unbake import folders

    rules = folders.unused_rules([UNIT], layout)
    mapping, _ = folders.moves(rules, [UNIT, "SOURCE/MENU/MENU.CPP"])
    assert mapping == {UNIT: "leftover/DATA/OVERLAYS/NINE/RESOURCES/G_ARENACOMPATIBILITYTABLE.CPP"}
    text, count = folders.makefile_rules(f"CFLAGS.{UNIT} := -O1\n", [UNIT], mapping)
    assert count == 1 and text == f"CFLAGS.{mapping[UNIT]} := -O1\n"


def test_the_census_lists_placeholder_names_outside_the_unused_root_only(layout, unit):
    from unbake import symbol_names

    moved = "leftover/DATA/OVERLAYS/NINE/RESOURCES/G_ARENACOMPATIBILITYTABLE.CPP"
    found = symbol_names.census({UNIT: unit}, "f626ced59", layout)["placeholders"]
    assert found and all(row.startswith(UNIT + ": ") for row in found)
    assert symbol_names.census({moved: unit}, "f626ced59", layout)["placeholders"] == []


def test_the_rename_proof_replays_the_retired_and_merged_record(unit):
    from unbake import renameproof

    externs = sorted({n for n in L._IDENT.findall(unit) if n.endswith("PackedAddress242")})
    retired = L.literals({n: 0x80240000 + 4 * i for i, n in enumerate(externs)}, with_bytes=())
    unit = _adjacent_rows(unit)
    merged = L.tables(L.rows(unit))
    after = L.merge(L.retire(unit, retired), *next(iter(merged.items())))
    assert "ResidentEventHandlerEntry g_MenuEventHandlersPointerWordsTable[2][13]" in after
    record = L.rename_map(retired, merged)
    assert renameproof.reduction({UNIT: unit}, {UNIT: after}, record) == ()
    bad = after.replace("0x80240000", "0x80240001", 1)
    assert [f.path for f in renameproof.reduction({UNIT: unit}, {UNIT: bad}, record)] == [UNIT]
    with pytest.raises(ValueError):
        L.merge(
            (DATA / "G_ARENACOMPATIBILITYTABLE.CPP").read_text(),
            "g_t",
            list(merged["g_MenuEventHandlersPointerWordsTable"]),
        )


def test_the_region_census_gives_each_region_one_range_and_every_family(tmp_path):
    from types import SimpleNamespace

    (tmp_path / "versions" / "eu").mkdir(parents=True)
    (tmp_path / "versions" / "eu" / "earlier_build_symbols.txt").write_text(
        (DATA / "eu_earlier_build_symbols.txt").read_text()
    )
    cfg = SimpleNamespace(project=SimpleNamespace(root=tmp_path, versions=["eu", "de"], version={}))
    out = L.command(cfg, {"gap": "0x10"})
    assert list(out["versions"]) == ["eu"] and out["interleaved"] >= 1
    rows = out["versions"]["eu"]
    assert sum(len(r["names"]) for r in rows) == len(L.symbol_rows((DATA / "eu_earlier_build_symbols.txt").read_text()))
    assert all(int(r["start"], 16) < int(r["end"], 16) for r in rows)


def test_the_region_census_maps_each_region_to_its_window_rom_range():
    """The slice's own `rom:` comment (g at 0x450210 is ROM 0x149210) gives the eu window's address delta."""
    text = (DATA / "eu_earlier_build_symbols.txt").read_text()
    rows = L.symbol_rows(text)
    extent = (0x100000, 0x160000, 0x100000 + 0x307000)
    out = L.census({"eu": text}, 0x10, {"eu": [extent]})["versions"]["eu"]
    last = next(r for r in out if int(r["start"], 16) <= 0x450210 and int(r["end"], 16) > 0x450210)
    assert int(last["rom"][0], 16) == int(last["start"], 16) - 0x307000
    assert all("rom" in r for r in out if 0x407000 <= int(r["start"], 16) < 0x467000)
    assert sum(len(r["names"]) for r in out) == len(rows)
    assert L.rom_range(L.Region(0x10, 0x20), [extent]) is None


def test_stale_bytes_that_have_c_keep_it_in_the_unused_tree_not_a_bin(layout):
    """The 164 C units PkU8xkmKNkcr bb5c9d0d4 deleted for bins each move to the unused tree instead."""
    deleted = (DATA / "bins_deleted_c.txt").read_text().split()
    plan = L.keep_c(deleted, layout)
    assert len(plan) == 164 and all(to.startswith("leftover/") for to in plan.values())
    assert plan["SOURCE/ENGINE/EARLIER_ADDRTABLE2.CPP"] == "leftover/ENGINE/EARLIER_ADDRTABLE2.CPP"
    c = {"eu": [(0x1436DC, 0x143800)]}
    rows = {
        "engine_pools": {"eu": [(0x1436DC, 0x1437E0)], "eu-x": [(0x1436DC, 0x1437E0)]},
        "clear": {"eu": [(0x143800, 0x143900)]},
    }
    assert L.with_c(rows, c) == {"engine_pools": ["eu"]}


def test_blobs_land_refuses_a_bin_over_bytes_the_c_builds(monkeypatch):
    from types import SimpleNamespace

    from unbake import blobs, config

    monkeypatch.setattr(config, "layout", lambda project: {"unused_root": "leftover"})
    parts = {"eu": {"spans": {"code": [], "data": [[0x1436DC, 0x143800]]}}}
    packet = {
        "blob": {
            "engine_pools": {"rom": {"eu": [[0x1436DC, 0x1437E0]]}},
            "clear": {"rom": {"eu": [[0x150000, 0x150010]]}},
        }
    }
    cfg = SimpleNamespace(project=SimpleNamespace(root=Path("/nowhere"), layout={}))
    monkeypatch.setattr(blobs, "path_of", lambda config: "blobs.toml")
    found = blobs._c_findings(cfg, packet, parts)
    assert [f.unit for f in found] == ["engine_pools"] and "leftover/" in found[0].reason


def test_an_uppercase_rename_refuses_a_case_fold_collision():
    """The 123 lowercase SOURCE/include basenames at f626ced59 uppercase cleanly; SCENE's copy of
    sched_runeventloopus.CPP moved as SCHED/SCHED_RUNEVENTLOOPUS.CPP folds onto SCHED's copy, which stays."""
    from unbake import folders
    from unbake.contracts import Refusal

    low = (DATA / "lowercase_basenames.txt").read_text().split()
    upper = [{"match": p, "to": p.rsplit("/", 1)[0] + "/" + p.rsplit("/", 1)[1].upper()} for p in low]
    mapping, _ = folders.moves(upper, low)
    assert len(mapping) == 123
    clash = [
        {"match": "SOURCE/ENGINE/SCENE/sched_runeventloopus.CPP", "to": "SOURCE/ENGINE/SCHED/SCHED_RUNEVENTLOOPUS.CPP"}
    ]
    with pytest.raises(Refusal) as refused:
        folders.moves(clash, low)
    assert "SOURCE/ENGINE/SCHED/sched_runeventloopus.CPP, which stays" in str(refused.value.findings[0].reason)


def test_a_retired_symbol_loses_its_symbols_file_row(symbols):
    text = (DATA / "eu_earlier_build_symbols.txt").read_text()
    gone = sorted(symbols)[:3]
    out = L.forget(text, gone)
    assert set(L.symbol_rows(out)) == set(symbols) - set(gone)
    assert len(out.splitlines()) == len(text.splitlines()) - 3


def test_a_unit_moved_to_the_unused_tree_keeps_its_makefile_flags_row(layout):
    """RageWars' cheat makefile_row (config.toml at f626ced59) names SOURCE and sdk only; a FILE row moved with its
    unit to leftover/ is still a per-object row, so the flags row is kept, not flagged as a hand Makefile edit."""
    import re

    from unbake import folders

    pattern = r"^(FILE|CPPFLAGS|CFLAGS)\.(SOURCE|sdk)/"
    unit = "SOURCE/ENGINE/EARLIER_ADDRTABLE2.CPP"
    text = f"FILE.{unit} := -O2\n"
    moved, count = folders.makefile_rules(text, [unit], {unit: L.home(unit, layout)})
    assert count == 1 and moved == "FILE.leftover/ENGINE/EARLIER_ADDRTABLE2.CPP := -O2\n"
    assert not re.match(pattern, moved)
    rows = re.compile(L.row_pattern(pattern, layout))
    assert rows.match(moved) and rows.match(text) and not rows.match("all: leftover/ENGINE/X.CPP\n")


# Retool row 2: every root (source, header, unused, asm, build, roms) comes from [layout]; no generator spells one.

UPPER = {"asm_root": "ASM", "build_root": "BUILD", "roms_root": "ROMS", "header_roots": ["INCLUDE"]}


@pytest.fixture
def roots(layout):
    from unbake import config

    saved = dict(config._ROOTS)
    yield lambda rows: config.use_layout({**layout, **rows})
    config._ROOTS.clear()
    config._ROOTS.update(saved)


def test_every_build_root_comes_from_the_layout(layout, roots):
    """RageWars' [layout] (f626ced59) keeps the packaged asm/build/roms; an all-uppercase layout moves every one."""
    from unbake import config, renameproof, repo

    assert {k: layout[k] for k in ("asm_root", "build_root", "roms_root")} == {
        "asm_root": "asm",
        "build_root": "build",
        "roms_root": "roms",
    }
    roots({})
    assert (config.root("asm"), config.source_roots()[0], repo.header()) == ("asm", "SOURCE", "include/include_asm.h")
    roots(UPPER)
    assert [config.root(k) for k in config.BUILD_ROOTS] == ["ASM", "BUILD", "ROMS"]
    assert repo.header() == "INCLUDE/include_asm.h"
    assert {
        "SOURCE",
        "sdk",
        "include",
        "INCLUDE",
        "ASM",
        "ROMS",
        "tools",
    } == renameproof.build_tops()  # RageWars compiles include/


def test_no_generator_spells_a_root():
    """No pass, template rule or make helper holds a literal src/, asm/, build/ or roms/ path outside comments."""
    import re

    src = Path(__file__).parents[1] / "src" / "unbake"
    literal = re.compile(r"""(?<![\w.~$/-])f?["'](?:src|asm|build|roms)/|/ ["'](?:src|asm|build|roms)["']""")
    code = [p for p in src.rglob("*.py")]
    hits = [
        f"{p.name}:{i}"
        for p in code
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if literal.search(line)
        and not line.lstrip().startswith("#")
        and '"""' not in line
        and "Path.home()" not in line
    ]
    assert hits == []
    make = (src / "resources" / "templates" / "Makefile.in").read_text().splitlines()
    rules = [line for line in make if not line.lstrip().startswith("#")]
    assert not [r for r in rules if re.search(r"(?<![\w.$/-])(?:asm|build|roms)/|-Iinclude\b", r)]
    assert any(r.startswith("OBJECT_ARGS = $@ $(ASM)/") for r in rules)


def test_the_make_helpers_take_their_roots_from_the_makefile(tmp_path, monkeypatch):
    """asm_deps.py reads the source roots and writes the asm and build roots it is given, here RageWars' SOURCE."""
    import runpy
    import sys

    helper = Path(__file__).parents[1] / "src" / "unbake" / "resources" / "make" / "asm_deps.py"
    (tmp_path / "SOURCE" / "MENU").mkdir(parents=True)
    (tmp_path / "SOURCE" / "MENU" / "X.CPP").write_text('INCLUDE_ASM("menu", func_80001000);\n')
    (tmp_path / "ASM" / "eu" / "nonmatchings" / "menu").mkdir(parents=True)
    (tmp_path / "ASM" / "eu" / "nonmatchings" / "menu" / "func_80001000.s").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(helper), "ASM", "BUILD", "SOURCE,sdk", "deps", "eu"])
    runpy.run_path(str(helper), run_name="__main__")
    assert (tmp_path / "deps").read_text() == "BUILD/eu/SOURCE/MENU/X.CPP.o: ASM/eu/nonmatchings/menu/func_80001000.s\n"


# Retool row 8: filler words become one table named for where they sit (R2a).


@pytest.fixture
def inventory():
    """bGbz ddc602da6 SOURCE/DATA/OVERLAYS/TWO/OBJECTDATA/G_MENUINVENTORYDATA.CPP: four filler-word arrays named by
    ordinal (`PartTwoTrailingWords`, `PartSeventeenFirstExtraWords`) between the menu's data tables."""
    return (DATA / "G_MENUINVENTORYDATA.CPP").read_text()


def test_filler_words_are_named_for_the_table_they_trail(inventory):
    from unbake import pads

    assert pads.fillers(inventory) == {
        "g_menuInventoryDataTail": ["g_menuInventoryPartTwoTrailingWords"],
        "g_menuInventoryDataPart3Tail": ["g_menuInventoryPartThreeTrailingWord"],
        "g_menuSettingsDataTail": ["g_menuInventoryPartSeventeenFirstExtraWords"],
        "g_menuSettingsDataPart3Tail": ["g_menuInventoryPartSeventeenSecondExtraWords"],
    }


def test_a_run_of_filler_rows_becomes_one_table_with_the_same_words(inventory):
    """Two adjacent filler rows (the real Seventeen pair laid side by side) are one table: same words, same order."""
    import re

    from unbake import pads

    first = re.search(r"unsigned int g_menuInventoryPartSeventeenFirstExtraWords\[3\] = \{[^}]*\};\n", inventory)[0]
    second = re.search(r"unsigned int g_menuInventoryPartSeventeenSecondExtraWords\[2\] = \{[^}]*\};\n", inventory)[0]
    text = (
        "} g_menuSettingsData = {\n};\n\n"
        + first
        + "\n"
        + second
        + "\nvoid *use = g_menuInventoryPartSeventeenSecondExtraWords;\n"
    )
    out, merged = pads.name_fillers(text)
    assert merged == {
        "g_menuSettingsDataTail": [
            "g_menuInventoryPartSeventeenFirstExtraWords",
            "g_menuInventoryPartSeventeenSecondExtraWords",
        ]
    }
    words = lambda t: re.findall(r"0x[0-9A-F]{8}", t)  # noqa: E731
    assert words(out) == words(text)
    assert "unsigned int g_menuSettingsDataTail[5] = {" in out and "use = (&g_menuSettingsDataTail[3]);" in out
    assert "Seventeen" not in out
