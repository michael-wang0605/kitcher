import hashlib
import json
import urllib.request
from pathlib import Path

name = "tesseract-ocr-w64-setup-5.5.3.20260724.exe"
url = f"https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/{name}"
digest = "bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4"
root = Path(__file__).resolve().parents[1] / "var" / "tools"
root.mkdir(parents=True, exist_ok=True)
installer = root / name
if not installer.exists():
    pending = installer.with_suffix(".download")
    with (
        urllib.request.urlopen(url, timeout=60) as response,
        pending.open("wb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    pending.replace(installer)
with installer.open("rb") as stream:
    actual = hashlib.file_digest(stream, "sha256").hexdigest()
if actual != digest:
    raise SystemExit("OCR checksum mismatch. Installer was not run.")
(root / "tesseract-runtime.json").write_text(
    json.dumps({"version": "5.5.3.20260724", "url": url, "sha256": actual}, indent=2),
    encoding="utf-8",
)
print("Official OCR installer checksum verified.", flush=True)
