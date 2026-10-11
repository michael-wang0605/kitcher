"""Exercise both restoration paths in new ignored directories, never the live DB.

Run with .venv/Scripts/python.exe scripts/check-recovery.py.
The sample is deliberately representative; the live owner's data stays untouched.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def run(data_dir, *arguments):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("KITCHER_")
    }
    environment.update(
        KITCHER_DATA_DIR=str(data_dir),
        KITCHER_DATABASE=str(data_dir / "kitcher.sqlite3"),
        KITCHER_BACKUP_DIR=str(data_dir / "backups"),
        KITCHER_BACKUP_MIRROR=str(data_dir / "mirror"),
    )
    if data_dir == ROOT / "var":
        environment.pop("KITCHER_BACKUP_DIR", None)
        environment.pop("KITCHER_BACKUP_MIRROR", None)
    result = subprocess.run(
        [str(PYTHON), str(ROOT / "manage.py"), *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout.strip()


def comparable(payload):
    tables = payload["tables"]
    names = {row["id"]: row["name"] for row in tables["ingredients"]}
    recipes = {row["id"]: index for index, row in enumerate(tables["recipes"])}
    events = {row["id"]: row["request_id"] for row in tables["events"]}
    converted = {}
    for name, rows in tables.items():
        output = []
        for original in rows:
            row = {key: value for key, value in original.items() if key != "id"}
            if "ingredient" in row:
                row["ingredient"] = names[row["ingredient"]]
            if "recipe" in row:
                row["recipe"] = recipes[row["recipe"]]
            for key in ("event", "reversal_of", "correction_of"):
                if key in row and row[key] is not None:
                    row[key] = events[row[key]]
            if "operations" in row:
                row["operations"] = [
                    {**op, "ingredient": names[op["ingredient"]]}
                    for op in row["operations"]
                ]
            output.append(row)
        converted[name] = output
    return {
        "pantry_revision": payload["pantry_revision"],
        "selection_revision": payload["selection_revision"],
        "tables": converted,
    }


SEED = """
from django.contrib.auth import get_user_model
from pantry.services import catalog, inventory, recipes, suggestions, cooking
owner=get_user_model().objects.get(username="owner")
eggs=catalog.save_ingredient(owner,name="Eggs",dimension="count",aliases=["large eggs"])
rice=catalog.save_ingredient(owner,name="Rice",dimension="mass")
catalog.add_package(owner,eggs,"carton","6","count")
def apply(ops):
    return inventory.confirm(owner,inventory.preview(owner,ops)["token"])
first=apply([inventory.operation(eggs,"set",10000),inventory.operation(rice,"set",None)])
purchase=apply([inventory.operation(eggs,"add",4000)])
meal=recipes.save_recipe(owner,title="Eggs",default_servings="1",instructions="Cook gently.",lines=[{"ingredient":eggs,"amount":"2","unit":"count"}])
recipes.save_recipe(owner,title="Unresolved rice",default_servings="1",lines=[{"ingredient":rice,"amount":"","unit":"g"}])
s=suggestions.selection(owner)
suggestions.replace_selection(owner,[(meal.pk,"2")],s.revision)
inventory.confirm(owner,cooking.preview(owner,meal,"1",actual={eggs.pk:1500})["token"])
inventory.confirm(owner,cooking.reversal_preview(owner,purchase)["token"])
inventory.confirm(owner,cooking.correction_preview(owner,first,{eggs.pk:9000,rice.pk:None},"Recounted.")["token"])
recipes.save_recipe(owner,recipe_id=meal.pk,expected_revision=meal.revision,title="Updated eggs",default_servings="1",lines=[{"ingredient":eggs,"amount":"3","unit":"count"}])
"""
CHANGE = """
from django.contrib.auth import get_user_model
from pantry.models import Ingredient,PantryItem,PantryState
from pantry.services import inventory
owner=get_user_model().objects.get(username="owner")
eggs=Ingredient.objects.get(owner=owner,name="Eggs")
inventory.confirm(owner,inventory.preview(owner,[inventory.operation(eggs,"subtract",1000)])["token"])
assert PantryItem.objects.get(owner=owner,ingredient=eggs).quantity==8000
assert PantryItem.objects.get(owner=owner,ingredient__name="Rice").quantity is None
assert PantryState.objects.get(owner=owner).revision==6
print("New reviewed change confirmed; eggs=8000, rice=unknown, revision=6.")
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Restore and compare the live database/export in fresh directories; do not seed or mutate live data.",
    )
    options = parser.parse_args()
    if sys.executable.lower() != str(PYTHON).lower():
        raise RuntimeError("Run this script with the repository virtual environment.")
    directory = (
        ROOT
        / "var"
        / "recovery-checks"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    source = ROOT / "var" if options.live else directory / "source"
    database_restore, json_restore = (
        directory / "sqlite-restore",
        directory / "json-restore",
    )
    for data in (database_restore, json_restore):
        data.mkdir(parents=True, exist_ok=False)
    if not options.live:
        source.mkdir(parents=True, exist_ok=False)
        run(source, "migrate", "--noinput")
        run(source, "setup_account", "--username", "owner")
        run(source, "shell", "-c", SEED)
    export_path = directory / "source-domain.json"
    run(source, "export_pantry", str(export_path), "--username", "owner")
    run(source, "backup_pantry")
    status = json.loads((source / "backup-status.json").read_text())
    run(
        source,
        "restore_backup",
        status["path"],
        str(database_restore / "kitcher.sqlite3"),
    )
    run(database_restore, "check")
    run(database_restore, "recover_imports")
    run(
        database_restore,
        "export_pantry",
        str(database_restore / "domain.json"),
        "--username",
        "owner",
    )
    run(json_restore, "migrate", "--noinput")
    run(json_restore, "setup_account", "--username", "owner")
    run(
        json_restore,
        "import_pantry",
        str(export_path),
        "--username",
        "owner",
    )
    run(
        json_restore,
        "export_pantry",
        str(json_restore / "domain.json"),
        "--username",
        "owner",
    )
    expected = comparable(json.loads(export_path.read_text(encoding="utf-8")))
    results = {}
    for label, data in (("sqlite", database_restore), ("json", json_restore)):
        actual = comparable(
            json.loads((data / "domain.json").read_text(encoding="utf-8"))
        )
        if actual != expected:
            raise RuntimeError(f"{label} restored domain differs from its source.")
        change = CHANGE
        if options.live:
            change = """
from django.contrib.auth import get_user_model
from pantry.models import PantryItem,PantryState
from pantry.services import catalog,inventory
owner=get_user_model().objects.get(username="owner")
before=PantryState.objects.filter(owner=owner).first()
revision=before.revision if before else 0
probe=catalog.save_ingredient(owner,name="Recovery verification probe",dimension="count")
inventory.confirm(owner,inventory.preview(owner,[inventory.operation(probe,"set",1000)])["token"])
assert PantryItem.objects.get(owner=owner,ingredient=probe).quantity==1000
assert PantryState.objects.get(owner=owner).revision==revision+1
print("One new reviewed change confirmed in the restored database only.")
"""
        result = run(data, "shell", "-c", change)
        run(
            data,
            "export_pantry",
            str(data / "after-change.json"),
            "--username",
            "owner",
        )
        results[label] = {
            "database": str(data / "kitcher.sqlite3"),
            "semantic_comparison": "equal",
            "new_reviewed_change": result.splitlines()[-1],
        }
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "live database restored independently; live domain unchanged"
        if options.live
        else "isolated representative sample; live data unchanged",
        "source_database": str(source / "kitcher.sqlite3"),
        "backup_sha256": status["sha256"],
        "source_counts": {key: len(rows) for key, rows in expected["tables"].items()},
        "restores": results,
    }
    (directory / "report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
