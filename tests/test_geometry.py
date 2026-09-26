from dungeon.geometry import AABB, Direction, Rotation, Vec3


def test_rotation_matches_minecraft_convention():
    # CLOCKWISE_90 turns north into east, as StructureTemplate.transform does with pivot 0
    assert Rotation.CLOCKWISE_90.direction(Direction.NORTH) is Direction.EAST
    assert Rotation.CLOCKWISE_90.apply(Vec3(0, 0, -1)) == Vec3(1, 0, 0)
    assert Rotation.CLOCKWISE_180.apply(Vec3(3, 5, 7)) == Vec3(-3, 5, -7)
    assert Rotation.COUNTERCLOCKWISE_90.apply(Vec3(1, 0, 0)) == Vec3(0, 0, -1)
    for r in Rotation:
        assert r.direction(Direction.UP) is Direction.UP


def test_rotated_bounding_box():
    box = AABB.from_size(Vec3(10, 0, 10), Vec3(4, 2, 6), Rotation.CLOCKWISE_90)
    assert box.min == Vec3(5, 0, 10) and box.max == Vec3(10, 1, 13)
    assert box.size == Vec3(6, 2, 4)


def test_aabb_intersection_is_inclusive_and_adjacency_is_not():
    a = AABB(Vec3(0, 0, 0), Vec3(4, 4, 4))
    assert a.intersects(AABB(Vec3(4, 4, 4), Vec3(6, 6, 6)))
    assert not a.intersects(AABB(Vec3(5, 0, 0), Vec3(6, 4, 4)))
