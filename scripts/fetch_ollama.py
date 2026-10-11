"""Fetch the pinned official Windows Ollama package into private local tools."""

import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.40.0"
DIGEST = "3623e256762ca89bd6fa99b0cc4106401919ce9df926411673e632e3ea287bb5"
URL = f"https://github.com/ollama/ollama/releases/download/v{VERSION}/ollama-windows-amd64.zip"
ROOT = Path(__file__).resolve().parents[1] / "var" / "tools"
ROOT.mkdir(parents=True, exist_ok=True)
ARCHIVE = ROOT / f"ollama-{VERSION}.zip"
if not ARCHIVE.exists():
    pending = ARCHIVE.with_suffix(".download")
    print(
        f"Downloading official Ollama {VERSION} Windows runtime (about 1.47 GB).",
        flush=True,
    )
    with (
        urllib.request.urlopen(URL, timeout=60) as response,
        pending.open("wb") as output,
    ):
        size = 0
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            size += len(chunk)
            if size % (100 * 1024 * 1024) == 0:
                print(f"{size // (1024 * 1024)} MiB downloaded", flush=True)
    pending.replace(ARCHIVE)
with ARCHIVE.open("rb") as stream:
    actual = hashlib.file_digest(stream, "sha256").hexdigest()
if actual != DIGEST:
    raise SystemExit("Runtime checksum mismatch. Package was not extracted.")
destination = (ROOT / "ollama").resolve()
destination.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(ARCHIVE) as archive:
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if not target.is_relative_to(destination):
            raise SystemExit("Unsafe archive member. Package was not extracted.")
    archive.extractall(destination)
(ROOT / "ollama-runtime.json").write_text(
    json.dumps({"version": VERSION, "url": URL, "sha256": actual}, indent=2),
    encoding="utf-8",
)
print(f"Checksum verified. Runtime extracted to {destination}.", flush=True)
