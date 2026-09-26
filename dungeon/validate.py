"""Stage 9 (automated half): deterministic gates a dungeon must pass before a human sees it."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import vanilla
from .layout import Layout
from .logic import LogicSpec


@dataclass
class Report:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    def render(self) -> str:
        width = max(len(n) for n, _, _ in self.checks)
        lines = [f"{'PASS' if ok else 'FAIL'}  {n.ljust(width)}  {d}" for n, ok, d in self.checks]
        lines.append(f"{'ALL GATES PASSED' if self.ok else 'GATES FAILED'} ({sum(ok for _, ok, _ in self.checks)}/{len(self.checks)})")
        return "\n".join(lines)


def _connection_graph(layout: Layout) -> dict[int, set[int]]:
    """Adjacency between placements, derived purely from matching jigsaw pairs in
    world space — independent of the parent/child bookkeeping the solver kept."""
    placements = layout.solver.placements
    by_pos: dict[tuple, list[tuple[int, object]]] = {}
    for i, p in enumerate(placements):
        for w in p.world_jigsaws():
            by_pos.setdefault(tuple(w.pos), []).append((i, w))
    adj: dict[int, set[int]] = {i: set() for i in range(len(placements))}
    for i, p in enumerate(placements):
        for w in p.world_jigsaws():
            for j, w2 in by_pos.get(tuple(w.connect_pos), []):
                if j != i and w2.front == w.front.opposite:
                    adj[i].add(j)
                    adj[j].add(i)
    return adj


def validate_layout(layout: Layout, *, max_solid_blocks: int = 250_000, max_extent: int = 256,
                    require_boss: bool = True) -> Report:
    rep = Report()
    placements = layout.solver.placements
    index = {id(p): i for i, p in enumerate(placements)}

    # 1. exterior pieces never overlap
    ext = layout.solver.exterior()
    overlaps = [(a.short_id, b.short_id) for i, a in enumerate(ext) for b in ext[i + 1:] if a.box.intersects(b.box)]
    rep.add("no exterior overlap", not overlaps, f"{len(ext)} exterior pieces" if not overlaps else str(overlaps[:3]))

    # 2. interior children of one parent never overlap each other
    bad = []
    for p in placements:
        if p.interior:
            occ = p.interior.occupied
            bad += [(p.short_id, a, b) for i, a in enumerate(occ) for b in occ[i + 1:] if a.intersects(b)]
    rep.add("no interior overlap", not bad, f"{len(bad)} collisions" if bad else "")

    # 3. every piece is jigsaw-connected to its parent (positions + facings agree)
    disconnected = []
    for p in placements:
        if p.parent is None:
            continue
        cw = p.world_jigsaws()[p.via]
        ok = any(w.index in p.parent.used and w.connect_pos == cw.pos and w.front == cw.front.opposite
                 for w in p.parent.world_jigsaws())
        if not ok:
            disconnected.append(p.short_id)
    rep.add("pieces connect to parents", not disconnected, f"{len(placements) - 1} connections" if not disconnected else str(disconnected[:3]))

    # 4. graph connectivity: every room reachable from the entrance via jigsaw links
    adj = _connection_graph(layout)
    start_room = next((r for r in layout.rooms if r.kind in ("entrance", "arrival")), None)
    start = index[id(start_room.placement)] if start_room else index[id(layout.entrance)]
    seen = {start}
    dist = {start: 0}
    dq = deque([start])
    while dq:
        u = dq.popleft()
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                dist[v] = dist[u] + 1
                dq.append(v)
    unreachable = [r.id for r in layout.rooms if index[id(r.placement)] not in seen]
    rep.add("all rooms reachable", not unreachable, f"{len(seen)}/{len(placements)} pieces reachable" if not unreachable else str(unreachable))

    # 5. the boss room is the furthest room from the entrance
    room_dist = {r.id: dist.get(index[id(r.placement)], -1) for r in layout.rooms}
    boss_room = next((r for r in layout.rooms if r.kind in ("boss", "finale")), None)
    if require_boss or boss_room is not None:
        boss_d = room_dist.get(boss_room.id, -1) if boss_room else -1
        furthest = boss_d >= 0 and all(d <= boss_d for d in room_dist.values())
        rep.add("boss room is furthest", furthest, str(room_dist))

    # 6. budget
    solid = layout.solver.solid_blocks()
    b = layout.bounds()
    within = solid <= max_solid_blocks and max(b.size.x, b.size.z) <= max_extent
    rep.add("volume within budget", within, f"{solid} solid blocks, footprint {b.size.x}x{b.size.y}x{b.size.z}")

    # 7. every template referenced exists in the vendored vanilla data
    missing = [p.piece.id for p in placements if not vanilla.structure_nbt_path(p.piece.id).exists()]
    rep.add("templates exist", not missing, f"{len({p.piece.id for p in placements})} distinct templates" if not missing else str(missing[:3]))
    return rep


def validate_spec(spec: LogicSpec, layout: Layout) -> Report:
    rep = Report()
    room_ids = {r.id for r in layout.rooms if r.kind != "entrance"}
    spec_rooms = [r.room for r in spec.rooms]
    rep.add("spec rooms match layout", set(spec_rooms) == room_ids and len(spec_rooms) == len(set(spec_rooms)),
            f"spec={sorted(spec_rooms)} layout={sorted(room_ids)}")
    ents = vanilla.registry_keys("minecraft:entity_type")
    items = vanilla.registry_keys("minecraft:item")
    refs_ok = (
        spec.boss.entity in ents
        and all(g.entity in ents for r in spec.rooms for g in r.ambush)
        and all(g.entity in ents for g in spec.boss.minions)
        and all(e.item in items for e in spec.boss.equipment)
        and all(e.item in items for e in spec.reward.loot)
    )
    rep.add("referenced ids exist", refs_ok, "entities, items")
    boss_room = next((r for r in spec.rooms if r.room == "boss"), None)
    rep.add("boss room has no ambush", boss_room is not None and not boss_room.ambush, "")
    return rep
