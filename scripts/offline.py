"""Prepare an offline Docker image and hashed Python wheelhouse."""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Outil introuvable : {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Commande échouée : {command[0]} (code {exc.returncode})") from exc


def prepare(destination: Path) -> None:
    lock = ROOT / "requirements.lock"
    if not lock.is_file():
        raise RuntimeError("Verrou introuvable : requirements.lock")
    if not shutil.which("docker"):
        raise RuntimeError("Docker introuvable : installer Docker sur la machine de préparation")
    if destination.exists():
        raise RuntimeError(f"Destination déjà présente : {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cloudinventory-offline-", dir=destination.parent) as tmp:
        bundle = Path(tmp) / "bundle"
        bundle.mkdir()
        image = "cloudinventory:offline"
        run(["docker", "build", "-t", image, str(ROOT)])
        run(["docker", "save", "-o", str(bundle / "image.tar"), image])
        wheels = bundle / "wheelhouse"
        wheels.mkdir()
        run([sys.executable, "-m", "pip", "download", "--require-hashes", "--only-binary=:all:", "--dest", str(wheels), "-r", str(lock)])
        shutil.copyfile(lock, bundle / "requirements.lock")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        compose = compose.replace("    build: .\n", "    image: cloudinventory:offline\n", 1)
        if "    image: cloudinventory:offline\n" not in compose or "    build:" in compose:
            raise RuntimeError("Compose incompatible : construction non remplaçable")
        (bundle / "docker-compose.yml").write_text(compose, encoding="utf-8")
        shutil.copyfile(ROOT / "docs/hors-ligne.md", bundle / "hors-ligne.md")
        files = sorted(path for path in bundle.rglob("*") if path.is_file())
        if not any(wheels.iterdir()):
            raise RuntimeError("Wheelhouse vide")
        (bundle / "SHA256SUMS").write_text(
            "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(bundle).as_posix()}\n" for path in files),
            encoding="utf-8",
        )
        bundle.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?", default=ROOT / "hors-ligne")
    args = parser.parse_args()
    try:
        prepare(args.destination.resolve())
    except RuntimeError as exc:
        parser.exit(1, f"Erreur : {exc}\n")
