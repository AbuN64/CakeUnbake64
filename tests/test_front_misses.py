"""Two rounds of one build leave the same kind of line per missed compile and object, so they can be diffed by output
name: the front's lines come from the records the batches left, the compiles first, then the objects."""

from __future__ import annotations

import sys
from pathlib import Path

from unbake import front


def test_a_round_says_a_line_per_missed_compile_and_object_and_a_hit_none(tmp_path):
    root = tmp_path / "t"
    root.mkdir()
    cc = tmp_path / "cc1"
    cc.write_text("#!/bin/sh\n")
    manifest = root / "cc-commands.1"
    manifest.write_text("".join(f"{cc}\x1f-O2\x1fu{n}.i\x1f-o\x1fu{n}.s\n" for n in range(2)))
    for n in range(2):
        (root / f"u{n}.i").write_text(f"int u{n};")
        (root / f"u{n}.s.cc.miss").write_text(f"compiler=aa head=bb text=c{n} key=dd{n}\n")
    objects = manifest.with_name(manifest.name + ".obj")
    objects.write_text(
        "\x1f".join([sys.executable, "-m", "unbake.objcache", "store", "9", "get", "o.o", str(cc), "-O2", "", "as"])
        + "\n"
    )
    (root / "o.o.oc.miss").write_text("flags=ff tools=tt key=kk\n")
    assert front._misses(manifest, objects, root) == [
        "make.front: miss compile u0.s compiler=aa head=bb text=c0 key=dd0",
        "make.front: miss compile u1.s compiler=aa head=bb text=c1 key=dd1",
        "make.front: miss object o.o flags=ff tools=tt key=kk",
    ]
    (root / "u1.s.cc.miss").unlink()  # a hit left no record
    (root / "o.o.oc.miss").unlink()
    assert len(front._misses(manifest, objects, root)) == 1
    front._clear(manifest, root)
    assert not (root / "u0.s.cc.miss").exists() and not objects.exists()


def test_build_says_the_miss_lines_after_the_count_line_and_clears_them(tmp_path, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(front.stages, "say", said.append)
    monkeypatch.setattr(front.process, "scratch", lambda root: root)
    cc = tmp_path / "cc1"

    def start(record: str | None, preprocess: bool) -> str:
        if record is not None:  # the collecting run records one compile and one object
            Path(record).write_text(f"{cc}\x1f-O2\x1fu.i\x1f-o\x1fu.s\n")
            Path(record + ".obj").write_text(
                "\x1f".join([sys.executable, "-m", "unbake.objcache", "s", "9", "get", "o.o", str(cc), "-O2", "", "as"])
                + "\n"
            )
        return "built"

    def batch(store: Path, root: Path, manifest: Path, workers: int, mode: str = "--batch") -> None:
        (root / "u.s.cc.miss").write_text("compiler=aa head=bb text=cc key=dd\n")

    def objects(root: Path, manifest: Path, workers: int) -> None:
        (root / "o.o.oc.miss").write_text("flags=ff key=kk\n")

    monkeypatch.setattr(front.process, "batch", batch)
    monkeypatch.setattr(front.process, "objects", objects)
    assert front.build(start, tmp_path, tmp_path / "store", 2) == "built"
    assert said[0] == "make.front: 0 objects from the store"
    assert said[1:] == [
        "make.front: 0 of 1 compiles from the cache, 1 compile",
        "make.front: miss compile u.s compiler=aa head=bb text=cc key=dd",
        "make.front: miss object o.o flags=ff key=kk",
    ]
    assert not (tmp_path / "u.s.cc.miss").exists() and not (tmp_path / "o.o.oc.miss").exists()
