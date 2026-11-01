"""The object rule's store: `python -m unbake.objcache STORE LIMIT get|put OUT CC FLAGS SIZE PROGRAM [FILE...]`
and its counts: `python -m unbake.objcache stats STORE [OFFSET]`.

An object is stored in abucache's content store under abucache.compile's key: the bytes of its `.i`, the program (the
assembler, the passes and their per-file switches, as make expands them), the flags row, the text size it is cut to and
the sum of the extracted files it assembles, with the identity of the toolchain's files. A fresh tree stores every
object it compiles and a later tree with the same inputs copies it. `get` exits 0 on a hit (the object, and the
assembly it was made from, are in place) and MISS when the program has to run.

Every lookup and store goes through abucache's count seam (`ContentCache.count`) into the store's own tally, one line
per event, so the processes of one make run, and of any other tree on the host, add to the same count. `stats` reads
the tally from OFFSET (default 0) to its end: `stores: N`, `hits: N`, `misses: N` and the `offset: N` the next run
starts from; read it before a make run for its OFFSET and after for the counts of that run."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from abucache import compile
from abucache.store import ContentCache, open_cache

MISS = 3  # `get` exit code: no object of this key is stored (the program runs); 0 is a hit
_KIND = "unit"
_STORED = "store"  # a tally line besides abucache's hit and miss: an object put in the store
_TALLY = "objcache.tally"
_PASSES = Path(__file__).parent / "resources" / "make"
# unbake's resources, spelled by whichever install runs make
_INSTALLED = re.compile(r"[^\s'\"]*/unbake/resources(?=/)")


def _tally_path(store: Path) -> Path:
    return store / _TALLY


def _tally(store: Path) -> Callable[[str, bool], None]:
    """abucache's count callback for the store at `store`: one `hit`, `miss` or `store` line per event, appended whole
    (an O_APPEND write of one short line is atomic, so concurrent make jobs never interleave)."""

    def count(kind: str, hit: bool) -> None:
        if kind == _STORED:
            _record(store, _STORED)
        elif kind == _KIND:
            _record(store, "hit" if hit else "miss")

    return count


def _record(store: Path, event: str) -> None:
    with contextlib.suppress(OSError):  # a tally that cannot be written costs only the count
        fd = os.open(_tally_path(store), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, f"{event}\n".encode())
        finally:
            os.close(fd)


def stats(store: Path, offset: int = 0) -> dict[str, int]:
    """The `stores`, `hits` and `misses` tallied from byte `offset` of the tally, and the `offset` of its end."""
    try:
        with _tally_path(store).open("rb") as stream:
            stream.seek(offset)
            lines = stream.read().splitlines(keepends=True)
    except FileNotFoundError:
        lines = []
    whole = [line for line in lines if line.endswith(b"\n")]  # a half-written last line is read by the next call
    events = [line.strip().decode(errors="replace") for line in whole]
    return {
        "stores": events.count(_STORED),
        "hits": events.count("hit"),
        "misses": events.count("miss"),
        "offset": offset + sum(len(line) for line in whole),
    }


def tools(compiler: str) -> str:
    """The identity of the toolchain: every file beside the compiler (a driver such as IDO's cc runs its passes in its
    own folder)."""
    folder = Path(compiler).resolve().parent
    return compile.identity(sorted(p for p in folder.iterdir() if p.is_file()))


def _installed(text: str) -> str:
    """`text` with the place of unbake's own resources (the front script in the flags, the passes in the program)
    named `<unbake>`: a gate job runs from a venv of its own, so that place differs per job, and keyed by it no job
    found an object another had made (9278 compiles from the cache, 450 s of objects made again)."""
    return _INSTALLED.sub("<unbake>", text)


def _passes() -> str:
    """What the passes in the program do: the digest of their text, which the place no longer says."""
    sums = hashlib.sha256()
    for path in sorted(_PASSES.glob("*.py")):
        sums.update(path.name.encode() + b"\0" + path.read_bytes())
    return sums.hexdigest()


def entry(out: str, compiler: str, flags: str, textsize: str, program: str, extracted: Sequence[str]) -> str:
    """The store key of the object `out` the program would make."""
    sums = hashlib.sha256()
    for name in extracted:
        with contextlib.suppress(OSError):
            sums.update(Path(name).read_bytes())
    source = out.removesuffix(".o") + ".i"
    step = compile.Step(
        "object", Path(out).name, (_installed(flags), textsize, _installed(program), _passes(), sums.hexdigest())
    )
    return compile.key(Path(source).read_bytes(), Path(source).name, [step], tools(compiler))


def frame(obj: bytes, assembly: bytes | None) -> bytes:
    """An entry: the lengths of the object and of its assembly (-1: none), then the two."""
    return f"{len(obj)} {-1 if assembly is None else len(assembly)}\n".encode() + obj + (assembly or b"")


def unframe(blob: bytes) -> tuple[bytes, bytes | None]:
    """The object and assembly of an entry; a torn or foreign one raises ValueError."""
    head, _, body = blob.partition(b"\n")
    size, extra = (int(n) for n in head.split())
    if size < 0 or len(body) != size + max(extra, 0):
        raise ValueError("torn entry")
    return body[:size], None if extra < 0 else body[size:]


def _replace(path: str, data: bytes) -> None:
    part = f"{path}.{os.getpid()}.part"  # a copy, never a link: the trim that follows rewrites the object in place
    Path(part).write_bytes(data)
    os.replace(part, path)


def get(
    cache: ContentCache, out: str, compiler: str, flags: str, textsize: str, program: str, extracted: Sequence[str]
) -> int:
    """A hit puts the stored object (and the assembly it was made from) in place (0); else MISS."""
    try:
        blob = cache.get(_KIND, entry(out, compiler, flags, textsize, program, extracted))
        if blob is None:
            cache.count(_KIND, False)
            return MISS
        obj, assembly = unframe(blob)
        if assembly is not None:
            _replace(out.removesuffix(".o") + ".s", assembly)
        _replace(out, obj)
    except (OSError, ValueError):
        return MISS
    cache.count(_KIND, True)
    return 0


def put(
    cache: ContentCache, out: str, compiler: str, flags: str, textsize: str, program: str, extracted: Sequence[str]
) -> int:
    """Store the object the program made; a full or unreadable store costs only the saving."""
    with contextlib.suppress(OSError):
        assembly = Path(out.removesuffix(".o") + ".s")
        blob = frame(Path(out).read_bytes(), assembly.read_bytes() if assembly.is_file() else None)
        cache.put(_KIND, entry(out, compiler, flags, textsize, program, extracted), blob)
        cache.count(_STORED, True)
    return 0


def main(argv: list[str]) -> int:
    """OBJSTORE is `objcache STORE LIMIT`; the rule appends `get|put OUT CC FLAGS SIZE PROGRAM FILE...`."""
    if len(argv) in (3, 4) and argv[1] == "stats":
        for name, value in stats(Path(argv[2]), int(argv[3]) if len(argv) == 4 else 0).items():
            sys.stdout.write(f"{name}: {value}\n")
        return 0
    if len(argv) < 9 or argv[3] not in ("get", "put"):
        raise SystemExit("usage: STORE LIMIT get|put OUT CC FLAGS SIZE PROGRAM [FILE...]; stats STORE [OFFSET]")
    action = get if argv[3] == "get" else put
    cache = open_cache(Path(argv[1]), int(argv[2]), _tally(Path(argv[1])))
    out, compiler, flags, textsize, program = argv[4:9]
    return action(cache, out, compiler, flags, textsize, program, argv[9:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
