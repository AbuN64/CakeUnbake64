"""The object rule's store: `python -m unbake.objcache get|put STORE LIMIT OUT CC FLAGS SIZE PROGRAM [FILE...]`.

An object is stored in abucache's content store under abucache.compile's key: the bytes of its `.i`, the program (the
assembler, the passes and their per-file switches, as make expands them), the flags row, the text size it is cut to and
the sum of the extracted files it assembles, with the identity of the toolchain's files. A fresh tree stores every
object it compiles and a later tree with the same inputs copies it. `get` exits 0 on a hit (the object, and the
assembly it was made from, are in place) and MISS when the program has to run."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from abucache import compile
from abucache.store import ContentCache, open_cache

MISS = 3  # `get` exit code: no object of this key is stored (the program runs); 0 is a hit
_KIND = "unit"


def tools(compiler: str) -> str:
    """The identity of the toolchain: every file beside the compiler (a driver such as IDO's cc runs its passes in its
    own folder)."""
    folder = Path(compiler).resolve().parent
    return compile.identity(sorted(p for p in folder.iterdir() if p.is_file()))


def entry(out: str, compiler: str, flags: str, textsize: str, program: str, extracted: Sequence[str]) -> str:
    """The store key of the object `out` the program would make."""
    sums = hashlib.sha256()
    for name in extracted:
        with contextlib.suppress(OSError):
            sums.update(Path(name).read_bytes())
    source = out.removesuffix(".o") + ".i"
    step = compile.Step("object", Path(out).name, (flags, textsize, program, sums.hexdigest()))
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
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 9 or argv[1] not in ("get", "put"):
        raise SystemExit("usage: objcache get|put STORE LIMIT OUT COMPILER FLAGS TEXTSIZE PROGRAM [EXTRACTED...]")
    action = get if argv[1] == "get" else put
    cache = open_cache(Path(argv[2]), int(argv[3]))
    out, compiler, flags, textsize, program = argv[4:9]
    return action(cache, out, compiler, flags, textsize, program, argv[9:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
