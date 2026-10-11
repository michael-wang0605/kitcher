"""Apply the ordinary source lifecycle to local benchmark databases."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def trial_directories(var):
    directories = []
    var = var.resolve()
    for name in ("benchmarks", "offline-checks"):
        container = var / name
        if not container.exists():
            continue
        if container.resolve() != container or not container.is_relative_to(var):
            raise RuntimeError(
                "Trial container must be a regular directory inside var."
            )
        for child in sorted(container.iterdir()):
            if not re.fullmatch(r"20\d{6}T\d{6,13}Z", child.name) or not child.is_dir():
                continue
            if child.resolve() != child or not child.is_relative_to(container):
                raise RuntimeError(
                    "Trial directory must remain inside its own container."
                )
            database = child / "kitcher.sqlite3"
            if database.is_file() and database.resolve().parent == child:
                directories.append(child)
    return directories


def main():
    root = Path(__file__).resolve().parents[1]
    report = {
        "created": datetime.now(timezone.utc).isoformat(),
        "directories": [],
        "errors": [],
    }
    for trial in trial_directories(root / "var"):
        environment = os.environ.copy()
        environment.update(
            KITCHER_DATA_DIR=str(trial), KITCHER_DATABASE=str(trial / "kitcher.sqlite3")
        )
        try:
            result = subprocess.run(
                [sys.executable, str(root / "manage.py"), "cleanup_imports"],
                cwd=root,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode:
                raise RuntimeError(
                    "Trial cleanup command failed; inspect its schema/version before retrying."
                )
            report["directories"].append(
                {"directory": str(trial), "result": result.stdout.strip()}
            )
            print(f"Source cleanup completed: {trial.name}")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            report["errors"].append({"directory": str(trial), "error": str(exc)})
    (root / "var" / "trial-cleanup-status.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    if report["errors"]:
        print(
            "Some isolated trial sources require cleanup; see var/trial-cleanup-status.json.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
