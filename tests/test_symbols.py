"""symbols: symbol_addrs facts and the cross-version name join, over two fake versions."""

from __future__ import annotations

from pathlib import Path

import fixture
import pytest

from unbake import pool, symbols


@pytest.fixture(autouse=True)
def _inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool jobs run in this process, so spies see them."""
    monkeypatch.setattr(pool, "_INLINE_AT", 10**6)


def asm(*rows: tuple[str, str]) -> str:
    return "".join(f"/* {i * 4:X} 8000{0x400 + i * 4:04X} {word} */  {op}\n" for i, (word, op) in enumerate(rows))


CALLER = {
    "a": asm(("0C000100", "jal callee_a"), ("00000000", "nop")),
    "b": asm(("0C000104", "jal callee_b"), ("00000000", "nop")),
}
CALLEE = asm(("03E00008", "jr $ra"), ("00000000", "nop"))


def project(tmp: Path) -> symbols.Config:
    table = {
        v: f"caller = 0x80000400; // type:func rom:0x1000 size:8\n{c} = 0x80000410; // type:func rom:0x1010 size:0x8\n"
        for v, c in (("a", "callee_a"), ("b", "callee_b"))
    }
    extra = {}
    for v, callee in (("a", "callee_a"), ("b", "callee_b")):
        extra[f"asm/{v}/nonmatchings/main/caller.s"] = CALLER[v]
        extra[f"asm/{v}/nonmatchings/main/{callee}.s"] = CALLEE
    return fixture.config(tmp, symbols=table, extra=extra)


def test_parse_reads_splat_lines() -> None:
    text = "# note\nfoo = 0x80000400; // type:func rom:0x1000 size:0x20\nbar = 0x80000500;\n"
    text += "baz = 1; // type:u32 rom:0x2000\n"
    assert symbols.parse(text) == {
        "foo": (0x80000400, 0x1000, 0x20, "func"),
        "bar": (0x80000500, None, None, "data"),
        "baz": (1, 0x2000, None, "u32"),
    }


def test_asm_path_follows_the_segment_and_type(tmp_path: Path) -> None:
    config = fixture.config(
        tmp_path, symbols={"a": "d = 0x80100000; // type:u32 rom:0x3004\nf = 0x80000400; // type:func rom:0x1000\n"}
    )
    assert symbols.asm_path(config, "a", "d") == "asm/a/data/ovl/d.s"
    assert symbols.asm_path(config, "a", "f") == "asm/a/nonmatchings/main/f.s"


def test_rename_lines_keeps_every_other_byte() -> None:
    text = "# keep\nold = 0x1; // type:func rom:0x2\nolder = 0x3;\n"
    assert symbols.rename_lines(text, {"old": "new"}) == "# keep\nnew = 0x1; // type:func rom:0x2\nolder = 0x3;\n"


def test_join_renames_the_other_version_to_the_source_spelling(tmp_path: Path) -> None:
    config = project(tmp_path)
    assert symbols.join(config) == (("callee_a", "a", "callee_b", "b"),)
    root = config.project.root
    assert "callee_a" in (root / "versions/b/symbol_addrs.txt").read_text()
    assert "callee_b" not in (root / "versions/b/symbol_addrs.txt").read_text()
    assert "callee_a" in (root / "versions/a/symbol_addrs.txt").read_text()
    assert symbols.join(config) == ()  # the names agree now


def test_join_reads_only_symbol_addrs(tmp_path: Path) -> None:
    config = project(tmp_path)
    symbols.join(config)
    assert not (config.project.root / "symbols.toml").exists()


def test_correspondences_cache_on_asm_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = project(tmp_path)
    calls = []
    real = symbols._align
    monkeypatch.setattr(symbols, "_align", lambda item: calls.append(1) or real(item))
    first = symbols.correspondences(config)
    assert first and len(calls) == 2  # one alignment per direction
    assert symbols.correspondences(config) == first and len(calls) == 2
    (config.project.root / "asm/b/nonmatchings/main/caller.s").write_text(
        CALLER["b"] + "/* 8 80000408 00000000 */  nop\n"
    )
    symbols.correspondences(config)
    assert len(calls) == 4


@pytest.mark.parametrize("name", [".L8024D278", "a$b", "x@1"])
def test_a_fact_named_with_no_c_identifier_is_refused_by_both_readers(name: str) -> None:
    """Both readers of symbol_addrs.txt read the same rows: an assembler label is refused, never dropped by one."""
    from unbake import versions

    text = f"f = 0x80000400; // type:func rom:0x1000\n{name} = 0x80000410; // type:func rom:0x1010\n"
    with pytest.raises(symbols.Refusal) as caught:
        symbols.parse(text, "versions/de/symbol_addrs.txt")
    assert caught.value.findings[0].key == "symbols.name"
    assert caught.value.findings[0].path == "versions/de/symbol_addrs.txt:2"
    with pytest.raises(symbols.Refusal):
        versions.parse_symbol_addrs(text.encode())


def test_size_conflicts_name_a_symbol_with_two_sizes():
    sym = "literals_f = 0x80215410; // rom:0x1000 size:0x4\nsame = 0x80000000; // size:0x8\nnosize = 0x80000010;\n"
    alias = (
        "literals_f = 0x80215410; // rom:0x1000 size:0x20\nsame = 0x80000000; // size:0x8\n"
        "nosize = 0x80000010; // size:0x4\n"
    )
    assert symbols.size_conflicts(sym, alias) == {"literals_f": [(4,), (0x20,)]}
    assert symbols.size_conflicts(sym) == {}


GAME_ROWS = """\
literals_Stage_PlaceStageOpponents = 0x800E1B70; // rom:0xE2770 size:0x14
D_800E1B7C_us_rev1 = 0x800E1B7C; // rom:0xE277C
literals_Player_FormatPlayerPartnerStatus = 0x800E2738; // rom:0xE3338 size:0x3C
D_blob_94247f6784238752b222 = 0x800E2754; // rom:0xE3354
k_playerMenuSpriteScaleConstants = 0x800E2770; // rom:0xE3370
VideoModeIds = 0x800E2A4C; // rom:0xE364C size:0x60
D_src_6b2060eb = 0x800E2A56; // rom:0xE3656
g_playerReserveBounds_de = 0x800CE3E0; // rom:0xCEFE0
gReactionReserveMaximum = 0x800CE3E0; // reaction reserve upper limit (g_playerReserveBounds_de[0])
func_800E2A00 = 0x800E2A00; // type:func rom:0xE3600 size:0x30
"""


def test_nested_rows_inside_a_pool_or_data_span_leave_splat_s_file() -> None:
    """The five user-declared symbols splat could not place after a merge: four sit inside a sized row's bytes (a
    literals pool, a data span), one is a second name at an address another row starts."""
    kept, moved = symbols.nested(GAME_ROWS)
    assert set(symbols.parse("".join(moved))) == {
        "D_800E1B7C_us_rev1",
        "D_blob_94247f6784238752b222",
        "k_playerMenuSpriteScaleConstants",
        "D_src_6b2060eb",
        "gReactionReserveMaximum",
    }
    assert set(symbols.parse(kept)) == {
        "literals_Stage_PlaceStageOpponents",
        "literals_Player_FormatPlayerPartnerStatus",
        "VideoModeIds",
        "g_playerReserveBounds_de",
        "func_800E2A00",
    }
    assert symbols.nested(kept) == (kept, [])  # settled: a second pass moves nothing


def test_nested_keeps_a_row_that_starts_a_piece() -> None:
    """A row at the end of a sized row, a row of its own rom at a shared address, and a code row stay."""
    text = (
        "a = 0x80000100; // rom:0x100 size:0x10\n"
        "b = 0x80000110; // rom:0x110\n"
        "c = 0x80000100; // rom:0x100\n"
        "d = 0x80000104; // type:func rom:0x104 size:4\n"
    )
    assert symbols.nested(text) == (text, [])
