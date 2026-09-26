import pytest

from dungeon.catalogue import default_catalogue
from dungeon.geometry import Rotation, Vec3
from dungeon.layout import build_phase1
from dungeon.logic import load_spec
from dungeon.solver import LayoutError, Solver
from dungeon.validate import validate_layout, validate_spec

TC = "minecraft:trial_chambers/"


@pytest.fixture(scope="module")
def cat():
    return default_catalogue()


def test_catalogue_reads_jigsaws(cat):
    p = cat.piece(TC + "hallway/straight")
    assert tuple(p.size) == (5, 7, 5)
    names = {j.name for j in p.jigsaws} | {j.target for j in p.jigsaws}
    assert "minecraft:in_connector" in names
    assert p.solid_blocks > 0 and p.is_solid(Vec3(0, 0, 0))


def test_attach_is_geometrically_connected(cat):
    s = Solver(cat, seed=3)
    root = s.place_root(TC + "chamber/chamber_1", Vec3(0, 0, 0), Rotation.CLOCKWISE_90)
    hall = s.attach(root, TC + "hallway/straight", parent_jigsaw="name:minecraft:in_connector", child_jigsaw="name:minecraft:in_connector")
    cw = hall.world_jigsaws()[hall.via]
    matches = [w for w in root.world_jigsaws() if w.connect_pos == cw.pos and w.front == cw.front.opposite]
    assert matches and matches[0].index in root.used
    assert not root.box.intersects(hall.box)


def test_attach_refuses_when_blocked(cat):
    s = Solver(cat, seed=3)
    root = s.place_root(TC + "hallway/straight", Vec3(0, 0, 0))
    # something huge can't hang off a hallway in a direction already occupied by a root twin
    s.place_root(TC + "hallway/straight", Vec3(0, 0, 5))
    with pytest.raises(LayoutError):
        s.attach(root, TC + "hallway/straight", parent_jigsaw="target:minecraft:in_connector", child_jigsaw="name:minecraft:in_connector")


def test_phase1_layout_is_deterministic_and_passes_gates(cat):
    a = build_phase1(cat, seed=11)
    b = build_phase1(cat, seed=11)
    key = lambda lay: [(p.piece.id, tuple(p.origin), p.rotation) for p in lay.solver.placements]
    assert key(a) == key(b)
    assert key(a) != key(build_phase1(cat, seed=12))
    rep = validate_layout(a)
    assert rep.ok, rep.render()
    assert {r.id for r in a.rooms} == {"entrance", "room1", "room2", "boss"}


def test_sample_spec_validates_against_layout(cat):
    spec = load_spec("specs/example.json")
    lay = build_phase1(cat, seed=7, spawner_contents=spec.spawner_contents)
    assert validate_spec(spec, lay).ok
    # spawner contents chosen by the logic layer are honoured by the solver
    details = {s.detail for s in lay.slots if s.kind == "trial_spawner"}
    assert all(d.split("/")[0] == "breeze" or d.split("/")[1] in spec.spawner_contents.values() for d in details)
