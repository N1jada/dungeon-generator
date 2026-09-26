"""Stage 8: deploy a generated datapack to the Pterodactyl server through the MCP server.

The generator is a *client* of pterodactyl-mcp and gets no exemption from its guards.
With the MCP server's defaults the upload is refused on purpose (datapacks live under the
protected ``world/**`` path, the mutation budget is 20 and auto-backup runs per write).
Deploying therefore uses a *deploy profile*: ``.env.deploy`` beside the MCP server,
loaded on top of ``.env``, which carves out ``world/datapacks/dungeon/**`` via
``PTERODACTYL_UNPROTECTED_PATHS``, raises the budget and turns off per-write backups.
This script takes one explicit backup before it writes anything instead.

Flow: preflight (dry-run every file, stop on any refusal) -> backup -> one human
approval for the batch -> upload (confirming each overwrite with the guard's token)
-> optional ``--restart`` (new dimension) and ``--build``.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

LONG = 900.0  # seconds: backups and restarts can take minutes


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class Mcp:
    def __init__(self, session, key: str) -> None:
        self.session = session
        self.key = key

    def scrub(self, s) -> str:
        return str(s).replace(self.key, "[KEY]") if self.key else str(s)

    async def call(self, name: str, args: dict, timeout: float = 120.0):
        res = await self.session.call_tool(name, args, read_timeout_seconds=timeout)
        sc = res.structured_content or {}
        text = self.scrub(res.content[0].text if res.content else "")
        status = sc.get("status", "error" if res.is_error else "ok")
        return status, sc, text

    async def mutate(self, name: str, args: dict, timeout: float = 120.0):
        """Run a mutating tool, answering the guard's confirmation with its token.

        The human approved the batch this belongs to (see ``deploy``); each preview is
        still printed so the approval was informed.
        """
        status, sc, text = await self.call(name, args, timeout)
        if status == "needs_confirmation":
            preview = sc.get("preview") or {}
            print(f"    preview: {self.scrub(preview)}"[:220])
            status, sc, text = await self.call(name, {**args, "confirmation_token": sc.get("confirmation_token")}, timeout)
        return status, sc, text


def _ask(prompt: str, yes: bool) -> bool:
    if yes:
        print(f"{prompt} [auto-yes]")
        return True
    if not sys.stdin.isatty():
        print(f"{prompt} — no terminal to ask on; pass --yes to approve")
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


async def _deploy(args) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    mcp_dir = Path(args.mcp_dir).resolve()
    datapack = Path(args.datapack).resolve()
    files = sorted(p for p in datapack.rglob("*") if p.is_file())
    if not files:
        print(f"no files under {datapack}")
        return 1
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(_read_env(mcp_dir / ".env"))
    profile = mcp_dir / ".env.deploy"
    env.update(_read_env(profile))
    env["PTERODACTYL_READ_ONLY"] = "false"
    print(f"deploy profile: {profile if profile.exists() else 'none (MCP defaults, expect refusals; copy deploy.env.example to ' + str(profile) + ')'}")
    key = env.get("PTERODACTYL_API_KEY", "")

    params = StdioServerParameters(command="node", args=[str(mcp_dir / "dist" / "index.js")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            m = Mcp(session, key)

            # ---- preflight: dry-run every write; any refusal stops the deploy -------
            plan = []
            refused = 0
            for f in files:
                rel = f.relative_to(datapack).as_posix()
                remote = f"{args.remote_dir}/{rel}"
                status, sc, text = await m.call("ptero_write_file", {"path": remote, "content": f.read_text(), "dry_run": True})
                preview = sc.get("preview") or {}
                action = preview.get("action", "?") if status == "dry_run" else status
                refused += status not in ("dry_run",)
                plan.append((remote, f, action, text.splitlines()[0] if text else ""))
            new = sum(a == "create" for _, _, a, _ in plan)
            over = sum(a == "overwrite" for _, _, a, _ in plan)
            unknown = sum(a == "unknown" for _, _, a, _ in plan)
            print(f"preflight: {len(plan)} files -> {new} new, {over} overwrite, {unknown} parent-missing (created on write), {refused} refused")
            for remote, _, action, line in plan:
                if action in ("refused", "error"):
                    print(f"  REFUSED {remote}: {line[:160]}")
            if refused:
                print("stopping: the guard refused part of the plan; fix the profile or the plan, nothing was written")
                return 1
            if args.dry_run:
                for remote, _, action, _ in plan:
                    print(f"  [{action}] {remote}")
                return 0

            # ---- backup: one explicit backup before anything changes ----------------
            if not args.skip_backup:
                status, sc, text = await m.call("ptero_list_backups", {})
                count, limit = sc.get("backup_count"), sc.get("backup_limit")
                if limit is not None and count is not None and count >= limit:
                    print(f"backup slot full ({count}/{limit}); delete one in the panel or pass --skip-backup. Nothing written.")
                    return 1
                print("taking a backup before writing (this can take a few minutes)...")
                status, sc, text = await m.mutate("ptero_create_backup", {"name": "pre-dungeon-deploy", "wait": True}, LONG)
                print(f"  backup: {status} {text.splitlines()[0][:160] if text else ''}")
                if status not in ("success", "ok"):
                    print("stopping: no backup, nothing written")
                    return 1

            # ---- approval ------------------------------------------------------------
            if not _ask(f"Write {len(plan)} files to {args.remote_dir} on the live server?", args.yes):
                print("not approved; nothing written")
                return 1

            # ---- upload --------------------------------------------------------------
            failures = 0
            for remote, f, action, _ in plan:
                status, sc, text = await m.mutate("ptero_write_file", {"path": remote, "content": f.read_text()})
                ok = status in ("success", "ok")
                failures += not ok
                print(f"  [{status}] {remote}" + ("" if ok else f": {text[:160]}"))
            if failures:
                print(f"{failures} file(s) failed — the datapack on the server is incomplete; re-run to finish")
                return 1
            print(f"uploaded {len(plan)} files")

            # ---- restart / build ------------------------------------------------------
            if args.restart:
                if not _ask("Restart the server now? This disconnects every player.", args.yes):
                    print("restart skipped; the dimension registers on the next restart, then run /function "
                          f"{args.namespace}:build")
                    return 0
                status, sc, text = await m.mutate("ptero_set_power_state", {"signal": "restart", "wait_seconds": 60}, LONG)
                print(f"  restart: {status} {text.splitlines()[0][:160] if text else ''}")
                if status not in ("success", "ok"):
                    return 1
                # The console tool streams a live window, not history, so "Done" can't be
                # read back after the fact: poll the power state, then let it settle.
                for _ in range(60):
                    await asyncio.sleep(5)
                    status, sc, text = await m.call("ptero_get_server_resources", {})
                    if sc.get("state") == "running" or text.endswith("is running."):
                        break
                else:
                    print("server did not come back within 5 minutes; check the console before building")
                    return 1
                await asyncio.sleep(45)  # plugins and worlds finish loading after the state flips
                print("  server is back up")
            elif args.reload:
                status, sc, text = await m.mutate("ptero_send_console_command", {"command": "reload"})
                print(f"  reload: {status} {text[:120]}")
            if args.build:
                log_task = asyncio.create_task(m.call("ptero_get_console_log", {"window_seconds": 12, "max_lines": 200, "filter": "dungeon|Running function|Unknown|ERROR"}, 60.0))
                await asyncio.sleep(1.5)
                status, sc, text = await m.mutate("ptero_send_console_command", {"command": f"function {args.namespace}:build"})
                print(f"  build: {status}")
                _, _, log = await log_task
                for line in log.splitlines()[1:12]:
                    print("   ", line[:160])
    return 0


def deploy(args) -> int:
    started = time.time()
    rc = asyncio.run(_deploy(args))
    print(f"done in {time.time() - started:.0f}s, exit {rc}")
    return rc
