"""Stage 7 (and the Phase 1 slice of stage 6): the logic layer.

An LLM writes *what happens* in the dungeon — encounters, boss, loot, lore — against
fixed geometry, and it does so only through this schema.  It never emits commands.
Deterministic code (``datapack.py``) compiles the validated spec into functions.

The spec can come from Claude via :func:`generate_spec`, or from a JSON file written
by any other author (a person, another model, a test fixture) via :func:`load_spec`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import vanilla
from .layout import Layout

# Vanilla trial-spawner pieces, by category -> allowed mob variants.
SPAWNER_CHOICES: dict[str, tuple[str, ...]] = {
    "melee": ("zombie", "husk", "spider"),
    "ranged": ("skeleton", "stray", "poison_skeleton"),
    "slow_ranged": ("skeleton", "stray", "poison_skeleton"),
    "small_melee": ("slime", "cave_spider", "silverfish", "baby_zombie"),
}

# Mobs the logic layer may summon directly (ambushes, bosses).  Kept to hostile mobs
# that behave sensibly indoors; the schema validates against the live registry too.
SUMMONABLE: tuple[str, ...] = (
    "minecraft:zombie", "minecraft:husk", "minecraft:drowned", "minecraft:zombie_villager",
    "minecraft:skeleton", "minecraft:stray", "minecraft:bogged", "minecraft:wither_skeleton",
    "minecraft:spider", "minecraft:cave_spider", "minecraft:creeper", "minecraft:witch",
    "minecraft:breeze", "minecraft:blaze", "minecraft:vindicator", "minecraft:evoker",
    "minecraft:pillager", "minecraft:ravager", "minecraft:piglin_brute", "minecraft:zoglin",
    "minecraft:hoglin", "minecraft:enderman", "minecraft:endermite", "minecraft:silverfish",
    "minecraft:slime", "minecraft:magma_cube", "minecraft:phantom", "minecraft:guardian",
    "minecraft:elder_guardian", "minecraft:warden", "minecraft:iron_golem", "minecraft:wolf",
)

EQUIPMENT_SLOTS = ("mainhand", "offhand", "head", "chest", "legs", "feet")


def _mc(res: str) -> str:
    return res if ":" in res else f"minecraft:{res}"


class Equipment(BaseModel):
    slot: Literal["mainhand", "offhand", "head", "chest", "legs", "feet"]
    item: str = Field(description="Vanilla item id, e.g. minecraft:diamond_sword")

    @field_validator("item")
    @classmethod
    def _item_exists(cls, v: str) -> str:
        v = _mc(v)
        if v not in vanilla.registry_keys("minecraft:item"):
            raise ValueError(f"unknown item {v}")
        return v


class Effect(BaseModel):
    effect: str = Field(description="Vanilla mob effect id, e.g. minecraft:speed")
    amplifier: int = Field(0, ge=0, le=3)

    @field_validator("effect")
    @classmethod
    def _effect_exists(cls, v: str) -> str:
        v = _mc(v)
        if v not in vanilla.registry_keys("minecraft:mob_effect"):
            raise ValueError(f"unknown mob effect {v}")
        return v


class MobGroup(BaseModel):
    entity: str = Field(description="Vanilla hostile mob id from the allowed list")
    count: int = Field(1, ge=1, le=6)
    name: str | None = Field(None, max_length=32, description="Optional display name")

    @field_validator("entity")
    @classmethod
    def _entity_ok(cls, v: str) -> str:
        v = _mc(v)
        if v not in SUMMONABLE:
            raise ValueError(f"{v} is not in the summonable mob list")
        if v not in vanilla.registry_keys("minecraft:entity_type"):
            raise ValueError(f"unknown entity {v}")
        return v


class Boss(BaseModel):
    entity: str = Field(description="Vanilla hostile mob id from the allowed list")
    name: str = Field(min_length=1, max_length=40)
    health: float = Field(80, ge=10, le=600, description="Max health in half-hearts... (20 = a player)")
    equipment: list[Equipment] = Field(default_factory=list, max_length=6)
    effects: list[Effect] = Field(default_factory=list, max_length=4)
    glowing: bool = True
    minions: list[MobGroup] = Field(default_factory=list, max_length=3, description="Summoned alongside the boss")
    intro: str = Field(max_length=120, description="Shown as a subtitle when the boss appears")

    @field_validator("entity")
    @classmethod
    def _entity_ok(cls, v: str) -> str:
        return MobGroup._entity_ok(v)


class RoomLogic(BaseModel):
    room: str = Field(description="Room id from the brief (room1, room2, boss)")
    title: str = Field(min_length=1, max_length=40, description="Title shown on first entry")
    subtitle: str = Field("", max_length=80)
    sign: list[str] = Field(default_factory=list, max_length=4, description="Up to 4 lines of <= 15 characters on a sign at the door")
    ambush: list[MobGroup] = Field(default_factory=list, max_length=3, description="Mobs summoned on first entry")

    @field_validator("sign")
    @classmethod
    def _short_lines(cls, v: list[str]) -> list[str]:
        for line in v:
            if len(line) > 15:
                raise ValueError(f"sign line too long ({len(line)} > 15): {line!r}")
        return v


class LootEntry(BaseModel):
    item: str
    min: int = Field(1, ge=1, le=64)
    max: int = Field(1, ge=1, le=64)
    weight: int = Field(1, ge=1, le=100)
    name: str | None = Field(None, max_length=40, description="Optional custom item name")

    @field_validator("item")
    @classmethod
    def _item_exists(cls, v: str) -> str:
        return Equipment._item_exists(v)


class Reward(BaseModel):
    rolls: int = Field(3, ge=1, le=8)
    loot: list[LootEntry] = Field(min_length=1, max_length=12)
    message: str = Field(max_length=120)


class LoreBook(BaseModel):
    title: str = Field(min_length=1, max_length=32)
    author: str = Field(min_length=1, max_length=32)
    pages: list[str] = Field(min_length=1, max_length=6, description="Each page <= 256 characters")

    @field_validator("pages")
    @classmethod
    def _page_len(cls, v: list[str]) -> list[str]:
        for p in v:
            if len(p) > 256:
                raise ValueError("page longer than 256 characters")
        return v


class LogicSpec(BaseModel):
    """Everything the logic layer decides.  Compiled to a datapack by ``datapack.py``."""
    name: str = Field(min_length=1, max_length=40, description="Dungeon name, shown as a title on entry")
    tagline: str = Field("", max_length=80)
    lore_book: LoreBook
    spawner_contents: dict[Literal["melee", "ranged", "slow_ranged", "small_melee"], str] = Field(
        description="Which vanilla mob each trial-spawner category uses; see the brief for choices"
    )
    rooms: list[RoomLogic] = Field(min_length=1)
    boss: Boss
    reward: Reward
    victory_title: str = Field(min_length=1, max_length=40)
    victory_subtitle: str = Field("", max_length=80)

    @field_validator("spawner_contents")
    @classmethod
    def _spawner_choice(cls, v: dict[str, str]) -> dict[str, str]:
        for cat, mob in v.items():
            if mob not in SPAWNER_CHOICES[cat]:
                raise ValueError(f"{cat} spawner cannot be {mob}; choose from {SPAWNER_CHOICES[cat]}")
        return v


# ---------------------------------------------------------------------------
# The geometry brief: what the LLM is told about the fixed layout
# ---------------------------------------------------------------------------

def geometry_brief(layout: Layout) -> dict:
    rooms = []
    for r in layout.rooms:
        if r.kind == "entrance":
            continue
        slots = [s for s in layout.slots if s.room == r.id]
        rooms.append({
            "id": r.id,
            "kind": r.kind,
            "template": r.placement.short_id,
            "size_blocks": list(r.box.size),
            "steps_from_entrance": r.depth,
            "trial_spawners": sorted(s.detail for s in slots if s.kind == "trial_spawner"),
            "vaults": sum(1 for s in slots if s.kind == "vault"),
            "loot_chests": sum(1 for s in slots if s.kind in ("chest", "barrel")),
        })
    return {
        "style": "vanilla trial chamber (tuff, copper, deep underground)",
        "entrance": "a stair shaft down from the surface into an atrium, then a corridor and a crossing",
        "rooms_in_order": rooms,
        "spawner_choices": SPAWNER_CHOICES,
        "summonable_mobs": list(SUMMONABLE),
        "rules": [
            "Room ids must be exactly those listed.",
            "Sign lines are at most 15 characters; four lines max.",
            "Item and effect ids must be vanilla Minecraft ids.",
            "The boss room is the last room; its ambush should be empty (the boss spawns there).",
        ],
    }


SYSTEM_PROMPT = """You design the encounter and narrative layer of a small Minecraft dungeon.
The geometry already exists and cannot change; you decide what lives in it, what it is
called, what the signs and the lore book say, who the boss is, and what the reward is.
Write in the voice of the narrative you are given. Keep every string within the limits
the schema gives you. Prefer vanilla item ids you are certain exist."""


def generate_spec(narrative: str, layout: Layout, model: str | None = None) -> LogicSpec:
    """Ask Claude for a LogicSpec for ``narrative`` against ``layout``."""
    import anthropic

    client = anthropic.Anthropic()
    brief = geometry_brief(layout)
    response = client.messages.parse(
        model=model or os.environ.get("DUNGEON_MODEL", "claude-opus-5"),
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                "NARRATIVE:\n" + narrative.strip() + "\n\n"
                "GEOMETRY BRIEF (JSON):\n" + json.dumps(brief, indent=1)
            ),
        }],
        output_format=LogicSpec,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"model declined: {response.stop_details}")
    spec = response.parsed_output
    if spec is None:
        raise RuntimeError("model returned no parsable spec")
    return spec


def load_spec(path: Path | str) -> LogicSpec:
    return LogicSpec.model_validate_json(Path(path).read_text())


def spec_schema() -> dict:
    return LogicSpec.model_json_schema()
