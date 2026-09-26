# vendor/

Mojang's game data lives here. **It is never committed**; everything in this folder except
this README is git-ignored.

| Folder | What | How to get it |
|---|---|---|
| `vanilla-26.2/` | Structure templates, template pools, loot tables and the registry report from the official Minecraft 26.2 server jar | `uv run python scripts/fetch_vanilla.py` |

Structures from other namespaces can be added as `vendor/<name>/` with a `SOURCE.json`
recording where they came from and their licence. The catalogue only resolves a namespace
whose licence is on the allowlist in `dungeon/licences.py`, so unlicensed pieces can't be
placed by accident.
