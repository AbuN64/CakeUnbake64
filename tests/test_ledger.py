"""The unknown-bytes ledger: one cause class per span, and the ratchet that lets unknown bytes only shrink."""

import json

import pytest

from unbake import ledger, report
from unbake.contracts import Refusal


def test_classify_one_cause_per_span():
    cs = ledger.classify
    assert cs(b"\0\0\0\0\0\0", 0x100, 0x106, "x", None)[0] == "fill-run"
    assert cs(b"\0" * 8, 0x100, 0x108, "x", None)[0] == "alignment-pad"
    assert cs(b"\x01\x02\x03", 0, 3, "x", None)[0] == "tiny-fragment"
    assert cs(b"\x01\x02\x03\x04\x05\x06\x07\x08", 0, 8, "x", {"version": "eu", "at": 16})[0] == "copy-of-known"
    assert cs(b"Hello world\0\0\0\0", 0, 15, "x", None)[0] == "c-string"
    assert cs(b"\xff\xfeHello wor", 0, 12, "x", None)[0] == "text-fragment"
    assert cs(bytes.fromhex("80123456 80123460 80123470"), 0, 12, "x", None)[0] == "pointer-run"
    assert cs(bytes.fromhex("3f800000 40000000 3f000000"), 0, 12, "x", None)[0] == "float-run"
    assert cs(bytes.fromhex("00000001 00000002 00000003"), 0, 12, "x", None)[0] == "packed-record"
    assert cs(bytes(range(1, 14)), 0, 13, "seg_assets", None)[0] == "asset-data"
    assert cs(bytes(range(1, 14)), 0, 13, "game_data", None)[0] == ledger.UNCLASSIFIED


def test_every_class_names_a_reader_and_a_card():
    for cause in [*ledger.READERS]:
        reader, card = ledger.READERS[cause]
        assert reader and card


def _cart(version, unknown):
    return {"version": version, "partition": {"unknown": {"bytes": unknown}}}


def test_ratchet_refuses_growth_and_allows_shrink(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text(json.dumps({"carts": [_cart("eu", 306), _cart("de", 4)]}))
    report._ratchet(path, {"carts": [_cart("eu", 300), _cart("de", 4), _cart("us", 9)]})  # shrink, same, new version
    with pytest.raises(Refusal) as refused:
        report._ratchet(path, {"carts": [_cart("eu", 307), _cart("de", 0)]})
    assert "eu 306 -> 307" in repr(refused.value.findings[0])
    report._ratchet(tmp_path / "missing.json", {"carts": [_cart("eu", 1)]})  # nothing written yet: no ceiling
