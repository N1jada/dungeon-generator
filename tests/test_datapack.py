from dungeon.catalogue import default_catalogue
from dungeon.datapack import DatapackBuilder
from dungeon.layout import build_phase1
from dungeon.logic import load_spec


def test_datapack_places_every_piece_and_replaces_every_jigsaw():
    spec = load_spec("specs/example.json")
    lay = build_phase1(default_catalogue(), seed=7, spawner_contents=spec.spawner_contents)
    dp = DatapackBuilder(lay, spec).build_files()
    place = dp.files["data/dungeon/function/build/place.mcfunction"]
    assert place.count("place template ") == len(lay.solver.placements)
    jig = sum(len(p.piece.jigsaws) for p in lay.solver.placements)
    assert place.count("setblock ") >= jig
    assert "pack.mcmeta" in dp.files and "data/dungeon/dimension/arena.json" in dp.files
    assert "written_book" in dp.files["data/dungeon/function/enter.mcfunction"]
    assert spec.boss.name in dp.files["data/dungeon/function/boss/summon.mcfunction"]
