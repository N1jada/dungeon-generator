"""Stage 4: spatial layout.  Deterministic code, never an LLM.

Phase 1 uses a hand-authored room chain built from vanilla trial-chamber pieces:

    surface entrance -> atrium -> corridor -> crossing -> hallway -> ROOM 1 (encounter)
                        -> staircase down -> ROOM 2 (encounter) -> hallway -> ROOM 3 (boss)

The chain is expressed as edges between named nodes; the solver turns it into exact
placements and then fills every remaining connector the way vanilla would (addons,
spawners, vaults, decor, caps), from a seed, so the same layout + seed always yields
the same dungeon.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .catalogue import Catalogue, load_structure, resolve_pool_aliases
from .geometry import AABB, Direction, Rotation, Vec3
from .solver import LayoutError, Placement, Solver

TC = "minecraft:trial_chambers/"


@dataclass(frozen=True)
class Edge:
    parent: str
    parent_jigsaw: str | None
    child_jigsaw: str | None


@dataclass(frozen=True)
class Node:
    id: str
    piece: str | tuple[str, ...]   # one template, or candidates tried in order until one fits
    kind: str                      # entrance | arrival | passage | room | hub | boss | finale
    edge: Edge | None = None       # None => root

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.piece,) if isinstance(self.piece, str) else self.piece


PHASE1_NODES: tuple[Node, ...] = (
    Node("entrance", TC + "corridor/entrance_1", "entrance"),
    Node("atrium", TC + "corridor/atrium_1", "passage", Edge("entrance", "name:minecraft:entrance", "target:minecraft:entrance")),
    Node("corridor", TC + "corridor/first_plate", "passage", Edge("atrium", "name:minecraft:atrium", "target:minecraft:atrium")),
    Node("crossing", TC + "intersection/intersection_1", "passage", Edge("corridor", "name:minecraft:first_corridor", "target:minecraft:first_corridor")),
    Node("hall1", TC + "hallway/straight", "passage", Edge("crossing", "target:minecraft:in_connector", "name:minecraft:in_connector")),
    Node("room1", TC + "chamber/chamber_1", "room", Edge("hall1", "target:minecraft:in_connector", "name:minecraft:in_connector")),
    Node("hall2", TC + "hallway/long_straight_staircase_down", "passage", Edge("room1", "name:minecraft:in_connector", "name:minecraft:in_connector")),
    Node("room2", TC + "chamber/assembly", "room", Edge("hall2", "target:minecraft:in_connector", "name:minecraft:in_connector")),
    Node("hall3", TC + "hallway/straight", "passage", Edge("room2", "name:minecraft:in_connector", "name:minecraft:in_connector")),
    Node("boss", TC + "chamber/pedestal", "boss", Edge("hall3", "target:minecraft:in_connector", "name:minecraft:in_connector")),
)


@dataclass
class Room:
    id: str
    kind: str
    placement: Placement
    door: Vec3            # world position of the jigsaw the player enters through
    inward: Direction     # direction from the door into the room
    entry_floor: Vec3     # a floor block just inside the door (stand on entry_floor + up)
    depth: int            # graph distance from the entrance node
    box_override: AABB | None = None   # e.g. a plaza: the finale piece plus its walls

    @property
    def box(self) -> AABB:
        return self.box_override or self.placement.box


@dataclass
class Slot:
    """Something the logic layer may address: a spawner, vault or chest."""
    kind: str             # trial_spawner | vault | chest | barrel | dispenser
    pos: Vec3
    room: str | None      # nearest room id (by containment), else None
    piece: str
    detail: str = ""      # e.g. the spawner's mob category / vanilla config


@dataclass
class Layout:
    seed: int
    solver: Solver
    nodes: dict[str, Placement]
    rooms: list[Room]
    slots: list[Slot]
    order: list[str] = field(default_factory=list)
    zone: str = "main"

    @property
    def entrance(self) -> Placement:
        first = self.order[0]
        return self.nodes.get("entrance", self.nodes[first])

    def room(self, room_id: str) -> Room:
        return next(r for r in self.rooms if r.id == room_id)

    @property
    def boss_room(self) -> Room:
        return next(r for r in self.rooms if r.kind in ("boss", "finale"))

    def spare_doors(self, room_id: str) -> list:
        """Unused in_connector-style doorways of a room, as WorldJigsaw objects."""
        pl = self.nodes[room_id]
        return [w for w in pl.world_jigsaws() if w.index not in pl.used and w.jigsaw.name not in ("", "minecraft:empty")]

    def bounds(self) -> AABB:
        return self.solver.bounds()

    def room_for(self, pos: Vec3) -> str | None:
        for r in self.rooms:
            if r.box.contains(pos):
                return r.id
        return None


INVERSE = {
    Rotation.NONE: Rotation.NONE, Rotation.CLOCKWISE_90: Rotation.COUNTERCLOCKWISE_90,
    Rotation.CLOCKWISE_180: Rotation.CLOCKWISE_180, Rotation.COUNTERCLOCKWISE_90: Rotation.CLOCKWISE_90,
}


def solid_at(placements: list[Placement], world: Vec3) -> bool:
    """World solidity judged from template data: the last placed piece covering the
    block wins (templates place their air too), and a jigsaw counts as its final state."""
    result = False
    for pl in placements:
        if not pl.box.contains(world):
            continue
        local = INVERSE[pl.rotation].apply(world - pl.origin)
        jig = next((j for j in pl.piece.jigsaws if j.pos == local), None)
        if jig is not None:
            result = not jig.final_state.startswith("minecraft:air")
        else:
            result = pl.piece.is_solid(local)
    return result


def standing_spot(placements: list[Placement], within: Placement, near_world: Vec3, radius: int = 6) -> Vec3 | None:
    """Nearest floor block (solid, two air above) to ``near_world`` inside ``within``'s box,
    judged from the composite of every placed template — no server needed."""
    best: tuple[int, Vec3] | None = None
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                w = near_world + Vec3(dx, dy, dz)
                if not within.box.contains(w) or not within.box.contains(w + Vec3(0, 2, 0)):
                    continue
                if solid_at(placements, w) and not solid_at(placements, w + Vec3(0, 1, 0)) and not solid_at(placements, w + Vec3(0, 2, 0)):
                    d = abs(dx) + abs(dz) + 2 * abs(dy)
                    if best is None or d < best[0]:
                        best = (d, w)
    return best[1] if best else None


def _entry_info(placements: list[Placement], placement: Placement) -> tuple[Vec3, Direction, Vec3]:
    """Door position, inward direction and a floor block inside the door for a piece."""
    if placement.via is None:
        # root (e.g. entrance_1, a capped shaft): land the player on a floor mid-piece
        box = placement.box
        guess = Vec3(box.center.x, box.min.y + 6, box.center.z)
        spot = standing_spot(placements, placement, guess, radius=9) or guess
        return spot + Vec3(0, 1, 0), Direction.DOWN, spot
    wj = placement.world_jigsaws()[placement.via]
    inward = wj.front.opposite
    # Jigsaws sit at the centre of a 5x5 doorway: floor is two blocks down
    guess = wj.pos + inward.vec.scale(2) + Vec3(0, -2, 0)
    floor = standing_spot(placements, placement, guess, radius=4) or guess
    return wj.pos, inward, floor


def resolve_chain(
    catalogue: Catalogue,
    nodes: tuple[Node, ...],
    *,
    seed: int = 0,
    origin: Vec3 = Vec3(0, 40, 0),
    rotation: Rotation = Rotation.NONE,
    alias_structure: str = "minecraft:trial_chambers",
    spawner_contents: dict[str, str] | None = None,
    expand: bool = True,
    max_depth: int | None = None,
    reserve: dict[str, list[str]] | None = None,
    zone: str = "main",
) -> Layout:
    """Resolve a hand-authored chain of nodes into a fully expanded, deterministic layout.

    ``reserve`` maps a node id to jigsaw selectors (``name:``/``target:``) that expansion
    must leave alone, e.g. a spare doorway that will hold a zone gate.
    """
    rng = random.Random(seed)
    aliases = resolve_pool_aliases(load_structure(alias_structure), rng)
    for category, piece in (spawner_contents or {}).items():
        aliases[f"{TC}spawner/contents/{category}"] = f"{TC}spawner/{category}/{piece}"
    solver = Solver(catalogue, seed=seed, pool_aliases=aliases)
    if max_depth is not None:
        solver.max_depth = max_depth

    placed: dict[str, Placement] = {}
    depth: dict[str, int] = {}
    for node in nodes:
        if node.edge is None:
            placed[node.id] = solver.place_root(node.candidates[0], origin, rotation, role=f"{node.kind}:{node.id}")
            depth[node.id] = 0
        else:
            parent = placed[node.edge.parent]
            last_error: Exception | None = None
            for candidate in node.candidates:
                try:
                    placed[node.id] = solver.attach(
                        parent, candidate,
                        parent_jigsaw=node.edge.parent_jigsaw,
                        child_jigsaw=node.edge.child_jigsaw,
                        role=f"{node.kind}:{node.id}",
                    )
                    break
                except LayoutError as e:
                    last_error = e
            else:
                raise LayoutError(f"node {node.id}: no candidate fits ({last_error})")
            depth[node.id] = depth[node.edge.parent] + 1

    reserved = reserve or {}

    def skip(pl: Placement, wj) -> bool:
        node_id = pl.role.split(":", 1)[1] if ":" in pl.role else ""
        for sel in reserved.get(node_id, []):
            if sel.startswith("name:") and wj.jigsaw.name == sel[5:]:
                return True
            if sel.startswith("target:") and wj.jigsaw.target == sel[7:]:
                return True
        return False

    if expand:
        for node in nodes:
            solver.expand(placed[node.id], skip_jigsaw=skip)

    rooms: list[Room] = []
    for node in nodes:
        if node.kind in ("room", "boss", "entrance", "hub", "arrival", "finale"):
            door, inward, floor = _entry_info(solver.placements, placed[node.id])
            rooms.append(Room(node.id, node.kind, placed[node.id], door, inward, floor, depth[node.id]))

    layout = Layout(seed=seed, solver=solver, nodes=placed, rooms=rooms, slots=[], order=[n.id for n in nodes])
    layout.zone = zone
    for p in solver.placements:
        for be in p.piece.block_entities:
            kind = be.block.replace("minecraft:", "")
            pos = p.world_pos(be.pos)
            detail = ""
            if kind == "trial_spawner":
                detail = p.piece.id.replace(TC + "spawner/", "")
            elif kind == "vault":
                detail = be.nbt.get("config", {}).get("loot_table", "")
            elif kind in ("chest", "barrel"):
                detail = be.nbt.get("LootTable", "")
            layout.slots.append(Slot(kind, pos, layout.room_for(pos), p.piece.id, detail))
    return layout


def build_phase1(
    catalogue: Catalogue,
    seed: int = 0,
    origin: Vec3 = Vec3(0, 40, 0),
    rotation: Rotation = Rotation.NONE,
    nodes: tuple[Node, ...] = PHASE1_NODES,
    spawner_contents: dict[str, str] | None = None,
) -> Layout:
    """The Phase 1 three-room chain (kept for the first dungeon)."""
    return resolve_chain(catalogue, nodes, seed=seed, origin=origin, rotation=rotation, spawner_contents=spawner_contents)
