# Recovery implementation and evidence

Public documentation uses example LAN addresses and a placeholder Windows username; substitute your configured values.

Updated October 9, 2026. Architecture authority: [../architecture.md](../architecture.md).

## Implemented commands

Run from D:\kitcher with the repository virtual environment:

```powershell
.\.venv\Scripts\python.exe manage.py backup_pantry
.\.venv\Scripts\python.exe manage.py backup_pantry --pre-migration
.\.venv\Scripts\python.exe manage.py export_pantry backups\exports\pantry.json --username owner
.\.venv\Scripts\python.exe manage.py restore_backup <verified-backup.sqlite3> <new-database.sqlite3>
.\.venv\Scripts\python.exe manage.py import_pantry <pantry.json> --username owner
```

Database backups use the [SQLite online backup API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup), then check integrity and foreign keys, hash the closed copy, and publish a temporary file atomically. Each backup has an adjacent JSON manifest with checksum, size, migrations, and timestamp. Daily retention keeps seven date-named copies; pre-migration copies are separate and never pruned by daily retention. Filenames use UTC dates; the scheduled daily backup runs at 20:00 local time.

The chosen local directory is D:\kitcher\backups. The verified mirror is C:\Users\<Windows-user>\KitcherBackups. Inspection confirmed D: is disk 1 and C: is disk 2. They are separate physical disks on the same computer. The configured paths and backup status are ignored local files. Mirror failure leaves the verified local copy available and is reported on Status; the command exits unsuccessfully if its mirror did not complete.

Restore requires a valid manifest, matching checksum, database integrity, and installed migration code. It refuses any existing destination file. The live database is never replaced by a demonstration. Full backups contain account records, sessions, drafts, job metadata, and domain history; they exclude source images and the separately stored application secret. A new secret invalidates restored sessions, so sign in again.

Portable exports contain only the explicitly selected owner's ingredient/alias/package catalog, items including null quantities, pantry revision, recipes and unresolved requirements, shortlist and revision, immutable events and lines, snapshots, and reversal/correction links. They exclude authentication records, sessions, source photos, drafts, and leases.

Schema 1 uses integer thousandths for quantities and servings. Row IDs are references within the export, mapped to newly created local IDs during import. Historical recipe snapshot JSON, request UUIDs, command hashes, timestamps, and source-input JSON are preserved as archival data. Embedded IDs in historical snapshots/source-input JSON describe the original observation; they are not current database foreign keys. Event-line operation references and relational history links are mapped to restored records. Old signed previews are not imported. New actions use fresh previews and the restored account's current IDs/revisions.

The importer validates the complete bounded payload before writing: fields/types, quantities, timestamps, UUIDs, normalized uniqueness, references, event and item version chains, replayed operations, final balances, snapshots, and reversal/correction relationships. It then checks that the selected account has an empty domain and imports within one transaction. Recovery is the explicit exception to the ordinary preview/confirmation write path: it inserts validated historical records into an empty account and never edits an existing domain. A concurrent second import is refused; a late failure rolls back all inserted records.

## Independent demonstrations

Two runs are recorded in ignored var/recovery-checks:

| Run | Scope | Result |
| --- | --- | --- |
| 20261009T001614137003Z | Representative sample in an isolated source database | SQLite and JSON restores compared equal; both accepted another reviewed change. |
| 20261009T131107122663Z | Actual live database/export | SQLite and JSON restores compared equal; both accepted a verification change only in their restored copies. Live domain remained unchanged. |

The representative sample includes two ingredients, three aliases, one package, known eggs, explicitly unknown rice, two recipes including an unresolved requirement, a selected recipe, five events, seven event lines, cooking with actual usage, a reversal, a correction, and a recipe edit after cooking. Comparison includes archived snapshots, timestamps, request UUIDs/hashes, links, versions, quantities, recipe contents, and selection membership. After each restore, a new reviewed deduction changed eggs from 9000 to 8000 thousandths, kept rice unknown, and advanced pantry revision from 5 to 6.

The live owner's domain was empty, so its restore alone would not prove the richer history cases. Its private drafts/jobs and account were covered by the actual SQLite copy. Database-only recovery runs recover_imports: jobs without their source image become expired, unfinished processing drafts expire, and already interpreted review drafts keep their structured data. This does not apply inventory.

The representative run retains its isolated source/export/backup. Both runs retain restored databases, after-change exports, and report.json. The live run records the backup checksum at restoration time; subsequent daily/shutdown backups can replace that UTC day?s backup file. No original fridge photo was changed. Reproduce with:

```powershell
.\.venv\Scripts\python.exe scripts\check-recovery.py
.\.venv\Scripts\python.exe scripts\check-recovery.py --live
```

The first command creates a new representative source. The second reads the actual owner, takes a verified live backup/export, and makes all verification inventory changes in separate restored databases.

## Restore a SQLite backup

1. Keep the existing application/database intact. Choose an empty directory under var/recovered-<date>.
2. Run restore_backup with a verified backup and a new database path in that directory.
3. In a new PowerShell session set KITCHER_DATA_DIR to that directory and KITCHER_DATABASE to its restored database file. Use separate backup/mirror paths while inspecting it.
4. Run manage.py check, manage.py migrate --check, and manage.py recover_imports with compatible code/dependencies. Pending forward migrations require a separate pre-migration backup before applying them.
5. Sign in with the restored account; a new application secret means previous sessions/previews are invalid. Compare balances, knowledge states, recipes, shortlist, and history, then preview/confirm a new change.
6. Reconfigure the chosen data directory and HTTPS setup only after verification. The machine startup scripts intentionally operate on this repository's var directory; they do not overwrite or silently switch the live database.

## Restore a portable JSON export

1. Choose another empty directory; set KITCHER_DATA_DIR and KITCHER_DATABASE in a fresh PowerShell session.
2. Run migrate --noinput and setup_account --username owner. Credentials are newly generated locally.
3. Run import_pantry <export.json> --username owner. The command refuses existing domain records, drafts/jobs, or nonempty state.
4. Compare domain contents and history, then preview/confirm a new change. Recognition requires new uploads because drafts/photos are excluded.
5. Preserve the original live database and verified backup until the recovered installation is accepted.

## Upgrade procedure

Stop local services and retain a pre-migration backup before replacing code/dependencies. Recheck official supported patch releases, update exact runtime/tool pins and checksums, install the pinned requirements, and run the planned acceptance checks. Use start-local.ps1 to apply pending migrations only while the web/worker are stopped; it requires a successful verified pre-migration backup first. Repeat both recovery demonstrations after a schema/export change. Compare recognition changes against the same annotated corpus and record new model/tool/prompt identities.