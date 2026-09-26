#!/usr/bin/env python3
"""Extract the Minecraft game data this project reads into ``vendor/vanilla-26.2/``.

Downloads Mojang's official server jar for the target version (or uses one you already
have), verifies its SHA-1, and extracts:

* ``version.json``        the data pack format, from the jar root
* ``data/minecraft/**``   structure templates, template pools, loot tables, ...
* ``registries.json``     every registry's entries, from the jar's data generator

Mojang's content is used under the Minecraft EULA and is never committed: ``vendor/`` is
git-ignored. Needs network access (unless you pass ``--jar``) and a Java runtime that can
run the server jar (JDK 25 for 26.2).

    uv run python scripts/fetch_vanilla.py
    uv run python scripts/fetch_vanilla.py --jar ~/Downloads/server.jar   # skip the download
    uv run python scripts/fetch_vanilla.py --java /path/to/jdk-25/bin/java
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dungeon.vanilla import GAME_VERSION as TARGET_GAME_VERSION  # noqa: E402
from dungeon.vanilla import VENDOR  # noqa: E402

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
CACHE = PROJECT_ROOT / "build" / "cache"


class FetchError(RuntimeError):
    pass


def server_jar_info(version: str) -> tuple[str, str]:
    """(download url, sha1) of the official server jar for ``version``."""
    import httpx

    try:
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            manifest = c.get(MANIFEST_URL).raise_for_status().json()
            entry = next((v for v in manifest["versions"] if v["id"] == version), None)
            if entry is None:
                raise FetchError(f"Minecraft {version} is not in Mojang's version manifest")
            meta = c.get(entry["url"]).raise_for_status().json()
    except httpx.HTTPError as e:
        raise FetchError(f"could not reach Mojang ({e}). Download the server jar yourself and pass --jar.") from None
    server = meta.get("downloads", {}).get("server")
    if not server:
        raise FetchError(f"Minecraft {version} has no server download")
    return server["url"], server["sha1"]


def download(url: str, sha1: str, dest: Path) -> Path:
    """Download ``url`` to ``dest`` unless a copy with the right SHA-1 is already there."""
    import httpx

    if dest.exists() and _sha1(dest) == sha1:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"download failed ({e})") from None
    got = _sha1(tmp)
    if got != sha1:
        tmp.unlink()
        raise FetchError(f"SHA-1 mismatch for {url}: got {got}, expected {sha1}")
    tmp.replace(dest)
    return dest


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inner_server_jar(outer: zipfile.ZipFile) -> zipfile.ZipFile:
    """The real server jar. Modern server jars are "bundlers" that carry it under
    ``META-INF/versions/``, listed in ``META-INF/versions.list`` as ``sha256<TAB>id<TAB>path``."""
    try:
        listing = outer.read("META-INF/versions.list").decode()
    except KeyError:
        return outer  # an old-style, unbundled jar
    lines = [ln.split("\t") for ln in listing.splitlines() if ln.strip()]
    if len(lines) != 1 or len(lines[0]) != 3:
        raise FetchError(f"unexpected META-INF/versions.list: {listing!r}")
    return zipfile.ZipFile(io.BytesIO(outer.read(f"META-INF/versions/{lines[0][2]}")))


def extract_data(jar: Path, dest: Path) -> int:
    """Copy ``version.json`` and ``data/minecraft/**`` out of the server jar into ``dest``.
    Returns the number of files written."""
    written = 0
    with zipfile.ZipFile(jar) as outer:
        inner = inner_server_jar(outer)
        for info in inner.infolist():
            name = info.filename
            if info.is_dir() or not (name == "version.json" or name.startswith("data/minecraft/")):
                continue
            if ".." in Path(name).parts or name.startswith("/"):
                raise FetchError(f"refusing unsafe path in jar: {name}")
            out = dest / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(inner.read(info))
            written += 1
    if not (dest / "version.json").exists():
        raise FetchError("the jar has no version.json; is it a Minecraft server jar?")
    return written


def generate_registries(jar: Path, dest: Path, java: str) -> None:
    """Run the jar's data generator (``--reports``) and keep ``registries.json``."""
    with tempfile.TemporaryDirectory(prefix="mc-reports-") as work:
        out = Path(work) / "generated"
        cmd = [java, "-DbundlerMainClass=net.minecraft.data.Main", "-jar", str(jar.resolve()),
               "--reports", "--output", str(out)]
        try:
            proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=900)
        except FileNotFoundError:
            raise FetchError(f"Java not found ({java!r}). Install JDK 25 or pass --java.") from None
        report = out / "reports" / "registries.json"
        if proc.returncode != 0 or not report.exists():
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
            raise FetchError(f"the data generator failed (exit {proc.returncode}). Is {java!r} JDK 25?\n{tail}")
        shutil.copyfile(report, dest / "registries.json")


def _show(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--jar", type=Path, help="use this server jar instead of downloading one")
    ap.add_argument("--java", default=os.environ.get("JAVA") or shutil.which("java") or "java",
                    help="Java executable for the data generator (default: $JAVA or java on PATH)")
    ap.add_argument("--force", action="store_true", help="replace an existing vendor/vanilla-* folder")
    args = ap.parse_args(argv)

    dest = VENDOR
    if dest.exists() and not args.force:
        print(f"{_show(dest)} already exists; pass --force to rebuild it")
        return 0
    staging = dest.with_name(dest.name + ".partial")
    try:
        if args.jar:
            jar = args.jar
        else:
            url, sha1 = server_jar_info(TARGET_GAME_VERSION)
            print(f"downloading the Minecraft {TARGET_GAME_VERSION} server jar from Mojang...")
            jar = download(url, sha1, CACHE / f"server-{TARGET_GAME_VERSION}.jar")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        n = extract_data(jar, staging)
        print(f"extracted {n} files")
        print("running the data generator for registries.json (takes a minute)...")
        generate_registries(jar, staging, args.java)
        shutil.rmtree(dest, ignore_errors=True)
        staging.rename(dest)
    except (FetchError, zipfile.BadZipFile, OSError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"done: {_show(dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
