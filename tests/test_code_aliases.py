"""Public setup/sync with the SDK identities reported by the PAK screen run; only native seams are replaced."""

import fixture
import pytest

from unbake import repo, tree
from unbake.contracts import Refusal, Segment, sha1

pytestmark = pytest.mark.usefixtures("toolchains")

# The ten US data rows removed by the reported run, retained as linker identities at their original addresses.
SDK = {
    "g_libsnHostRequestMessage": (0x800F0CB0, 0xF18B0),
    "g_libsnDmaCompletionMessage": (0x800F0CB4, 0xF18B4),
    "g_libsnDmaCompletionQueue": (0x800F0CC0, 0xF18C0),
    "g_libsnDmaRequest": (0x800F0CE0, 0xF18E0),
    "g_libsnHostMemoryRanges": (0x800F0D00, 0xF1900),
    "g_libsnHostRequestQueue": (0x800F0F40, 0xF1B40),
    "g_libsnHostReplyMessage": (0x800F0F58, 0xF1B58),
    "g_libsnHostRequestThread": (0x800F0F60, 0xF1B60),
    "g_libsnHostReplyQueue": (0x800F1110, 0xF1D10),
    "g_debugExceptionContext": (0x800F1130, 0xF1D30),
}
FUNCTION = "HostCode = 0x800F0CA0; // type:func rom:0xF18A0 size:0x4A0\n"


@pytest.fixture
def code_aliases(tmp_path, monkeypatch):
    rows = "".join(f"{n} = 0x{vram:X}; // rom:0x{rom:X}\n" for n, (vram, rom) in SDK.items())
    cfg = fixture.config(
        tmp_path,
        sources={"src/main/host.c": "void HostCode(void) {}\n"},
        symbols={v: FUNCTION + rows for v in ("a", "b")},
        extra={f"versions/{v}/aliases.txt": rows for v in ("a", "b")},
    )
    for v in cfg.project.versions:
        # Remove the byte-owning rows; aliases.txt remains exactly as written.
        (cfg.project.root / f"versions/{v}/symbol_addrs.txt").write_text(FUNCTION)
        image = bytes(0xF4000)
        (cfg.project.root / f"roms/baserom.{v}.z64").write_bytes(image)
        cfg.project.version[v].size = len(image)
        cfg.project.version[v].sha1 = sha1(image)
        cfg.project.version[v].segments = (
            Segment(name="main", rom=0xF1000, vram=0x800F0400, kind="code", window_of=None),
            Segment(name="ovl", rom=0xF2000, vram=0x80100000, kind="code", window_of=None),
        )

    def extract(config, version):
        (config.project.root / f"asm/{version}").mkdir(parents=True, exist_ok=True)
        return fixture.native_result()

    monkeypatch.setattr(repo.build, "install_toolchains", lambda *args: None)
    monkeypatch.setattr(repo.build, "extract", extract)
    return cfg


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).parts[0] not in (".unbake", ".git", "asm", "build")
    }


def test_setup_and_sync_preserve_the_ten_sdk_aliases_without_allocating_bytes(code_aliases):
    cfg, root = code_aliases, code_aliases.project.root
    before = {v: (root / f"versions/{v}/aliases.txt").read_bytes() for v in cfg.project.versions}
    assert len(SDK) == 10
    # Capture and unowned classification run normally; the stand-in decision must recognize the alias contract.
    assert "g_libsnHostRequestMessage" in tree.capture(cfg).unowned("a")
    repo.setup(cfg, {"no_make": True})
    held = tree.capture(cfg)
    assert repo.stand_ins(cfg, held) == {}
    for v in cfg.project.versions:
        assert (root / f"versions/{v}/aliases.txt").read_bytes() == before[v]
        assert held.owner(v, "HostCode") == "src/main/host.c"
        assert all(held.owned(v, n) is None for n in SDK)
    assert (root / "src/main/host.c").read_text() == "void HostCode(void) {}\n"
    assert sorted(p.relative_to(root).as_posix() for p in (root / "src").rglob("*") if p.is_file()) == [
        "src/main/host.c"
    ]
    first = snapshot(root)
    assert repo.sync_command(cfg, {})["written"] == []
    assert repo.sync_command(cfg, {"check": True})["written"] == []
    assert snapshot(root) == first


@pytest.mark.parametrize(
    "case",
    [
        "before",
        "end",
        "mapping",
        "primary",
        "earlier",
        "sized",
        "unsized-code",
        "stand-in",
        "assembly",
        "other-version",
        "version-span",
        "not-alias",
    ],
)
def test_sync_refuses_unproven_aliases_and_byte_owners_without_writing(code_aliases, case):
    cfg, root = code_aliases, code_aliases.project.root
    path = root / "versions/a/aliases.txt"
    name = "g_libsnHostRequestMessage"
    row = path.read_text().splitlines(keepends=True)[0]
    if case in ("before", "end", "mapping"):
        vram, rom = {"before": (0x800F0C9C, 0xF189C), "end": (0x800F1140, 0xF1D40), "mapping": (0x800F0CB0, 0xF18B8)}[
            case
        ]
        path.write_text(f"{name} = 0x{vram:X}; // rom:0x{rom:X}\n")
    elif case in ("primary", "earlier"):
        file = "symbol_addrs.txt" if case == "primary" else "earlier_build_symbols.txt"
        with (root / f"versions/a/{file}").open("a") as out:
            out.write(row)
    elif case == "sized":
        path.write_text(row.rstrip() + " size:0x4\n")
    elif case == "unsized-code":
        (root / "versions/a/symbol_addrs.txt").write_text(FUNCTION.replace(" size:0x4A0", ""))
    elif case == "stand-in":
        (root / "src/main/host.c").write_text('INCLUDE_ASM("main", HostCode);\n')
    elif case == "assembly":
        (root / "src/main/host.c").unlink()
        (root / "src/main/host.s").write_text(".text\nglabel HostCode\nnop\n")
    elif case == "version-span":
        (root / "versions/a/symbol_addrs.txt").write_text(FUNCTION.replace("size:0x4A0", "size:0x10"))
    elif case == "not-alias":
        path.write_text("")
        with (root / "versions/a/symbol_addrs.txt").open("a") as out:
            out.write(row)
    else:
        (root / "src/main/host.c").write_text("#ifdef VERSION_B\nvoid HostCode(void) {}\n#endif\n")
    before = snapshot(root)
    with pytest.raises(Refusal) as caught:
        repo.sync_command(cfg, {})
    assert all(f.key == "sync.stand-in" for f in caught.value.findings)
    assert name in {n for f in caught.value.findings for n in f.missing}
    assert snapshot(root) == before


def test_alias_at_span_start_needs_no_new_owner(code_aliases):
    cfg, root = code_aliases, code_aliases.project.root
    for v in cfg.project.versions:
        with (root / f"versions/{v}/aliases.txt").open("a") as out:
            out.write("AtStart = 0x800F0CA0; // rom:0xF18A0\n")
    repo.setup(cfg, {"no_make": True})
    assert repo.sync_command(cfg, {"check": True})["written"] == []


def test_rom_inferred_aliases_and_distinct_version_spans(code_aliases):
    cfg, root = code_aliases, code_aliases.project.root
    for v in cfg.project.versions:
        delta = 0 if v == "a" else 0x20
        primary = FUNCTION.replace("0x800F0CA0", f"0x{0x800F0CA0 + delta:X}")
        primary = primary.replace("0xF18A0", f"0x{0xF18A0 + delta:X}")
        (root / f"versions/{v}/symbol_addrs.txt").write_text(primary)
        (root / f"versions/{v}/aliases.txt").write_text(
            f"Inside = 0x{0x800F0CA4 + delta:X};\nLastByte = 0x{0x800F113F + delta:X};\n"
        )
    held = tree.capture(cfg)
    for v in cfg.project.versions:
        assert repo._code_aliases(cfg, held, v) == {"Inside", "LastByte"}
    repo.setup(cfg, {"no_make": True})
    assert repo.sync_command(cfg, {"check": True})["written"] == []
