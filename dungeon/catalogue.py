"""The piece catalogue: structure templates with their jigsaw connectors.

Built from vanilla ``.nbt`` structure files (jigsaw-native, so the connector metadata
is read straight off the jigsaw blocks — nothing is annotated by hand).
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

import nbtlib

from . import vanilla
from .geometry import Direction, Vec3

JIGSAW_BLOCK = "minecraft:jigsaw"
INTERESTING_BLOCK_ENTITIES = {
    "minecraft:trial_spawner",
    "minecraft:vault",
    "minecraft:chest",
    "minecraft:barrel",
    "minecraft:dispenser",
}


@dataclass(frozen=True)
class Jigsaw:
    """One jigsaw block inside a template, in template-local coordinates."""
    pos: Vec3
    front: Direction
    top: Direction
    name: str
    target: str
    pool: str
    joint: str            # "rollable" | "aligned"
    final_state: str      # block state string, e.g. "minecraft:air" or "minecraft:tuff_bricks[...]"
    selection_priority: int = 0
    placement_priority: int = 0

    @property
    def outbound(self) -> bool:
        """A jigsaw that wants to attach something (has a real pool)."""
        return self.pool not in ("", "minecraft:empty")


@dataclass(frozen=True)
class BlockEntityRef:
    pos: Vec3
    block: str
    nbt: dict


@dataclass
class Piece:
    id: str                      # resource location, e.g. minecraft:trial_chambers/chamber/chamber_1
    size: Vec3
    jigsaws: list[Jigsaw]
    block_entities: list[BlockEntityRef]
    solid_blocks: int            # non-air blocks in the template
    total_blocks: int
    structure_blocks: list[Vec3] = field(default_factory=list)  # vanilla strips these at placement
    solid_positions: frozenset[Vec3] = frozenset()             # template-local non-air blocks

    def is_solid(self, local: Vec3) -> bool:
        return local in self.solid_positions

    @property
    def volume(self) -> int:
        return self.size.x * self.size.y * self.size.z

    def jigsaws_named(self, name: str) -> list[Jigsaw]:
        return [j for j in self.jigsaws if j.name == name]

    def outbound_jigsaws(self) -> list[Jigsaw]:
        return [j for j in self.jigsaws if j.outbound]


def _parse_orientation(value: str) -> tuple[Direction, Direction]:
    front, top = value.split("_", 1)
    return Direction.parse(front), Direction.parse(top)


def _state_string(state) -> str:
    name = str(state["Name"])
    props = state.get("Properties")
    if not props:
        return name
    inner = ",".join(f"{k}={props[k]}" for k in sorted(props.keys()))
    return f"{name}[{inner}]"


def _plain(value):
    """Convert nbtlib tags into plain python data (recursively)."""
    if isinstance(value, nbtlib.Compound):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, nbtlib.List):
        return [_plain(v) for v in value]
    if isinstance(value, (nbtlib.String,)):
        return str(value)
    if isinstance(value, (nbtlib.Byte, nbtlib.Short, nbtlib.Int, nbtlib.Long)):
        return int(value)
    if isinstance(value, (nbtlib.Float, nbtlib.Double)):
        return float(value)
    if isinstance(value, (nbtlib.ByteArray, nbtlib.IntArray, nbtlib.LongArray)):
        return [int(v) for v in value]
    return value


def load_piece(res: str) -> Piece:
    path = vanilla.structure_nbt_path(res)
    root = nbtlib.load(path)
    size = Vec3(*(int(v) for v in root["size"]))
    palette = root["palette"] if "palette" in root else root["palettes"][0]
    palette_names = [str(p["Name"]) for p in palette]
    jigsaws: list[Jigsaw] = []
    block_entities: list[BlockEntityRef] = []
    structure_blocks: list[Vec3] = []
    solid = 0
    solid_positions: set[Vec3] = set()
    for b in root["blocks"]:
        state_idx = int(b["state"])
        name = palette_names[state_idx]
        pos = Vec3(*(int(v) for v in b["pos"]))
        if name != "minecraft:air" and name != "minecraft:cave_air" and name != "minecraft:structure_void":
            solid += 1
            if name != JIGSAW_BLOCK:
                solid_positions.add(pos)
        if name == JIGSAW_BLOCK:
            state = palette[state_idx]
            front, top = _parse_orientation(str(state["Properties"]["orientation"]))
            nbt = b.get("nbt", {})
            jigsaws.append(
                Jigsaw(
                    pos=pos,
                    front=front,
                    top=top,
                    name=str(nbt.get("name", "minecraft:empty")),
                    target=str(nbt.get("target", "minecraft:empty")),
                    pool=str(nbt.get("pool", "minecraft:empty")),
                    joint=str(nbt.get("joint", "rollable")),
                    final_state=str(nbt.get("final_state", "minecraft:air")),
                    selection_priority=int(nbt.get("selection_priority", 0)),
                    placement_priority=int(nbt.get("placement_priority", 0)),
                )
            )
        elif name in INTERESTING_BLOCK_ENTITIES and "nbt" in b:
            block_entities.append(BlockEntityRef(pos=pos, block=name, nbt=_plain(b["nbt"])))
        elif name == "minecraft:structure_block":
            structure_blocks.append(pos)
    return Piece(
        id=res,
        size=size,
        jigsaws=jigsaws,
        block_entities=block_entities,
        solid_blocks=solid,
        total_blocks=len(root["blocks"]),
        structure_blocks=structure_blocks,
        solid_positions=frozenset(solid_positions),
    )


# ---------------------------------------------------------------------------
# Template pools
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PoolElement:
    weight: int
    kind: str                    # "single" | "empty" | other element types (unsupported)
    location: str | None = None  # structure id for single elements
    projection: str = "rigid"


@dataclass(frozen=True)
class TemplatePool:
    id: str
    fallback: str
    elements: tuple[PoolElement, ...]


def load_pool(res: str) -> TemplatePool:
    if res in ("minecraft:empty", ""):
        return TemplatePool(id="minecraft:empty", fallback="minecraft:empty", elements=())
    path = vanilla.template_pool_path(res)
    if not path.exists():
        # e.g. an alias-only pool (trial_chambers/spawner/contents/*) that was not
        # resolved: behave like vanilla with an unknown pool -> nothing placed.
        return TemplatePool(id=res, fallback="minecraft:empty", elements=())
    data = json.loads(path.read_text())
    elements = []
    for e in data["elements"]:
        el = e["element"]
        et = el["element_type"].replace("minecraft:", "")
        if et == "single_pool_element":
            elements.append(PoolElement(int(e["weight"]), "single", el["location"], el.get("projection", "rigid")))
        elif et == "empty_pool_element":
            elements.append(PoolElement(int(e["weight"]), "empty"))
        else:
            elements.append(PoolElement(int(e["weight"]), et, el.get("location")))
    return TemplatePool(id=res, fallback=data.get("fallback", "minecraft:empty"), elements=tuple(elements))


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

class Catalogue:
    """Lazy cache of pieces and pools, keyed by resource location."""

    def __init__(self) -> None:
        self._pieces: dict[str, Piece] = {}
        self._pools: dict[str, TemplatePool] = {}

    def piece(self, res: str) -> Piece:
        if res not in self._pieces:
            self._pieces[res] = load_piece(res)
        return self._pieces[res]

    def pool(self, res: str) -> TemplatePool:
        if res not in self._pools:
            self._pools[res] = load_pool(res)
        return self._pools[res]

    def all_vanilla_pieces(self, prefix: str = "trial_chambers/") -> list[Piece]:
        base = vanilla.STRUCTURES
        out = []
        for path in sorted((base / prefix).rglob("*.nbt")):
            rel = path.relative_to(base).with_suffix("").as_posix()
            out.append(self.piece(f"minecraft:{rel}"))
        return out

    def summary(self, pieces: Iterable[Piece]) -> list[dict]:
        rows = []
        for p in pieces:
            rows.append({
                "id": p.id,
                "size": list(p.size),
                "solid_blocks": p.solid_blocks,
                "jigsaws": len(p.jigsaws),
                "inbound_names": sorted({j.name for j in p.jigsaws if j.name != "minecraft:empty"}),
                "outbound_targets": sorted({j.target for j in p.jigsaws if j.outbound}),
                "block_entities": sorted({b.block for b in p.block_entities}),
            })
        return rows


@lru_cache(maxsize=1)
def default_catalogue() -> Catalogue:
    return Catalogue()


# ---------------------------------------------------------------------------
# Pool aliases (structure-level, resolved once per generated structure)
# ---------------------------------------------------------------------------

def load_structure(res: str) -> dict:
    return json.loads(vanilla.worldgen_structure_path(res).read_text())


def resolve_pool_aliases(structure: dict, rng) -> dict[str, str]:
    """Resolve a structure's ``pool_aliases`` the way vanilla does at generation time.

    ``direct`` maps alias -> target; ``random`` picks one weighted target for the whole
    structure; ``random_group`` picks one weighted group and applies all its bindings.
    """
    out: dict[str, str] = {}

    def apply(binding: dict) -> None:
        kind = binding["type"].replace("minecraft:", "")
        if kind == "direct":
            out[binding["alias"]] = binding["target"]
        elif kind == "random":
            targets = binding["targets"]
            weights = [t["weight"] for t in targets]
            chosen = rng.choices(targets, weights=weights, k=1)[0]
            out[binding["alias"]] = chosen["data"]
        elif kind == "random_group":
            groups = binding["groups"]
            weights = [g["weight"] for g in groups]
            chosen = rng.choices(groups, weights=weights, k=1)[0]
            for b in chosen["data"]:
                apply(b)
        else:
            raise ValueError(f"unknown pool alias type {kind}")

    for binding in structure.get("pool_aliases", []):
        apply(binding)
    return out
