"""Integer 3D geometry matching Minecraft's structure-template conventions.

Rotation semantics follow ``StructureTemplate.transform`` with pivot (0,0,0):
  CLOCKWISE_90:        (x, z) -> (-z,  x)   north -> east
  CLOCKWISE_180:       (x, z) -> (-x, -z)
  COUNTERCLOCKWISE_90: (x, z) -> ( z, -x)
World position of a template-local block = piece.origin + rotate(local).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, order=True)
class Vec3:
    x: int
    y: int
    z: int

    def __add__(self, o: Vec3) -> Vec3:
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: Vec3) -> Vec3:
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __neg__(self) -> Vec3:
        return Vec3(-self.x, -self.y, -self.z)

    def __iter__(self) -> Iterator[int]:
        yield self.x
        yield self.y
        yield self.z

    def scale(self, k: int) -> Vec3:
        return Vec3(self.x * k, self.y * k, self.z * k)

    def cmd(self) -> str:
        """Render as a command-argument block position."""
        return f"{self.x} {self.y} {self.z}"


class Direction(Enum):
    DOWN = Vec3(0, -1, 0)
    UP = Vec3(0, 1, 0)
    NORTH = Vec3(0, 0, -1)
    SOUTH = Vec3(0, 0, 1)
    WEST = Vec3(-1, 0, 0)
    EAST = Vec3(1, 0, 0)

    @property
    def vec(self) -> Vec3:
        return self.value

    @property
    def vertical(self) -> bool:
        return self in (Direction.UP, Direction.DOWN)

    @property
    def opposite(self) -> Direction:
        return Direction(-self.value)

    @staticmethod
    def parse(name: str) -> Direction:
        return Direction[name.upper()]


class Rotation(Enum):
    NONE = "none"
    CLOCKWISE_90 = "clockwise_90"
    CLOCKWISE_180 = "180"
    COUNTERCLOCKWISE_90 = "counterclockwise_90"

    def apply(self, p: Vec3) -> Vec3:
        if self is Rotation.NONE:
            return p
        if self is Rotation.CLOCKWISE_90:
            return Vec3(-p.z, p.y, p.x)
        if self is Rotation.CLOCKWISE_180:
            return Vec3(-p.x, p.y, -p.z)
        return Vec3(p.z, p.y, -p.x)

    def direction(self, d: Direction) -> Direction:
        if d.vertical:
            return d
        return Direction(self.apply(d.vec))

    def compose(self, other: Rotation) -> Rotation:
        """self then other."""
        order = [Rotation.NONE, Rotation.CLOCKWISE_90, Rotation.CLOCKWISE_180, Rotation.COUNTERCLOCKWISE_90]
        return order[(order.index(self) + order.index(other)) % 4]

    @property
    def cmd(self) -> str:
        return self.value


ALL_ROTATIONS = (Rotation.NONE, Rotation.CLOCKWISE_90, Rotation.CLOCKWISE_180, Rotation.COUNTERCLOCKWISE_90)


@dataclass(frozen=True)
class AABB:
    """Inclusive integer bounding box."""
    min: Vec3
    max: Vec3

    @staticmethod
    def of(a: Vec3, b: Vec3) -> AABB:
        return AABB(
            Vec3(min(a.x, b.x), min(a.y, b.y), min(a.z, b.z)),
            Vec3(max(a.x, b.x), max(a.y, b.y), max(a.z, b.z)),
        )

    @staticmethod
    def from_size(origin: Vec3, size: Vec3, rotation: Rotation) -> AABB:
        far = Vec3(size.x - 1, size.y - 1, size.z - 1)
        return AABB.of(origin + rotation.apply(Vec3(0, 0, 0)), origin + rotation.apply(far))

    @property
    def size(self) -> Vec3:
        return Vec3(self.max.x - self.min.x + 1, self.max.y - self.min.y + 1, self.max.z - self.min.z + 1)

    @property
    def volume(self) -> int:
        s = self.size
        return s.x * s.y * s.z

    @property
    def center(self) -> Vec3:
        return Vec3((self.min.x + self.max.x) // 2, (self.min.y + self.max.y) // 2, (self.min.z + self.max.z) // 2)

    def intersects(self, o: AABB) -> bool:
        return (
            self.min.x <= o.max.x and self.max.x >= o.min.x
            and self.min.y <= o.max.y and self.max.y >= o.min.y
            and self.min.z <= o.max.z and self.max.z >= o.min.z
        )

    def contains_box(self, o: AABB) -> bool:
        return (
            self.min.x <= o.min.x and o.max.x <= self.max.x
            and self.min.y <= o.min.y and o.max.y <= self.max.y
            and self.min.z <= o.min.z and o.max.z <= self.max.z
        )

    def contains(self, p: Vec3) -> bool:
        return (
            self.min.x <= p.x <= self.max.x
            and self.min.y <= p.y <= self.max.y
            and self.min.z <= p.z <= self.max.z
        )

    def union(self, o: AABB) -> AABB:
        return AABB(
            Vec3(min(self.min.x, o.min.x), min(self.min.y, o.min.y), min(self.min.z, o.min.z)),
            Vec3(max(self.max.x, o.max.x), max(self.max.y, o.max.y), max(self.max.z, o.max.z)),
        )
