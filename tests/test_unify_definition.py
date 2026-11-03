"""Definition-owned unify over reduced bc0d034ac census rows; no compiler or shared test seam."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from unbake import config, typeunify
from unbake.typeunify import Seams, Unit

FIXTURE = Path(__file__).parent / "data/unify_definition"
FUNCTION = "Achieve_ApplyPlayerOptionOverrides"
DATA = "g_Ovltab6EncodedWordsBlock6"
MISSING = "Cache_AllocResourceBlock"
FUNCTION_HEADER = "include/ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"
DATA_HEADER = "include/WEAPON_STATE_TABLES/WEAPON_STATE_TABLES.H"
DEFINITION = "SOURCE/ACHIEVE/MATCH_QUALIFIES/achieve_match_setup.CPP"
CALLER = "SOURCE/AI/PLAYERAI/PLAYERAI__PLAYERAI.CPP"
BARE_CALLER = "SOURCE/ENGINE/LINK/LINK_DISPATCHMATCHINGTRIGGERS.CPP"
DATA_UNIT = "SOURCE/DATA/WEAPON_STATE_TABLES/WEAPON_STATE_TABLES.CPP"
DATA_USER = "SOURCE/DATA/OVERLAYS/SEVEN/RESOURCES/WEAPON_SLOT_6_BEHAVIOR_US_REV1.CPP"


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
        self.calls: list[list[str]] = []
        self.different: set[str] = set()
        self.broken: set[str] = set()
        self.snapshots: list[dict[str, str]] = []
        self.seams = Seams(self.units, self.read, self.make)

    def units(self) -> dict[str, list[str]]:
        units = sorted(p.relative_to(self.root).as_posix() for p in (self.root / "SOURCE").rglob("*.CPP"))
        return {"us": units, "us-rev1": units}

    def expand(self, path: str, seen: frozenset[str] = frozenset()) -> str:
        if path in seen:
            return ""
        text = (self.root / path).read_text()
        out = f'# 1 "{path}"\n'
        for line, part in enumerate(text.splitlines(), 1):
            match = re.match(r'\s*#include "([^"]+)"', part)
            if match:
                candidates = [self.root / "include" / match[1], self.root / Path(path).parent / match[1]]
                header = next(p for p in candidates if p.is_file()).resolve().relative_to(self.root).as_posix()
                out += self.expand(header, seen | {path}) + f'# {line + 1} "{path}"\n'
            else:
                out += ("" if part.lstrip().startswith("#") else part) + "\n"
        return out

    def read(self, items: Sequence[Unit]) -> list[typeunify.Facts]:
        return [typeunify.facts(self.expand(u), u, lambda f: f.startswith(("SOURCE/", "include/"))) for _, u in items]

    def make(self, targets: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(targets))
        self.snapshots.append({p.relative_to(self.root).as_posix(): p.read_text() for p in self.root.rglob("*.CPP")})
        failed = []
        for target in targets:
            unit = target.split("/", 2)[2][:-2]
            changed = len(self.calls) > 1 and (
                (
                    unit in (DEFINITION, CALLER, BARE_CALLER)
                    and "struct PlayerLoadoutSelectionView *player);" in (self.root / FUNCTION_HEADER).read_text()
                )
                or (unit in (DATA_UNIT, DATA_USER) and (self.root / DATA_HEADER).exists())
            )
            if changed and unit in self.broken:
                failed.append(target)
                continue
            # Stable bytes unless a configured object rejects its rewritten declaration.
            out = self.root / target
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(target + (" differs" if changed and unit in self.different else ""))
        return (2 if failed else 0), "".join(f"make: *** [Makefile:1: {p}] Error 1\n" for p in failed)


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, **params: object) -> dict:
    monkeypatch.setattr(typeunify, "seams", lambda cfg: world.seams)
    result = typeunify.unify_command(SimpleNamespace(project=SimpleNamespace(root=world.root)), params)
    schema = Path(typeunify.__file__).parent / "resources/schemas/result.unify.schema.json"
    Draft202012Validator(json.loads(schema.read_text())).validate(result)
    return result


def test_census_slice_through_unify_reuses_and_creates_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(tmp_path)
    before = {p: (tmp_path / p).read_text() for p in world.units()["us"]}
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == {FUNCTION, DATA}
    assert result["unified"][FUNCTION]["type"] == "void(struct PlayerLoadoutSelectionView*)"
    assert result["unified"][DATA]["type"] == "void*[]"
    assert result["unified"][FUNCTION]["header"] == FUNCTION_HEADER
    assert result["unified"][DATA]["header"] == DATA_HEADER
    assert result["unresolved"] == {MISSING: {"": "no definition"}}
    assert "struct PlayerLoadoutSelectionView;" in (tmp_path / FUNCTION_HEADER).read_text()
    assert (
        "void Achieve_ApplyPlayerOptionOverrides(struct PlayerLoadoutSelectionView *player);"
        in (tmp_path / FUNCTION_HEADER).read_text()
    )
    assert "extern void *g_Ovltab6EncodedWordsBlock6[];" in (tmp_path / DATA_HEADER).read_text()
    assert "#ifndef" in (tmp_path / DATA_HEADER).read_text()
    for path in (CALLER, BARE_CALLER, DEFINITION):
        text = (tmp_path / path).read_text()
        assert '#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"' in text
        if path != DEFINITION:
            assert not re.search(r"void Achieve_ApplyPlayerOptionOverrides\([^;]*;", text)
    assert "extern char" not in (tmp_path / DATA_USER).read_text()
    assert '#include "WEAPON_STATE_TABLES/WEAPON_STATE_TABLES.H"' in (tmp_path / DATA_USER).read_text()
    for path, text in before.items():
        if MISSING in text:
            assert (tmp_path / path).read_text() == text
    assert len(world.calls) == 3  # baseline, disjoint rewrites together, exactly one final drift check
    assert result["rounds"] == result["batches"] == 1
    assert world.calls[-1] == world.calls[-2]
    assert config.module_map_at(tmp_path) == "modules.tsv"


@pytest.mark.parametrize("kind", ["object", "compile"])
def test_bad_objects_hold_their_names_without_individual_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    world = World(tmp_path)
    before = {p: (tmp_path / p).read_text() for p in (DEFINITION, CALLER, BARE_CALLER, FUNCTION_HEADER)}
    (world.different if kind == "object" else world.broken).add(CALLER)
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == {DATA}
    reasons = result["unresolved"][FUNCTION]
    assert len(reasons) == 1
    assert all("src/" not in reason and CALLER + ".o" in reason for reason in reasons.values())
    assert ("object differs" if kind == "object" else "does not build") in next(iter(reasons.values()))
    assert {p: (tmp_path / p).read_text() for p in before} == before
    assert len(world.calls) == 3 and result["rounds"] == 1
    assert "extern void *" in (tmp_path / DATA_HEADER).read_text()


def test_rejected_new_header_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(tmp_path)
    world.different.add(DATA_USER)
    before = (tmp_path / DATA_USER).read_text()
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == {FUNCTION}
    assert DATA in result["unresolved"]
    assert not (tmp_path / DATA_HEADER).exists()
    assert (tmp_path / DATA_USER).read_text() == before


@pytest.mark.parametrize("same_type", [True, False])
def test_multiple_physical_definitions_are_never_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_type: bool
) -> None:
    world = World(tmp_path)
    duplicate = tmp_path / "SOURCE/duplicate.CPP"
    duplicate.write_text(
        (tmp_path / DEFINITION).read_text()
        if same_type
        else "int Achieve_ApplyPlayerOptionOverrides(int player) { return player; }\n"
    )
    before = (tmp_path / FUNCTION_HEADER).read_text()
    result = invoke(world, monkeypatch)
    assert result["unresolved"][FUNCTION] == {"": "multiple definitions"}
    assert (tmp_path / FUNCTION_HEADER).read_text() == before


def test_bad_object_pins_only_the_name_that_breaks_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(tmp_path)
    user = tmp_path / CALLER
    user.write_text(user.read_text() + f"extern char {DATA}[];\nvoid *shared(void) {{ return {DATA}; }}\n")
    world.different.add(CALLER)
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == {DATA}
    assert set(result["unresolved"]) == {FUNCTION, MISSING}
    assert FUNCTION in user.read_text() and "extern char" not in user.read_text()
    assert (tmp_path / DATA_HEADER).exists()
    assert set(result["held"]) == {FUNCTION}
    assert all(v["names"] == [FUNCTION] for v in result["objects"].values())
    assert len(world.calls) == 5  # baseline, wave, split, reduced wave, final drift
    assert all(CALLER + ".o" in t for t in world.calls[2])
    assert len(world.calls[2]) == 2  # only the two bad version objects, never the whole wave


class Killed(BaseException):
    """The job bound: the process is gone between two builds."""


def shared_object_world(root: Path) -> World:
    """FUNCTION and DATA meet in one object that differs while both are rewritten; FUNCTION is the culprit."""
    world = World(root)
    user = root / CALLER
    user.write_text(user.read_text() + f"extern char {DATA}[];\nvoid *shared(void) {{ return {DATA}; }}\n")
    world.different.add(CALLER)
    return world


def stable(result: dict) -> dict:
    """The decisions of a result: its counts of builds belong to the run that made them."""
    return {k: v for k, v in result.items() if k not in ("batches", "rounds", "rebuilt")}


def test_a_unify_cut_short_in_its_isolate_rounds_leaves_its_decisions_and_the_resume_matches_a_whole_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(typeunify.time, "monotonic", lambda: 0.0)
    whole = tmp_path / "whole"
    paths = {"result": whole.with_suffix(".json"), "out": whole.with_suffix(".tsv")}
    result = invoke(shared_object_world(whole), monkeypatch, **paths)
    assert result["complete"] is True and set(result["held"]) == {FUNCTION}

    cut_root = tmp_path / "cut"
    cut = shared_object_world(cut_root)
    make = cut.make

    def dies(targets: Sequence[str]) -> tuple[int, str]:
        if len(cut.calls) == 3:  # baseline, wave and split built: the reduced wave is where the bound falls
            raise Killed
        return make(targets)

    cut.seams = Seams(cut.units, cut.read, dies)
    partial, table = cut_root.with_suffix(".json"), cut_root.with_suffix(".tsv")
    with pytest.raises(Killed):
        invoke(cut, monkeypatch, result=partial, out=table)
    left = json.loads(partial.read_text())
    schema = json.loads((Path(typeunify.__file__).parent / "resources/schemas/result.unify.schema.json").read_text())
    Draft202012Validator(schema).validate(left)
    assert left["complete"] is False and left["unified"] == {}
    assert set(left["held"]) == {FUNCTION} and left["unresolved"][FUNCTION]
    assert [r.split("\t")[:2] for r in table.read_text().splitlines()[1:]] == [
        [FUNCTION, "unresolved"],
        [MISSING, "unresolved"],
    ]

    again = shared_object_world(tmp_path / "again")  # the tree as the cut run found it
    resumed = invoke(again, monkeypatch, result=partial, out=table)
    assert len(again.calls) == 3  # baseline, wave, final check: the rejected name is not isolated again
    assert stable(resumed) == stable(result)
    assert stable(json.loads(partial.read_text())) == stable(json.loads(whole.with_suffix(".json").read_text()))
    assert table.read_bytes() == whole.with_suffix(".tsv").read_bytes()


def test_a_partial_result_of_another_baseline_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    partial = tmp_path / "unify.json"
    partial.write_text(json.dumps({"complete": False, "baseline": "other", "held": {}, "seconds": {}, "objects": {}}))
    with pytest.raises(typeunify.Refusal) as refused:
        invoke(World(tmp_path / "tree"), monkeypatch, result=partial)
    assert refused.value.findings[0].key == "unify.resume"
    partial.write_text("")  # a job that wrote nothing is no partial result
    assert invoke(World(tmp_path / "again"), monkeypatch, result=partial)["complete"] is True


def test_existing_same_type_declarations_are_removed_and_other_names_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    caller = tmp_path / CALLER
    caller.write_text(
        caller.read_text() + f"void {FUNCTION}(struct PlayerLoadoutSelectionView *player), other_function(void);\n"
    )
    data_user = tmp_path / DATA_USER
    data_user.write_text(data_user.read_text() + f"extern void *{DATA}[], *other_data;\n")
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == {FUNCTION, DATA}
    assert "void other_function(void);" in caller.read_text()
    assert "extern void *other_data;" in data_user.read_text()
    assert f"void {FUNCTION}(" not in caller.read_text()
    assert f"extern void *{DATA}" not in data_user.read_text()


def test_anonymous_definition_is_reported_without_an_alternate_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    definition = tmp_path / DATA_UNIT
    definition.write_text(f"struct {{ int value; }} {DATA} = {{ 0 }};\n")
    before = definition.read_text()
    result = invoke(world, monkeypatch)
    assert "anonymous definition type" in result["unresolved"][DATA][""]
    assert DATA not in result["unified"]
    assert definition.read_text() == before
    assert not (tmp_path / DATA_HEADER).exists()


def test_a_data_name_with_two_definitions_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(tmp_path)
    (tmp_path / "SOURCE/duplicate.CPP").write_text((tmp_path / DATA_UNIT).read_text())
    result = invoke(world, monkeypatch)
    assert result["unresolved"][DATA] == {"": "multiple definitions"}
    assert not (tmp_path / DATA_HEADER).exists()


def test_existing_module_header_is_reused_when_not_yet_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    definition = tmp_path / DEFINITION
    definition.write_text(definition.read_text().split("\n", 1)[1])
    result = invoke(world, monkeypatch)
    assert result["unified"][FUNCTION]["header"] == FUNCTION_HEADER
    assert len(list((tmp_path / "include").rglob("MATCH_QUALIFIES.H"))) == 1
    assert '#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"' in definition.read_text()


def test_one_physical_definition_with_version_dependent_types_is_not_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    original = world.read

    def read(items: Sequence[Unit]) -> list[typeunify.Facts]:
        out = original(items)
        for i, (version, _) in enumerate(items):
            if version == "us-rev1":
                typedefs, statements, files, words = out[i]
                out[i] = (
                    typedefs,
                    [
                        (
                            f,
                            line,
                            text.replace("PlayerLoadoutSelectionView", "DifferentView") if f == DEFINITION else text,
                            hint,
                        )
                        for f, line, text, hint in statements
                    ],
                    files,
                    words,
                )
        return out

    world.seams = Seams(world.units, read, world.make)
    before = (tmp_path / FUNCTION_HEADER).read_text()
    result = invoke(world, monkeypatch)
    assert result["unresolved"][FUNCTION] == {"": "definition type differs across versions"}
    assert (tmp_path / FUNCTION_HEADER).read_text() == before


@pytest.mark.parametrize("interaction", [False, True])
def test_one_object_with_two_culprits_or_an_interaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interaction: bool
) -> None:
    world = World(tmp_path)
    user = tmp_path / CALLER
    user.write_text(user.read_text() + f"extern char {DATA}[];\nvoid *shared(void) {{ return {DATA}; }}\n")
    original = world.make

    def make(targets: Sequence[str]) -> tuple[int, str]:
        code, log = original(targets)
        function = "struct PlayerLoadoutSelectionView *player);" in (tmp_path / FUNCTION_HEADER).read_text()
        data = (tmp_path / DATA_HEADER).exists()
        for t in targets:
            if CALLER + ".o" in t and ((function and data) if interaction else (function or data)):
                (tmp_path / t).write_text("different")
        return code, log

    world.seams = Seams(world.units, world.read, make)
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == ({FUNCTION} if interaction else set())
    assert set(result["held"]) == ({DATA} if interaction else {FUNCTION, DATA})


def test_failed_object_and_held_name_keep_compiler_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(tmp_path)
    world.broken.add(CALLER)
    original = world.make

    def make(targets: Sequence[str]) -> tuple[int, str]:
        code, log = original(targets)
        if code:
            log = "".join(f"{t[:-2]}.i:12: error: unknown type name 'u32'\n" for t in targets) + log
        return code, log

    world.seams = Seams(world.units, world.read, make)
    result = invoke(world, monkeypatch)
    assert all("unknown type name 'u32'" in o["error"] for o in result["objects"].values())
    assert all("unknown type name 'u32'" in e for e in result["held"][FUNCTION].values())


def test_object_groups_join_transitively_and_keep_independent_groups() -> None:
    a, b, c = ("us", "a"), ("us", "b"), ("us", "c")
    groups = typeunify._object_groups({a, b, c}, {"x": frozenset({a}), "y": frozenset({a, b}), "z": frozenset({c})})
    assert groups == [(("x", "y"), {a, b}), (("z",), {c})]


@pytest.mark.parametrize("kind", ["object", "compiler"])
def test_shared_owning_header_bisects_many_innocent_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    world = World(tmp_path)
    names = [f"Innocent{i}" for i in range(8)]
    definition = tmp_path / DEFINITION
    header = tmp_path / FUNCTION_HEADER
    definition.write_text(definition.read_text() + "".join(f"void {n}(int value) {{ }}\n" for n in names))
    header.write_text(header.read_text() + "".join(f"int {n}(int value);\n" for n in names))
    caller = tmp_path / CALLER
    caller.write_text('#include "ACHIEVE/MATCH_QUALIFIES/MATCH_QUALIFIES.H"\n' + caller.read_text())
    (world.different if kind == "object" else world.broken).add(CALLER)
    original = world.make

    def make(targets: Sequence[str]) -> tuple[int, str]:
        code, log = original(targets)
        diagnostics = "".join(
            f"{t[:-2]}.i:12: error: unknown type name 'PlayerLoadoutSelectionView'\n"
            for t in targets
            if not (tmp_path / t).exists()
        )
        return code, diagnostics + log

    world.seams = Seams(world.units, world.read, make)
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == {DATA, *names}
    assert set(result["held"]) == {FUNCTION}
    error = (
        "object differs"
        if kind == "object"
        else "does not build: error: unknown type name 'PlayerLoadoutSelectionView'"
    )
    objects = {typeunify.target((version, CALLER)) for version in world.units()}
    assert result["objects"] == {obj: {"error": error, "names": [FUNCTION]} for obj in objects}
    assert result["held"][FUNCTION] == dict.fromkeys(objects, error)
    final_header = header.read_text()
    assert all(f"void {name}(int value);" in final_header for name in names)
    assert "struct PlayerLoadoutSelectionView *player);" not in final_header
    assert len(world.calls) == 7  # three halvings for nine names, one reduced wave, final drift
    assert all(len(batch) == 2 and all(CALLER + ".o" in t for t in batch) for batch in world.calls[2:-1])


def test_interacting_object_cleared_by_another_objects_culprit_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World(tmp_path)
    for unit in (CALLER, BARE_CALLER):
        path = tmp_path / unit
        path.write_text(path.read_text() + f"extern char {DATA}[];\nvoid *shared(void) {{ return {DATA}; }}\n")
    original = world.make

    def make(targets: Sequence[str]) -> tuple[int, str]:
        code, log = original(targets)
        function = "struct PlayerLoadoutSelectionView *player);" in (tmp_path / FUNCTION_HEADER).read_text()
        data = (tmp_path / DATA_HEADER).exists()
        for t in targets:
            if function and (CALLER + ".o" in t or (BARE_CALLER + ".o" in t and data)):
                (tmp_path / t).write_text("different")
        return code, log

    world.seams = Seams(world.units, world.read, make)
    result = invoke(world, monkeypatch)
    assert set(result["unified"]) == {DATA}
    assert len(result["objects"]) == len(result["held"][FUNCTION]) == 4
    assert all(o["names"] == [FUNCTION] for o in result["objects"].values())
