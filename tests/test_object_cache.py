"""The object store: an object made once under a key (toolchain, flags, `.i` bytes, program) is copied, not made twice.
The store is a dict behind abucache's ContentCache get/put; the compiler is a counter, no process starts."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

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
    assert a.store.counts == [("unit", False)]


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
