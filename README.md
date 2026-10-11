# Kitcher

A local pantry and grocery-planning app for the recipes you like.

[Screenshot demo](docs/demo.md) shows the running application with sample data. This version runs locally on Windows; local photo recognition is experimental and the full phone/recognition acceptance checks remain open.

The current version was [submitted to Terra on October 11, 2026](docs/shipping-closeout.md) and is awaiting review.

[Architecture](docs/architecture.md) | [Execution plan](docs/architecture-execution/README.md) | [Implementation status](docs/architecture-execution/06-implementation-status.md)

## Run locally on Windows

Use Python 3.12 and the project-local virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-runtime.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py setup_account
$env:KITCHER_DEBUG = '1'
$env:KITCHER_HTTPS = '0'
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

Open http://127.0.0.1:8001. The generated account credentials are in the ignored `var/initial-account.txt`; they are not printed. Running setup_account again preserves the existing account and password. Change the password with `manage.py changepassword owner` when needed.

This HTTP development profile is restricted to this computer. Stop the regular HTTPS services before using the same application port. Regular use and iPhone setup are documented below.

## Implemented workflows

- Add ingredients, equivalent names, and known package sizes.
- Track known, zero, and unknown inventory separately.
- Preview purchases, usage, and observations before atomic confirmation.
- Keep immutable event history with retry and stale-preview protection.
- Edit recipes, favorites, quantities, instructions, and servings.
- Save and swap a shortlist; calculate purchases using the largest individual recipe requirement.
- Record actual cooking usage and preserve its recipe snapshot.
- Review undo against current stock or record a linked correction.
- Review explicit text updates and private fridge/receipt photo drafts.
- Edit recognized fields, reconcile unknown stock, and confirm the resulting balances.
- Recover local jobs with leases/retries; cancel jobs and expire private source copies.

The app starts with an empty pantry and recipe book. The import pipeline is implemented. Recovery commands, regular startup/shutdown, scheduled maintenance, and local HTTPS are implemented. Recognition quality, router reservation, iPhone trust/walkthrough, and desktop visual release checks remain open.

## Local recognition

The installed runtime and English OCR live under ignored var/tools. Start the dedicated cloud-disabled service, then the single importer:

```powershell
.\scripts\start-recognition.ps1
.\.venv\Scripts\python.exe manage.py process_imports
```

The worker command holds an OS lock, so a second worker is refused. Manual review works while recognition is unavailable. The source policy is shown on Imports. See [the preliminary recognition report](docs/architecture-execution/07-recognition-report.md) for the installed model manifest and real-photo limitations.

## Checks

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format .
.\.venv\Scripts\python.exe manage.py test --noinput
```

Django's test runner uses an isolated file-backed SQLite test database under `var/`. Focused checks cover quantities, transactions, ownership, concurrent confirmations, recipes, ranking, shopping, cooking, corrections, admin editing, imports, recovery, source cleanup, and loaded model identity. VS Code's **Python: Test** task runs this command.

## Local data and configuration

Optional settings go in `var/local.env`; use [config/local.env.example](config/local.env.example) as a guide. The app generates its own independent Django secret under `var/`. Existing `config/secret_key` and `config/hypercorn.toml` are not used by Kitcher.

Put original fridge sample photos in `D:\kitcher\fridge`. That folder, `receipts/`, `var/`, and `backups/` are excluded from Git. Receipt samples are pending. Recognition benchmarking will use real samples; originals in the sample folders are separate from expiring application-upload copies.
## Regular local use

Run `scripts/start-local.ps1` and open the configured HTTPS address. Use `scripts/stop-local.ps1` for shutdown with backup. An example configured address is https://192.168.1.100/; see [Windows/iPhone setup](docs/architecture-execution/09-iphone-and-startup.md) for certificate trust and outstanding device checks. [Recovery commands and demonstrations](docs/architecture-execution/08-recovery-evidence.md) cover verified backups and portable exports.

`manage.py test --noinput` uses the HTTP test-client settings profile; production retains HTTPS-only settings and has separate HTTPS acceptance checks.
