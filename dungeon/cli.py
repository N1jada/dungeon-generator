"""Command line entry point: ``uv run dungeon <command>``."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .catalogue import default_catalogue
from .geometry import Vec3
from .layout import build_phase1
from .logic import generate_spec, geometry_brief, load_spec, spec_schema
from .validate import validate_layout, validate_spec


def _layout(args, spec=None):
    contents = spec.spawner_contents if spec else None
    origin = Vec3(*args.origin) if getattr(args, "origin", None) else Vec3(0, 40, 0)
    return build_phase1(default_catalogue(), seed=args.seed, origin=origin, spawner_contents=contents)


def cmd_catalogue(args) -> int:
    cat = default_catalogue()
    rows = cat.summary(cat.all_vanilla_pieces())
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"{r['id'].replace('minecraft:trial_chambers/', ''):45s} {r['size']!s:14s} jig={r['jigsaws']:3d} be={','.join(b.replace('minecraft:', '') for b in r['block_entities'])}")
        print(f"{len(rows)} pieces")
    return 0


def cmd_layout(args) -> int:
    lay = _layout(args)
    print(f"seed {lay.seed}: {len(lay.solver.placements)} pieces, bounds {lay.bounds().min.cmd()} .. {lay.bounds().max.cmd()}, {lay.solver.solid_blocks()} solid blocks")
    for nid in lay.order:
        p = lay.nodes[nid]
        print(f"  {nid:9s} {p.short_id:40s} @ {p.origin.cmd():16s} {p.rotation.value}")
    for r in lay.rooms:
        print(f"  ROOM {r.id:9s} door {r.door.cmd():14s} entry floor {r.entry_floor.cmd()}")
    rep = validate_layout(lay)
    print(rep.render())
    return 0 if rep.ok else 1


def cmd_brief(args) -> int:
    print(json.dumps(geometry_brief(_layout(args)), indent=2))
    return 0


def cmd_schema(args) -> int:
    print(json.dumps(spec_schema(), indent=2))
    return 0


def cmd_spec(args) -> int:
    narrative = Path(args.narrative).read_text() if Path(args.narrative).exists() else args.narrative
    spec = generate_spec(narrative, _layout(args), model=args.model)
    Path(args.out).write_text(spec.model_dump_json(indent=2) + "\n")
    print(f"wrote {args.out}: {spec.name}")
    return 0


def cmd_generate(args) -> int:
    from .datapack import DatapackBuilder

    spec = load_spec(args.spec)
    lay = _layout(args, spec)
    rep = validate_layout(lay)
    rep2 = validate_spec(spec, lay)
    print(rep.render())
    print(rep2.render())
    if not (rep.ok and rep2.ok) and not args.force:
        print("refusing to generate: gates failed (use --force to override)")
        return 1
    hub = (args.hub_dimension, Vec3(*args.hub))
    dp = DatapackBuilder(lay, spec, namespace=args.namespace, hub=hub).build_files()
    dest = Path(args.out)
    if dest.exists():
        shutil.rmtree(dest)
    files = dp.write(dest)
    print(f"wrote {len(files)} files to {dest}")
    print(f"next: copy to <server>/world/datapacks/{dest.name}, restart, then /function {args.namespace}:build")
    return 0


def cmd_local_test(args) -> int:
    from .local_test import run_local_test

    return run_local_test(args)


def cmd_deploy(args) -> int:
    from .deploy import deploy

    return deploy(args)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dungeon", description="Narrative-driven Minecraft dungeon generator")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--origin", type=int, nargs=3, metavar=("X", "Y", "Z"), help="world origin of the entrance piece (default 0 40 0)")

    p = sub.add_parser("catalogue", help="list the vendored vanilla pieces"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_catalogue)
    p = sub.add_parser("layout", help="resolve the room layout and run the geometry gates"); common(p); p.set_defaults(fn=cmd_layout)
    p = sub.add_parser("brief", help="print the geometry brief the logic layer sees"); common(p); p.set_defaults(fn=cmd_brief)
    p = sub.add_parser("schema", help="print the logic spec JSON schema"); p.set_defaults(fn=cmd_schema)
    p = sub.add_parser("spec", help="ask Claude for a logic spec from a narrative"); common(p)
    p.add_argument("narrative", help="narrative text or a path to a text file"); p.add_argument("--out", default="specs/generated.json"); p.add_argument("--model", default=None); p.set_defaults(fn=cmd_spec)
    p = sub.add_parser("generate", help="compile layout + spec into a datapack"); common(p)
    p.add_argument("--spec", default="specs/example.json"); p.add_argument("--out", default="build/dungeon")
    p.add_argument("--namespace", default="dungeon"); p.add_argument("--hub-dimension", default="minecraft:overworld")
    p.add_argument("--hub", type=int, nargs=3, default=[0, 80, 0], metavar=("X", "Y", "Z"), help="where /trigger dungeon.leave sends players")
    p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("local-test", help="build on the local Paper server and verify every placement"); common(p)
    p.add_argument("--spec", default="specs/example.json"); p.add_argument("--server-dir", default=".local-server")
    p.add_argument("--rcon-port", type=int, default=25598); p.add_argument("--rcon-password", default="localtest"); p.set_defaults(fn=cmd_local_test)
    p = sub.add_parser("deploy", help="push a generated datapack to the Pterodactyl server through the MCP server")
    p.add_argument("--datapack", default="build/dungeon"); p.add_argument("--mcp-dir", default="../pterodactyl-mcp")
    p.add_argument("--remote-dir", default="world/datapacks/dungeon"); p.add_argument("--namespace", default="dungeon")
    p.add_argument("--dry-run", action="store_true", help="preflight only: dry-run every write against the guard, write nothing")
    p.add_argument("--skip-backup", action="store_true", help="do not take the pre-deploy backup (needed when the panel's one slot is already used)")
    p.add_argument("--yes", action="store_true", help="approve the upload (and restart, if --restart) without prompting")
    p.add_argument("--restart", action="store_true", help="restart the server after uploading (needed once so the dimension registers; disconnects players)")
    p.add_argument("--reload", action="store_true", help="/reload after uploading (enough for function-only changes)")
    p.add_argument("--build", action="store_true", help="run /function <ns>:build after the restart/reload")
    p.set_defaults(fn=cmd_deploy)

    args = ap.parse_args(argv)
    return args.fn(args)


def main(argv: list[str] | None = None) -> int:
    from .vanilla import VanillaDataMissing

    try:
        return _main(argv)
    except VanillaDataMissing as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
