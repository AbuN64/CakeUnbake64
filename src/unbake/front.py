"""One build, two make runs: the compile commands a build needs are answered in a few batch processes between them,
so make starts no python front per source (about ten thousand of them after a header edit)."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from abucache import stages

from unbake import process
from unbake.contracts import Host
from unbake.resources import cc_cache


def cache(host: Host) -> Path | None:
    """The directory of the compiler front's assembly cache, None on a host without a content cache."""
    content = getattr(host, "cache_dir", None)
    return None if content is None else content.with_name(f"{content.name}.cc")


def build[Result](start: Callable[[str | None, bool], Result], root: Path, store: Path | None, workers: int) -> Result:
    """Run the make that `start(record, preprocess)` starts, in `root`, with the compile commands answered first.
    `start` runs it once; with a `record` file name it sets ABU_CC_COLLECT to it and keeps going past failed units
    (make -k), and with `preprocess` it also sets ABU_PP_COLLECT to `<record>.pp`.
    The front (resources/cc_front.sh) first only records each compile command the build needs and fails it, so that
    run does the preprocessing and finds the stale units; one batch of `workers` processes (resources/cc_cache.py)
    then answers every unit the cache can; the second run is the build, taking those answers and compiling the rest.
    A first run that recorded nothing was the build, and a host without a cache runs once. Preprocessing is batched
    the same way before that: a run that finds many stale stamps records their commands (past the first few, which it
    makes itself), one batch makes them all, and the compile commands are recorded by the run after it."""
    if store is None:
        return start(None, False)
    manifest = process.scratch(root) / f"cc-commands.{os.getpid()}"
    stamps = manifest.with_name(manifest.name + ".pp")
    objects = manifest.with_name(manifest.name + ".obj")
    _clear(manifest, root)
    try:
        first = start(str(manifest), True)
        if stamps.exists():
            with stages.stage("make.front.preprocess") as span:
                process.batch(store, root, stamps, workers, "--units")
                span.add(items=len(cc_cache.commands(str(stamps))))
            stamps.unlink()
            manifest.unlink(missing_ok=True)
            objects.unlink(missing_ok=True)
            first = start(str(manifest), False)
        if not manifest.exists():
            return first
        if objects.exists():
            with stages.stage("make.front.objects") as span:
                process.objects(root, objects, workers)
                found = _drop_stored(manifest, objects, root)
                span.add(items=found)
                stages.say(f"make.front: {found} objects from the store")
        with stages.stage("make.front") as span:
            process.batch(store, root, manifest, workers)
            answered, total = _answered(manifest, root)
            span.add(items=total)
            stages.say(f"make.front: {answered} of {total} compiles from the cache, {total - answered} compile")
            for line in _misses(manifest, objects, root):
                stages.say(line)
        return start(None, False)
    finally:
        _clear(manifest, root)


def _recorded(objects: Path) -> list[str]:
    """The object each get the build recorded (the words of each, `get OUT ...`) puts in place."""
    return [
        row[row.index("get") + 1]
        for row in (
            line.rstrip("\n").split("\x1f") for line in objects.read_text(errors="surrogateescape").splitlines()
        )
        if "get" in row[:-1]
    ]


def _drop_stored(manifest: Path, objects: Path, root: Path) -> int:
    """Remove from the recorded compiles those of the objects the store answered (`<out>.oc`): the object is taken as it
    is, so no compile is asked for it. Returns how many objects the store answered."""
    stored = {out.removesuffix(".o") + ".s" for out in _recorded(objects) if (root / (out + ".oc")).exists()}
    kept = [
        cc_cache.SEPARATOR.join([compiler, *args])
        for compiler, args in cc_cache.commands(str(manifest))
        if ((p := cc_cache.parse(args)) is None or p[1] not in stored)
    ]
    manifest.write_text("".join(f"{row}\n" for row in kept), errors="surrogateescape")
    return len(stored)


def _answered(manifest: Path, root: Path) -> tuple[int, int]:
    """(compiles the batch answered, compiles the build recorded): the rest run the compiler in the second make."""
    parsed = [p for _, args in cc_cache.commands(str(manifest)) if (p := cc_cache.parse(args)) is not None]
    return sum((root / (out + ".cc")).exists() for _, out in parsed), len(parsed)


def _misses(manifest: Path, objects: Path, root: Path) -> list[str]:
    """One line per compile (`<out>.cc.miss`) and per object (`<out>.oc.miss`) the batches could not answer, from the
    records they left: the output and the digests of the parts of its key, to diff two rounds by output name."""
    lines = []
    outs = [(f"{out}.cc.miss", "compile", out) for _, out in _parsed(manifest)]
    if objects.exists():
        outs += [(f"{out}.oc.miss", "object", out) for out in _recorded(objects)]
    for name, kind, out in outs:
        try:
            record = (root / name).read_text(errors="surrogateescape").strip()
        except OSError:
            continue
        lines.append(f"make.front: miss {kind} {out} {record}")
    return lines


def _parsed(manifest: Path) -> list[tuple[str, str]]:
    return [p for _, args in cc_cache.commands(str(manifest)) if (p := cc_cache.parse(args)) is not None]


def _clear(manifest: Path, root: Path) -> None:
    """Remove the record and any answer a unit left unclaimed, so a later build never takes one for another text."""
    if manifest.exists():
        for _, args in cc_cache.commands(str(manifest)):
            if (parsed := cc_cache.parse(args)) is not None:
                for suffix in (".cc", ".cc.miss"):
                    (root / (parsed[1] + suffix)).unlink(missing_ok=True)
    objects = manifest.with_name(manifest.name + ".obj")
    if objects.exists():
        for out in _recorded(objects):
            for suffix in (".oc", ".oc.s", ".oc.miss"):
                (root / (out + suffix)).unlink(missing_ok=True)
    for name in (
        manifest,
        manifest.with_name(manifest.name + ".pp"),
        manifest.with_name(manifest.name + ".pp.n"),
        objects,
    ):
        name.unlink(missing_ok=True)
