"""mergecheck and merge judge through one identity check; library ownership follows the destination; a file moved one
folder deeper keeps its relative includes; data used before its definition gets an extern. Rows are a slice of SDK-4's
modules.tsv (sndplayer, seqplayer, MATH2, alsynset) and of SRC-MENU-3's PRINTF."""

from __future__ import annotations

from dataclasses import is_dataclass, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from merge_prepared import prepared

from unbake import classwise, folders
from unbake import merge as unitmerge
from unbake.contracts import Config, FileFacts, Refusal

DATA = "src/DATA/AUDIO_SEQUENCE_DATA/SEQPLAYER.CPP"
CODE = "sdk/libultra/audio/seqplayer_text.c"
SEQ = "sdk/libultra/audio/seqplayer.c"
SEQ_MERGED = "sdk/libultra/audio/seqplayer/seqplayer.c"
MATH_UNIT, MATH_TARGET = "sdk/MATH/aigetlen.CPP", "sdk/MATH2.c"


def project_of(cfg: Config, **changes) -> Config:
    project = (
        replace(cfg.project, **changes)
        if is_dataclass(cfg.project)
        else SimpleNamespace(**{**vars(cfg.project), **changes})
    )
    return replace(cfg, project=project)


def seq_of(units: list[str]) -> dict[str, list[dict]]:
    sections = (".text",)
    return {
        "v": [
            {"o": ".text", "k": "entry", "u": u, "cls": "text", "n": 8, "sections": sections, "parts": {}}
            for u in units
        ]
    }


def scan(monkeypatch, cfg: Config, rows: list[tuple[str, str]], tracked: list[str], targets: str = "") -> None:
    root = cfg.project.root
    for path in tracked:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("int f(void) { return 1; }\n")
    monkeypatch.setattr(folders, "_tracked", Mock(return_value=tracked))
    monkeypatch.setattr(folders, "_module_facts", Mock(return_value=(rows, seq_of([u for u, _ in rows]), {})))
    (root / "modules.tsv").write_text("unit\tmodule\ttarget\n" + "".join(f"{u}\t{m}\t{targets}\n" for u, m in rows))


def judge(cfg: Config) -> dict[str, dict]:
    return {v["module"]: v for v in folders._verdicts(cfg, {"modules": cfg.project.root / "modules.tsv"})[0]}


def test_a_data_unit_merged_into_a_library_module_becomes_the_librarys(cfg, monkeypatch) -> None:
    cfg = project_of(cfg, libraries=("sdk/libultra/",))
    scan(monkeypatch, cfg, [(DATA, SEQ), (CODE, SEQ)], [DATA, CODE])
    assert judge(cfg)[SEQ]["hard"] == []
    assert folders._unit_identity(cfg.project, DATA) == folders._unit_identity(cfg.project, SEQ_MERGED)


def test_a_library_folder_unit_merged_to_a_target_outside_every_library_folder_merges(cfg, monkeypatch) -> None:
    cfg = project_of(cfg, libraries=("sdk/MATH/",))
    other = "sdk/MATH/OVLTBL.CPP"
    scan(monkeypatch, cfg, [(MATH_UNIT, "sdk/MATH2.c"), (other, "sdk/MATH2.c")], [MATH_UNIT, other], MATH_TARGET)
    assert judge(cfg)["sdk/MATH2.c"]["verdict"] == "merge"


@pytest.mark.parametrize("flags", [{"cflags": ("-O1",)}, {"omit": ("-g",)}, {"toolchain": "other-cc"}])
def test_a_flags_mismatch_with_the_destination_refuses_in_mergecheck_and_merge_alike(cfg, monkeypatch, flags) -> None:
    cfg = project_of(cfg, libraries=("sdk/libultra/",), files={"sdk/libultra/": FileFacts(**flags)})
    scan(monkeypatch, cfg, [(DATA, SEQ), (CODE, SEQ)], [DATA, CODE])
    hard = judge(cfg)[SEQ]["hard"]
    assert any("compiler, flags or omit" in reason for reason in hard)
    with pytest.raises(Refusal) as caught:
        folders.merge(cfg, {"modules": cfg.project.root / "modules.tsv"})
    assert [f"{SEQ}: {reason}" for reason in hard] == list(caught.value.findings[0].missing)


def test_mergecheck_reports_what_merge_refuses_beyond_the_link_findings(cfg, monkeypatch) -> None:
    cfg = project_of(cfg, libraries=("sdk/libultra/",))
    scan(monkeypatch, cfg, [(DATA, SEQ), (CODE, SEQ), ("sdk/libultra/audio/gone.c", "sdk/gone.c")], [DATA, CODE])
    found = judge(cfg)
    assert found["sdk/gone.c"]["hard"] == ["sdk/libultra/audio/gone.c is not a tracked source file"]
    assert found[SEQ]["verdict"] == "merge"


def test_a_file_moved_one_folder_deeper_keeps_its_relative_includes(cfg, monkeypatch) -> None:
    path, scan_, *_ = prepared(cfg, monkeypatch, 1)
    root = cfg.project.root
    (root / "src/include").mkdir(parents=True, exist_ok=True)
    (root / "src/include/libgultra.h").write_text("extern int g;\n")
    unit = "src/sdk/audio/alsynset_text.c"
    (root / unit).parent.mkdir(parents=True)
    (root / unit).write_text('#include "../../include/libgultra.h"\n#include <ultra64.h>\nint f(void) { return g; }\n')
    rows = [(unit, "src/sdk/audio/alsynset.c")]
    scan_.return_value = ([{"module": rows[0][1], "hard": [], "soft": [], "verdict": "merge"}], rows, {})
    path.write_text("unit\tmodule\n" + f"{unit}\t{rows[0][1]}\n")
    monkeypatch.setattr(folders, "_tracked", Mock(return_value=[unit, "src/include/libgultra.h"]))
    result = folders.merge(cfg, {"modules": path})
    merged = (root / "src/sdk/audio/alsynset/alsynset.c").read_text()
    assert '#include "../../../include/libgultra.h"' in merged and "<ultra64.h>" in merged
    assert result["modules"][0]["file"] == "src/sdk/audio/alsynset/alsynset.c"


PRINTF_C = (
    "#if defined(VERSION_DE)\nconst char k_PrintfEncodedWords[4] = {1, 2, 3, 4};\n#endif\n"
    "#if !defined(VERSION_DE)\nconst char k_PrintfEncodedWords[4] = {5, 6, 7, 8};\n#endif\n"
)


def arranged(units: dict[str, str], classes: dict, orders: dict):
    return classwise.arrange(
        [(u, t, unitmerge.mask(t), unitmerge.decls(t, u)) for u, t in units.items()], orders, classes
    )


def test_data_used_before_its_definition_gets_an_extern_as_a_function_gets_a_prototype() -> None:
    # the text chunk uses D, D sits behind R in the rodata chain, R uses Z, Z sits behind W, W uses D: a cycle only a
    # declaration breaks
    units = {
        "a": "int Printf_Digit(void) { return g_printfDigits; }\n",
        "b": "const int g_printfRef = g_printfFlag;\nconst int g_printfDigits = 5;\n",
        "c": "int g_printfLate = g_printfDigits;\nint g_printfFlag = 7;\n",
    }
    classes = {
        "a": {"Printf_Digit": "text"},
        "b": {"g_printfRef": "rodata", "g_printfDigits": "rodata"},
        "c": {"g_printfLate": "data", "g_printfFlag": "data"},
    }
    text, conflicts = arranged(units, classes, {"text": ["a"], "rodata": ["b"], "data": ["c"]})
    assert conflicts == []
    assert text.count("extern const int g_printfDigits;") == 1
    assert text.index("extern const int g_printfDigits;") < text.index("Printf_Digit")
    assert text.count("g_printfDigits = 5;") == 1


def test_definitions_under_mutually_exclusive_conditions_are_one_definition() -> None:
    use = "const char Printf_Word(int i) { return k_PrintfEncodedWords[i]; }\n"
    classes = {"a": {"Printf_Word": "text"}, "c": {"k_PrintfEncodedWords": "rodata"}}
    _, conflicts = arranged({"c": PRINTF_C}, {"c": classes["c"]}, {"rodata": ["c"]})
    assert conflicts == []
    text, conflicts = arranged({"a": use, "c": PRINTF_C}, classes, {"text": ["a"], "rodata": ["c"]})
    assert [c for c in conflicts if c["kind"] == "forward"] == []
    assert text.count("k_PrintfEncodedWords[4] =") == 2


# SRC-SDK-3 rows: LIBC_SIGNEDLONGLONGTODOUBLE_USREV1 and ospfschecker -> pfschecker carry cflags, entry.s -> BOOT.c is
# assembler, cspsetseq is stale beside its in-place target
FP = "sdk/libultra/libc/LIBC_SIGNEDLONGLONGTODOUBLE_USREV1.CPP"
FP_MODULE = "sdk/libultra/libc/LIBC_SIGNEDLONGLONGTODOUBLE_USREV1.CPP.c"
FP_MERGED = "sdk/libultra/libc/LIBC_SIGNEDLONGLONGTODOUBLE_USREV1.CPP/LIBC_SIGNEDLONGLONGTODOUBLE_USREV1.CPP.c"


def test_a_units_file_row_goes_with_it_to_the_target_in_the_verdict_and_in_config(cfg, monkeypatch) -> None:
    cfg = project_of(cfg, libraries=("sdk/libultra/",), files={FP: FileFacts(cflags=("-mfp32",))})
    scan(monkeypatch, cfg, [(FP, FP_MODULE)], [FP])
    assert judge(cfg)[FP_MODULE]["hard"] == []
    text = f'[file."{FP}"]\ncflags = ["-mfp32"]\n\n[file."sdk/other.c"]\ncflags = ["-O1"]\n'
    carried = folders._carry_rows(text, cfg.project, [([FP], FP_MERGED)])
    assert f'[file."{FP_MERGED}"]' in carried and f'[file."{FP}"]' not in carried and "sdk/other.c" in carried


def test_units_whose_carried_flags_still_differ_stay_separate(cfg, monkeypatch) -> None:
    files = {"sdk/a.c": FileFacts(cflags=("-mfp32",)), "sdk/b.c": FileFacts(cflags=("-O1",))}
    cfg = project_of(cfg, files=files)
    scan(monkeypatch, cfg, [("sdk/a.c", "sdk/M.c"), ("sdk/b.c", "sdk/M.c")], ["sdk/a.c", "sdk/b.c"])
    assert any("compiler, flags or omit" in r for r in judge(cfg)["sdk/M.c"]["hard"])


def test_an_assembler_unit_gives_an_assembler_target() -> None:
    from unbake import mergecheck

    assert mergecheck.merged_path("sdk/BOOT.c", "sdk/BOOT.c", ["sdk/boot/entry.s"]) == "sdk/BOOT.s"
    assert mergecheck.merged_path("sdk/LIBSN.c", None, ["sdk/LIBSN/pfs_setwatchpoint.s"]) == "sdk/LIBSN/LIBSN.s"
    assert mergecheck.merged_path("sdk/BOOT.c", "sdk/BOOT.c", ["a.s", "b.c"]) == "sdk/BOOT.c"


def test_a_unit_already_at_its_module_path_is_an_identity_row_and_a_stale_row_beside_its_target_is_in_place(
    cfg, monkeypatch
) -> None:
    scan(monkeypatch, cfg, [("sdk/libultra/audio/cseq.c", "sdk/libultra/audio/cseq.c")], ["sdk/libultra/audio/cseq.c"])
    assert judge(cfg)["sdk/libultra/audio/cseq.c"]["hard"] == []
    stale = "sdk/libultra/audio/cspsetseq.CPP"
    scan(
        monkeypatch,
        cfg,
        [(stale, "sdk/libultra/audio/cspsetseq.c")],
        ["sdk/libultra/audio/cspsetseq.c"],
        "sdk/libultra/audio/cspsetseq.c",
    )
    (cfg.project.root / stale).unlink(missing_ok=True)
    assert judge(cfg)["sdk/libultra/audio/cspsetseq.c"]["hard"] == []
