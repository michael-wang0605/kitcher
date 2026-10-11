"""Download the pinned official Caddy Windows binary, verifying its SHA-256."""

import hashlib
import json
import os
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = "2.11.7"
NAME = f"caddy_{VERSION}_windows_amd64.zip"
URL = f"https://github.com/caddyserver/caddy/releases/download/v{VERSION}/{NAME}"
SHA256 = "0a1edc0b799512051c57071ce0e798d3f2cf67dc98171366f1d2b326072e3b06"


def main():
    directory = ROOT / "var" / "tools" / "caddy"
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / NAME
    if not archive.exists():
        temporary = archive.with_suffix(".partial")
        try:
            request = urllib.request.Request(
                URL, headers={"User-Agent": "Kitcher-local-setup"}
            )
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                temporary.open("wb") as stream,
            ):
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > 32 * 1024 * 1024:
                        raise RuntimeError("Caddy download exceeds 32 MiB.")
                    stream.write(block)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)
    with archive.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != SHA256:
        raise RuntimeError(
            "Caddy archive checksum differs from the pinned official release."
        )
    with zipfile.ZipFile(archive) as bundle:
        entry = bundle.getinfo("caddy.exe")
        if entry.file_size > 100 * 1024 * 1024:
            raise RuntimeError("Unexpected Caddy binary size.")
        with (
            bundle.open(entry) as original,
            (directory / "caddy.exe").open("wb") as target,
        ):
            while block := original.read(1024 * 1024):
                target.write(block)
    (directory / "release.json").write_text(
        json.dumps({"version": VERSION, "url": URL, "sha256": SHA256}, indent=2) + "\n"
    )
    print(f"Installed verified Caddy {VERSION}: {directory / 'caddy.exe'}")


if __name__ == "__main__":
    main()
