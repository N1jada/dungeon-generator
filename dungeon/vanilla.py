"""Where vendored game data lives, and which namespaces are allowed to resolve.

``minecraft:`` comes from the 26.2 server jar under ``vendor/vanilla-26.2/``.  Any
other namespace must come from a Tier 2 pack vendored under ``vendor/<slug>/`` *with*
a ``SOURCE.json`` sidecar whose licence is on the allowlist — that is the licence gate,
enforced at the point of resolution, so an unlicensed pack cannot be
placed even if somebody drops its files into ``vendor/``.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .licences import verdict

GAME_VERSION = "26.2"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_ROOT = PROJECT_ROOT / "vendor"
VENDOR = VENDOR_ROOT / f"vanilla-{GAME_VERSION}"
DATA = VENDOR / "data" / "minecraft"
STRUCTURES = DATA / "structure"
TEMPLATE_POOLS = DATA / "worldgen" / "template_pool"
WORLDGEN_STRUCTURES = DATA / "worldgen" / "structure"
LOOT_TABLES = DATA / "loot_table"
TRIAL_SPAWNER_CONFIGS = DATA / "trial_spawner"


class VanillaDataMissing(FileNotFoundError):
    """``vendor/vanilla-26.2/`` has not been extracted yet (see scripts/fetch_vanilla.py)."""


def _vendor_json(name: str) -> dict:
    path = VENDOR / name
    if not path.exists():
        raise VanillaDataMissing(
            f"Minecraft game data not found: {path.relative_to(PROJECT_ROOT)}. "
            "Run `uv run python scripts/fetch_vanilla.py` once to extract it from Mojang's server jar."
        )
    return json.loads(path.read_text())


@lru_cache(maxsize=1)
def version() -> dict:
    return _vendor_json("version.json")


@lru_cache(maxsize=1)
def registries() -> dict:
    return _vendor_json("registries.json")


def registry_keys(name: str) -> set[str]:
    """All resource keys in a registry, e.g. registry_keys('minecraft:entity_type')."""
    return set(registries()[name]["entries"].keys())


def data_pack_format() -> tuple[int, int]:
    pv = version()["pack_version"]
    return pv["data_major"], pv["data_minor"]


# --------------------------------------------------------------------------
# Namespace registry: namespace -> the vendor directory containing ``data/<ns>/``
# --------------------------------------------------------------------------

_EXTRA: dict[str, Path] = {}


def register_namespace(ns: str, vendor_dir: Path) -> None:
    """Register a namespace explicitly (tests, or a pack vetted by hand)."""
    _EXTRA[ns] = Path(vendor_dir)
    namespaces.cache_clear()


def clear_registered_namespaces() -> None:
    _EXTRA.clear()
    namespaces.cache_clear()


@lru_cache(maxsize=1)
def namespaces() -> dict[str, Path]:
    """Every resolvable namespace mapped to the directory that holds its ``data/``.

    Discovered by scanning ``vendor/*/SOURCE.json``.  A vendor directory with no
    sidecar, or one whose licence fails the gate, is skipped — deliberately, so the
    failure mode is "structure not found" rather than "unlicensed content shipped".
    """
    out: dict[str, Path] = {"minecraft": VENDOR}
    if VENDOR_ROOT.exists():
        for sidecar in sorted(VENDOR_ROOT.glob("*/SOURCE.json")):
            try:
                meta = json.loads(sidecar.read_text())
            except json.JSONDecodeError:
                continue
            if not verdict(meta.get("licence")).allowed:
                continue
            for ns in meta.get("namespaces", []):
                if ns != "minecraft" and (sidecar.parent / "data" / ns).is_dir():
                    out[ns] = sidecar.parent
    out.update(_EXTRA)
    return out


def namespace_root(res_or_ns: str) -> Path:
    """The vendor directory backing a namespace (or the namespace of a resource id)."""
    ns = split_id(res_or_ns)[0] if ":" in res_or_ns else res_or_ns
    root = namespaces().get(ns)
    if root is None:
        known = ", ".join(sorted(namespaces()))
        raise FileNotFoundError(
            f"namespace {ns!r} is not vendored (or its licence sidecar failed the gate); known: {known}"
        )
    return root


def split_id(res: str) -> tuple[str, str]:
    """'minecraft:foo/bar' -> ('minecraft', 'foo/bar'); bare ids default to minecraft."""
    if ":" in res:
        ns, path = res.split(":", 1)
        return ns, path
    return "minecraft", res


def structure_nbt_path(res: str) -> Path:
    ns, path = split_id(res)
    if ns == "minecraft":
        return STRUCTURES / f"{path}.nbt"
    return namespace_root(ns) / "data" / ns / "structure" / f"{path}.nbt"


def template_pool_path(res: str) -> Path:
    ns, path = split_id(res)
    if ns == "minecraft":
        return TEMPLATE_POOLS / f"{path}.json"
    return namespace_root(ns) / "data" / ns / "worldgen" / "template_pool" / f"{path}.json"


def worldgen_structure_path(res: str) -> Path:
    ns, path = split_id(res)
    if ns == "minecraft":
        return WORLDGEN_STRUCTURES / f"{path}.json"
    return namespace_root(ns) / "data" / ns / "worldgen" / "structure" / f"{path}.json"


def structures_in(ns: str, prefix: str = "") -> list[str]:
    """Every structure id in a vendored namespace, sorted."""
    base = namespace_root(ns) / "data" / ns / "structure"
    return sorted(
        f"{ns}:{p.relative_to(base).with_suffix('').as_posix()}"
        for p in (base / prefix).rglob("*.nbt")
    ) if (base / prefix).exists() else []
