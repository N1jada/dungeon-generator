# dungeon-generator

[![CI](https://github.com/N1jada/dungeon-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/N1jada/dungeon-generator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![Minecraft 26.2 (Paper)](https://img.shields.io/badge/minecraft-26.2%20Paper-green)

**Write a story, get a playable dungeon on your Minecraft server.**

Describe a dungeon in a sentence or two. `dungeon-generator` turns it into a vanilla
datapack: a sealed arena dimension, three trial-chamber rooms leading to a boss, ambushes,
signs, a lore book, a boss fight and a loot chest, plus the triggers players use to get in
and out. No plugins or mods needed on the server.

The one rule that shapes everything: **the AI never invents geometry.** Rooms are Mojang's
own jigsaw-native trial-chamber pieces, joined by a deterministic solver and checked by
gates. Claude only decides what *happens* in them (who ambushes you, the boss, the loot,
the words on the signs), and only through a validated schema. You can also skip the AI and
write that part by hand.

## How it works

| Stage | Module | Who |
|---|---|---|
| Piece catalogue from vanilla `.nbt` (jigsaw connectors read straight off the jigsaw blocks) | `catalogue.py` | code |
| Room chain → exact placements, then vanilla-style expansion of corridors, spawners, vaults and caps from a seed | `layout.py`, `solver.py` | code |
| Logic spec (rooms, ambushes, boss, reward, lore) from your story, against a brief of the real geometry | `logic.py` | **Claude**, or hand-written JSON |
| Gates: no overlaps, every room reachable, boss furthest from the door, block budget, every id exists | `validate.py` | code |
| Datapack: arena dimension, build function, state machine, loot table | `datapack.py` | code |
| Optional: build on a local test server and verify it block by block | `local_test.py` | code |
| Optional: deploy to a hosted server through [pterodactyl-mcp](https://github.com/N1jada/pterodactyl-mcp) | `deploy.py` | code |

The solver's rotation and connection maths was checked against the game: every jigsaw
position and orientation matched what `/place template` produced on a Paper 26.2 server,
and a full build lands all 499 jigsaw replacements, 10 trial spawners, 23 vaults and 3
chests exactly where the model says.

## Requirements

| For | You need |
|---|---|
| Everything | Python 3.12+ and [uv](https://docs.astral.sh/uv/) |
| Generating | Minecraft 26.2 game data, extracted once by `scripts/fetch_vanilla.py` (needs JDK 25) |
| Writing specs from a story | An [Anthropic API key](https://console.anthropic.com/) (optional: you can hand-write specs) |
| Playing | A [Paper](https://papermc.io) 26.2 server |
| Local verification | A local Paper 26.2 server with RCON enabled (optional) |
| Deploying to a host | [pterodactyl-mcp](https://github.com/N1jada/pterodactyl-mcp) (optional) |

## Quick start

```bash
git clone https://github.com/N1jada/dungeon-generator.git
cd dungeon-generator
uv sync

# once: extract Mojang's structure pieces and registries from the official server jar
uv run python scripts/fetch_vanilla.py

uv run dungeon layout                              # solve the rooms and run the geometry gates
uv run dungeon generate --spec specs/example.json  # build/dungeon is now a datapack
```

Copy `build/dungeon` to `<server>/world/datapacks/dungeon`, restart once (the
`dungeon:arena` dimension only registers at startup), then in game:

```
/function dungeon:build                         # places the rooms (about 190 templates)
/execute as <player> run function dungeon:enter
/trigger dungeon.leave                          # players use this to get out
/function dungeon:reset                         # clear mobs and per-player progress
```

Players use `/trigger dungeon.enter` and `/trigger dungeon.leave`. Inside the arena
everyone is in adventure mode (players tagged `dungeon_admin` are exempt). Use `--hub X Y Z`
on `generate` to choose where leaving sends people, and `--seed` for a different
arrangement of the same rooms.

## Write your own dungeon

### With Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...
uv run dungeon spec "A flooded copper vault whose warden never left." --out specs/mine.json
uv run dungeon generate --spec specs/mine.json
```

You can pass a text file instead of a sentence. Claude sees a brief of the real rooms
(`uv run dungeon brief`) and must answer with JSON matching `uv run dungeon schema`. Every
item, entity and effect id is checked against the game's registry, so it can't invent
things that don't exist. Set `DUNGEON_MODEL` to use a different Claude model.

### By hand

A spec is a small JSON file. [`specs/example.json`](specs/example.json) is a complete one:

- **Name, tagline and a lore book** handed to players on entry
- **Rooms** (`room1`, `room2`, `boss`): a title and subtitle on first entry, up to four
  sign lines, and an ambush of up to three mob groups
- **What the trial spawners spawn**, per spawner type
- **The boss**: mob, name, health, equipment, effects, minions and an intro line
- **The reward**: a loot table and a victory message and title

`uv run dungeon generate` validates it before writing anything and tells you exactly
what's wrong.

## Verify on a local server

With a local Paper 26.2 server in `.local-server/` (git-ignored) and RCON enabled:

```bash
uv run dungeon local-test --spec specs/mine.json --rcon-port 25575 --rcon-password <yours>
```

It regenerates the datapack into that world, reloads, builds, and checks every jigsaw
replacement, spawner, vault, chest, room entry point and the boss and reward functions
against the model.

## Deploy to a hosted server

If your server runs on a Pterodactyl panel, `dungeon deploy` uploads the datapack through
[pterodactyl-mcp](https://github.com/N1jada/pterodactyl-mcp), subject to all of its
guardrails. Clone and build it next to this repo (`../pterodactyl-mcp`, or pass
`--mcp-dir`), then copy [`deploy.env.example`](deploy.env.example) to
`../pterodactyl-mcp/.env.deploy`. That opens exactly `world/datapacks/dungeon/` for writing
and nothing else.

```bash
uv run dungeon deploy --dry-run            # every write checked against the guard, nothing written
uv run dungeon deploy                      # backup, one approval, upload
uv run dungeon deploy --restart --build    # first time: restart (new dimension), then build
uv run dungeon deploy --reload --build     # later changes: no restart needed
```

## Commands

Run `uv run dungeon <command> --help` for options.

| Command | What it does |
|---|---|
| `catalogue` | List the vanilla trial-chamber pieces and their connectors |
| `layout` | Solve the room layout and run the geometry gates |
| `brief` | Print the geometry brief Claude is shown |
| `schema` | Print the logic spec JSON schema |
| `spec` | Ask Claude for a logic spec from a story |
| `generate` | Validate a spec and compile layout + spec into a datapack |
| `local-test` | Build on a local Paper server and verify every placement |
| `deploy` | Upload a generated datapack to a Pterodactyl server through pterodactyl-mcp |

## Project layout

```
dungeon/          one module per stage (see "How it works"), plus cli.py
specs/            example.json, a complete hand-written spec
scripts/          fetch_vanilla.py
vendor/           Mojang's game data, downloaded, never committed (see vendor/README.md)
tests/            uv run pytest
```

## Development

```bash
uv sync
uv run pytest
```

Tests that need Mojang's game data skip, with the command to fetch it, so a fresh clone
runs clean. Run `scripts/fetch_vanilla.py` to run everything. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Known limitations

- The room chain (two rooms and a boss room) is hand-authored. The solver can attach any
  piece to any connector, but nothing generates the room graph from the story yet.
- No pathfinding gates (jump height, fall damage, lava). Overlap, connectivity and budget
  gates are in place.
- Vaults are per-player and chests loot once; `dungeon:reset` does not re-roll them.
- The copper-bulb oxidation that vanilla applies to trial chambers is skipped.
- Built and tested against Minecraft 26.2 on Paper only.

## Credits and licence

This project's code is [MIT licensed](LICENSE). It contains no Minecraft content: the
structures, loot tables and registries are Mojang's, extracted from the official server
jar on your own machine under the [Minecraft EULA](https://www.minecraft.net/eula). See
[ATTRIBUTION.md](ATTRIBUTION.md).

Not an official Minecraft product. Not approved by or associated with Mojang or Microsoft.
