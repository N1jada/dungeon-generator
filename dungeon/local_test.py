"""Build the generated dungeon on the local Paper server and check the result matches the model."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .catalogue import default_catalogue
from .datapack import DatapackBuilder
from .geometry import Vec3
from .layout import build_phase1
from .logic import load_spec
from .rcon import Rcon


def run_local_test(args) -> int:
    server = Path(args.server_dir)
    spec = load_spec(args.spec)
    lay = build_phase1(default_catalogue(), seed=args.seed, spawner_contents=spec.spawner_contents)
    dp = DatapackBuilder(lay, spec, hub=("minecraft:overworld", Vec3(0, 64, 0))).build_files()
    dest = server / "world" / "datapacks" / "dungeon"
    if dest.exists():
        shutil.rmtree(dest)
    dp.write(dest)
    print(f"datapack -> {dest}")
    D = "execute in dungeon:arena run "
    with Rcon(port=args.rcon_port, password=args.rcon_password) as r:
        log = server / "logs" / "latest.log"
        before = log.read_text().count("\n") if log.exists() else 0
        out = r.command("reload")
        print("reload:", out)
        time.sleep(1)
        new_lines = log.read_text().splitlines()[before:] if log.exists() else []
        load_errors = [l for l in new_lines if "Failed to load function" in l or "Whilst parsing command" in l]
        if load_errors:
            print("FUNCTION LOAD ERRORS:"); [print("  " + l[:200]) for l in load_errors[:6]]
            return 1
        if "dungeon:arena" not in r.command("execute in dungeon:arena run seed") and "Seed" not in r.command("execute in dungeon:arena run seed"):
            print("dimension dungeon:arena is not registered — restart the local server once with the datapack in place")
            return 2
        b = lay.bounds()
        r.command(D + f"forceload add {b.min.x} {b.min.z} {b.max.x} {b.max.z}")
        time.sleep(2)
        r.command("function dungeon:build/clear")
        r.command("function dungeon:build")
        time.sleep(6)
        r.command(D + f"forceload add {b.min.x} {b.min.z} {b.max.x} {b.max.z}")
        time.sleep(2)
        fails = 0
        left = sum(r.command(D + f"execute if block {w.pos.cmd()} minecraft:jigsaw").startswith("Test passed")
                   for p in lay.solver.placements for w in p.world_jigsaws())
        print(f"jigsaw blocks left: {left}"); fails += left > 0
        for kind in ("trial_spawner", "vault", "chest"):
            slots = [s for s in lay.slots if s.kind == kind]
            ok = sum(r.command(D + f"execute if block {s.pos.cmd()} minecraft:{kind}").startswith("Test passed") for s in slots)
            print(f"{kind}: {ok}/{len(slots)}"); fails += ok != len(slots)
        for rm in lay.rooms:
            f = rm.entry_floor
            solid = not r.command(D + f"execute if block {f.cmd()} minecraft:air").startswith("Test passed")
            space = all(r.command(D + f"execute if block {f.x} {f.y + k} {f.z} minecraft:air").startswith("Test passed") for k in (1, 2))
            print(f"{rm.id}: entry floor {f.cmd()} solid={solid} standing space={space}"); fails += not (solid and space)
        r.command("function dungeon:boss/summon")
        boss = r.command(D + "execute if entity @e[tag=dungeon_boss]").startswith("Test passed")
        r.command("function dungeon:boss/defeated")
        chest = r.command(D + f"execute if block {(lay.boss_room.entry_floor + lay.boss_room.inward.vec.scale(6)).cmd()} minecraft:chest").startswith("Test passed")
        r.command("function dungeon:reset")
        print(f"boss summon={boss} reward chest={chest}"); fails += not (boss and chest)
        r.command(D + "forceload remove all")
    print("LOCAL TEST", "PASSED" if not fails else f"FAILED ({fails})")
    return 0 if not fails else 1
