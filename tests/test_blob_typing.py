"""Blob rows typed exact, fuzzy or untyped: the measure, the schema of a draft and the audit rules on a change."""

import shutil
import subprocess
from types import SimpleNamespace as N

import pytest

from unbake import blobs, policy

REAL_GIT = "the audit compares two commits of a real git repository"


def _row(kind="leftover", state="decoded", rom=None, **extra):
    return {"type": kind, "state": state, "rom": {"v": rom or [[0, 16]]}, **extra}


def test_typing_names_each_row_exact_fuzzy_or_untyped():
    assert blobs.typing(_row()) == "untyped"
    assert blobs.typing(_row(draft="model", draft_evidence="same type as its source in eu")) == "fuzzy"
    assert blobs.typing(_row("graphics", "identified")) == "fuzzy"
    assert blobs.typing(_row("graphics")) == "exact"
    assert blobs.typing(_row("fill", "observed")) == "exact"


def test_states_count_rows_and_bytes_of_a_version_without_children():
    document = {
        "blob": {
            "a": _row(rom=[[0, 16], [32, 40]]),
            "b": _row(draft="audio", draft_evidence="e", rom=[[64, 80]]),
            "c": _row("model", rom=[[100, 164]]),
            "child": {"type": "model", "state": "decoded", "parent": "c", "decoded": 8},
            "other": {**_row(), "rom": {"w": [[0, 4]]}},
        }
    }
    assert blobs.states(document, "v") == {
        "exact": {"rows": 1, "bytes": 64},
        "fuzzy": {"rows": 1, "bytes": 16},
        "untyped": {"rows": 1, "bytes": 24},
    }


@pytest.mark.parametrize(
    "extra, kind, refused",
    [
        ({"draft": "model", "draft_evidence": "same as eu"}, "leftover", False),
        ({"draft": "model"}, "leftover", True),
        ({"draft": "nonsense", "draft_evidence": "x"}, "leftover", True),
        ({"draft": "model", "draft_evidence": "x"}, "graphics", True),
    ],
)
def test_a_draft_needs_a_leftover_row_a_content_type_and_evidence(extra, kind, refused):
    row = {**_row(kind), "format": "copy", **extra}
    del row["rom"]
    found = blobs.check(N(), {"blob": {"r": row}}, "blobs.toml")
    assert bool(found) is refused


def _blob_text(rows):
    return "schema = 1\n" + "".join(
        f'\n[blob."{name}"]\ntype = "{kind}"\nformat = "copy"\nstate = "decoded"\n'
        f'rom."v" = [[{start}, {start + 16}]]\n' + ('draft = "model"\ndraft_evidence = "e"\n' if drafted else "")
        for name, kind, start, drafted in rows
    )


def _audit(tmp_path, monkeypatch, before, after):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "config/blobs.toml").write_text(_blob_text(before))
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(tmp_path)]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        subprocess.run([*git, *args], check=True)
    (tmp_path / "config/blobs.toml").write_text(_blob_text(after))
    return policy._untyped_findings(tmp_path, "HEAD", shutil.which("git"), tmp_path, {"untyped": {"reach": 100}})


@pytest.mark.integration(REAL_GIT)
def test_audit_refuses_an_added_untyped_row(tmp_path, monkeypatch):
    refused, warned = _audit(
        tmp_path,
        monkeypatch,
        [("a", "leftover", 0, False)],
        [("a", "leftover", 0, False), ("b", "leftover", 5000, False)],
    )
    assert [(f.key, f.missing, f.severity) for f in refused] == [("audit.untyped_row", ("b",), "Refuse")]
    assert warned == []


@pytest.mark.integration(REAL_GIT)
def test_audit_allows_a_draft_and_an_untyped_row_the_base_held(tmp_path, monkeypatch):
    refused, warned = _audit(
        tmp_path,
        monkeypatch,
        [("a", "leftover", 0, False)],
        [("a", "leftover", 0, False), ("b", "leftover", 5000, True)],
    )
    assert refused == [] and warned == []


@pytest.mark.integration(REAL_GIT)
def test_audit_warns_on_untyped_rows_in_a_touched_window_only(tmp_path, monkeypatch):
    base = [("near", "leftover", 64, False), ("far", "leftover", 9000, False), ("t", "model", 0, False)]
    refused, warned = _audit(
        tmp_path,
        monkeypatch,
        base,
        [("near", "leftover", 64, False), ("far", "leftover", 9000, False), ("t", "audio", 0, False)],
    )
    assert refused == []
    assert [(f.key, f.missing, f.severity) for f in warned] == [("audit.untyped_window", ("near",), "Warn")]
