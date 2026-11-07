"""objects: a slice of a base compiled without a link, one TSV row per unit with its object SHA-1 and cache answer."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import fixture
import pytest

from unbake import build, objcache, objects, store
from unbake.contracts import Refusal

UNITS = {"a": ["src/one.o", "src/two.o"], "b": ["src/one.o"]}


def _tree(cfg):
    root = cfg.project.root
    for version, units in UNITS.items():
        folder = root / "build" / version
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "objs.mk").write_text(
            f"OBJS_{version} := " + " \\\n  ".join(f"build/{version}/{u}" for u in units) + "\n"
        )
    return root


def _fake_make(cfg, hits, broken=()):
    """make as the object rules do it: a lookup tallied per object, an object per unit (none for `broken`)."""
    content = store.content_directory(cfg.host)
    content.mkdir(parents=True, exist_ok=True)
    count = objcache._tally(content)

    def make(config, targets):
        for target in targets:
            objcache._current = target
            count("unit", target in hits)
            if target not in broken:
                path = config.project.root / target
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(target.encode())
        return SimpleNamespace(exit=1 if broken else 0)

    return make


def test_every_unit_of_every_version_gets_a_row_with_its_sha1_and_cache_answer(tmp_path, monkeypatch):
    cfg = fixture.config(tmp_path)
    root = _tree(cfg)
    monkeypatch.setattr(build, "make", _fake_make(cfg, hits={"build/a/src/one.o"}))
    result = objects.run(cfg, {})
    assert result["versions"] == ["a", "b"] and result["units"] == 3
    assert result["cache"] == {"hit": 1, "miss": 2, "none": 0}
    rows = (root / ".unbake/objects.tsv").read_text().splitlines()
    sha = hashlib.sha1(b"build/a/src/two.o").hexdigest()
    assert rows[0] == "unit\tversion\tsha1\tcache"
    assert f"src/two.o\ta\t{sha}\tmiss" in rows
    assert rows[1].split("\t")[0::3] == ["src/one.o", "hit"]
    assert len(rows) == 4


def test_a_named_version_alone_is_compiled_to_the_named_file(tmp_path, monkeypatch):
    cfg = fixture.config(tmp_path)
    root = _tree(cfg)
    monkeypatch.setattr(build, "make", _fake_make(cfg, hits=set()))
    result = objects.run(cfg, {"version": ["b"], "tsv": "base.tsv"})
    assert result["tsv"] == "base.tsv" and result["units"] == 1
    assert [r.split("\t")[1] for r in (root / "base.tsv").read_text().splitlines()[1:]] == ["b"]
    assert not (root / "build/a/src/one.o").exists()


def test_an_unknown_version_is_refused_before_any_compile(tmp_path):
    cfg = fixture.config(tmp_path)
    with pytest.raises(Refusal) as refused:
        objects.run(cfg, {"version": ["zz"]})
    assert refused.value.findings[0].key == "objects.input"


def test_a_unit_that_made_no_object_is_marked_failed_and_refused_after_the_tsv(tmp_path, monkeypatch):
    cfg = fixture.config(tmp_path)
    root = _tree(cfg)
    monkeypatch.setattr(build, "make", _fake_make(cfg, hits=set(), broken={"build/a/src/two.o"}))
    with pytest.raises(Refusal) as refused:
        objects.run(cfg, {"version": ["a"]})
    assert refused.value.findings[0].key == "objects.compile"
    assert "src/two.o\ta\t-\tfailed" in (root / ".unbake/objects.tsv").read_text().splitlines()


def test_units_reads_the_lookup_of_each_object_from_an_offset(tmp_path):
    objcache._current = "build/a/x.o"
    count = objcache._tally(tmp_path)
    count("unit", False)
    count("store", True)
    mark = objcache.stats(tmp_path)["offset"]
    objcache._current = "build/a/y.o"
    count("unit", True)
    assert objcache.units(tmp_path) == {"build/a/x.o": "miss", "build/a/y.o": "hit"}
    assert objcache.units(tmp_path, mark) == {"build/a/y.o": "hit"}
