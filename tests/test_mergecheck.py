"""Which merges must stay separate files: the pure rule over plain link-order facts."""

from __future__ import annotations

from typing import Any

import pytest

from unbake import mergecheck


def entry(unit: str, cls: str, size: int, out: str = ".text") -> dict[str, Any]:
    return {"o": out, "k": "entry", "u": unit, "cls": cls, "n": size}


def align(n: int, out: str = ".rodata") -> dict[str, Any]:
    return {"o": out, "k": "align", "a": n}


def run(rows, seq, statics=None, identity=lambda unit: "cc"):
    return {v["module"]: v for v in mergecheck.verdicts(rows, {"us": seq}, statics or {}, identity)}


def test_adjacent_units_in_map_order_merge() -> None:
    rows = [("a", "M"), ("b", "M"), ("c", "N")]
    seq = [entry("a", "text", 8), entry("b", "text", 8), entry("c", "text", 8)]
    assert run(rows, seq) == {"M": {"module": "M", "units": 2, "verdict": "merge", "hard": [], "soft": [], "parts": {}}}


def test_another_modules_bytes_between_two_units_keep_them_separate() -> None:
    rows = [("a", "M"), ("x", "N"), ("b", "M")]
    seq = [entry("a", "text", 8), entry("x", "text", 4), entry("b", "text", 8)]
    out = run(rows, seq)["M"]
    assert out["verdict"] == "separate" and out["hard"] == ["gap: us text a .. b has 1 between: x"]


def test_a_gap_names_every_unit_between_once_not_the_first_three() -> None:
    """SMP_OVERLAY: ~35 other modules sit between two units; a unit with several runs counts once."""
    rows = [("a", "M"), ("b", "M")]
    between = ["u1", "u2", "u3", "u4", "u5"]
    seq = [
        entry("a", "text", 8),
        *(entry(u, "text", 4) for u in between),
        entry("u2", "text", 4),
        entry("b", "text", 8),
    ]
    out = run(rows, seq)["M"]
    assert out["hard"] == ["gap: us text a .. b has 5 between: u1, u2, u3, u4, u5"]


def test_an_empty_entry_between_does_not_break_adjacency() -> None:
    rows = [("a", "M"), ("x", "N"), ("b", "M")]
    seq = [entry("a", "text", 8), entry("x", "text", 0), entry("b", "text", 8)]
    assert run(rows, seq)["M"]["verdict"] == "merge"


def test_units_linked_in_another_order_than_the_map_merge_class_wise_but_other_flags_stay_separate() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [entry("b", "text", 8), entry("a", "text", 8)]
    assert run(rows, seq)["M"]["hard"] == []
    out = run(rows, [entry("a", "text", 8), entry("b", "text", 8)], identity=lambda unit: unit)["M"]
    assert out["verdict"] == "separate" and out["hard"][0].startswith("flags:")


def test_a_class_in_two_output_sections_stays_separate() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [entry("a", "text", 8, ".text"), entry("b", "text", 8, ".boot")]
    assert run(rows, seq)["M"]["hard"] == [
        "sections: text parts: 2: .text [a]; .boot [b]",
        "sections: us text sits in several output sections",
    ]


def test_padding_statics_and_literals_are_soft_findings() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [entry("a", "rodata", 4, ".rodata"), align(8), entry("b", "rodata", 4, ".rodata")]
    out = run(rows, seq, {"a": {"helper"}, "b": {"helper", "other"}})["M"]
    assert out["verdict"] == "merge"
    assert out["soft"] == [
        "align: us rodata ALIGN(8) before b",
        "literals: us rodata in 2 units",
        "statics: helper is local to a and b",
    ]


def test_single_unit_modules_have_no_verdict() -> None:
    assert run([("a", "M"), ("b", "N")], [entry("a", "text", 8)]) == {}


def test_unplaced_audio_microcode_cannot_be_activated_by_merging_into_linked_data():
    base = "src/game/ENGINE/AUDIO/AUDIOMGR/AUDIOMGR_INIT/"
    units = [base + f"AUDIOMGR_INIT_{n}.CPP" for n in (1, 2, 3)]
    rows = [(u, "audio") for u in units]
    seqs = {v: [entry(units[1 if v == "us-rev1" else 0], "data", 8)] for v in ("us", "us-rev1", "de", "eu", "eu-x")}
    result = mergecheck.verdicts(rows, seqs, {}, lambda _: "cc")[0]
    assert result["hard"] == [f"unplaced: {units[2]} has no linker input in any version"]
    assert result["verdict"] == "separate"
    assert mergecheck.verdicts(rows[:2], seqs, {}, lambda _: "cc")[0]["hard"] == []


LINK = """\
SECTIONS {
 .text 0x80000400 : AT(0x400) SUBALIGN(1)
 {
  src/a.o(.text);
  . = ALIGN(8);
  src/b.o(.text .data);
 }
 .rodata : AT(0x10)
 {
  src/a.o(.rodata .rdata);
 }
}
"""


def test_the_link_script_rows_keep_their_order_output_section_and_classes() -> None:
    rows = mergecheck.parse_link(LINK)
    assert [(r["k"], r.get("u"), r["o"]) for r in rows] == [
        ("entry", "src/a", ".text"),
        ("align", None, ".text"),
        ("entry", "src/b", ".text"),
        ("entry", "src/a", ".rodata"),
    ]
    assert rows[2]["cls"] == ("data", "text") and rows[3]["cls"] == ("rodata",)


def test_a_module_map_needs_unit_and_module_columns() -> None:
    assert mergecheck.read_modules("module\tunit\tx\nM\ta\t1\n# c\n\nM\tb\t2\n") == [("a", "M"), ("b", "M")]
    with pytest.raises(ValueError):
        mergecheck.read_modules("a\tb\n1\t2\n")


def piece(size: int, align: int) -> list[tuple[int, int]]:
    return [(size, align)]


def with_parts(row: dict[str, Any], **parts: list[tuple[int, int]]) -> dict[str, Any]:
    return {**row, "parts": parts}


def test_pieces_that_pack_longer_in_one_object_are_a_hard_lma_finding() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [
        with_parts(entry("a", "rodata", 2, ".rodata"), data=piece(1, 1), rodata=piece(1, 1)),
        with_parts(entry("b", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
    ]
    out = run(rows, seq)["M"]
    assert out["verdict"] == "separate"
    assert out["hard"] == ["lma: us .rodata merged 12 B against linked 8 B: overlaps the next section"]


def verdict_of(rows, seqs, module="M"):
    return {v["module"]: v for v in mergecheck.verdicts(rows, seqs, {}, lambda unit: "cc")}[module]


def test_a_class_linked_in_another_unit_order_in_another_version_is_a_soft_order_finding() -> None:
    rows = [("a", "M"), ("b", "M")]
    seqs = {"us": [entry("a", "text", 8), entry("b", "text", 8)], "eu": [entry("b", "text", 8), entry("a", "text", 8)]}
    out = verdict_of(rows, seqs)
    assert out["verdict"] == "merge"
    assert out["hard"] == []
    assert out["soft"] == ["order: eu text links b elsewhere than the other versions"]


def test_each_class_may_be_linked_in_its_own_unit_order() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [entry("a", "text", 8), entry("b", "text", 8), entry("b", "data", 4, ".data"), entry("a", "data", 4, ".data")]
    assert run(rows, seq)["M"]["hard"] == []
    assert mergecheck.class_orders(["a", "b"], {"us": seq}) == {"text": {"us": ["a", "b"]}, "data": {"us": ["b", "a"]}}


def test_a_version_that_splits_a_class_over_sections_still_reports_its_order() -> None:
    rows = [("a", "M"), ("b", "M")]
    seqs = {
        "us": [entry("a", "text", 8), entry("b", "text", 8)],
        "eu": [entry("b", "text", 8), entry("a", "text", 8, ".other")],
    }
    out = verdict_of(rows, seqs)
    assert out["hard"] == [
        "sections: eu text sits in several output sections",
        "sections: text parts: 2: us .text, eu .other [a]; .text [b]",
    ]
    assert out["soft"] == ["order: eu text links b elsewhere than the other versions"]


def test_a_unit_only_a_later_version_places_is_ordered_in_its_own_sequence_not_by_an_index_of_another() -> None:
    others = [entry(f"k{i}", "text", 8) for i in range(3)]
    seqs = {
        "us": [entry("a", "text", 8), *others, entry("b", "text", 8)],
        "eu": [entry("a", "text", 8), entry("b", "text", 8), entry("n", "text", 8)],
    }
    assert mergecheck.code_order(["a", "b", "n"], seqs) == ["a", "b", "n"]
    assert verdict_of([("a", "M"), ("b", "M"), ("n", "M")], seqs)["soft"] == []  # no order finding either


def test_a_unit_that_one_version_links_two_places_earlier_is_the_only_moved_unit() -> None:
    # the real rows: ACHIEVE_CREATERESULTSSCREEN.CPP is unit 10 in one version and 8 in another
    names = [f"u{i:02d}" for i in range(12)]
    shifted = [*names[:8], names[10], *names[8:10], names[11]]
    seqs = {
        "de": [entry(u, "rodata", 4, ".rodata") for u in names],
        "eu": [entry(u, "rodata", 4, ".rodata") for u in shifted],
    }
    out = verdict_of([(u, "M") for u in names], seqs)
    assert out["hard"] == []
    assert [r for r in out["soft"] if r.startswith("order")] == [
        "order: eu rodata links u10 elsewhere than the other versions"
    ]


def test_an_align_directive_inside_the_span_is_a_zero_array_of_the_merged_source_not_a_fill() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [
        with_parts(entry("a", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
        align(8),
        with_parts(entry("b", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
    ]
    out = run(rows, seq)["M"]
    assert out["hard"] == []
    assert not [s for s in out["soft"] if s.startswith("fill")]
    assert mergecheck.interior_fills(["a", "b"], {"us": seq}) == [
        {"after": "a", "cls": "rodata", "data": bytes(4), "versions": ["us"]}
    ]


def test_pieces_that_pack_the_same_merge() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [
        with_parts(entry("a", "rodata", 8, ".rodata"), rodata=piece(8, 8)),
        with_parts(entry("b", "rodata", 8, ".rodata"), rodata=piece(8, 8)),
    ]
    assert run(rows, seq)["M"]["verdict"] == "merge"


def test_spans_count_align_directives_but_not_the_bytes_of_other_modules_between_units() -> None:
    seq = [
        with_parts(entry("a", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
        align(8),
        with_parts(entry("x", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
        with_parts(entry("b", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
    ]
    assert mergecheck.spans(seq, {"a", "b"}) == [(".rodata", 12, 12, 0)]


def test_units_are_ordered_by_where_their_code_is_linked_not_their_first_data() -> None:
    seq = [entry("a", "rodata", 8, ".rodata"), entry("b", "text", 8), entry("a", "text", 8), entry("c", "data", 8)]
    assert mergecheck.code_order(["a", "b", "c"], {"us": seq}) == ["b", "a", "c"]


def test_merged_units_leave_the_module_map_as_one_row_of_the_merged_file() -> None:
    text = "unit\tmodule\tkind\nA/a.CPP\tA/M.CPP\tcode\nB/b.CPP\tB/N.CPP\tcode\nA/a_2.CPP\tA/M.CPP\tdata\n#c\n"
    got = mergecheck.merged_modules(text, {"A/a.CPP": "A/M/M.CPP", "A/a_2.CPP": "A/M/M.CPP"})
    assert got == "unit\tmodule\tkind\nA/M/M.CPP\tA/M.CPP\tcode\nB/b.CPP\tB/N.CPP\tcode\n#c\n"
    assert mergecheck.merged_modules(got, {"A/M/M.CPP": "A/M/M.CPP"}) == got


def test_a_merged_file_sits_in_the_module_folder_under_the_module_name() -> None:
    assert mergecheck.merged_path("src/game/AI/NAV/PICKUP.CPP") == "src/game/AI/NAV/PICKUP/PICKUP.CPP"


def test_object_facts_read_the_elf_once_and_keep_sizes_alignments_and_static_names(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    class Section(dict):
        def __init__(self, name, size, alignment):
            super().__init__(sh_size=size, sh_addralign=alignment)
            self.name = name

    class Symbol(dict):
        def __init__(self, name, bind, kind):
            super().__init__(st_info={"bind": bind, "type": kind}, st_shndx=1)
            self.name = name

    class Table:
        def iter_symbols(self):
            return iter(
                [
                    Symbol("helper", "STB_LOCAL", "STT_FUNC"),
                    Symbol("state", "STB_LOCAL", "STT_OBJECT"),
                    Symbol("exported", "STB_GLOBAL", "STT_FUNC"),
                    Symbol("label", "STB_LOCAL", "STT_NOTYPE"),
                ]
            )

    sections = [Section(".text", 24, 4), Section(".data", 8, 0), Section(".rdata", 4, 8), Section(".bss", 0, 4)]
    elf = SimpleNamespace(iter_sections=lambda: iter(sections), get_section_by_name=lambda _: Table())
    read = Mock(return_value=elf)
    monkeypatch.setattr(mergecheck, "ELFFile", read)
    monkeypatch.setattr(mergecheck, "SymbolTableSection", Table)
    path = tmp_path / "object.o"
    path.write_bytes(b"fixture")
    assert mergecheck.object_facts(path) == (
        {".text": 24, ".data": 8, ".rdata": 4, ".bss": 0},
        {"helper", "state"},
        {".text": [(24, 4)], ".data": [(8, 1)], ".rdata": [(4, 8)]},
        {"helper", "state", "exported", "label"},
    )
    read.assert_called_once()


def test_fact_scan_reuses_one_read_for_multiple_sections_of_same_object(tmp_path, monkeypatch):
    from unittest.mock import Mock

    (tmp_path / "us.ld").write_text(LINK)
    for name in ("a", "b"):
        path = tmp_path / f"src/{name}.o"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"fixture")
    read = Mock(return_value=({".text": 8, ".data": 4, ".rodata": 4}, {"local"}, {".rodata": [(4, 4)]}, set()))
    monkeypatch.setattr(mergecheck, "object_facts", read)
    seqs, statics = mergecheck.facts_of(tmp_path, ["us"], "{version}.ld", "{unit}.o")
    assert read.call_count == 2
    assert statics == {"src/a": {"local"}, "src/b": {"local"}}
    assert [e["n"] for e in seqs["us"] if e["k"] == "entry"] == [8, 12, 4]


def test_original_linker_minimum_offsets_keep_internal_holes_in_real_data_slice():
    # Six original US-rev1 table fragments: a 32-byte hole after the second piece is a linker constraint, not source
    # padding that disappears in one object: the merged source holds it as a zero array.
    offsets = [0x923C, 0x9248, 0x9274, 0x9280, 0x929C, 0x92A8]
    sizes = [12, 12, 12, 28, 12, 12]
    text = ".data_800C 0x800C6AB0 : AT(0xC76B0) SUBALIGN(1)\n{\n"
    text += "\n".join(f". = MAX(., 0x{o:X});\nu{i}.o(.data);" for i, o in enumerate(offsets))
    seq = mergecheck.parse_link(text + "\n}")
    for row in seq:
        if row["k"] == "entry":
            size = sizes[int(row["u"][1:])]
            row.update(n=size, cls="data", parts={"data": [(size, 4)]})
    rows = [(f"u{i}", "tables") for i in range(6)]
    assert mergecheck.spans(seq, {u for u, _ in rows}) == [(".data_800C", 120, 120, 0x923C)]
    out = run(rows, seq)["tables"]
    assert out["hard"] == []
    assert not [s for s in out["soft"] if s.startswith("fill")]
    # the 32 B hole after the second piece is a zero array of the merged source: the bytes after it stay in place
    assert mergecheck.interior_fills([u for u, _ in rows], {"us": seq}) == [
        {"after": "u1", "cls": "data", "data": bytes(32), "versions": ["us"]}
    ]


def test_merged_span_pads_pieces_to_their_own_alignment_under_subalign() -> None:
    """sndplayer: a 62 B (align 8) and an 8 B (align 4) rodata piece link 2 B apart under SUBALIGN(1); in one object the
    compiler pads the second to 4, so the object is 72 B + 8 B of data, the 80 B the link spans."""
    seq = [
        {**with_parts(entry("a", "rodata", 62, ".data"), rodata=piece(62, 8)), "subalign": 1},
        {"o": ".data", "k": "minimum", "offset": 64},
        {**with_parts(entry("b", "rodata", 8, ".data"), rodata=piece(8, 4)), "subalign": 1},
        {"o": ".data", "k": "minimum", "offset": 72},
        {**with_parts(entry("b", "data", 8, ".data"), data=piece(8, 16)), "subalign": 1},
    ]
    assert mergecheck.spans(seq, {"a", "b"}) == [(".data", 80, 80, 0)]


def test_a_merged_class_aligns_where_the_link_places_it_not_from_the_module_start() -> None:
    """A module that starts 4 B into its output section: its 16-aligned data lands 12 B after the module's first byte,
    so the object ends at 24 B and not 12 B; a fill row written at 8 B made ld refuse the link (location counter
    backwards, 12 to 16 B short)."""
    seq = [
        {**with_parts(entry("x", "text", 4), text=piece(4, 4))},
        {**with_parts(entry("a", "data", 8), data=piece(8, 16))},
        {**with_parts(entry("b", "data", 4), data=piece(4, 4))},
    ]
    assert mergecheck.spans(seq, {"a", "b"}) == [(".text", 24, 24, 4)]
    built = {".data": [(8, 16), (4, 4)]}
    assert mergecheck.spans(seq, {"a", "b"}, built) == [(".text", 24, 24, 4)]


def test_a_class_the_link_pinned_starts_no_earlier_in_the_merged_object() -> None:
    """de pirawread.c (.game_data 0x5FB0): 18 B of rodata, a pin at 0x5FC3, 13 B of data. The merged object's data
    still starts at the pin, so the object ends at 0x5FD0 and not 0x5FCF; a fill row written at 0x5FCF made ld refuse
    the link (800c7990 -> 800c798f)."""
    seq = [
        {"o": ".game_data", "k": "minimum", "offset": 0x5FB0},
        {**with_parts(entry("a", "rodata", 18, ".game_data"), rodata=piece(18, 1)), "subalign": 1},
        {"o": ".game_data", "k": "minimum", "offset": 0x5FC3},
        {**with_parts(entry("b", "data", 13, ".game_data"), data=piece(13, 1)), "subalign": 1},
    ]
    built = {".rdata": [(18, 1)], ".data": [(13, 16)]}
    assert mergecheck.spans(seq, {"a", "b"}) == [(".game_data", 32, 32, 0x5FB0)]
    assert mergecheck.spans(seq, {"a", "b"}, built) == [(".game_data", 32, 32, 0x5FB0)]


def test_minimum_does_not_move_back_or_count_prefix_space_as_module_bytes():
    seq = [
        {"o": ".data", "k": "minimum", "offset": 100},
        with_parts(entry("a", "data", 8, ".data"), data=piece(8, 1)),
        {"o": ".data", "k": "minimum", "offset": 104},
        with_parts(entry("b", "data", 8, ".data"), data=piece(8, 1)),
    ]
    assert mergecheck.spans(seq, {"a", "b"}) == [(".data", 16, 16, 100)]


def test_named_sections_are_counted_once_and_conditional_align_uses_actual_definitions(tmp_path, monkeypatch):
    from unittest.mock import Mock

    (tmp_path / "us.ld").write_text(
        ".rodata : SUBALIGN(1)\n{\na.o(.rodata.first);\n"
        ". = DEFINED(yes) ? ALIGN(8) : .;\na.o(.rodata.second);\n"
        ". = DEFINED(no) ? ALIGN(8) : .;\nb.o(.rodata);\n}\n"
    )
    for unit in ("a", "b"):
        (tmp_path / f"{unit}.o").write_bytes(b"fixture")
    read = Mock(
        side_effect=[
            (
                {".rodata.first": 3, ".rodata.second": 4},
                set(),
                {".rodata.first": [(3, 4)], ".rodata.second": [(4, 4)]},
                {"yes"},
            ),
            ({".rodata": 4}, set(), {".rodata": [(4, 4)]}, set()),
        ]
    )
    monkeypatch.setattr(mergecheck, "object_facts", read)
    seqs, _ = mergecheck.facts_of(tmp_path, ["us"], "{version}.ld", "{unit}.o")
    seq = seqs["us"]
    assert [r["n"] for r in seq if r["k"] == "entry"] == [3, 4, 4]
    assert [r["symbol"] for r in seq if "symbol" in r] == ["yes"]
    assert mergecheck.spans(seq, {"a", "b"}) == [(".rodata", 16, 16, 0)]
    assert read.call_count == 2


def test_missing_selected_object_is_not_an_empty_safe_member(tmp_path):
    (tmp_path / "us.ld").write_text(".text :\n{\na.o(.text);\nb.o(.text);\n}\n")
    seqs, statics = mergecheck.facts_of(tmp_path, ["us"], "{version}.ld", "{unit}.o")
    result = mergecheck.verdicts([("a", "M"), ("b", "M")], seqs, statics, lambda _: "cc")[0]
    assert result["verdict"] == "separate"
    assert result["hard"] == ["object: us a has no built object", "object: us b has no built object"]


def _linked(order: list[str], cls: str) -> list[dict[str, Any]]:
    return [{"o": ".game_data", "k": "entry", "u": u, "cls": cls, "n": 4} for u in order]


def _filled(size: int, unit: str, at: int) -> list[str]:
    names = [f"u{i}" for i in range(size - 1)]
    names.insert(at, unit)
    return names


@pytest.mark.parametrize(
    ("cls", "unit", "base_at", "moved_at", "size"),
    [
        ("rodata", "ACHIEVE_CREATERESULTSSCREEN.CPP", 10, 8, 40),  # PAKINTERFACESCREEN eu and eu-x
        ("text", "NODEPAIR.CPP", 63, 54, 151),  # PAKINTERFACESCREEN eu-x
        ("text", "FRONTEND_SELECTNEXTJOINMODEL.CPP", 0, 3, 30),  # src/MENU.CPP us
    ],
)
def test_order_real_rows_one_unit_sits_elsewhere_in_one_version(
    cls: str, unit: str, base_at: int, moved_at: int, size: int
) -> None:
    base = _filled(size, unit, base_at)
    other = [u for u in base if u != unit]
    other.insert(moved_at, unit)
    found = mergecheck.verdicts(
        [(u, "M") for u in base], {"de": _linked(base, cls), "eu": _linked(other, cls)}, {}, lambda u: "cc"
    )
    assert found[0]["hard"] == []
    assert [r for r in found[0]["soft"] if r.startswith("order")] == [
        f"order: eu {cls} links {unit} elsewhere than the other versions"
    ]


def test_order_real_rows_us_rev1_swaps_the_two_common_units_of_volume() -> None:
    # src/ENGINE/VOLUME.CPP us-rev1 rodata: .game_data_E1720 links the two common units the other way round
    base = ["VOLUME_A.CPP", "VOLUME_B.CPP"]
    found = mergecheck.verdicts(
        [(u, "M") for u in base],
        {"de": _linked(base, "rodata"), "us-rev1": _linked(base[::-1], "rodata")},
        {},
        lambda u: "cc",
    )
    assert found[0]["hard"] == []
    assert len([r for r in found[0]["soft"] if r.startswith("order")]) == 1


def test_order_real_rows_a_unit_only_the_later_version_places_follows_its_neighbour_in_that_version() -> None:
    # PAKINTERFACESCREEN us-rev1 rodata holds 55 units, de 43: the units only us-rev1 places are not ordered by de
    de = [f"u{i}" for i in range(43)]
    us = [*de[:27], "extra1", "extra2", *de[27:]]
    assert mergecheck.code_order([*us], {"de": _linked(de, "rodata"), "us-rev1": _linked(us, "rodata")}, "rodata") == us


def _parts(rows, seqs, cls, module="M"):
    return verdict_of(rows, seqs, module)["parts"][cls]


def test_sections_parts_real_rows_pakinterfacescreen_us_text_has_108_units_in_the_overlay_and_43_resident() -> None:
    overlay = [f"ovl{i:03}" for i in range(108)]
    resident = [f"res{i:02}" for i in range(43)]
    rows = [(u, "M") for u in overlay + resident]
    seq = [entry(u, "text", 8, ".ovl_frontendMenu") for u in overlay] + [
        entry(u, "text", 8, ".game_data") for u in resident
    ]
    out = verdict_of(rows, {"us": seq})
    assert out["verdict"] == "separate"
    assert "sections: text parts: 2: .ovl_frontendMenu [ovl000, ovl001" in out["hard"][0] + out["hard"][1]
    assert [(p["sections"], len(p["units"])) for p in out["parts"]["text"]] == [
        ({"us": ".ovl_frontendMenu"}, 108),
        ({"us": ".game_data"}, 43),
    ]
    assert out["parts"]["text"][1]["units"] == resident


def test_sections_parts_real_rows_io_hostmemory_data_keeps_the_code_table_apart_from_libsn_data() -> None:
    rows = [("K_MAINCODEENCODEDWORDS", "M"), ("libsn_data", "M")]
    seqs = {
        v: [entry("K_MAINCODEENCODEDWORDS", "data", 64, ".game_code"), entry("libsn_data", "data", 16, ".game_data")]
        for v in ("de", "eu", "eu-x", "us", "us-rev1")
    }
    found = _parts(rows, seqs, "data")
    assert [p["units"] for p in found] == [["K_MAINCODEENCODEDWORDS"], ["libsn_data"]]
    assert found[0]["sections"] == dict.fromkeys(seqs, ".game_code")
    assert found[1]["sections"] == dict.fromkeys(seqs, ".game_data")
    line = next(h for h in verdict_of(rows, seqs)["hard"] if "parts" in h)
    assert line == "sections: data parts: 2: .game_code [K_MAINCODEENCODEDWORDS]; .game_data [libsn_data]"


def test_sections_parts_a_unit_one_version_places_apart_forms_its_own_part_with_each_versions_section() -> None:
    rows = [(u, "M") for u in "abcd"]
    seqs = {
        "us": [entry(u, "text", 8, ".game_data") for u in "abcd"],
        "us-rev1": [entry("a", "text", 8, ".game_data"), entry("b", "text", 8, ".game_data_E1720")]
        + [entry(u, "text", 8, ".game_data") for u in "cd"],
    }
    found = _parts(rows, seqs, "text")
    assert [p["units"] for p in found] == [["a", "c", "d"], ["b"]]
    assert found[1]["sections"] == {"us": ".game_data", "us-rev1": ".game_data_E1720"}
    line = next(h for h in verdict_of(rows, seqs)["hard"] if "parts" in h)
    assert line == "sections: text parts: 2: .game_data [a, c, d]; us .game_data, us-rev1 .game_data_E1720 [b]"


def test_sections_parts_real_rows_menu_menu_text_sits_in_three_sections_in_link_order() -> None:
    rows = [(u, "M") for u in ("m0", "m1", "m2", "m3", "m4")]
    seq = [
        entry("m0", "text", 8, ".game_data"),
        entry("m1", "text", 8, ".ovl_frontendMenu"),
        entry("m2", "text", 8, ".game_data"),
        entry("m3", "text", 8, ".ovl_frontendMenu"),
        entry("m4", "text", 8, ".boot"),
    ]
    found = _parts(rows, {"us": seq}, "text")
    assert [(p["sections"]["us"], p["units"]) for p in found] == [
        (".game_data", ["m0", "m2"]),
        (".ovl_frontendMenu", ["m1", "m3"]),
        (".boot", ["m4"]),
    ]


def test_sections_parts_real_rows_scene_text_parts_agree_across_five_versions_and_a_whole_module_has_none() -> None:
    rows = [("s0", "M"), ("s1", "M"), ("t0", "N"), ("t1", "N")]
    seqs = {
        v: [
            entry("s0", "text", 8, ".game_data"),
            entry("s1", "text", 8, ".ovl_frontendMenu"),
            entry("t0", "text", 8, ".game_data"),
            entry("t1", "text", 8, ".game_data"),
        ]
        for v in ("de", "eu", "eu-x", "us", "us-rev1")
    }
    out = {v["module"]: v for v in mergecheck.verdicts(rows, seqs, {}, lambda unit: "cc")}
    assert [p["units"] for p in out["M"]["parts"]["text"]] == [["s0"], ["s1"]]
    assert out["N"]["parts"] == {}
    assert out["N"]["verdict"] == "merge"


def test_merged_span_follows_the_built_objects_own_padding() -> None:
    """CURVE: the model packs 4 + 8 B of rodata into 12 B, the built object holds 4 B of padding more (a 8-aligned
    piece), so the span is 16 B and the regenerated fill starts where the object really ends."""
    seq = [
        with_parts(entry("a", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
        with_parts(entry("b", "rodata", 8, ".rodata"), rodata=piece(8, 4)),
    ]
    assert mergecheck.spans(seq, {"a", "b"}) == [(".rodata", 12, 12, 0)]
    assert mergecheck.spans(seq, {"a", "b"}, {".rodata": [(16, 8)]}) == [(".rodata", 12, 16, 0)]


def _slice(rows: list[tuple[str, str, int, int, int]], section: str, subalign: int = 1) -> list[dict[str, Any]]:
    """Link rows of a SUBALIGN section: (unit, class, size, section alignment, pinned offset or 0) in link order."""
    seq: list[dict[str, Any]] = []
    for unit, cls, size, align_, pin in rows:
        if pin:
            seq.append({"o": section, "k": "minimum", "offset": pin})
        parts = {cls: piece(size, align_)}
        seq.append({**with_parts(entry(unit, cls, size, section), **parts), "subalign": subalign})
    return seq


def test_regression_slices_of_merged_sections_that_kept_the_linked_size() -> None:
    """Pieces whose object alignment is 16 but that the link packed at 4 or 8 under SUBALIGN(1): one object packs them
    the same way, so the merged span is the linked one (timerintr 776 vs 772, vi us-rev1 120 vs 112, sinf 88 vs 80,
    PRINTF .ovl_frontendMenu 6176 vs 6172; MERGE-LMA-PAD)."""
    cases = {
        "timerintr": ([("a", "text", 508, 16, 0), ("b", "text", 264, 16, 767660)], 767152, 772),
        "vi": ([("a", "data", 96, 16, 0), ("b", "data", 8, 16, 72080), ("c", "data", 8, 16, 72088)], 71984, 112),
        "sinf": ([("a", "data", 8, 16, 0), ("s", "rodata", 68, 4, 24344), ("b", "data", 4, 16, 24412)], 24336, 80),
        "printf": ([("a", "text", 2252, 16, 0), ("b", "text", 3920, 16, 0x80414CCC - 0x80414400)], 0x80414400, 6172),
    }
    for name, (rows, start, linked) in cases.items():
        seq = _slice(rows, ".sec")
        seq.insert(0, {"o": ".sec", "k": "minimum", "offset": start})
        units = {r[0] for r in rows}
        [(section, span, merged, _)] = mergecheck.spans(seq, units)
        assert (section, span, merged) == (".sec", linked, linked), name
        hard = run([(u, "M") for u in units], seq)["M"]["hard"]
        # sinf links its pool between two data units: the span is kept, the order is the interleave finding
        assert [h for h in hard if not h.startswith("interleave:")] == [], name
        assert any(h.startswith("interleave:") for h in hard) == (name == "sinf"), name


def test_a_piece_the_link_aligned_keeps_its_alignment_in_the_merged_span() -> None:
    seq = [
        with_parts(entry("a", "text", 12, ".text"), text=piece(12, 16)),
        with_parts(entry("b", "text", 16, ".text"), text=piece(16, 16)),
    ]
    assert mergecheck.spans(seq, {"a", "b"}) == [(".text", 32, 32, 0)]


def test_a_gap_in_a_version_leaves_no_lma_finding_for_it() -> None:
    """MATH.c, NOTE.cpp: another module's bytes sit between the units, so the span the units fill is not one run and
    the gap is the finding."""
    rows = [("a", "M"), ("b", "M")]
    seq = [
        with_parts(entry("a", "rodata", 1, ".rodata"), rodata=piece(1, 1)),
        with_parts(entry("x", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
        with_parts(entry("b", "rodata", 4, ".rodata"), rodata=piece(4, 4)),
    ]
    out = run(rows, seq)["M"]
    assert out["hard"] == ["gap: us rodata a .. b has 1 between: x"]


def test_interior_fills_are_the_stale_link_bytes_and_zero_pads_between_two_units_of_the_module() -> None:
    link = (
        "  .rodata : {\n"
        "        src/a.CPP.o(.rodata .rdata);\n"
        "        . = 0x4; BYTE(0x24); BYTE(0x63); BYTE(0x00);\n"
        "        . = MAX(., 0x8);\n"
        "        src/b.CPP.o(.rodata .rdata);\n"
        "        . = 0x3000; BYTE(0x01);\n"
        "        src/other.CPP.o(.rodata .rdata);\n"
        "  }\n"
    )
    seq = mergecheck.parse_link(link)
    for row in seq:
        if row["k"] == "entry":
            row["n"], row["cls"] = 4, "+".join(row["cls"])
            row["parts"] = {"rodata": [(4, 4)]}
    assert [r["data"] for r in seq if r["k"] == "fill"] == [bytes([0x24, 0x63, 0]), bytes([1])]
    found = mergecheck.interior_fills(["src/a.CPP", "src/b.CPP"], {"de": seq})
    # the pad behind the stale bytes is only the alignment of src/b.CPP (4), which the compiler gives it: no array
    assert found == [{"after": "src/a.CPP", "cls": "rodata", "data": bytes([0x24, 0x63, 0]), "versions": ["de"]}]
    # the stale bytes and the alignment behind them are in the merged span: it fills the span the pieces linked in
    assert [s[1:] for s in mergecheck.spans(seq, {"src/a.CPP", "src/b.CPP"})] == [(12, 12, 0)]


def _rodata_link(pad: int, second: int) -> list[dict[str, Any]]:
    """A 3 byte piece of a.CPP, the link's `MAX(., offset)` pad of `pad` bytes, then a piece of b.CPP of `second`
    bytes (SYNTHESIZER_DATA.CPP, `s_synthesizerExText[3]` before synthesizer_data.c, CakeUnbake64 aa0b039)."""
    link = (
        "  .rodata : {\n"
        "        src/a.CPP.o(.rodata .rdata);\n"
        f"        . = MAX(., 0x{3 + pad:X});\n"
        "        src/b.CPP.o(.rodata .rdata);\n"
        "  }\n"
    )
    seq = mergecheck.parse_link(link)
    for row in seq:
        if row["k"] == "entry":
            size = 3 if row["u"] == "src/a.CPP" else second
            row["n"], row["cls"] = size, "+".join(row["cls"])
            row["parts"] = {"rodata": [(size, 4)]}
    return seq


def test_a_link_pad_that_is_the_next_pieces_alignment_is_no_array_for_the_merged_source() -> None:
    """The merged source declares every global on a word (`.align 2`): a one byte `unbake_fill` after the 3 byte text
    stood a word wide and the merged object was 4 bytes larger than the 40 linked, `cannot move location counter
    backwards` on us and us-rev1."""
    seq = _rodata_link(1, 36)
    both = {"src/a.CPP", "src/b.CPP"}
    assert mergecheck.interior_fills(sorted(both), {"us": seq}) == []
    assert [s[1:] for s in mergecheck.spans(seq, both)] == [(40, 40, 0)]


def test_a_link_pad_longer_than_the_next_pieces_alignment_stays_a_zero_array() -> None:
    seq = _rodata_link(5, 36)
    both = sorted({"src/a.CPP", "src/b.CPP"})
    assert mergecheck.interior_fills(both, {"us": seq}) == [
        {"after": "src/a.CPP", "cls": "rodata", "data": bytes(5), "versions": ["us"]}
    ]


def _cluster() -> tuple[list[tuple[str, str]], dict[str, list[dict[str, Any]]]]:
    """The player cluster's gap shape: PLAYER and PLAYERAI interleave in rodata, PLAYERAI reaches the WEAPONAI
    units and those the loose CAMERA unit, so no one module is mergeable before the others (a slice of SRC-AI-1
    cluster.tsv)."""
    rows = [
        ("P1", "PLAYER"), ("P2", "PLAYER"), ("A1", "PLAYERAI"), ("A2", "PLAYERAI"),
        ("W1", "WEAPONAI"), ("W2", "WEAPONAI"), ("Q1", "OTHER"), ("Q2", "OTHER"),
    ]  # fmt: skip
    ro = [
        ("P1", 4),
        ("A1", 4),
        ("P2", 4),
        ("W1", 4),
        ("A2", 4),
        ("CAM", 4),
        ("W2", 4),
        ("FAR", 4),
        ("Q1", 4),
        ("Q2", 4),
    ]
    seq = [entry("P1", "text", 8), entry("P2", "text", 8), entry("A1", "text", 8), entry("A2", "text", 8)]
    seq += [entry(u, "rodata", n, ".rodata") for u, n in ro]
    return rows, {"us": seq, "eu": list(seq)}


def test_closure_reports_the_group_that_must_merge_together() -> None:
    rows, seqs = _cluster()
    [group] = mergecheck.closures(rows, seqs)
    assert group["modules"] == ["PLAYER", "PLAYERAI", "WEAPONAI"]
    assert group["added"] == ["CAM"]
    assert group["units"] == 7 and group["bounded"] is True
    assert [sorted(p["units"]) for p in group["parts"]] == [["A1", "A2", "CAM", "P1", "P2", "W1", "W2"]]
    assert group["parts"][0]["module"] == "PLAYER"
    # the units left out are not between anything of the group: a verdict on the group has no gap
    merged = [(u, "G") for u in group["parts"][0]["units"]]
    assert not [h for h in mergecheck.verdicts(merged, seqs, {}, lambda _: "cc")[0]["hard"] if h.startswith("gap")]


def test_unplaced_units_of_the_map_are_named_not_read_as_a_bounded_closure() -> None:
    # a slice of lanes/SRC-AI-1/cluster.tsv against a build whose link names the units under another layout (src/)
    rows = [
        ("SOURCE/AI/PLAYER/CARTRIDGE_INDEX.CPP", "SOURCE/AI/PLAYER.CPP"),
        ("SOURCE/AI/PLAYER/DECODE.CPP", "SOURCE/AI/PLAYER.CPP"),
        ("SOURCE/AI/PLAYER2.CPP", "SOURCE/AI/PLAYER2.CPP"),
    ]
    stale = [entry("src/AI/PLAYER/DECODE.CPP", "text", 8), entry("src/AI/PLAYER2.CPP", "text", 8)]
    assert mergecheck.closures(rows, {"us": stale}) == []
    assert mergecheck.unplaced(rows, {"us": stale}) == [u for u, _ in rows]
    fresh = [entry(u, "text", 8) for u, _ in rows]
    assert mergecheck.unplaced(rows, {"us": fresh, "eu": stale}) == []


def test_closure_leaves_a_module_with_nothing_between_out() -> None:
    seq = [entry("a", "text", 8), entry("b", "text", 8), entry("c", "text", 8)]
    assert mergecheck.closures([("a", "M"), ("b", "M"), ("c", "N")], {"us": seq}) == []


def test_closure_of_a_group_in_two_output_sections_is_unbounded_and_proposed_in_parts() -> None:
    rows = [("X", "M"), ("Y", "M"), ("Z", "N"), ("Z2", "N")]
    seq = [entry("X", "text", 8), entry("Z", "text", 8), entry("Z2", "text", 8), entry("Y", "text", 8, ".boot")]
    seq += [entry(u, "rodata", 4, ".rodata") for u in ("X", "Z", "Z2", "Y")]
    [group] = mergecheck.closures(rows, {"us": seq})
    assert group["bounded"] is False and group["sound"] is True
    assert [p["units"] for p in group["parts"]] == [["X", "Z", "Z2"], ["Y"]]
    assert [p["module"] for p in group["parts"]] == ["M_PART1", "M_PART2"]
    assert group["parts"][1]["sections"] == {"text": ".boot", "rodata": ".rodata"}
    for part in (p for p in group["parts"] if len(p["units"]) > 1):  # a lone unit has no verdict
        mapped = [(u, "G") for u in part["units"]]
        assert not [
            h
            for h in mergecheck.verdicts(mapped, {"us": seq}, {}, lambda _: "cc")[0]["hard"]
            if h.startswith(("sections", "gap"))
        ]


def test_parts_tied_by_a_gap_become_one_and_are_not_sound() -> None:
    seq = [entry("X", "text", 8), entry("Y", "text", 8, ".boot"), entry("Z", "text", 8)]
    seq += [entry(u, "rodata", 4, ".rodata") for u in ("X", "Y", "Z")]
    rows = [("X", "M"), ("Z", "M")]
    [group] = mergecheck.closures(rows, {"us": seq})
    assert [part["units"] for part in group["parts"]] == [["X", "Z"]] and group["sound"] is False
    assert group["parts"][0]["conflicts"] == {}
    assert {(b["class"], b["width"], tuple(b["between"])) for b in group["parts"][0]["blockers"]} == {
        ("text", 1, ("X", "Z")),
        ("rodata", 1, ("X", "Z")),
    }


def test_closure_of_a_module_with_a_far_unit_names_the_wide_gap_not_the_program() -> None:
    # real shape of SRC-AI-1 PLAYER.CPP: two rodata-only units of one module sit 183 foreign units apart in `.game_data`
    # of one version; the foreign units (libultra xprintf among them) must not join the group
    foreign = [f"sdk/libultra/F{n}.c" for n in range(6)]
    seq = [entry("P1", "text", 8), entry("P2", "text", 8), entry("xprintf", "text", 8, ".libultra")]
    seq += [entry("P1", "rodata", 4, ".game_data"), *(entry(u, "rodata", 4, ".game_data") for u in foreign)]
    seq += [entry("xprintf", "rodata", 4, ".game_data"), entry("P2", "rodata", 4, ".game_data")]
    [group] = mergecheck.closures([("P1", "PLAYER"), ("P2", "PLAYER")], {"de": seq, "us": list(seq)})
    assert group["bounded"] is False and group["units"] == 2 + len(foreign) + 1 and group["sound"] is False
    carried = {u for p in group["parts"] for u in p["units"]}
    assert carried == {"P1", "P2"}
    [blocker, *_] = group["parts"][0]["blockers"]
    assert blocker["width"] == len(foreign) + 1 and blocker["between"] == ["P1", "P2"] and blocker["class"] == "rodata"


def test_a_pool_linked_between_two_data_units_of_one_module_keeps_it_separate() -> None:
    """eu-x NAV ATTACKTARGET: CLOSEATTACK .data @0x584, the pool @0x590..0x5C4, K_NAVENCODEDWORDSBLOCK6 .data @0x5C4:
    one merged object emits all .data then the pool, so the pool and the data after it would trade places."""
    rows = [("CLOSEATTACK", "M"), ("ATTACKTARGET", "M"), ("K_NAVENCODEDWORDSBLOCK6", "M")]
    seq = [
        entry("CLOSEATTACK", "data", 12, ".data"),
        entry("ATTACKTARGET", "rodata", 52, ".data"),
        entry("K_NAVENCODEDWORDSBLOCK6", "data", 12, ".data"),
    ]
    out = run(rows, seq)["M"]
    assert out["verdict"] == "separate"
    assert out["hard"] == [
        "interleave: us .data links data on both sides of another class: "
        "data(CLOSEATTACK) rodata(ATTACKTARGET) data(K_NAVENCODEDWORDSBLOCK6)"
    ]


def test_a_module_whose_classes_each_link_whole_has_no_interleave() -> None:
    rows = [("a", "M"), ("b", "M")]
    seq = [entry("a", "data", 4, ".data"), entry("b", "data", 4, ".data"), entry("b", "rodata", 8, ".data")]
    assert run(rows, seq)["M"]["hard"] == []


def test_a_link_that_interleaves_rodata_and_data_keeps_its_runs_in_the_merged_span() -> None:
    """initialize.c .game_data (de, eu, eu-x, us-rev1): the link places rodata 3 and 15, data 13, rodata 3 and 16, data
    220 under SUBALIGN(1) with a pin before each piece, 272 B. One object that grouped the classes padded to 276 B and
    would move the data; the span keeps each run where the link put it."""

    def at(unit: str, cls: str, size: int, align: int) -> dict[str, Any]:
        return {**with_parts(entry(unit, cls, size, ".game_data"), **{cls: piece(size, align)}), "subalign": 1}

    def pin(offset: int) -> dict[str, Any]:
        return {"o": ".game_data", "k": "minimum", "offset": offset}

    seq = [
        pin(24608),
        at("read", "rodata", 3, 4),
        pin(24612),
        at("raw", "rodata", 15, 1),
        pin(24627),
        at("raw", "data", 13, 16),
        pin(24640),
        at("write", "rodata", 3, 4),
        pin(24644),
        at("init", "rodata", 16, 4),
        pin(24660),
        at("decode", "data", 220, 16),
    ]
    units = {"read", "raw", "write", "init", "decode"}
    assert mergecheck.spans(seq, units) == [(".game_data", 272, 272, 24608)]
    rows = [(u, "M") for u in sorted(units)]
    assert not [h for h in run(rows, seq)["M"]["hard"] if h.startswith("lma:")]


def test_pieces_the_link_packs_from_a_misaligned_run_start_keep_their_offsets_in_the_merged_span() -> None:
    """initialize.c .game_data in us-rev1: data 1 at 24979 and data 12 at 24980, each aligned 16 in its object, follow
    rodata 3, 15 and precede rodata 3, 16 and data 220 (272 B linked). SUBALIGN(1) starts the data run at 24979; the
    12 B follows it at offset 1, as the link has it, not padded to the 4 of its address (275 B)."""

    def at(unit: str, cls: str, size: int, align: int) -> dict[str, Any]:
        return {**with_parts(entry(unit, cls, size, ".game_data"), **{cls: piece(size, align)}), "subalign": 1}

    def pin(offset: int) -> dict[str, Any]:
        return {"o": ".game_data", "k": "minimum", "offset": offset}

    seq = [
        pin(24960),
        at("read", "rodata", 3, 4),
        pin(24964),
        at("raw", "rodata", 15, 1),
        pin(24979),
        at("raw", "data", 1, 16),
        pin(24980),
        at("block", "data", 12, 16),
        pin(24992),
        at("write", "rodata", 3, 4),
        pin(24996),
        at("init", "rodata", 16, 4),
        pin(25012),
        at("tables", "data", 220, 16),
    ]
    units = {"read", "raw", "block", "write", "init", "tables"}
    assert mergecheck.spans(seq, units) == [(".game_data", 272, 272, 24960)]
    rows = [(u, "M") for u in sorted(units)]
    assert not [h for h in run(rows, seq)["M"]["hard"] if h.startswith("lma:")]


def test_regression_slice_vimgr_de_game_data_keeps_the_linked_size_when_the_module_starts_off_the_alignment() -> None:
    """de .game_data of sdk/libultra/io/VIMGR/VIMGR.CPP.c: the module's two 8 B pieces (AUDIO_ABORTDISKTRANSFER_DATA and
    CREATEMESGQUEUE_DATA, object alignment 16) link under SUBALIGN(1) at 80024 and 80032. The second sits 16 aligned in
    the address only because the module starts 8 off it: one object starts at 80024 and packs it at 8, so the merged
    span is the linked 16 B, not 24 (MERGE-LMA-PAD missed the module start)."""
    unit = "src/DATA/OVERLAYS/SEVEN/LINKTABLES/"
    rows = [
        (unit + "AUDIO_ABORTDISKTRANSFER_DATA.CPP", "data", 8, 16, 80024),
        (unit + "CREATEMESGQUEUE_DATA.CPP", "data", 8, 16, 80032),
    ]
    seq = _slice(rows, ".game_data")
    units = {r[0] for r in rows}
    [(section, span, merged, start)] = mergecheck.spans(seq, units)
    assert (section, span, merged, start) == (".game_data", 16, 16, 80024)
    assert run([(u, "VIMGR") for u in units], seq)["VIMGR"]["hard"] == []
