"""scripts/fetch_vanilla.py, offline: a fake bundler jar and a fake `java`."""
import importlib.util
import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fetch_vanilla.py"
spec = importlib.util.spec_from_file_location("fetch_vanilla", SCRIPT)
fv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fv)

VERSION_JSON = json.dumps({"id": "26.2", "pack_version": {"data_major": 107, "data_minor": 1}})


def _inner_jar() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("version.json", VERSION_JSON)
        z.writestr("data/minecraft/structure/trial_chambers/chamber/chamber_1.nbt", b"\x0a\x00\x00")
        z.writestr("data/minecraft/worldgen/template_pool/x.json", "{}")
        z.writestr("data/other_ns/ignored.json", "{}")
        z.writestr("assets/minecraft/lang/en_us.json", "{}")
        z.writestr("net/minecraft/Main.class", b"\xca\xfe")
    return buf.getvalue()


def _bundler(tmp_path: Path) -> Path:
    jar = tmp_path / "server.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("META-INF/versions.list", "abc123\t26.2\t26.2/server-26.2.jar\n")
        z.writestr("META-INF/versions/26.2/server-26.2.jar", _inner_jar())
    return jar


def test_extracts_only_version_and_minecraft_data_from_a_bundler(tmp_path):
    dest = tmp_path / "out"
    n = fv.extract_data(_bundler(tmp_path), dest)
    got = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert got == [
        "data/minecraft/structure/trial_chambers/chamber/chamber_1.nbt",
        "data/minecraft/worldgen/template_pool/x.json",
        "version.json",
    ]
    assert n == 3


def test_extracts_from_an_unbundled_jar(tmp_path):
    jar = tmp_path / "old.jar"
    jar.write_bytes(_inner_jar())
    fv.extract_data(jar, tmp_path / "out")
    assert json.loads((tmp_path / "out" / "version.json").read_text())["id"] == "26.2"


def test_refuses_a_jar_without_version_json(tmp_path):
    jar = tmp_path / "not-minecraft.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("data/minecraft/a.json", "{}")
    with pytest.raises(fv.FetchError, match="version.json"):
        fv.extract_data(jar, tmp_path / "out")


def test_refuses_path_traversal(tmp_path):
    jar = tmp_path / "evil.jar"
    with zipfile.ZipFile(jar, "w") as z:
        z.writestr("version.json", VERSION_JSON)
        z.writestr("data/minecraft/../../escape.json", "{}")
    with pytest.raises(fv.FetchError, match="unsafe"):
        fv.extract_data(jar, tmp_path / "out")


def test_missing_java_is_a_clear_error(tmp_path):
    with pytest.raises(fv.FetchError, match="Java not found"):
        fv.generate_registries(_bundler(tmp_path), tmp_path, "/nonexistent/java")


def _fake_java(tmp_path: Path) -> str:
    """Stands in for the data generator: writes <--output>/reports/registries.json."""
    script = tmp_path / "java"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
        "(out / 'reports').mkdir(parents=True)\n"
        "(out / 'reports' / 'registries.json').write_text(json.dumps({'minecraft:item': {'entries': {'minecraft:stone': {}}}}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_main_builds_the_vendor_folder_atomically(tmp_path, monkeypatch):
    dest = tmp_path / "vendor" / "vanilla-26.2"
    monkeypatch.setattr(fv, "VENDOR", dest)
    rc = fv.main(["--jar", str(_bundler(tmp_path)), "--java", _fake_java(tmp_path)])
    assert rc == 0
    assert (dest / "registries.json").exists() and (dest / "version.json").exists()
    assert not dest.with_name("vanilla-26.2.partial").exists()
    # a second run leaves it alone unless --force
    assert fv.main(["--jar", "unused"]) == 0


def test_main_cleans_up_when_the_generator_fails(tmp_path, monkeypatch):
    dest = tmp_path / "vendor" / "vanilla-26.2"
    monkeypatch.setattr(fv, "VENDOR", dest)
    rc = fv.main(["--jar", str(_bundler(tmp_path)), "--java", "/nonexistent/java"])
    assert rc == 1
    assert not dest.exists() and not dest.with_name("vanilla-26.2.partial").exists()
