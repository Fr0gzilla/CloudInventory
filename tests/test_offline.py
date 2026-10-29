import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import offline


def test_bundle(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "docs").mkdir(parents=True)
    (root / "requirements.lock").write_text("example==1 --hash=sha256:abc\n")
    (root / "docker-compose.yml").write_text("services:\n  app:\n    build: .\n    ports: []\n")
    (root / "docs/hors-ligne.md").write_text("offline notice")
    (root / ".env").write_text("SECRET_KEY=do-not-copy")
    monkeypatch.setattr(offline, "ROOT", root)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")
    calls = []

    def fake_run(command, check):
        calls.append(command)
        if command[:2] == ["docker", "save"]:
            Path(command[command.index("-o") + 1]).write_bytes(b"image")
        if "download" in command:
            Path(command[command.index("--dest") + 1], "example.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    bundle = tmp_path / "hors-ligne"
    offline.prepare(bundle)
    assert {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()} == {
        "image.tar", "wheelhouse/example.whl", "requirements.lock", "docker-compose.yml", "hors-ligne.md", "SHA256SUMS"
    }
    assert "SECRET_KEY=do-not-copy" not in "".join(p.read_text(errors="replace") for p in bundle.rglob("*") if p.is_file())
    assert (bundle / "hors-ligne.md").read_text() == "offline notice"
    assert "image: cloudinventory:offline" in (bundle / "docker-compose.yml").read_text()
    assert "build:" not in (bundle / "docker-compose.yml").read_text()
    assert "--require-hashes" in calls[2] and "--only-binary=:all:" in calls[2]
    assert str(root / "requirements.lock") in calls[2]
    for line in (bundle / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("missing", ["docker", "lock"])
def test_missing_prerequisite(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(offline, "ROOT", tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    if missing == "docker":
        (tmp_path / "requirements.lock").write_text("locked")
    with pytest.raises(RuntimeError, match="Docker introuvable" if missing == "docker" else "Verrou introuvable"):
        offline.prepare(tmp_path / "bundle")
