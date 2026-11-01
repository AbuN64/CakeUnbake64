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


# the gets of one build, answered in one process (front.build: record, batch, answer)

SLICE = [  # objects of the shape a version's build records: a C source, a split one, an assembly source
    f"build/us/src/{name}.o"
    for name in ("main", "game/player", "game/camera", "gfx/draw_01", "gfx/draw_02", "audio/synth", "libc/memcpy")
]


def record(root: Path, store: Path, compiler: Path, objects: list[str]) -> Path:
    """The manifest the object rules write while the build collects: one line per object, its OBJSTORE words first."""
    words = [sys.executable, "-m", "unbake.objcache", str(store), "1000000"]
    manifest = root / "cc-commands.1.obj"
    lines = []
    for out in objects:
        (root / out).parent.mkdir(parents=True, exist_ok=True)
        (root / out.replace(".o", ".i")).write_text(f"int {Path(out).stem};")
        lines.append("\x1f".join([*words, "get", out, str(compiler), "-O2", "", "as", "d.s"]))
    manifest.write_text("".join(f"{line}\n" for line in lines))
    return manifest


def test_a_batch_answers_every_recorded_get_beside_its_object_in_one_process(tmp_path, monkeypatch):
    a, _ = trees(tmp_path)
    monkeypatch.chdir(tmp_path)
    manifest = record(tmp_path, tmp_path / "store", a.compiler, SLICE)
    stored = SLICE[::2]
    for out in stored:  # the store holds these (a tree that made them put them under the same key)
        key = objcache.entry(out, str(a.compiler), "-O2", "", "as", ["d.s"])
        a.store.put("unit", key, objcache.frame(f"obj {out}".encode(), b"asm"))
    reads = []
    real = objcache.compile.identity
    monkeypatch.setattr(objcache.compile, "identity", lambda files: reads.append(files) or real(files))
    assert objcache.batch(a.store, str(manifest), 0, 1) == len(stored)
    assert len(reads) == 1  # the toolchain's files are read once, not per object
    for out in SLICE:
        object_made = (tmp_path / out).exists()
        answered = (tmp_path / (out + ".oc")).exists()
        assert not object_made  # the rule that runs again makes the object: the batch only answers
        assert answered == (out in stored)
        assert (tmp_path / (out + ".oc.miss")).exists() == (out not in stored)
    out = stored[0]
    assert (tmp_path / (out + ".oc")).read_bytes() == f"obj {out}".encode()
    assert (tmp_path / (out + ".oc.s")).read_bytes() == b"asm"
    assert a.store.counts.count(("unit", True)) == len(stored)
    assert objcache._read is None


def test_workers_split_the_gets_by_number(tmp_path, monkeypatch):
    a, _ = trees(tmp_path)
    monkeypatch.chdir(tmp_path)
    manifest = record(tmp_path, tmp_path / "store", a.compiler, SLICE)
    for index in range(3):
        objcache.batch(a.store, str(manifest), index, 3)
    assert sum((tmp_path / (out + ".oc.miss")).exists() for out in SLICE) == len(SLICE)
    assert len(a.store.counts) == len(SLICE)


def cc_probe():
    from unbake.resources import cc_cache

    return cc_cache


def test_a_collecting_build_records_the_get_and_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ABU_CC_COLLECT", str(tmp_path / "cc"))
    monkeypatch.setattr("subprocess.call", lambda *a, **k: pytest.fail("a process started"))
    words = ["py", "-m", "unbake.objcache", "store", "9", "get", "build/a.o", "cc", "-O2", "", "as", "d.s"]
    assert cc_probe().probe(words) == cc_probe().OBJECT_MISS
    assert (tmp_path / "cc.obj").read_text() == "\x1f".join(words) + "\n"


def test_the_build_takes_a_batched_answer_without_starting_a_process(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABU_CC_COLLECT", raising=False)
    monkeypatch.setattr("subprocess.call", lambda *a, **k: pytest.fail("a process started"))
    (tmp_path / "build").mkdir()
    (tmp_path / "build/a.i").write_text("i")
    (tmp_path / "build/a.o.oc").write_text("object")
    (tmp_path / "build/a.o.oc.s").write_text("assembly")
    (tmp_path / "build/b.i").write_text("i")
    (tmp_path / "build/b.o.oc.miss").write_text("")
    words = ["py", "get", "build/a.o"]
    assert cc_probe().probe(words) == 0
    assert (tmp_path / "build/a.o").read_text() == "object"
    assert (tmp_path / "build/a.s").read_text() == "assembly"
    assert not (tmp_path / "build/a.o.oc").exists()
    assert cc_probe().probe(["py", "get", "build/b.o"]) == cc_probe().OBJECT_MISS
    assert not (tmp_path / "build/b.o.oc.miss").exists()  # a marker is taken once


def test_an_answer_older_than_its_source_is_not_taken(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABU_CC_COLLECT", raising=False)
    (tmp_path / "a.o.oc").write_text("old object")
    (tmp_path / "a.i").write_text("moved")
    os.utime(tmp_path / "a.o.oc", (1, 1))
    called = []
    monkeypatch.setattr("subprocess.call", lambda words, **k: called.append(words) or 3)
    assert cc_probe().probe(["py", "get", "a.o"]) == 3
    assert called == [["py", "get", "a.o"]]  # nobody batched it: the get itself
    assert not (tmp_path / "a.o").exists()


def test_the_compiles_of_the_objects_the_store_answered_are_dropped(tmp_path):
    from unbake import front

    manifest = record(tmp_path, tmp_path / "store", Path("cc"), SLICE)
    for out in SLICE[:3]:
        (tmp_path / (out + ".oc")).write_text("object")
    cc = tmp_path / "cc-commands.1"
    cc.write_text("".join(f"cc\x1f-o\x1f{out[:-2]}.s\x1f{out[:-2]}.i\n" for out in SLICE))
    assert front._drop_stored(cc, manifest, tmp_path) == 3
    assert [args[1] for _, args in front.cc_cache.commands(str(cc))] == [out[:-2] + ".s" for out in SLICE[3:]]
    front._clear(cc, tmp_path)
    assert not manifest.exists()
    assert not list(tmp_path.rglob("*.oc*"))
