# Contributing

Thanks for your interest! Bug reports, fixes, docs and example dungeons are all welcome.

## Setup

```bash
git clone https://github.com/N1jada/dungeon-generator.git
cd dungeon-generator
uv sync
uv run pytest
```

A fresh clone runs clean: tests that need Mojang's game data skip and tell you how to
fetch it. To run everything:

```bash
uv run python scripts/fetch_vanilla.py     # needs JDK 25
uv run pytest
```

## Ground rules

- **Never commit game content.** Mojang's files stay in `vendor/`, which is git-ignored.
- **The AI never invents geometry.** Anything a model produces goes through the logic spec
  schema; positions come from structure templates and the solver.
- **New behaviour has tests**, and anything that can produce a broken dungeon gets a gate
  in `validate.py`.
- **Tests never touch a real server.** In-game verification uses a throwaway local Paper
  server over RCON (`dungeon local-test`).
- Keep pull requests focused, and say how you tested in-game if you did.

## Sharing a dungeon

Wrote a spec you're proud of? Open a pull request adding it to `specs/`. Specs are plain
JSON, checked by `uv run dungeon generate` against `uv run dungeon schema`.
