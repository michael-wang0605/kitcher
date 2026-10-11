"""Prepare a checksum-verified copy of the current Python 3.12 executables."""

import hashlib
import json
import shutil
import sys
from pathlib import Path


def main():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("The offline trial uses the project's Python 3.12 runtime.")
    root = Path(__file__).resolve().parents[1]
    target = (root / "var" / "offline-runtime").resolve()
    if not target.is_relative_to(root.resolve()):
        raise RuntimeError("Keep the dedicated runtime inside this repository.")
    base = Path(sys.base_prefix)
    target.mkdir(parents=True, exist_ok=True)
    files = []
    for name in (
        "python.exe",
        "python312.dll",
        "python3.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ):
        source = base / name
        if not source.is_file():
            if name in {"python.exe", "python312.dll"}:
                raise RuntimeError("Required Python runtime file is missing.")
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = target / name
        if (
            not destination.is_file()
            or hashlib.sha256(destination.read_bytes()).hexdigest() != digest
        ):
            shutil.copy2(source, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Dedicated runtime checksum verification failed.")
        files.append({"name": name, "sha256": digest})
    (target / "runtime-manifest.json").write_text(
        json.dumps(
            {"base": str(base), "python": sys.version, "files": files}, indent=2
        ),
        encoding="utf-8",
    )
    print("Dedicated offline-test runtime files are checksum-verified.")


if __name__ == "__main__":
    main()
