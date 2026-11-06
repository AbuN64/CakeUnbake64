"""The width census over a slice copied verbatim (file:line kept) from the history of a fork of a decomp, and over the
pure parts: element widths, assembly data labels, object relocations, the alias join."""

from __future__ import annotations

import json
import struct
import types
from pathlib import Path

from unbake import typecensus, typeunify, widthcensus
from unbake.typeunify import Seams
from unbake.widthcensus import Evidence

SLICE = json.loads((Path(__file__).parent / "data/widthcensus/slice.json").read_text())
KEEP = lambda f: f.startswith(("code/", "include/"))  # noqa: E731
TYPES = "typedef unsigned int u32;\ntypedef int s32;\ntypedef short s16;\n"
READ = (  # a function of extracted assembly reading the record's first halfword
    "glabel Weaponai_Read\n/* 0 80000000 3C018010 */ lui $at, %hi(g_weaponActionRecords)\n"
    "/* 4 80000004 84220000 */ lh $v0, %lo(g_weaponActionRecords)($at)\n/* 8 80000008 03E00008 */ jr $ra\n"
    "/* C 8000000C 00000000 */ nop\nendlabel Weaponai_Read\n"
)
STORED = {"g_weaponActionRecords": 0x800CE8DC, "g_weaponActionRecords_StorageWords": 0x800CE8DC}
STORED |= {"k_Ovldat9EncodedWords": 0x800E5320, "g_scenarioResultRowWidgetIds": 0x800E5320}


def names(rev: str) -> dict[str, dict]:
    return SLICE[rev]["names"]


def unit_text(rev: str, skip: tuple[str, ...] = ()) -> str:
    """The preprocessed text of the slice at a revision: every line under a marker holding its own file:line."""
    out = [f'# 1 "include/types.H"\n{TYPES}']
    for name, entry in names(rev).items():
        for f, n, text in (*entry["definition"], *entry["externs"]):
            if name in skip and text.startswith("extern"):
                continue
            body = text + " 0 };" if text.endswith("{") else text
            out.append(f'# {n} "{f}"\n{body}\n')
    return "".join(out)


def seams(rev: str, skip: tuple[str, ...] = ()) -> Seams:
    text = unit_text(rev, skip)
    units = {"us-rev1": ["code/u.CPP"]}
    return Seams(lambda: units, lambda items: [typeunify.facts(text, u, KEEP) for _v, u in items], lambda _t: (0, ""))


def evidence(asm: str = "") -> Evidence:
    table = {n: (STORED[n], None, None, "u32") for n in STORED}
    return Evidence(lambda _v: table, lambda _v: [("asm/x.s", asm)], lambda _v: [])


def run(rev: str, asm: str = "", skip: tuple[str, ...] = ()) -> dict[str, dict[str, str]]:
    real = seams(rev, skip)
    census = typeunify.collect(real.units(), real.read)
    return {r["name"]: r for r in widthcensus.rows(census.per, evidence(asm), real.units())}


def test_weapon_records_were_a_word_read_as_a_halfword() -> None:
    got = run("359dc614^", READ)["g_weaponActionRecords"]
    assert got["status"] == "other-width" and got["def_width"] == "4"
    assert "unsigned int 4 => code/game/src/DATA/PLAYER_STATE_NORMAL/PLAYER_STATE_NORMAL.CPP:9" in got["definition"]
    assert "code/game/src/AI/WEAPON/WEAPON.CPP:405 ext" in got["readers"]
    assert got["loads"] == "2 => Weaponai_Read" and got["versions"] == "us-rev1"


def test_weapon_records_written_as_halfwords_are_no_row() -> None:
    # the struct extern is left out: the tool cannot size a struct, so a struct reader of a scalar table stays a row
    got = run("359dc614", READ, skip=("g_weaponActionRecords_StorageWords",))["g_weaponActionRecords"]
    assert got["status"] == "same-width" and got["def_width"] == "2" and got["loads"] == "2 => Weaponai_Read"
    records = list(run("359dc614", READ).values())
    assert [r["name"] for r in records if r["status"] != "same-width"] == []


def test_results_row_ids_are_a_word_table_read_as_halfword_ids() -> None:
    got = run("f3e0e01c^")["g_scenarioResultRowWidgetIds"]
    assert got["status"] == "other-width" and "ResourceBank" in got["readers"]
    assert got["definition"].startswith("unsigned int 4 =>") and got["def_width"] == "4"


def test_every_suspect_of_the_sweep_is_other_width_and_a_bare_table_unread() -> None:
    got = run("359dc614^", READ)
    suspects = [n for n, e in names("359dc614^").items() if e["definition"] and e["externs"] and "weapon" not in n]
    assert len(suspects) == 27  # the 22 extern char and 5 struct arrays of the run-24 sweep list
    assert {n: got[n]["status"] for n in suspects} == {n: "other-width" for n in suspects}
    assert got["g_GMenuboxcornersLevelTextWords1"]["status"] == "unread"


def test_same_width_is_no_row() -> None:
    per = {"t": {"v": [("a.c:1", "vdef", "unsigned int[4]", 1), ("b.c:2", "ext", "unsigned int[]", 1)]}}
    records = widthcensus.rows(per, Evidence(lambda _v: {}, lambda _v: [], lambda _v: []), ["v"])
    assert [r["status"] for r in records] == ["same-width"]
    lines = widthcensus.table(records).splitlines()
    assert lines == [widthcensus.HEADER.rstrip("\n")] and widthcensus.counts(records)["same-width"] == 1


def test_element_width_follows_the_target_abi() -> None:
    sized = {t: widthcensus.element_width(t)[1] for t in ("char[8]", "unsigned short[]", "int", "long[2]", "float[]")}
    assert list(sized.values()) == [1, 2, 4, 4, 4]
    assert widthcensus.element_width("double[3]")[1] == 8 and widthcensus.element_width("char*[3]")[1] == 4
    assert widthcensus.element_width("unsigned char[2][3]")[1] == 3
    assert widthcensus.element_width("struct Foo[]")[1] == "struct Foo"


def test_assembly_data_labels_have_the_width_of_their_directive() -> None:
    text = "glabel a\n/* 1 2 3 */ .word 0x1\nglabel b\n.half 1\nglabel f\n/* 1 2 3 */ lw $t0, 0($a0)\n.L1:\n.byte 1\n"
    assert widthcensus.data_defs(text) == [("a", 4, 1), ("b", 2, 3)]


def elf(code: bytes, symbols: list[tuple[str, int, int, int, int]], rels: list[tuple[int, int, int]]) -> bytes:
    """A big-endian ELF32 relocatable: .text, .symtab, .strtab, .rel.text, .shstrtab."""
    strtab, at = b"\0", []
    for s in symbols:
        at.append(len(strtab))
        strtab += s[0].encode() + b"\0"
    sym = b"\0" * 16 + b"".join(
        struct.pack(">3IBBH", a, v, z, k, 0, x) for a, (_n, v, z, k, x) in zip(at, symbols, strict=True)
    )
    rel = b"".join(struct.pack(">II", where, (s << 8) | t) for where, s, t in rels)
    blobs, body, positions = [code, sym, strtab, rel, b"\0"], b"", []
    for blob in blobs:
        positions.append(52 + len(body))
        body += blob
    kinds = [(1, 0, 0), (2, 3, 1), (3, 0, 0), (9, 2, 1), (3, 0, 0)]  # type, link, info
    sections = [(0, 0, 0, 0, 0)] + [(k, positions[i], len(blobs[i]), ln, inf) for i, (k, ln, inf) in enumerate(kinds)]
    table = b"".join(struct.pack(">10I", 0, k, 0, 0, o, z, ln, i, 0, 0) for k, o, z, ln, i in sections)
    ident = b"\x7fELF\x01\x02\x01" + b"\0" * 9
    head = struct.pack(">HHIIIIIHHHHHH", 1, 8, 1, 0, 0, 52 + len(body), 0, 52, 0, 0, 40, len(sections), 5)
    return ident + head + body + table


def test_object_relocations_give_the_load_width_of_the_reading_function() -> None:
    code = struct.pack(">3I", 0x3C010000, 0x84220000, 0x24420000)  # lui, lh, addiu
    data = elf(code, [("g_table", 0, 0, 1, 0), ("Reader", 0, 12, 2, 1)], [(0, 1, 5), (4, 1, 6), (8, 1, 6)])
    assert widthcensus.object_loads(data) == [("g_table", 2, "Reader")]
    assert widthcensus.object_loads(b"not an object") == []


def test_command_writes_the_width_table_and_counts_beside_the_clash_table(tmp_path: Path) -> None:
    cfg = types.SimpleNamespace(project=types.SimpleNamespace(root=tmp_path))
    out, widths = tmp_path / "CLASH.tsv", tmp_path / "WIDTH.tsv"
    params = {"out": str(out), "widths": str(widths)}
    got = typeunify.census_command(cfg, params, seams("359dc614^"), evidence(READ))  # type: ignore[arg-type]
    rows = widths.read_text().splitlines()
    counts = got["width_counts"]
    assert rows[0] == widthcensus.HEADER.rstrip("\n") and out.read_text().startswith(typecensus.HEADER.rstrip("\n"))
    assert counts["other-width"] + counts["unread"] == len(rows) - 1 and counts["unread"] >= 1
    assert got["widths"] == str(widths)
