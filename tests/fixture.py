"""Test support: schema-4 projects, host files and small native-shaped values. Tests `import fixture`."""

from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any

import fakecpp

from unbake.contracts import Config, Json, NativeResult, Proof, Submission, digest, sha1, sha256

TOOLS = (*("git", "make", "cpp", "mips_as", "mips_ld", "mips_objcopy", "mips_objdump"), *("abumas", "splat", "armips"))


def _value(item: Any) -> str:
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, (int, float)):
        return repr(item)
    if isinstance(item, str):
        return json.dumps(item)
    if isinstance(item, Mapping):
        return "{" + ", ".join(f"{k} = {_value(v)}" for k, v in item.items()) + "}"
    return "[" + ", ".join(_value(v) for v in item) + "]"


def _table(name: str, body: Mapping[str, Any]) -> str:
    return f"[{name}]\n" + "".join(f"{k} = {_value(v)}\n" for k, v in body.items()) + "\n"


def _rows(name: str, rows: Sequence[Json]) -> str:
    return "".join(f"[[{name}]]\n" + "".join(f"{k} = {_value(v)}\n" for k, v in row.items()) + "\n" for row in rows)


def _write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    argv = ["git", "-C", str(root), "-c", "commit.gpgsign=false", *args]
    subprocess.run(argv, check=True, capture_output=True, env=env)


FAKE_CPP = """#!{python}
import sys
sys.path.insert(0, {tests!r})
import fakecpp
fakecpp.main()
"""  # the program form of tests/fakecpp.py, for a real process (make, a subprocess); unit tests answer it in process
TESTS = Path(__file__).resolve().parent

# Programs the fixture writes that unit tests answer in process (conftest routes process.run here): a program path ->
# fn(argv, cwd, stdin) -> (exit, stdout, stderr). The program files stay real for the integration set.
Fake = Callable[[Sequence[str], Path, bytes], tuple[int, bytes, bytes]]
FAKES: dict[Path, tuple[Fake, tuple[int, int]]] = {}


def _stat(path: Path) -> tuple[int, int]:
    found = path.stat()
    return found.st_size, found.st_mtime_ns


def fake(program: str | Path) -> Fake | None:
    """The in-process stand-in of a program the fixture wrote, while the file is still the one it wrote (a test that
    rewrites the program gets the program)."""
    path = Path(program)
    entry = FAKES.get(path)
    if entry is None:
        return None
    try:
        return entry[0] if _stat(path) == entry[1] else None
    except OSError:
        return None


def _cpp(argv: Sequence[str], cwd: Path, stdin: bytes) -> tuple[int, bytes, bytes]:
    program, args = Path(argv[0]), [str(a) for a in argv[1:]]
    return 0, fakecpp.run(program, args, stdin.decode(), cwd).encode(), b""


def _true(argv: Sequence[str], cwd: Path, stdin: bytes) -> tuple[int, bytes, bytes]:
    return 0, b"", b""


class Repo:
    """The committed state of a fixture project, standing in for its git repository in unit tests: conftest answers
    process.tracked/tree_id/dirty/changed/blob/show/spelling from it. `fixture.project(..., git=True)` makes a real
    repository instead (the integration set)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
        self.tree = sha1(json.dumps({k: sha1(v) for k, v in self.files.items()}, sort_keys=True).encode())

    COMMIT = "c" * 40

    def head(self, rev: str) -> bool:
        return rev.removesuffix("^{tree}").removesuffix("^{commit}") in ("HEAD", "main", self.COMMIT, self.tree)

    def changed(self) -> list[str]:
        out = []
        for path, data in self.files.items():
            file = self.root / path
            if not file.is_file() or file.read_bytes() != data:
                out.append(path)
        return out

    def on_disk(self) -> list[str]:
        """The work tree's files git would list: never .git, never a folder whose .gitignore ignores everything."""
        found = []
        for folder, dirs, names in os.walk(self.root):
            here = Path(folder)
            dirs[:] = sorted(
                d for d in dirs if d != ".git" and not ((here / d / ".gitignore").is_file() and
                                                        (here / d / ".gitignore").read_text().strip() == "*")
            )  # fmt: skip
            found += [(here / n).relative_to(self.root).as_posix() for n in names]
        return sorted(found)

    def git(self, argv: Sequence[str], cwd: Path, stdin: bytes) -> tuple[int, bytes, bytes]:
        """The git subcommands the code asks of a fixture project, answered from the committed state."""
        args = [str(a) for a in argv[1:]]
        while args and args[0] in ("-c", "-C"):
            args = args[2:]
        command, rest = args[0], args[1:]
        flags = [a for a in rest if a.startswith("-")]
        operands = [a for a in rest if not a.startswith("-")]
        z = "-z" in flags
        if command == "rev-parse":
            rev = operands[-1]
            if not self.head(rev):
                return 128, b"", f"fatal: bad revision {rev}\n".encode()
            return 0, ((self.tree if rev.endswith("^{tree}") else self.COMMIT) + "\n").encode(), b""
        if command == "ls-files":
            cached = not ({"-o", "--others"} & set(flags)) or bool({"-c", "--cached", "-co"} & set(flags))
            others = bool({"-o", "--others", "-co"} & set(flags))
            disk = set(self.on_disk())
            names = (set(self.files) if cached else set()) | ((disk - set(self.files)) if others else set())
            wanted = [o.rstrip("/") for o in operands]
            names = {n for n in names if not wanted or any(n == w or n.startswith(w + "/") for w in wanted)}
            return 0, "".join(n + ("\0" if z else "\n") for n in sorted(names)).encode(), b""
        if command == "status":
            lines = [f" {'M' if (self.root / p).is_file() else 'D'} {p}" for p in self.changed()]
            if "--untracked-files=no" not in flags:
                lines += [f"?? {p}" for p in self.on_disk() if p not in self.files]
            return 0, "".join(line + "\n" for line in lines).encode(), b""
        if command == "ls-tree" and self.head(operands[0]):
            hits = [p for p in self.files if any(p == o for o in operands[1:])]
            return 0, "".join(f"100644 blob {sha1(self.files[p])}\t{p}\n" for p in hits).encode(), b""
        if command == "show" and ":" in operands[0]:
            rev, _, path = operands[0].partition(":")
            if self.head(rev) and path in self.files:
                return 0, self.files[path], b""
            return 128, b"", f"fatal: path '{path}' does not exist in '{rev}'\n".encode()
        raise AssertionError(f"fixture.Repo does not answer git {command}: make the project with git=True")


REPOS: dict[Path, Repo] = {}


def repo(root: Path) -> Repo | None:
    """The stand-in repository of a fixture project at or above root, if root is one."""
    root = Path(root).resolve()
    return REPOS.get(root)


SEGMENTS = {"main": (0x1000, 0x80000400), "ovl": (0x3000, 0x80100000)}
SOURCES = {
    "src/main/c_func.c": "s32 c_func(void) {\n    return 1;\n}\n",
    "src/main/stand_in.c": '#include "include_asm.h"\n\nINCLUDE_ASM(main, stand_in);\n',
    "src/ovl/fuzzy.c": (
        '#include "include_asm.h"\n\n#ifdef NON_MATCHING\ns32 fuzzy(s32 a) {\n    return a + 1;\n}\n#else\n'
        "INCLUDE_ASM(ovl, fuzzy);\n#endif\n"
    ),
}
HEADERS = {
    "include/include_asm.h": (
        '#define INCLUDE_ASM(SEG, NAME) __asm__(".include \\"nonmatchings/" #SEG "/" #NAME ".s\\"")\n'
    )
}
# name -> (segment, offset in the segment, type) in every version; version b is laid 0x10 later in ovl
SYMBOLS = {"c_func": ("main", 0x0, "func"), "stand_in": ("main", 0x10, "func"), "fuzzy": ("ovl", 0x0, "func")}


def symbol_file(version: str, rows: Mapping[str, tuple[str, int, str]] = MappingProxyType(SYMBOLS)) -> str:
    """versions/<v>/symbol_addrs.txt text; the second version is laid 0x10 further into ovl."""
    shift = 0x10 if version != "a" else 0
    lines = []
    for name, (segment, offset, kind) in rows.items():
        rom, vram = SEGMENTS[segment]
        at = offset + (shift if segment == "ovl" else 0)
        lines.append(f"{name} = 0x{vram + at:08X}; // type:{kind} rom:0x{rom + at:X} size:0x10\n")
    return "".join(lines)


def project(
    tmp: Path,
    *,
    versions: Sequence[str] = ("a", "b"),
    sources: Mapping[str, str] | None = None,
    symbols: Mapping[str, str] | None = None,
    extra: Mapping[str, str | bytes] = MappingProxyType({}),
    git: bool = False,
) -> Path:
    """Write a committed schema-4 project under tmp/project: two segments, three modules (a C function, an
    INCLUDE_ASM stand-in, a NON_MATCHING body). sources replace src/, symbols replace a version's symbol file.
    Committed in a stand-in repository (Repo), or with `git` in a real one."""
    root = tmp / "project"
    root.mkdir(parents=True)
    rom = bytes(0x4000)
    segments = [{"name": n, "rom": r, "vram": v} for n, (r, v) in SEGMENTS.items()]
    document = (
        'schema = 4\nname = "Fixture"\ntoolchain = "gcc-test"\nnames_from = "a"\ncppflags = ["-DGAME"]\n\n'
        + "".join(_table(f"version.{v}", {"sha1": sha1(rom), "size": len(rom), "segments": segments}) for v in versions)
    )
    _write(root / "config.toml", document)
    for v in versions:
        _write(root / f"roms/baserom.{v}.z64", rom)
        _write(root / f"versions/{v}/symbol_addrs.txt", (symbols or {}).get(v, symbol_file(v)))
    for relative, text in {**HEADERS, **(SOURCES if sources is None else sources), **extra}.items():
        _write(root / relative, text)
    if git:
        _git(root, "init", "-q", "-b", "main")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "fixture project")
    else:
        REPOS[root.resolve()] = Repo(root.resolve())
    return root


def host(tmp: Path, **overrides: Any) -> Path:
    """Write tmp/host.toml, every tool a copy of true in tmp/bin.

    overrides: dotted keys such as resources.workers; cores and workers may be "auto".
    """
    tmp = tmp.resolve()
    (tmp / "bin").mkdir(parents=True, exist_ok=True)
    true = shutil.which("true")
    if true is None:
        raise RuntimeError("the true executable is required for the fixture host")
    tools = {}
    for name in TOOLS:
        shutil.copy2(true, tmp / "bin" / name)
        tools[name] = str(tmp / "bin" / name)
    tools["git"] = shutil.which("git") or tools["git"]  # the tree lists files through the real git
    fake = tmp / "bin" / "cpp"
    fake.unlink()
    fake.write_text(FAKE_CPP.format(python=sys.executable, tests=str(TESTS)))
    fake.chmod(0o755)
    FAKES[fake] = (_cpp, _stat(fake))
    FAKES.update((Path(p), (_true, _stat(Path(p)))) for name, p in tools.items() if name not in ("git", "cpp"))
    document: dict[str, dict[str, Any]] = {
        "resources": {
            "cores": 2,
            "workers": 2,
            "memory_parent_bytes": 1 << 30,
            "memory_worker_bytes": 1 << 30,
            "move_wait_s": 5,
        },
        "cache": {"dir": str(tmp / "cache"), "max_bytes": 1 << 30},
        "toolchains": {"root": str(tmp / "toolchains")},
        "tools": tools,
        "budgets": {"serial_seconds": 2, "serial_cores": 2, "pool_fill": 0.8, "pool_fanout": 2},
        "publish": {"author_name": "test", "author_email": "test@example.invalid"},
    }
    for dotted, value in overrides.items():
        section, _, key = dotted.partition(".")
        document.setdefault(section, {})[key] = value
    path = tmp / "host.toml"
    _write(path, "schema = 2\n\n" + "".join(_table(name, body) for name, body in document.items()))
    return path


def config(tmp: Path, **kw: Any) -> Config:
    """project() + host() + the config they describe. kw go to project(); kw["host"] is a dict of host overrides.

    The project record has the schema-4 shape (versions, version[v].segments, cppflags, digest) of contracts.Project."""
    from unbake import config as configuration

    overrides = kw.pop("host", {})
    root = project(tmp, **kw)
    names = sorted(v for v in ("a", "b") if (root / f"roms/baserom.{v}.z64").exists())
    version = {
        v: SimpleNamespace(
            sha1=sha1(bytes(0x4000)),
            size=0x4000,
            segments=tuple(
                SimpleNamespace(name=n, rom=r, vram=m, kind="code", window_of=None) for n, (r, m) in SEGMENTS.items()
            ),
        )
        for v in names
    }
    facts = SimpleNamespace(
        root=root, name="Fixture", title="Fixture", toolchain="gcc-test", names_from="a", libraries=(),
        cppflags=("-DGAME",), cflags=(), asflags=(), versions=tuple(names), version=version, files={}, digest="project",
    )  # fmt: skip
    host_config = configuration.load_host(host(tmp, **overrides))
    return Config(facts, host_config, "config")  # type: ignore[arg-type]


def elf_object(names: Sequence[str]) -> bytes:
    """An ELF32 big-endian MIPS object that holds only a symbol table naming NAMES."""
    strtab = b"\0" + b"\0".join(n.encode() for n in names) + b"\0"
    offsets = [1 + sum(len(n) + 1 for n in names[:i]) for i in range(len(names))]
    symtab = b"\0" * 16 + b"".join(struct.pack(">IIIBBH", o, 0, 0, 0x10, 0, 0) for o in offsets)
    shstr = b"\0.symtab\0.strtab\0.shstrtab\0"
    end = 52 + len(strtab) + len(symtab) + len(shstr)
    symbols_at = 52 + len(strtab)
    rows = [
        (0,) * 10,
        (1, 2, 0, 0, symbols_at, len(symtab), 2, 0, 4, 16),
        (9, 3, 0, 0, 52, len(strtab), 0, 0, 1, 0),
        (17, 3, 0, 0, symbols_at + len(symtab), len(shstr), 0, 0, 1, 0),
    ]
    fields = struct.pack(">HHIIIIIHHHHHH", 1, 8, 1, 0, 0, end, 0, 52, 0, 0, 40, 4, 3)
    header = b"\x7fELF\x01\x02\x01" + b"\0" * 9 + fields
    return header + strtab + symtab + shstr + b"".join(struct.pack(">10I", *r) for r in rows)


def native_result(
    argv: Sequence[str] = ("tool",),
    exit: int | None = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    cpu: float = 0.0,
    outputs: Mapping[str, Path] = MappingProxyType({}),
) -> NativeResult:
    return NativeResult(tuple(argv), "/", exit, None, stdout, stderr, 0.0, cpu, 0, dict(outputs))


def proof(
    unit: str,
    member: str,
    version: str,
    exact: bool,
    missing: Sequence[str] = (),
    score: float | None = None,
    symptoms: Json | None = None,
) -> Proof:
    """Test-helper defaults only: score 1.0 when exact else 0.5; symptoms {} when exact else bytes_differ."""
    score = (1.0 if exact else 0.5) if score is None else score
    if symptoms is None:
        symptoms = {} if exact else {"bytes_differ": True, "score": score}
    if exact == bool(missing):
        raise ValueError("a Proof is exact with no missing pieces, or not exact with at least one")
    sha = sha256(f"{unit}:{member}:{version}".encode())
    built = sha if exact else sha256(sha.encode())
    return Proof(unit, member, version, "recipe", sha, sha, built, sha, exact, tuple(missing), score, symptoms)


def submission(**fields: Any) -> Submission:
    """A complete Submission for tests; fields override the defaults."""
    member, source_sha = fields.get("member", "func_80000400"), "0" * 64
    values: dict[str, Any] = {"operation": "publish", "member": member, "function": None, "source_sha256": source_sha}
    values |= {"overrides": {"add": [], "omit": []}, "base": "0" * 40, "origin": "submit", "note": ""}
    values |= {"invocation": "test-invocation", "extras": {}, "extra_bases": {}, "withheld": ()}
    values |= fields
    values.setdefault("proofs", (proof(f"src/{member}.c", member, "a", True),))
    id_basis = (values["member"], values["source_sha256"], values["overrides"], values["operation"])
    values.setdefault("id", digest(id_basis))
    values.setdefault("source", f".unbake/inbox/{values['id']}.c")
    return Submission(**values)


def rom_bytes(game_code: bytes = b"NXX", region: bytes = b"E", order: str = "z64", body: bytes = b"") -> bytes:
    """A 0x101000-byte ROM image: z64 magic, game code at 0x3B, region at 0x3E, body at 0x1000.

    order is z64, v64 (byte pairs swapped) or n64 (little-endian words), the orders rom.toml names.
    """
    if len(game_code) != 3 or len(region) != 1:
        raise ValueError("game_code is 3 bytes and region is 1 byte")
    image = bytearray(0x101000)
    image[0:4] = bytes.fromhex("80371240")
    image[0x20:0x34] = b"FIXTURE".ljust(20, b" ")
    image[0x3B:0x3E], image[0x3E:0x3F] = game_code, region
    image[0x1000 : 0x1000 + len(body)] = body
    if order == "z64":
        return bytes(image)
    if order == "v64":
        image[0::2], image[1::2] = image[1::2], image[0::2]
    elif order == "n64":
        original = bytes(image)
        for lane in range(4):
            image[lane::4] = original[3 - lane :: 4]
    else:
        raise ValueError(f"order {order!r} is not z64, v64 or n64")
    return bytes(image)


def preprocessed(files: Mapping[str, str]) -> str:
    """Concatenate files as cpp output: a '# 1 "file"' marker, then the text."""
    return "".join(f'# 1 "{name}"\n{text.rstrip(chr(10))}\n' for name, text in files.items())


def elf_with_symbols(defined: Sequence[str], undefined: Sequence[str]) -> bytes:
    """Minimal big-endian MIPS ELF32 .o: .text (jr ra; nop), global FUNC symbols, undefined NOTYPE symbols."""
    text = struct.pack(">II", 0x03E00008, 0)
    names, strtab = {}, b"\0"
    for name in (*defined, *undefined):
        names[name] = len(strtab)
        strtab += name.encode() + b"\0"
    symtab = struct.pack(">IIIBBH", 0, 0, 0, 0, 0, 0)
    symtab += b"".join(struct.pack(">IIIBBH", names[n], 0, 8, 0x12, 0, 1) for n in defined)
    symtab += b"".join(struct.pack(">IIIBBH", names[n], 0, 0, 0x10, 0, 0) for n in undefined)
    shstr, section_names = b"\0", {}
    for name in (".text", ".symtab", ".strtab", ".shstrtab"):
        section_names[name] = len(shstr)
        shstr += name.encode() + b"\0"
    body, offsets = b"", {}
    for name, blob in ((".text", text), (".symtab", symtab), (".strtab", strtab), (".shstrtab", shstr)):
        body += b"\0" * (-(52 + len(body)) % 4)
        offsets[name] = 52 + len(body)
        body += blob
    body += b"\0" * (-(52 + len(body)) % 4)
    shoff = 52 + len(body)

    def header(name: str, kind: int, flags: int, blob: bytes, link: int = 0, info: int = 0, entsize: int = 0) -> bytes:
        fields = (section_names[name], kind, flags, 0, offsets[name], len(blob), link, info, 4, entsize)
        return struct.pack(">10I", *fields)

    sections = struct.pack(">10I", *([0] * 10))
    sections += header(".text", 1, 6, text) + header(".symtab", 2, 0, symtab, 3, 1, 16)
    sections += header(".strtab", 3, 0, strtab) + header(".shstrtab", 3, 0, shstr)
    ident = b"\x7fELF" + bytes([1, 2, 1, 0]) + bytes(8)
    return ident + struct.pack(">HHIIIIIHHHHHH", 1, 8, 1, 0, 0, shoff, 0, 52, 0, 0, 40, 5, 4) + body + sections


def program(argv: Sequence[str | Path], cwd: Path | None = None) -> SimpleNamespace:
    """Run a python program of the build (argv as make spells it: `python3 -I [-S] script args`, or the script alone)
    in this process, from its file as `__main__`, in `cwd`: returncode, stdout and stderr (bytes) as subprocess.run
    has them. The programs it starts are still processes; a unit test gives them stubs."""
    words = [str(a) for a in argv]
    while words and (Path(words[0]).name.startswith("python") or words[0] in ("-I", "-S", "-E", "-s")):
        words = words[1:]
    out, err = (io.TextIOWrapper(io.BytesIO(), write_through=True) for _ in range(2))  # .buffer as sys.stdout has
    code: int = 0
    saved = sys.argv
    sys.argv = words
    try:
        with contextlib.chdir(cwd or Path.cwd()), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runpy.run_path(words[0], run_name="__main__")
    except SystemExit as exit:
        if isinstance(exit.code, str):
            err.write(exit.code + "\n")
            code = 1
        else:
            code = exit.code or 0
    finally:
        sys.argv = saved
    return SimpleNamespace(returncode=code, stdout=out.buffer.getvalue(), stderr=err.buffer.getvalue())  # type: ignore[attr-defined]


def cpp_runs(tmp: Path) -> int:
    """How many times the fake cpp of tmp/bin ran."""
    log = tmp.resolve() / "bin" / "cpp.log"
    return len(log.read_text().splitlines()) if log.exists() else 0
