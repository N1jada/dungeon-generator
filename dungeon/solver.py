"""Deterministic jigsaw placement.

Reimplements the geometric core of Minecraft's ``JigsawPlacement`` so that a layout
can be resolved off-line, with a seed, into an exact list of (template, origin,
rotation) placements plus the world position of every jigsaw block and block entity.

Two operations:

* :meth:`Solver.attach` — attach ONE named piece to ONE jigsaw of an already placed
  piece.  This is how a hand-authored (or later, algorithmically generated) room graph
  is realised: the *macro* layout is fully deterministic.
* :meth:`Solver.expand` — vanilla-style weighted-random expansion of every remaining
  outbound jigsaw of a placement through its template pools (addons, decor, spawners,
  reward vaults, entrance caps ...), driven by a seeded RNG.  This is what makes the
  rooms look like real trial chambers instead of empty shells.

Free-space semantics follow vanilla: a child whose connection point lies inside its
parent's bounding box is placed in the parent's *interior* free space (and may overlap
the parent, but not other interior children); otherwise it goes in the *outer* free
space shared by all exterior pieces.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .catalogue import Catalogue, Jigsaw, Piece
from .geometry import AABB, ALL_ROTATIONS, Direction, Rotation, Vec3

DEFAULT_MAX_DEPTH = 20


class FreeSpace:
    """A region pieces may be placed into, minus what is already occupied."""

    def __init__(self, bounds: AABB | None) -> None:
        self.bounds = bounds
        self.occupied: list[AABB] = []

    def fits(self, box: AABB) -> bool:
        if self.bounds is not None and not self.bounds.contains_box(box):
            return False
        return not any(box.intersects(o) for o in self.occupied)

    def take(self, box: AABB) -> None:
        self.occupied.append(box)


@dataclass
class WorldJigsaw:
    """A jigsaw block of a placed piece, in world coordinates."""
    jigsaw: Jigsaw
    index: int
    pos: Vec3
    front: Direction
    top: Direction

    @property
    def connect_pos(self) -> Vec3:
        return self.pos + self.front.vec


@dataclass
class Placement:
    piece: Piece
    origin: Vec3
    rotation: Rotation
    box: AABB
    depth: int
    role: str
    parent: Placement | None = None
    free: FreeSpace | None = None          # the free space this placement was put into
    interior: FreeSpace | None = None      # lazily created: this piece's own interior
    used: set[int] = field(default_factory=set)   # jigsaw indices consumed by a connection
    children: list[Placement] = field(default_factory=list)
    # the jigsaw (index) on this piece through which it connects to its parent
    via: int | None = None

    def world_pos(self, local: Vec3) -> Vec3:
        return self.origin + self.rotation.apply(local)

    def world_jigsaws(self) -> list[WorldJigsaw]:
        out = []
        for i, j in enumerate(self.piece.jigsaws):
            out.append(
                WorldJigsaw(
                    jigsaw=j,
                    index=i,
                    pos=self.world_pos(j.pos),
                    front=self.rotation.direction(j.front),
                    top=self.rotation.direction(j.top),
                )
            )
        return out

    def unused_outbound(self) -> list[WorldJigsaw]:
        return [w for w in self.world_jigsaws() if w.jigsaw.outbound and w.index not in self.used]

    def interior_space(self) -> FreeSpace:
        if self.interior is None:
            self.interior = FreeSpace(self.box)
        return self.interior

    @property
    def short_id(self) -> str:
        return self.piece.id.replace("minecraft:trial_chambers/", "")

    def describe(self) -> str:
        return f"{self.short_id} @ {self.origin.cmd()} rot={self.rotation.value} role={self.role}"


class LayoutError(RuntimeError):
    pass


def _effective_joint(j: Jigsaw) -> str:
    if j.joint in ("rollable", "aligned"):
        return j.joint
    return "aligned" if not j.front.vertical else "rollable"


class Solver:
    def __init__(
        self,
        catalogue: Catalogue,
        seed: int = 0,
        pool_aliases: dict[str, str] | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        self.cat = catalogue
        self.rng = random.Random(seed)
        self.seed = seed
        self.pool_aliases = dict(pool_aliases or {})
        self.max_depth = max_depth
        self.outer = FreeSpace(None)
        self.placements: list[Placement] = []

    # ------------------------------------------------------------------ roots
    def place_root(self, piece_id: str, origin: Vec3, rotation: Rotation = Rotation.NONE, role: str = "root") -> Placement:
        piece = self.cat.piece(piece_id)
        box = AABB.from_size(origin, piece.size, rotation)
        if not self.outer.fits(box):
            raise LayoutError(f"root {piece_id} at {origin} overlaps an existing piece")
        p = Placement(piece=piece, origin=origin, rotation=rotation, box=box, depth=0, role=role, free=self.outer)
        self.outer.take(box)
        self.placements.append(p)
        return p

    # --------------------------------------------------------------- attaching
    def _candidate_origins(
        self,
        parent_wj: WorldJigsaw,
        child: Piece,
        rotations: Iterable[Rotation],
        match: Callable[[Jigsaw, Jigsaw], bool],
    ) -> list[tuple[Rotation, int, Vec3]]:
        """All (rotation, child_jigsaw_index, origin) that geometrically connect."""
        pj = parent_wj.jigsaw
        out: list[tuple[Rotation, int, Vec3]] = []
        for rot in rotations:
            for ci, cj in enumerate(child.jigsaws):
                if not match(pj, cj):
                    continue
                cfront = rot.direction(cj.front)
                if cfront != parent_wj.front.opposite:
                    continue
                # joint: use the outbound side's joint (vanilla uses the parent's)
                joint_owner = pj if pj.outbound else cj
                if _effective_joint(joint_owner) == "aligned" and parent_wj.front.vertical:
                    if rot.direction(cj.top) != parent_wj.top:
                        continue
                origin = parent_wj.connect_pos - rot.apply(cj.pos)
                out.append((rot, ci, origin))
        return out

    def _space_for(self, parent: Placement, parent_wj: WorldJigsaw) -> FreeSpace:
        if parent.box.contains(parent_wj.connect_pos):
            return parent.interior_space()
        assert parent.free is not None
        return parent.free

    def _commit(
        self,
        parent: Placement,
        parent_wj: WorldJigsaw,
        child: Piece,
        rot: Rotation,
        ci: int,
        origin: Vec3,
        space: FreeSpace,
        role: str,
    ) -> Placement:
        box = AABB.from_size(origin, child.size, rot)
        p = Placement(
            piece=child, origin=origin, rotation=rot, box=box, depth=parent.depth + 1,
            role=role, parent=parent, free=space, via=ci,
        )
        p.used.add(ci)
        parent.used.add(parent_wj.index)
        parent.children.append(p)
        space.take(box)
        self.placements.append(p)
        return p

    def attach(
        self,
        parent: Placement,
        child_id: str,
        *,
        parent_jigsaw: str | int | None = None,
        child_jigsaw: str | int | None = None,
        role: str = "piece",
        rotations: Iterable[Rotation] = ALL_ROTATIONS,
        reverse_ok: bool = True,
    ) -> Placement:
        """Attach ``child_id`` to ``parent`` through a jigsaw pair.

        ``parent_jigsaw`` / ``child_jigsaw`` select connectors by jigsaw *name* (the
        inbound identity, e.g. ``minecraft:in_connector``), by *target* (outbound), or
        by index.  Prefix with ``name:`` / ``target:`` to disambiguate; a bare string
        matches either field.  Leaving them ``None`` tries every compatible pair.  With
        ``reverse_ok`` the parent's jigsaw may be the inbound ("name") side and the
        child's the outbound ("target") side — geometry is symmetric, so this lets a
        hand layout chain rooms in whatever direction reads naturally.
        """
        child = self.cat.piece(child_id)

        def selects(sel: str | int | None, j: Jigsaw, idx: int) -> bool:
            if sel is None:
                return True
            if isinstance(sel, int):
                return idx == sel
            if sel.startswith("name:"):
                return j.name == sel[5:]
            if sel.startswith("target:"):
                return j.target == sel[7:]
            return sel in (j.name, j.target)

        def match(pj: Jigsaw, cj: Jigsaw) -> bool:
            # forward: vanilla rule, parent's target names the child's connector
            if pj.outbound and pj.target == cj.name:
                return True
            if not reverse_ok:
                return False
            # reverse: child's target names the parent's connector
            if cj.outbound and cj.target == pj.name:
                return True
            # symmetric: two inbound connectors of the same profile (e.g. two
            # in_connector doorways) — never used by vanilla, but geometrically sound
            # and what a hand-authored room chain needs to leave a room by a spare door
            return pj.name == cj.name and pj.name not in ("", "minecraft:empty")

        tried = []
        for wj in parent.world_jigsaws():
            if wj.index in parent.used or not selects(parent_jigsaw, wj.jigsaw, wj.index):
                continue
            space = self._space_for(parent, wj)
            for rot, ci, origin in self._candidate_origins(wj, child, rotations, match):
                if not selects(child_jigsaw, child.jigsaws[ci], ci):
                    continue
                box = AABB.from_size(origin, child.size, rot)
                tried.append((wj.index, rot.value, origin))
                if space.fits(box):
                    return self._commit(parent, wj, child, rot, ci, origin, space, role)
        raise LayoutError(
            f"cannot attach {child_id} to {parent.describe()} "
            f"(parent_jigsaw={parent_jigsaw!r}, child_jigsaw={child_jigsaw!r}); tried {len(tried)} candidates"
        )

    # --------------------------------------------------------------- expansion
    def resolve_pool(self, pool_id: str) -> str:
        seen = set()
        while pool_id in self.pool_aliases and pool_id not in seen:
            seen.add(pool_id)
            pool_id = self.pool_aliases[pool_id]
        return pool_id

    def _weighted_order(self, elements) -> list:
        """Weighted random ordering without replacement (vanilla draws one at a time)."""
        pool = list(elements)
        order = []
        while pool:
            total = sum(max(e.weight, 0) for e in pool)
            if total <= 0:
                order.extend(pool)
                break
            r = self.rng.uniform(0, total)
            acc = 0.0
            for e in pool:
                acc += max(e.weight, 0)
                if r <= acc:
                    order.append(e)
                    pool.remove(e)
                    break
            else:
                order.append(pool.pop())
        return order

    def expand(
        self,
        start: Placement,
        *,
        max_depth: int | None = None,
        skip_jigsaw: Callable[[Placement, WorldJigsaw], bool] | None = None,
        role: str = "addon",
    ) -> list[Placement]:
        """Vanilla-style expansion of all unused outbound jigsaws reachable from ``start``.

        Returns every placement created.  ``skip_jigsaw`` lets the caller reserve
        connectors for the hand layout (e.g. a chamber's in_connectors that will host
        hallways) so expansion doesn't seal them with entrance caps.
        """
        max_depth = self.max_depth if max_depth is None else max_depth
        created: list[Placement] = []
        queue: list[Placement] = [start]
        while queue:
            parent = queue.pop(0)
            if parent.depth >= max_depth:
                continue
            wjs = [w for w in parent.unused_outbound() if not (skip_jigsaw and skip_jigsaw(parent, w))]
            # vanilla: sort by selection_priority desc, shuffle within equal priority
            self.rng.shuffle(wjs)
            wjs.sort(key=lambda w: -w.jigsaw.selection_priority)
            for wj in wjs:
                pool_id = self.resolve_pool(wj.jigsaw.pool)
                pool = self.cat.pool(pool_id)
                fallback = self.cat.pool(self.resolve_pool(pool.fallback))
                if parent.depth + 1 >= max_depth:
                    elements = list(fallback.elements)
                else:
                    elements = list(pool.elements) + list(fallback.elements)
                if not elements:
                    continue
                space = self._space_for(parent, wj)
                placed = None
                for el in self._weighted_order(elements):
                    if el.kind == "empty":
                        break  # vanilla: EmptyPoolElement selected -> jigsaw stays unfilled
                    if el.kind != "single" or el.location is None:
                        continue
                    try:
                        child = self.cat.piece(el.location)
                    except FileNotFoundError:
                        # vanilla pools can name templates that don't ship (ancient_city
                        # walls/no_corners does); vanilla skips them, so do we
                        continue
                    rots = list(ALL_ROTATIONS)
                    self.rng.shuffle(rots)
                    cands = self._candidate_origins(
                        wj, child, rots, lambda pj, cj: pj.outbound and pj.target == cj.name
                    )
                    self.rng.shuffle(cands)
                    for rot, ci, origin in cands:
                        box = AABB.from_size(origin, child.size, rot)
                        if space.fits(box):
                            placed = self._commit(parent, wj, child, rot, ci, origin, space, role)
                            break
                    if placed:
                        break
                if placed:
                    created.append(placed)
                    queue.append(placed)
        return created

    # ---------------------------------------------------------------- queries
    def bounds(self) -> AABB:
        b = self.placements[0].box
        for p in self.placements[1:]:
            b = b.union(p.box)
        return b

    def solid_blocks(self) -> int:
        return sum(p.piece.solid_blocks for p in self.placements)

    def exterior(self) -> list[Placement]:
        return [p for p in self.placements if p.free is self.outer]
