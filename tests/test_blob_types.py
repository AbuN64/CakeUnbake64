"""The copy-typing step: the source of a copy row by its bytes in the other ROMs."""

from unbake import blob_types


def _doc() -> dict:
    return {
        "blob": {
            "pic": {"type": "graphics", "rom": {"eu": [[8, 16]]}},
            "plain": {"type": "leftover", "format": "copy", "rom": {"eu": [[16, 24]]}},
            "fuzzy": {"type": "leftover", "draft": "audio", "rom": {"eu": [[24, 32]]}},
            "fill": {"type": "fill", "rom": {"eu": [[32, 40]]}},
            "copy": {"type": "leftover", "format": "copy", "rom": {"us": [[0, 8]]}},
        }
    }


def test_copy_takes_the_type_of_its_source() -> None:
    eu = bytes(8) + b"PICTURE!" + bytes(24)
    kind, note = blob_types._source(_doc(), {"eu": eu, "us": b"PICTURE!" + bytes(32)}, "us", _doc()["blob"]["copy"])
    assert kind == "graphics" and "eu 0x8 pic" in note["evidence"]


def test_untyped_or_foreign_source_leaves_the_row_untyped() -> None:
    doc = _doc()
    for place, why in ((16, "plain"), (32, "fill")):
        eu = bytes(place) + b"PICTURE!" + bytes(40 - place)
        kind, note = blob_types._source(doc, {"eu": eu, "us": b"PICTURE!" + bytes(32)}, "us", doc["blob"]["copy"])
        assert kind is None and note["regions"] == [f"eu:{why}"]


def test_a_fuzzy_source_gives_its_draft() -> None:
    assert blob_types._kind(_doc()["blob"]["fuzzy"]) == "audio"
    assert blob_types._kind(_doc()["blob"]["fill"]) is None


def test_a_byte_string_in_too_many_places_names_no_source() -> None:
    assert blob_types._places(bytes(1000), bytes(4)) is None
