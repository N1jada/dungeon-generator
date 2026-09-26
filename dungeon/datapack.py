"""Stages 5 and 8 (the file half): compile a resolved layout + logic spec into a datapack.

The datapack is self-contained and vanilla-only:

* ``dungeon:arena`` — a void dimension of its own, so the generator can never touch an
  existing world.  (Requires a server restart to register; functions can be reloaded.)
* ``dungeon:build`` — force-loads the footprint, then places every vanilla structure
  template at the solver's coordinates and swaps each jigsaw block for its final state.
* ``dungeon:enter`` / ``dungeon:leave`` — teleport a player in/out.
* ``dungeon:tick`` — a light state machine (room titles, ambushes, boss, reward).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import vanilla
from .geometry import Direction, Vec3
from .layout import Layout, Room
from .logic import LogicSpec

FILL_LIMIT = 32768  # vanilla commandModificationBlockLimit default


WORLD_SPAWN = Vec3(-32, 70, 48)   # example overworld spawn (from level.dat); override per deploy


def spawn_functions(ns: str, world_spawn: Vec3 = WORLD_SPAWN) -> dict[str, str]:
    """Per-player respawn save/restore, keyed by UUID in storage, so leaving a dungeon
    puts the player's bed back instead of leaving a checkpoint behind.  26.2 has no
    `clearspawnpoint`, so a player who had no bed gets the world spawn instead."""
    return {
        f"data/{ns}/function/spawn/save.mcfunction": "\n".join([
            "# run as the player: remember their respawn before the dungeon overrides it",
            "# (enter/leave call save_uuid/restore_uuid directly: a macro called through a",
            "#  plain wrapper from inside another function did not take effect on 26.2)",
            f"function {ns}:spawn/save_uuid with entity @s",
            "",
        ]),
        f"data/{ns}/function/spawn/save_uuid.mcfunction": "\n".join([
            "# macro: $(UUID) from the player's own NBT",
            f'$data remove storage {ns}:spawns players."$(UUID)"',
            f'$execute if data entity @s respawn run data modify storage {ns}:spawns players."$(UUID)".dimension set from entity @s respawn.dimension',
            f'$execute if data entity @s respawn store result storage {ns}:spawns players."$(UUID)".x int 1 run data get entity @s respawn.pos[0]',
            f'$execute if data entity @s respawn store result storage {ns}:spawns players."$(UUID)".y int 1 run data get entity @s respawn.pos[1]',
            f'$execute if data entity @s respawn store result storage {ns}:spawns players."$(UUID)".z int 1 run data get entity @s respawn.pos[2]',
            "",
        ]),
        f"data/{ns}/function/spawn/restore.mcfunction": "\n".join([
            "# run as the player: put back whatever respawn they had before entering",
            f"function {ns}:spawn/restore_uuid with entity @s",
            "",
        ]),
        f"data/{ns}/function/spawn/restore_uuid.mcfunction": "\n".join([
            f'$execute unless data storage {ns}:spawns players."$(UUID)".x in minecraft:overworld run spawnpoint @s {world_spawn.cmd()}',
            f'$execute if data storage {ns}:spawns players."$(UUID)".x run function {ns}:spawn/apply with storage {ns}:spawns players."$(UUID)"',
            f'$data remove storage {ns}:spawns players."$(UUID)"',
            "",
        ]),
        f"data/{ns}/function/spawn/apply.mcfunction": "\n".join([
            "$execute in $(dimension) run spawnpoint @s $(x) $(y) $(z)",
            "",
        ]),
    }


def exit_pedestal(lay, room) -> tuple[Vec3, Vec3, Vec3] | None:
    """Where the arrival room's EXIT pedestal goes: a floor block 2-4 blocks from the
    entry point with three air above it (plinth, button, headroom), judged from the
    composite template model.  Returns (plinth, button, sign) or None."""
    from .layout import solid_at
    pls = lay.solver.placements
    up = Vec3(0, 1, 0)
    best = None
    for dist in (2, 3, 4):
        for dx in range(-dist, dist + 1):
            for dz in range(-dist, dist + 1):
                if max(abs(dx), abs(dz)) != dist:
                    continue
                f = room.entry_floor + Vec3(dx, 0, dz)
                if not room.box.contains(f + up.scale(3)):
                    continue
                if solid_at(pls, f) and not any(solid_at(pls, f + up.scale(k)) for k in (1, 2, 3)):
                    sign = f + Vec3(1 if dx >= 0 else -1, 0, 0) if abs(dx) >= abs(dz) else f + Vec3(0, 0, 1 if dz >= 0 else -1)
                    if solid_at(pls, sign) and not solid_at(pls, sign + up):
                        return f + up, f + up.scale(2), sign + up
                    best = best or (f + up, f + up.scale(2), None)
        if best:
            return best
    return None


def exit_pedestal_lines(ns: str, dim: str, ped) -> list[str]:
    plinth, button, sign = ped
    lines = [f"execute in {dim} run setblock {plinth.cmd()} minecraft:polished_deepslate",
             f"execute in {dim} run setblock {button.cmd()} minecraft:stone_button[face=floor,facing=north,powered=false]"]
    if sign is not None:
        msgs = ",".join(snbt_str(m) for m in ("EXIT", "press to return", "to the", "portal room"))
        lines.append(f"execute in {dim} run setblock {sign.cmd()} minecraft:oak_sign{{front_text:{{messages:[{msgs}]}},is_waxed:1b}}")
    return lines


def exit_poll_line(ns: str, dim: str, ped) -> str:
    button = ped[1]
    return (f"execute in {dim} if block {button.cmd()} minecraft:stone_button[powered=true] "
            f"positioned {button.cmd()} as @a[tag={ns}_inside,distance=..5] run function {ns}:leave")


def snbt_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def text_snbt(text: str, color: str | None = None, bold: bool = False, italic: bool | None = None) -> str:
    """A text component as SNBT, for anything stored in NBT (entity names, item
    components, text_display text). 26.x keeps components as NBT; a JSON string there is
    shown literally."""
    parts = [f"text:{snbt_str(text)}"]
    if color:
        parts.append(f'color:"{color}"')
    if bold:
        parts.append("bold:1b")
    if italic is not None:
        parts.append(f"italic:{'1b' if italic else '0b'}")
    return "{" + ",".join(parts) + "}"


def text_json(text: str, color: str | None = None, bold: bool = False) -> str:
    d: dict = {"text": text}
    if color:
        d["color"] = color
    if bold:
        d["bold"] = True
    return json.dumps(d, ensure_ascii=False)


@dataclass
class Datapack:
    files: dict[str, str] = field(default_factory=dict)

    def add(self, path: str, content: str) -> None:
        self.files[path] = content

    def write(self, dest: Path) -> list[Path]:
        written = []
        for rel, content in self.files.items():
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if rel == "data/minecraft/tags/function/load.json" and p.exists():
                # several dungeons share one datapack: merge the load tag instead of clobbering it
                merged = json.loads(p.read_text())
                for v in json.loads(content)["values"]:
                    if v not in merged["values"]:
                        merged["values"].append(v)
                content = json.dumps(merged) + "\n"
            p.write_text(content)
            written.append(p)
        return written


class DatapackBuilder:
    def __init__(
        self,
        layout: Layout,
        spec: LogicSpec,
        *,
        namespace: str = "dungeon",
        hub: tuple[str, Vec3] = ("minecraft:overworld", Vec3(0, 80, 0)),
        world_spawn: Vec3 = WORLD_SPAWN,
        dim: str | None = None,
        owns_dimension: bool = True,
    ) -> None:
        self.layout = layout
        self.spec = spec
        self.ns = namespace
        # several datapacks can share one arena: only the owner emits the dimension files
        self.dim = dim or f"{namespace}:arena"
        self.owns_dimension = owns_dimension
        self.hub_dim, self.hub_pos = hub
        self.world_spawn = world_spawn
        self.rooms = {r.id: r for r in layout.rooms}
        # the state holder has the same name in every datapack: the objective
        # (``<ns>.state``) is what separates them.
        self.state_holder = "#dungeon"

    # ------------------------------------------------------------ helpers
    def _in(self, cmd: str) -> str:
        return f"execute in {self.dim} run {cmd}"

    def _fn(self, name: str) -> str:
        return f"data/{self.ns}/function/{name}.mcfunction"

    def _room_sel(self, room: Room, extra: str = "") -> str:
        b = room.box
        s = b.size
        return f"@a[x={b.min.x},y={b.min.y},z={b.min.z},dx={s.x - 1},dy={s.y - 1},dz={s.z - 1}{extra}]"

    @staticmethod
    def _stand(pos: Vec3) -> str:
        """Centre of the block above a floor block, as a tp target."""
        return f"{pos.x + 0.5} {pos.y + 1} {pos.z + 0.5}"

    def _spawn_point(self, room: Room, steps: int) -> Vec3:
        return room.entry_floor + room.inward.vec.scale(steps)

    # ------------------------------------------------------------ pieces
    def build_files(self) -> Datapack:
        dp = Datapack()
        major, minor = vanilla.data_pack_format()
        dp.add("pack.mcmeta", json.dumps({
            "pack": {
                "pack_format": major,
                "min_format": [major, minor],
                "max_format": [major, 0x7fffffff],
                "description": f"{self.spec.name} — generated dungeon",
            }
        }, indent=2) + "\n")
        if self.owns_dimension:
            self._dimension_files(dp)
        dp.add("data/minecraft/tags/function/load.json", json.dumps({"values": [f"{self.ns}:load"]}) + "\n")
        dp.add(self._fn("load"), self._load())
        dp.add(self._fn("tick"), self._tick())
        dp.add(self._fn("build"), self._build())
        dp.add(self._fn("build/place"), self._place())
        dp.add(self._fn("build/exit"), self._exit())
        dp.add(self._fn("build/clear"), self._clear())
        dp.add(self._fn("enter"), self._enter())
        dp.add(self._fn("rejoin"), self._rejoin())
        for rel, content in spawn_functions(self.ns, self.world_spawn).items():
            dp.add(rel, content)
        dp.add(self._fn("leave"), self._leave())
        dp.add(self._fn("reset"), self._reset())
        dp.add(self._fn("boss/summon"), self._boss_summon())
        dp.add(self._fn("boss/defeated"), self._boss_defeated())
        for rl in self.spec.rooms:
            dp.add(self._fn(f"room/{rl.room}"), self._room_enter(rl))
        dp.add(f"data/{self.ns}/loot_table/boss_reward.json", self._loot_table())
        return dp

    def _dimension_files(self, dp: Datapack) -> None:
        """Only the dimension owner emits these; other datapacks sharing the arena don't."""
        ns = self.dim.split(":")[0]
        # 26.x dimension_type schema (modelled on vanilla the_nether: fixed time, no sky)
        dp.add(f"data/{ns}/dimension_type/arena.json", json.dumps({
            "ambient_light": 0.0,
            "attributes": {
                "minecraft:gameplay/bed_rule": {
                    "can_set_spawn": "never", "can_sleep": "never", "explodes": False,
                    "error_message": {"translate": "block.minecraft.bed.no_sleep"},
                },
                "minecraft:gameplay/respawn_anchor_works": False,
                "minecraft:gameplay/can_start_raid": False,
            },
            "coordinate_scale": 1.0,
            "has_ceiling": False,
            "has_ender_dragon_fight": False,
            "has_fixed_time": True,
            "has_skylight": False,
            "height": 384, "min_y": -64, "logical_height": 384,
            "infiniburn": "#minecraft:infiniburn_overworld",
            "monster_spawn_block_light_limit": 0,
            "monster_spawn_light_level": 0,
            "skybox": "none",
            "timelines": "#minecraft:universal",
        }, indent=2) + "\n")
        dp.add(f"data/{ns}/dimension/arena.json", json.dumps({
            "type": f"{ns}:arena",
            "generator": {
                "type": "minecraft:flat",
                "settings": {
                    "biome": "minecraft:the_void",
                    "features": False, "lakes": False,
                    "layers": [{"block": "minecraft:air", "height": 1}],
                    "structure_overrides": [],
                },
            },
        }, indent=2) + "\n")

    # ------------------------------------------------------------ functions
    def _load(self) -> str:
        return "\n".join([
            f"# {self.spec.name}: generated by dungeon-generator (seed {self.layout.seed})",
            f"scoreboard objectives add {self.ns}.state dummy",
            f"scoreboard objectives add {self.ns}.leave trigger",
            f"scoreboard objectives add {self.ns}.enter trigger",
            f"scoreboard players add {self.state_holder} {self.ns}.state 0",
            f"schedule function {self.ns}:tick 10t replace",
            f'tellraw @a[tag={self.ns}_admin] {text_json(f"[{self.ns}] loaded — run /function {self.ns}:build once; players use /trigger {self.ns}.enter", "gray")}',
            "",
        ])

    def exit_pedestal(self):
        return exit_pedestal(self.layout, self.rooms["entrance"])

    def _exit(self) -> str:
        ped = self.exit_pedestal()
        if ped is None:
            return "# no room for an exit pedestal in the entrance\n"
        return "\n".join(["# EXIT pedestal at the start: button returns the party to the portal room"] + exit_pedestal_lines(self.ns, self.dim, ped)) + "\n"

    def _tick(self) -> str:
        boss = self.rooms["boss"]
        lines = ["# state machine, runs every 10 ticks", f"schedule function {self.ns}:tick 10t replace"]
        if self.exit_pedestal() is not None:
            lines.append(exit_poll_line(self.ns, self.dim, self.exit_pedestal()))
        # leave trigger
        lines += [
            f"scoreboard players enable @a {self.ns}.leave",
            f"scoreboard players enable @a {self.ns}.enter",
            f"execute as @a[scores={{{self.ns}.leave=1..}}] run function {self.ns}:leave",
            f"execute as @a[scores={{{self.ns}.enter=1..}}] run function {self.ns}:enter",
            # anyone who ends up in the arena by other means (death, admin tp) is still
            # kept honest: nobody in the arena stays in survival/creative
            f"execute as @a[gamemode=!adventure,gamemode=!spectator,tag=!{self.ns}_admin] at @s if dimension {self.dim} run gamemode adventure @s",
        ]
        # room entry (first time per player)
        for rl in self.spec.rooms:
            room = self.rooms[rl.room]
            sel = self._room_sel(room, f",tag=!{self.ns}_seen_{rl.room}")
            lines.append(f"execute in {self.dim} as {sel} at @s run function {self.ns}:room/{rl.room}")
        # boss
        lines += [
            f"execute as @a[tag={self.ns}_inside] at @s unless dimension {self.dim} run function {self.ns}:leave",
            f"execute if score {self.state_holder} {self.ns}.state matches 0 in {self.dim} if entity {self._room_sel(boss)} run function {self.ns}:boss/summon",
            f"execute if score {self.state_holder} {self.ns}.state matches 1 in {self.dim} unless entity @e[tag={self.ns}_boss] run function {self.ns}:boss/defeated",
        ]
        return "\n".join(lines) + "\n"

    def _build(self) -> str:
        b = self.layout.bounds()
        return "\n".join([
            "# Step 1: force-load the footprint, then place two seconds later once chunks exist",
            self._in(f"forceload add {b.min.x} {b.min.z} {b.max.x} {b.max.z}"),
            f"schedule function {self.ns}:build/place 2s",
            f'tellraw @a {text_json("Building the dungeon...", "gray")}',
            "",
        ])

    def _place(self) -> str:
        lay = self.layout
        lines = [f"# Step 2: {len(lay.solver.placements)} vanilla templates at solver coordinates"]
        for p in lay.solver.placements:
            lines.append(self._in(f"place template {p.piece.id} {p.origin.cmd()} {p.rotation.cmd}"))
        lines.append("# jigsaw blocks -> final states")
        for p in lay.solver.placements:
            for w in p.world_jigsaws():
                lines.append(self._in(f"setblock {w.pos.cmd()} {w.jigsaw.final_state}"))
            for sb in p.piece.structure_blocks:
                lines.append(self._in(f"setblock {p.world_pos(sb).cmd()} minecraft:air"))
        lines.append(f"function {self.ns}:build/exit")
        lines.append("# dressing")
        for rl in self.spec.rooms:
            room = self.rooms[rl.room]
            if rl.sign:
                pos = self._sign_pos(room)
                msgs = ",".join(snbt_str(line) for line in (rl.sign + ["", "", "", ""])[:4])
                lines.append(self._in(
                    f"execute if block {pos.cmd()} minecraft:air run setblock {pos.cmd()} "
                    f"minecraft:oak_sign{{front_text:{{messages:[{msgs}]}},is_waxed:1b}}"
                ))
        b = lay.bounds()
        lines += [
            "# done — release only this dungeon's chunks (another build may be in flight)",
            self._in(f"forceload remove {b.min.x} {b.min.z} {b.max.x} {b.max.z}"),
            f"scoreboard players set {self.state_holder} {self.ns}.state 0",
            f"scoreboard players set #built {self.ns}.state 1",
            f'tellraw @a {text_json(f"{self.spec.name} is built. Players: /trigger {self.ns}.enter to go in, /trigger {self.ns}.leave to get out.", "green")}',
            "",
        ]
        return "\n".join(lines)

    def _sign_pos(self, room: Room) -> Vec3:
        side = Direction(room.inward.vec)  # perpendicular: rotate inward 90° about Y
        perp = Vec3(-side.vec.z, 0, side.vec.x)
        return room.entry_floor + room.inward.vec.scale(1) + perp.scale(2) + Vec3(0, 1, 0)

    def _clear(self) -> str:
        b = self.layout.bounds()
        s = b.size
        slab = max(1, FILL_LIMIT // (s.x * s.z))
        lines = ["# wipe the footprint (for a rebuild)", self._in(f"forceload add {b.min.x} {b.min.z} {b.max.x} {b.max.z}")]
        y = b.min.y
        while y <= b.max.y:
            y2 = min(b.max.y, y + slab - 1)
            lines.append(self._in(f"fill {b.min.x} {y} {b.min.z} {b.max.x} {y2} {b.max.z} minecraft:air"))
            y = y2 + 1
        lines += [self._in(f"kill @e[type=!player,x={b.min.x},y={b.min.y},z={b.min.z},dx={s.x},dy={s.y},dz={s.z}]"), ""]
        return "\n".join(lines)

    def _enter(self) -> str:
        ent = self.rooms["entrance"]
        book = self.spec.lore_book
        pages = ",".join(snbt_str(p) for p in book.pages)
        return "\n".join([
            f"# run as a player: /trigger {self.ns}.enter, or /execute as <player> run function {self.ns}:enter",
            f"scoreboard players reset @s {self.ns}.enter",
            *[f"tag @s remove {self.ns}_seen_{rl.room}" for rl in self.spec.rooms],
            f"execute unless entity @s[tag={self.ns}_inside] run function {self.ns}:spawn/save_uuid with entity @s",
            f"tag @s add {self.ns}_inside",
            f"execute in {self.dim} run tp @s {self._stand(ent.entry_floor)}",
            f"execute in {self.dim} run spawnpoint @s {ent.entry_floor.x} {ent.entry_floor.y + 1} {ent.entry_floor.z}",
            "gamemode adventure @s",
            "title @s times 10 60 20",
            f"title @s subtitle {text_json(self.spec.tagline, 'gray')}",
            f"title @s title {text_json(self.spec.name, 'gold', True)}",
            f"give @s minecraft:written_book[written_book_content={{title:{snbt_str(book.title)},author:{snbt_str(book.author)},pages:[{pages}]}}]",
            f'tellraw @s {text_json("Type /trigger " + self.ns + ".leave to leave at any time.", "gray")}',
            "",
        ])

    def _leave(self) -> str:
        return "\n".join([
            f"scoreboard players reset @s {self.ns}.leave",
            f"execute if entity @s[tag={self.ns}_inside] run function {self.ns}:spawn/restore_uuid with entity @s",
            f"tag @s remove {self.ns}_inside",
            f"execute in {self.hub_dim} run tp @s {self._stand(self.hub_pos)}",
            "execute if entity @s[gamemode=adventure] run gamemode survival @s",
            f'tellraw @s {text_json("You are still part of this run while it lasts: stand on its circle to go back in.", "gray")}',
            "",
        ])

    def _rejoin(self) -> str:
        """A member coming back into a run that is still going: no reset, no book."""
        ent = self.rooms["entrance"]
        return "\n".join([
            f"execute unless entity @s[tag={self.ns}_inside] run function {self.ns}:spawn/save_uuid with entity @s",
            f"tag @s add {self.ns}_inside",
            f"execute in {self.dim} run tp @s {self._stand(ent.entry_floor)}",
            f"execute in {self.dim} run spawnpoint @s {ent.entry_floor.x} {ent.entry_floor.y + 1} {ent.entry_floor.z}",
            "gamemode adventure @s",
            f'title @s actionbar {text_json("Back in " + self.spec.name, "aqua")}',
            "",
        ])

    def _reset(self) -> str:
        return "\n".join([
            f"execute in {self.dim} run kill @e[tag={self.ns}_boss]",
            f"execute in {self.dim} run kill @e[tag={self.ns}_mob]",
            f"scoreboard players set {self.state_holder} {self.ns}.state 0",
            *[f"tag @a remove {self.ns}_seen_{rl.room}" for rl in self.spec.rooms],
            f'tellraw @a {text_json("Dungeon reset.", "gray")}',
            "",
        ])

    def _summon(self, entity: str, pos: Vec3, tags: list[str], name: str | None = None, extra: str = "") -> str:
        nbt = [f"Tags:[{','.join(snbt_str(t) for t in tags)}]", "PersistenceRequired:1b"]
        if name:
            nbt.append(f"CustomName:{snbt_str(name)}")
            nbt.append("CustomNameVisible:1b")
        if extra:
            nbt.append(extra)
        return self._in(f"summon {entity} {pos.x + 0.5} {pos.y + 1} {pos.z + 0.5} {{{','.join(nbt)}}}")

    def _room_enter(self, rl) -> str:
        room = self.rooms[rl.room]
        lines = [
            f"tag @s add {self.ns}_seen_{rl.room}",
            "title @s times 10 50 20",
            f"title @s subtitle {text_json(rl.subtitle, 'gray')}",
            f"title @s title {text_json(rl.title, 'yellow')}",
        ]
        if rl.ambush:
            lines.append("playsound minecraft:block.trial_spawner.spawn_mob hostile @s")
        for i, g in enumerate(rl.ambush):
            for k in range(g.count):
                pos = self._spawn_point(room, 4 + i) + Vec3(0, 0, 0)
                offset = Vec3((k % 3) - 1, 0, (k // 3) - 1)
                perp = Vec3(-room.inward.vec.z, 0, room.inward.vec.x)
                p2 = pos + perp.scale(offset.x * 2) + room.inward.vec.scale(offset.z * 2)
                lines.append(self._summon(g.entity, p2, [f"{self.ns}_mob"], g.name))
        return "\n".join(lines) + "\n"

    def _boss_summon(self) -> str:
        boss = self.rooms["boss"]
        b = self.spec.boss
        pos = self._spawn_point(boss, 6)
        h = float(b.health)
        parts = [
            f"attributes:[{{id:\"minecraft:max_health\",base:{h}}}]",
            f"Health:{h}f",
        ]
        if b.glowing:
            parts.append("Glowing:1b")
        if b.equipment:
            eq = ",".join(f"{e.slot}:{{id:{snbt_str(e.item)},count:1}}" for e in b.equipment)
            parts.append(f"equipment:{{{eq}}}")
            parts.append("drop_chances:{" + ",".join(f"{e.slot}:0.0f" for e in b.equipment) + "}")
        if b.effects:
            fx = ",".join(f"{{id:{snbt_str(e.effect)},amplifier:{e.amplifier}b,duration:-1,show_particles:0b}}" for e in b.effects)
            parts.append(f"active_effects:[{fx}]")
        lines = [
            f"scoreboard players set {self.state_holder} {self.ns}.state 1",
            self._summon(b.entity, pos, [f"{self.ns}_boss"], b.name, ",".join(parts)),
        ]
        for i, g in enumerate(b.minions):
            for k in range(g.count):
                perp = Vec3(-boss.inward.vec.z, 0, boss.inward.vec.x)
                p2 = pos + perp.scale((k - 1) * 2) + boss.inward.vec.scale(2 + i * 2)
                lines.append(self._summon(g.entity, p2, [f"{self.ns}_mob"], g.name))
        sel = self._room_sel(boss)
        lines += [
            f"execute in {self.dim} run title {sel} times 10 70 20",
            f"execute in {self.dim} run title {sel} subtitle {text_json(b.intro, 'red')}",
            f"execute in {self.dim} run title {sel} title {text_json(b.name, 'dark_red', True)}",
            f"execute in {self.dim} run playsound minecraft:entity.wither.spawn hostile {sel}",
            "",
        ]
        return "\n".join(lines)

    def _boss_defeated(self) -> str:
        boss = self.rooms["boss"]
        r = self.spec.reward
        pos = self._spawn_point(boss, 6)
        sel = "@a[tag=" + f"{self.ns}_seen_boss]"
        return "\n".join([
            f"scoreboard players set {self.state_holder} {self.ns}.state 2",
            f"execute in {self.dim} run kill @e[tag={self.ns}_mob]",
            f"execute in {self.dim} run title {sel} times 10 80 20",
            f"execute in {self.dim} run title {sel} subtitle {text_json(self.spec.victory_subtitle, 'gold')}",
            f"execute in {self.dim} run title {sel} title {text_json(self.spec.victory_title, 'green', True)}",
            f"execute in {self.dim} run playsound minecraft:ui.toast.challenge_complete master {sel}",
            f"execute in {self.dim} run setblock {pos.cmd()} minecraft:chest{{LootTable:\"{self.ns}:boss_reward\"}}",
            f'execute in {self.dim} run tellraw {sel} {text_json(r.message, "gold")}',
            f'execute in {self.dim} run tellraw {sel} {text_json("A reward chest has appeared where the boss fell. /trigger " + self.ns + ".leave when you are done.", "gray")}',
            "",
        ])

    def _loot_table(self) -> str:
        r = self.spec.reward
        entries = []
        for e in r.loot:
            entry: dict = {"type": "minecraft:item", "name": e.item, "weight": e.weight, "functions": []}
            if e.min != e.max or e.min > 1:
                entry["functions"].append({"function": "minecraft:set_count", "count": {"min": e.min, "max": e.max}})
            if e.name:
                entry["functions"].append({"function": "minecraft:set_name", "name": {"text": e.name, "italic": False}, "target": "custom_name"})
            if not entry["functions"]:
                del entry["functions"]
            entries.append(entry)
        return json.dumps({
            "type": "minecraft:chest",
            "pools": [{"rolls": r.rolls, "entries": entries}],
        }, indent=2) + "\n"
