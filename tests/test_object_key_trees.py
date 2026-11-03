"""Two trees of the same sources at different paths give every object the same store key, whatever each tree did
before: a real preprocessor, the real hdrkey and the real object store (rig 11-03: a tree that took a header edit
which moved no code kept the old .i bytes, a fresh tree of the same commit had the new ones, and the object store,
keyed by those bytes, answered the fresh tree nothing)."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from abucache.store import open_cache

from unbake import adapters, objcache
from unbake.resources import cc_cache

pytestmark = pytest.mark.integration("the slice runs the host's cpp and builds hdrkey.c with the host compiler")

OUT = "build/us/src/game/player.c.o"
FLAGS = "gcc_2_8_1_sn64 sh /v/unbake/resources/cc_front.sh /c/content.cc {cc} -G0 -O2 -DVERSION_US"
PROGRAM = "@compile sh /v/unbake/resources/cc_front.sh /c/content.cc {cc} -quiet build/us/src/game/player.c.i"
SOURCE = '#include "game.h"\nint player_speed(void) { return SPEED; }\n'
OLD = "#define SPEED 3\ntypedef struct Camera { int zoom; } Camera;\n"
NEW = "#define SPEED 3\ntypedef struct Camera { int zoom, tilt; } Camera;\n"  # no code reads Camera: no code moves


@pytest.fixture(scope="module")
def hdrkey(tmp_path_factory) -> Path:
    cc, cpp = shutil.which("cc") or shutil.which("gcc"), shutil.which("cpp")
    if cc is None or cpp is None:
        pytest.skip("no host C compiler or preprocessor")
    out = tmp_path_factory.mktemp("hdrkey") / "hdrkey"
    done = subprocess.run([cc, "-O2", "-o", str(out), adapters.make_tool("hdrkey.c")], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out


def toolchain(tmp_path: Path) -> Path:
    compiler = tmp_path / "toolchains" / "gcc-2.8.1-sn64" / "cc1"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"\x7fELF cc1")
    (compiler.parent / ".pin-manifest.json").write_text(json.dumps({"cc1": [1, 8, "ab"]}))
    return compiler


def checkout(root: Path, header: str) -> None:
    (root / "src/game").mkdir(parents=True)
    (root / "include").mkdir()
    (root / "src/game/player.c").write_text(SOURCE)
    (root / "include/game.h").write_text(header)


def preprocess(root: Path, hdrkey: Path) -> int:
    """The `.i.ok` recipe of the object (cc_cache.unit, as PPUNIT runs it): the host cpp -P, settle, the stamp."""
    stamp = OUT[: -len(".o")] + ".i.ok"
    args = ["-P", "-MMD", "-MP", "-MF", stamp[:-5] + ".d", "-MT", stamp, "-Iinclude", "src/game/player.c"]
    with contextlib.chdir(root):
        return cc_cache.unit("", stamp, "F", str(hdrkey), "/bin/true", shutil.which("cpp") or "cpp", args)


def make(root: Path, store: Path, compiler: Path) -> tuple[str, bool]:
    """The object rule's store around a stub program: (the object's key, whether the store answered it)."""
    flags, program = FLAGS.format(cc=compiler), PROGRAM.format(cc=compiler)
    with contextlib.chdir(root):
        cache = open_cache(store, 100_000_000)
        key = objcache.entry(OUT, str(compiler), flags, "", program, [])
        if objcache.get(cache, OUT, str(compiler), flags, "", program, []) == 0:
            return key, True
        Path(OUT).write_bytes(b"object of " + Path(OUT[:-2] + ".i").read_bytes())
        objcache.put(cache, OUT, str(compiler), flags, "", program, [])
        return key, False


def test_two_trees_of_the_same_sources_at_different_paths_share_every_object(tmp_path, hdrkey):
    store, compiler = tmp_path / "store", toolchain(tmp_path)
    # the tree of a long-lived card: built before the header edit, then took it (no code moved, the object stands)
    worked = tmp_path / "runs" / "7-aCUtlvrWcq.9sZLcWpxeKnb" / "Projects/AbuN64/RageWarsDecomp@803d58c3d"
    checkout(worked, OLD)
    assert preprocess(worked, hdrkey) == 0
    made, hit = make(worked, store, compiler)
    assert not hit
    (worked / "include/game.h").write_text(NEW)
    assert preprocess(worked, hdrkey) == 0
    # the installer writes its stat cache again (setup after a toolchain's dates moved)
    (compiler.parent / ".pin-manifest.json").write_text(json.dumps({"cc1": [2, 8, "ab"]}))
    # a fresh tree of the same commit somewhere else: the gate's or a candidate's
    fresh = tmp_path / "elsewhere" / "job-rig-20261103T045121Z" / "RageWarsDecomp@540194332"
    checkout(fresh, NEW)
    assert preprocess(fresh, hdrkey) == 0
    i = OUT[:-2] + ".i"
    assert (worked / i).read_bytes() == (fresh / i).read_bytes()  # the same sources hold the same text
    assert (worked / (i + ".key")).read_bytes() == (fresh / (i + ".key")).read_bytes()
    with contextlib.chdir(worked):
        assert (
            objcache.entry(OUT, str(compiler), FLAGS.format(cc=compiler), "", PROGRAM.format(cc=compiler), []) == made
        )
    key, hit = make(fresh, store, compiler)
    assert (key, hit) == (made, True)
    assert (fresh / OUT).read_bytes() == (worked / OUT).read_bytes()


def test_a_unit_whose_code_moved_is_another_object(tmp_path, hdrkey):
    store, compiler = tmp_path / "store", toolchain(tmp_path)
    one, two = tmp_path / "a" / "RageWarsDecomp", tmp_path / "b" / "RageWarsDecomp"
    checkout(one, OLD)
    checkout(two, OLD.replace("SPEED 3", "SPEED 4"))
    for root in (one, two):
        assert preprocess(root, hdrkey) == 0
    assert make(one, store, compiler)[1] is False
    assert make(two, store, compiler)[1] is False


def test_the_compiler_front_keys_the_toolchain_by_content_not_place_or_installer_dates(tmp_path):
    folders = [tmp_path / "home/abu/.local/share/unbake/toolchains/ido-7.1", tmp_path / "opt/chains/ido-7.1"]
    for n, folder in enumerate(folders):
        folder.mkdir(parents=True)
        (folder / "cc").write_bytes(b"driver")
        (folder / "cfe").write_bytes(b"front")
        (folder / ".pin-manifest.json").write_text(str(n))
    one, two = (cc_cache.identity(str(folder / "cc")) for folder in folders)
    assert one == two
    (folders[1] / "cfe").write_bytes(b"other front")
    cc_cache.IDENTITIES.clear()
    assert cc_cache.identity(str(folders[0] / "cc")) != cc_cache.identity(str(folders[1] / "cc"))
    assert cc_cache.identity(str(folders[0] / "cc")) != cc_cache.identity(str(folders[0] / "cfe"))
    assert objcache.tools(str(folders[0] / "cc")) != objcache.tools(str(folders[1] / "cc"))
    (folders[1] / "cfe").write_bytes(b"front")
    assert objcache.tools(str(folders[0] / "cc")) == objcache.tools(str(folders[1] / "cc"))
