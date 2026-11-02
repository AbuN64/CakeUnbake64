"""The pad-tail reader on the real bytes of eu 0x14f7e2 and its twin eu-x 0x1447e2: the c-string 'Source' then 'R_'."""

import pytest

from unbake import pad_tail

# the 12 bytes before the tail are the end of a c-string; 'R_' (0x14f7ee..0x14f7f0) fills the pad up to the alignment
RECORD = bytes.fromhex("3e6d5f70536f757263652900")
TAIL = bytes.fromhex("525f")
EU, EUX = 0x14F7EE, 0x1447EE
SIZE = 0x150000


def roms(tail=TAIL, record=RECORD, eux_at=EUX):
    a, b = bytearray(SIZE), bytearray(SIZE)
    a[EU - 12 : EU + 2] = RECORD + TAIL
    b[eux_at - 12 : eux_at + len(tail)] = record + tail
    return {"eu": bytes(a), "eu-x": bytes(b)}


HELD = {"eu": [(EU - 12, EU)], "eu-x": [(EUX - 12, EUX)]}


def test_the_tail_after_a_c_string_is_proven_by_the_twin_both_ways():
    rows, refused = pad_tail.find(roms(), HELD, "eu", [(EU, EU + 2)], ["eu", "eu-x"])
    assert refused == []
    assert rows == [
        {"version": "eu", "region": [EU, EU + 2], "reader": "pad-tail", "source": EUX, "source_version": "eu-x"}
    ]
    rows, _ = pad_tail.find(roms(), HELD, "eu-x", [(EUX, EUX + 2)], ["eu", "eu-x"])
    assert rows[0]["source"] == EU and rows[0]["source_version"] == "eu"


def test_prove_rebuilds_the_tail_from_the_twin():
    r = roms()
    receipt = pad_tail.prove(r["eu"], r["eu-x"], EU, EU + 2, EUX, HELD["eu"], HELD["eu-x"])
    assert receipt == {"source": [EUX, EUX + 2], "record_end": "536f757263652900", "hex": "525f", "alignment": 4}


@pytest.mark.parametrize(
    ("twin", "why"),
    [
        (roms(tail=b"R\x00"), "tail differs"),
        (roms(record=RECORD[:-1] + b"\x01"), "no twin holds the record end"),
        (roms(eux_at=EUX + 1), "alignment differs"),
    ],
)
def test_a_twin_that_differs_in_the_tail_the_record_end_or_the_alignment_is_refused_and_listed(twin, why):
    twin = dict(twin, eu=roms()["eu"])
    rows, refused = pad_tail.find(twin, HELD, "eu", [(EU, EU + 2)], ["eu", "eu-x"])
    assert rows == [] and len(refused) == 1 and why in refused[0]["reason"]


def test_prove_refuses_what_find_would_not_emit():
    r = roms()
    with pytest.raises(pad_tail.TailError, match="not accounted"):
        pad_tail.prove(r["eu"], r["eu-x"], EU, EU + 2, EUX, [], HELD["eu-x"])
    with pytest.raises(pad_tail.TailError, match="record end differs"):
        pad_tail.prove(r["eu"], r["eu-x"], EU, EU + 2, EUX + 4, HELD["eu"], HELD["eu-x"])


def test_a_tail_over_seven_bytes_or_off_the_alignment_or_after_unaccounted_bytes_is_no_candidate():
    r = roms()
    assert pad_tail.find(r, HELD, "eu", [(EU, EU + 8)], ["eu"]) == ([], [])  # eight bytes: a copy's
    assert pad_tail.find(r, HELD, "eu", [(EU, EU + 3)], ["eu"]) == ([], [])  # ends off the alignment
    assert pad_tail.find(r, {"eu": [], "eu-x": []}, "eu", [(EU, EU + 2)], ["eu"]) == ([], [])  # record end unaccounted
