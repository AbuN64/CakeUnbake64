"""Units split into groups that stay contiguous in every version: the pure rule over plain link-order facts."""

from __future__ import annotations

from typing import Any

from unbake import partition


def entry(unit: str, cls: str, out: str = ".text") -> dict[str, Any]:
    return {"o": out, "k": "entry", "u": unit, "cls": cls, "n": 4}


def split(units, seqs):
    order = {r["u"]: i for i, r in enumerate(next(iter(seqs.values())))}
    return sorted(sorted(g) for g in partition.partition(units, seqs, order))


def test_contiguous_units_stay_one_group():
    seq = [entry("a", "text"), entry("b", "text"), entry("c", "text")]
    assert split(["a", "b", "c"], {"us": seq}) == [["a", "b", "c"]]


def test_a_foreign_unit_between_cuts_the_group():
    seq = [entry("a", "text"), entry("x", "text"), entry("b", "text")]
    assert split(["a", "b"], {"us": seq}) == [["a"], ["b"]]


def test_a_cut_in_any_version_holds_in_all():
    us = [entry("a", "text"), entry("b", "text")]
    eu = [entry("a", "text"), entry("x", "text"), entry("b", "text")]
    assert split(["a", "b"], {"us": us, "eu": eu}) == [["a"], ["b"]]


def test_a_class_split_by_another_class_in_a_section_is_cut():
    seq = [entry("a", "rodata", ".d"), entry("b", "data", ".d"), entry("c", "rodata", ".d")]
    assert split(["a", "b", "c"], {"us": seq}) == [["a", "b"], ["c"]]


def test_a_class_in_two_output_sections_is_cut():
    seq = [entry("a", "text", ".one"), entry("b", "text", ".two")]
    assert split(["a", "b"], {"us": seq}) == [["a"], ["b"]]


def test_a_unit_straddling_a_foreign_unit_stays_alone():
    seq = [entry("a", "text"), entry("x", "text"), entry("a", "text"), entry("b", "text")]
    assert split(["a", "b"], {"us": seq}) == [["a"], ["b"]]


def test_a_dead_entry_does_not_cut():
    seq = [entry("a", "text"), {**entry("x", "text"), "n": 0}, entry("b", "text")]
    assert split(["a", "b"], {"us": seq}) == [["a", "b"]]


def test_fit_cuts_where_the_check_refuses():
    refuses_c_after_a_start = lambda run: "c" not in run[1:] or run[0] == "c"  # noqa: E731
    assert partition.fit(list("abcde"), refuses_c_after_a_start) == [["a", "b"], ["c", "d", "e"]]


def test_fit_keeps_an_accepted_set_whole():
    assert partition.fit(list("abc"), lambda run: True) == [["a", "b", "c"]]
