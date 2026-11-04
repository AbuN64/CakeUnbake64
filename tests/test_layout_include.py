"""The preprocess argv and the closure search flags name the project's [layout] roots, in declared order."""

from __future__ import annotations

from pathlib import Path

import fixture
import pytest

from unbake import api, config, process, view
from unbake.contracts import Snapshot, digest

pytestmark = pytest.mark.usefixtures("toolchains")

RAGEWARS = '[layout]\nsource_roots = ["Source"]\nheader_roots = ["Include"]\n'  # the RageWarsDecomp config.toml slice
LOWERCASE = '[layout]\nsource_roots = ["src"]\nheader_roots = ["include"]\n'


def _project(tmp_path: Path, monkeypatch, text: str):
    root = tmp_path / "decl"
    root.mkdir()
    (root / "config.toml").write_text(text, encoding="utf-8")
    cfg = fixture.config(tmp_path)
    cfg.project.layout = config.layout_at(root)  # the real loader, validated against the project schema
    seen: list[list[str]] = []
    real = process.run

    def spy(name, argv, *args, **kw):
        seen.append([str(a) for a in argv])
        return real(name, argv, *args, **kw)

    monkeypatch.setattr(process, "run", spy)
    return cfg, seen


@pytest.mark.parametrize(
    ("text", "headers", "sources"), [(RAGEWARS, "Include", "Source"), (LOWERCASE, "include", "src")]
)
def test_preprocess_and_closure_flags_follow_the_declared_layout(tmp_path, monkeypatch, text, headers, sources) -> None:
    cfg, seen = _project(tmp_path, monkeypatch, text)
    api.preprocess(cfg, "src/ovl/fuzzy.c", "a")
    flags = [a for a in seen[-1] if a.startswith("-I")]
    assert flags == [f"-I{headers}", "-Isrc/ovl/"]
    snapshot = Snapshot(cfg, "0" * 40, None, {}, {}, digest("snapshot"))
    search = view.search(snapshot, "a")
    assert search[:2] == [f"-I{headers}", f"-I{sources}"]
    assert ("-Iinclude" in search + flags) == (headers == "include")
    assert ("-Isrc" in search) == (sources == "src")
