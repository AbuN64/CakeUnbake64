"""The object store: an object made once under a key (toolchain, flags, `.i` bytes, program) is copied, not made twice.
The store is a dict behind abucache's ContentCache get/put; the compiler is a counter, no process starts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from unbake import build, config, objcache


class Store:
    """abucache's ContentCache as a dict: what a compile stores is what a later compile reads."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], bytes] = {}
        self.counts: list[tuple[str, bool]] = []

    def get(self, kind: str, key: str) -> bytes | None:
        return self.entries.get((kind, key))

    def put(self, kind: str, key: str, value: bytes) -> None:
        self.entries[(kind, key)] = value

    def count(self, kind: str, hit: bool) -> None:
        self.counts.append((kind, hit))


class Tree:
    """A tree with one source text; its program is mocked: it counts its compiler runs."""

    def __init__(self, root: Path, compiler: Path, store: Store, text: str = "int a;") -> None:
        self.root, self.compiler, self.store, self.text = root, compiler, store, text
        self.runs = 0

    def build(self, flags: str = "-O2", textsize: str = "", program: str = "cc x.i", extracted: str = "") -> str:
        """`get`, else the compiler and `put` (the object rule's program); the object's text."""
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            (self.root / "x.i").write_text(self.text)
            args = ("x.o", str(self.compiler), flags, textsize, program, [extracted] if extracted else [])
            if objcache.get(self.store, *args) != 0:  # type: ignore[arg-type]
                self.runs += 1
                (self.root / "x.o").write_text(self.text.upper() + flags)
                assert objcache.put(self.store, *args) == 0  # type: ignore[arg-type]
            return (self.root / "x.o").read_text()
        finally:
            os.chdir(cwd)


def trees(tmp_path: Path) -> tuple[Tree, Tree]:
    compiler = tmp_path / "tc" / "cc"
    compiler.parent.mkdir()
    compiler.write_text("cc")
    store = Store()
    for name in "ab":
        (tmp_path / name).mkdir()
    return Tree(tmp_path / "a", compiler, store), Tree(tmp_path / "b", compiler, store)


def test_a_compile_stores_its_object(tmp_path):
    a, _ = trees(tmp_path)
    assert a.build() == "INT A;-O2"
    assert a.runs == 1
    assert [kind for kind, _ in a.store.entries] == ["unit"]
    assert a.store.counts == [("unit", False), ("store", True)]


def test_the_same_input_in_another_tree_hits(tmp_path):
    a, b = trees(tmp_path)
    a.build()
    assert b.build() == "INT A;-O2"
    assert (a.runs, b.runs) == (1, 0)
    assert b.store.counts[-1] == ("unit", True)


def test_a_changed_input_misses(tmp_path):
    a, b = trees(tmp_path)
    a.build()
    (tmp_path / "d.s").write_text("one")
    b.build(extracted=str(tmp_path / "d.s"))  # extracted files
    b.build(program="cc x.i -DX")  # program
    b.build(flags="-O1")  # flags
    b.build(textsize="0x40")  # text size
    b.text = "int b;"  # .i bytes
    b.build()
    assert b.runs == 5


def test_a_changed_extracted_file_misses(tmp_path):
    a, b = trees(tmp_path)
    extracted = tmp_path / "d.s"
    extracted.write_text("one")
    a.build(extracted=str(extracted))
    extracted.write_text("two")
    b.build(extracted=str(extracted))
    assert b.runs == 1


def test_a_changed_compiler_misses(tmp_path):
    a, b = trees(tmp_path)
    a.build()
    b.compiler.write_text("another cc")
    b.build()
    assert b.runs == 1


def test_the_hit_is_a_copy_the_trim_cannot_reach_the_store_through(tmp_path):
    a, b = trees(tmp_path)
    a.build()
    b.build()
    (tmp_path / "b" / "x.o").write_text("trimmed")  # trim_text rewrites the object in place
    assert Tree(tmp_path / "a", a.compiler, a.store).build() == "INT A;-O2"
    assert b.runs == 0


def test_the_assembly_comes_back_with_the_object(tmp_path):
    a, b = trees(tmp_path)
    (tmp_path / "a" / "x.s").write_text("asm")
    a.build()
    b.build()
    assert (tmp_path / "b" / "x.s").read_text() == "asm"


def test_a_torn_entry_is_a_miss(tmp_path):
    a, b = trees(tmp_path)
    a.build()
    for entry in a.store.entries:
        a.store.entries[entry] = b"torn"
    b.build()
    assert b.runs == 1


def test_local_mk_declares_the_object_store_beside_the_host_content_cache():
    host = SimpleNamespace(
        workers=4, tools={}, toolchain_root=Path("/chains"), cache_dir=Path("/c/unbake/content"), cache_max_bytes=7
    )
    text = build.local_mk(SimpleNamespace(host=host)).decode()
    assert f"OBJSTORE := {sys.executable} -m unbake.objcache /c/unbake/content.v" in text
    assert "OBJCACHE" not in text


def test_every_recipe_probes_the_store_then_stores_what_its_program_made():
    for id in config.toolchains():
        text = build._recipe(id)
        key = build._key(id)
        assert f"COMPILE_{key} = @probe $(OBJSTORE) get " in text
        assert f"'&&' $(PROGRAM_{key}) '&&' $(OBJSTORE) put " in text
        assert text.count(" get ") == text.count(" put ") == 1
        assert f"\nPROGRAM_{key} = " in text


def test_a_store_counts_a_store_event_and_a_lookup_counts_through_abucaches_seam(tmp_path):
    first, second = trees(tmp_path)
    first.build()
    second.build()
    assert first.store.counts == [("unit", False), ("store", True), ("unit", True)]


def test_the_tally_turns_abucaches_counts_into_stats_from_an_offset(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    count = objcache._tally(store)
    for kind, hit in [("unit", False), ("store", True), ("pool.spill", True)]:
        count(kind, hit)
    mark = objcache.stats(store)
    assert mark == {"stores": 1, "hits": 0, "misses": 1, "offset": mark["offset"]}
    for kind, hit in [("unit", True), ("unit", True), ("unit", False), ("store", True)]:
        count(kind, hit)
    assert objcache.stats(store, mark["offset"]) == {
        "stores": 1,
        "hits": 2,
        "misses": 1,
        "offset": objcache.stats(store)["offset"],
    }


def test_stats_of_a_store_never_used_is_zero_and_a_half_written_line_waits(tmp_path):
    assert objcache.stats(tmp_path) == {"stores": 0, "hits": 0, "misses": 0, "offset": 0}
    (tmp_path / "objcache.tally").write_bytes(b"store\nhi")
    assert objcache.stats(tmp_path) == {"stores": 1, "hits": 0, "misses": 0, "offset": 6}


def test_stats_prints_the_counts_the_measure_reads(tmp_path, capsys):
    objcache._record(tmp_path, "store")
    objcache._record(tmp_path, "hit")
    assert objcache.main(["objcache", "stats", str(tmp_path)]) == 0
    assert capsys.readouterr().out == "stores: 1\nhits: 1\nmisses: 0\noffset: 10\n"


def test_main_reads_the_makefiles_word_order(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(objcache, "open_cache", lambda *args: seen.append(args) or "cache")
    monkeypatch.setattr(objcache, "get", lambda *args: seen.append(args) or objcache.MISS)
    argv = ["objcache", str(tmp_path), "99", "get", "a.o", "cc", "flags", "0x10", "prog", "x.s"]
    assert objcache.main(argv) == objcache.MISS
    assert seen[0][:2] == (tmp_path, 99)
    assert seen[1] == ("cache", "a.o", "cc", "flags", "0x10", "prog", ["x.s"])
    with pytest.raises(SystemExit):
        objcache.main(["objcache", "get", str(tmp_path), "99", "a.o", "cc", "flags", "0x10", "prog"])


# a slice of the real gate job: make expands the flags and the program with the place of unbake's resources, and the
# gate job's venv is a new one per job (rig-20261101T102020Z: 0 of 9278 objects found, 450 s of passes and objects)
FRONT = "sh {venv}/lib/python3.12/site-packages/unbake/resources/cc_front.sh /c/store.cc tools/gcc-2.8.1-sn64/cc1"
PASS = "python3 -I {venv}/lib/python3.12/site-packages/unbake/resources/make/strip_align.py build/de/x.s 8"


def test_the_place_of_unbake_is_not_the_key(tmp_path):
    a, b = trees(tmp_path)
    venv_a, venv_b = "/run/user/1000/job/tRI5fG/gate-unbake-venv", "/run/user/1000/job/w9Kq2x/gate-unbake-venv"
    a.build(flags=f"{FRONT.format(venv=venv_a)} -O2", program=f"@compile cc x.i && {PASS.format(venv=venv_a)}")
    b.build(flags=f"{FRONT.format(venv=venv_b)} -O2", program=f"@compile cc x.i && {PASS.format(venv=venv_b)}")
    assert (a.runs, b.runs) == (1, 0)


def test_the_link_to_the_live_venv_names_the_same_place(tmp_path):
    a, b = trees(tmp_path)
    a.build(program="python3 -I /home/abu/venv-unbake.1f3a/lib/python3.12/site-packages/unbake/resources/make/p.py x")
    b.build(program="python3 -I /home/abu/venv-unbake/lib/python3.12/site-packages/unbake/resources/make/p.py x")
    assert b.runs == 0


def test_a_changed_pass_misses_where_it_is_installed(tmp_path, monkeypatch):
    a, b = trees(tmp_path)
    passes = tmp_path / "passes"
    passes.mkdir()
    (passes / "strip_align.py").write_text("one")
    monkeypatch.setattr(objcache, "_PASSES", passes)
    a.build()
    (passes / "strip_align.py").write_text("two")
    b.build()
    assert b.runs == 1


def test_another_path_that_is_not_unbake_is_the_key(tmp_path):
    a, b = trees(tmp_path)
    a.build(program="python3 -I /opt/one/make/strip_align.py x")
    b.build(program="python3 -I /opt/two/make/strip_align.py x")
    assert b.runs == 1
