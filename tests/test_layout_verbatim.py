"""Folder names come from [layout] verbatim: no module of src/unbake spells a layout folder or case folds one, and a
project whose [layout] spells mixed-case roots gets exactly those spellings in the resolved paths and the outputs."""

from __future__ import annotations

import ast
import functools
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from unbake import build, config, symbols, typeplan, windows

SRC = Path(__file__).resolve().parents[1] / "src" / "unbake"
FOLDERS = ("src", "include", "sdk", "asm", "versions", "roms", "build", "assets")
BARE = re.compile(rf"^({'|'.join(FOLDERS)})$")
LEADING = re.compile(rf"^({'|'.join(FOLDERS)})/")
FOLDS = {"upper", "lower", "casefold"}
PATHISH = re.compile(r"(path|dir|folder|root)", re.IGNORECASE)

# Literals that are not the project's layout, each with why: the packaged resource tree, packaged template names and
# a clone in the user's home folder.
NOT_LAYOUT = {
    ("debtrules.py", "sdk"): "the packaged data folder unbake/resources/data/sdk, not a project folder",
    ("policy.py", "sdk/gfx.h"): "a packaged template name (config.template)",
    ("policy.py", "sdk/acmd.h"): "a packaged template name (config.template)",
    ("permute.py", "src/decomp-permuter/permuter.py"): "the permuter clone under the user's home folder",
}
# Folds that only build a comparison key; the path they key keeps its spelling.
KEY_FOLDS = {
    ("folders.py", "path"): "collision key: a case-folding host would see two moved paths as one",
    ("folders.py", "new"): "collision key: a case-folding host would see two moved paths as one",
    ("buildproof.py", "path"): "include lookup key: the 1999 compiler resolves #include case-insensitively",
}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _name(node.value)
    return ""


def _strings(node: ast.AST) -> Iterator[ast.Constant]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node
    elif isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        yield node.values[0]


def _bare(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and BARE.match(node.value) is not None


@functools.cache
def scan(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(folder literals, folds) of one module in one walk. A folder literal is a string naming a layout folder joined
    into a path (`/`, Path(...)) or leading one (`versions/...`), docstrings aside. A fold is .upper()/.lower()/
    .casefold() on a path-named value or a folder literal; one whose result only feeds `.endswith(...)` classifies a
    file suffix (.C, .H) and is no fold of a folder name."""
    docs: set[int] = set()
    suffix: set[int] = set()
    spots: list[ast.Constant] = []
    calls: list[ast.Call] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
        ):
            docs.add(id(body[0].value))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            spots += [s for s in (node.left, node.right) if _bare(s)]  # type: ignore[misc]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and LEADING.match(node.value):
            spots.append(node)
        elif isinstance(node, ast.Call):
            attr = _name(node.func)
            if attr in {"Path", "PurePath", "PurePosixPath", "joinpath"}:
                spots += [c for a in node.args for c in _strings(a) if BARE.match(c.value)]
            elif attr == "endswith" and isinstance(node.func, ast.Attribute):
                suffix.add(id(node.func.value))
            elif attr in FOLDS and isinstance(node.func, ast.Attribute):
                calls.append(node)
    literals = {
        f"{path.name}:{s.lineno}: {s.value!r}"
        for s in spots
        if id(s) not in docs and (path.name, s.value) not in NOT_LAYOUT
    }
    folds = []
    for call in calls:
        target = call.func.value  # type: ignore[attr-defined]
        name = _name(target)
        pathish = _bare(target) or bool(name and PATHISH.search(name)) or name == "new"
        if id(call) not in suffix and pathish and (path.name, name) not in KEY_FOLDS:
            folds.append(f"{path.name}:{call.lineno}: {name or target.value}.{call.func.attr}()")  # type: ignore[attr-defined]
    return tuple(sorted(literals)), tuple(folds)


def literals(path: Path) -> list[str]:
    return list(scan(path)[0])


def folds(path: Path) -> list[str]:
    return list(scan(path)[1])


def modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


@pytest.mark.seconds(2)  # parses every source module
def test_no_folder_literal_outside_repo_toml() -> None:
    assert [hit for module in modules() for hit in literals(module)] == []


@pytest.mark.seconds(2)  # parses every source module
def test_no_fold_of_a_folder_name() -> None:
    assert [hit for module in modules() for hit in folds(module)] == []


def test_scanner_sees_literals_and_folds(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        'import pathlib\np = root / "include"\nq = f"versions/{v}/x"\nr = pathlib.Path("asm")\nroot_dir.lower()\n'
        'path.lower().endswith(".h")\n'
    )
    assert len(literals(probe)) == 3
    assert folds(probe) == ["probe.py:5: root_dir.lower()"]


MIXED = """\
[layout]
source_roots = ["Source"]
header_roots = ["Include"]
subsystem_source = "Source/Engine/{subsystem}"
subsystem_header = "Include/Engine/{subsystem}.H"
asm_root = "Asm"
build_root = "Build"
roms_root = "Roms"
versions_root = "Versions"
assets_root = "Assets"
"""


@pytest.fixture
def mixed(tmp_path: Path) -> Iterator[dict]:
    (tmp_path / "config.toml").write_text(MIXED, encoding="utf-8")
    rows = config.layout_at(tmp_path)
    config.use_layout(rows)
    yield rows
    config.use_layout(config.load_resource("repo.toml")["layout"])


def _variants(text: str) -> list[str]:
    spelled = ["Source", "Include", "Asm", "Build", "Roms", "Versions", "Assets", "Engine", "Unicol"]
    return [w for s in spelled for w in (s.lower(), s.upper()) if re.search(rf"(?<![A-Za-z_]){w}(?![A-Za-z_])", text)]


def test_mixed_case_layout_reaches_resolved_paths(mixed: dict) -> None:
    assert config.source_roots() == ("Source",)
    assert config.header_roots() == ("Include",)
    assert [config.root(k) for k in ("asm", "build", "roms", "versions", "assets")] == [
        "Asm",
        "Build",
        "Roms",
        "Versions",
        "Assets",
    ]
    assert mixed["subsystem_source"].format(subsystem="Unicol") == "Source/Engine/Unicol"
    assert config.header_prefixes() == ("Include/",)
    assert config.strip_header_root("Include/Engine/Unicol.H") == "Engine/Unicol.H"
    assert config.strip_header_root("Source/Engine/Unicol/collide.cpp") == "Source/Engine/Unicol/collide.cpp"


def test_mixed_case_layout_reaches_outputs_byte_for_byte(mixed: dict) -> None:
    outputs = {
        "symbols": symbols.path("us"),
        "aliases": symbols.aliases_path("us"),
        "windows": windows.symbols_path("us"),
    }
    assert outputs == {
        "symbols": "Versions/us/symbol_addrs.txt",
        "aliases": "Versions/us/aliases.txt",
        "windows": "Versions/us/earlier_build_symbols.txt",
    }
    text = "\n".join(outputs.values())
    assert _variants(text) == []
    assert typeplan is not None and build is not None


def test_default_layout_unchanged() -> None:
    rows = config.load_resource("repo.toml")["layout"]
    config.use_layout(rows)
    assert (config.source_roots(), config.header_roots()) == (("src",), ("include",))
    assert [config.root(k) for k in config.BUILD_ROOTS] == ["asm", "include", "build", "roms", "versions", "assets"]
