# Local operations and future deployment

Status: finalized for implementation on October 7, 2026. Architecture authority: [../architecture.md](../architecture.md).

The backup/export/import/restore commands, regular startup/shutdown, scheduling, Caddy HTTPS/static serving, and operational Status page are implemented. See [recovery evidence](08-recovery-evidence.md) and [actual machine/iPhone setup](09-iphone-and-startup.md). Router reservation and the actual-device walkthrough remain open M5 checks.

## Configuration defaults

Use the existing Python 3.12 virtual environment. Pin Django 5.2 LTS, Waitress, image decoding, OCR/inference clients, and their runtime dependencies in requirements-runtime.txt. Lock tested versions in M0 and apply supported security patch updates.

| Setting | Final value |
| --- | --- |
| Database | var/kitcher.sqlite3; integer thousandths; IMMEDIATE transactions; 5-second busy timeout. |
| Application server | Waitress bound to 127.0.0.1:8001. |
| Phone access | Caddy HTTPS on the computer's router-reserved private address; internal CA; phone trusts its public root certificate. |
| Static files | collectstatic output in var/static, served by Caddy under /static/. |
| Private spool | var/imports, outside Caddy's static root; source reads require application authentication. |
| Application secret | A separately generated secret in an ignored local configuration file or environment. |
| Allowed hosts/origins | Explicit localhost and reserved private address; the HTTPS address is a trusted CSRF origin. |
| Ollama | http://127.0.0.1:11435; OLLAMA_NO_CLOUD=1; downloaded gemma3:4b baseline with digest recorded in M0. |
| OCR | Local Tesseract 5.5.3.20260724 with English data; installation under var/tools/tesseract. |
| Uploads | One JPEG/PNG per job; 10 MiB encoded file cap; 25,000,000 decoded pixels. |
| Worker | One process; 120-second total OCR/inference budget; 300-second lease; three total transient-failure attempts with 10/30-second delays. |
| Source expiry | 24 hours; delete on apply/cancel; cleanup every 15 minutes while running and before worker startup. |
| Backups | backups/daily: seven most recent daily copies; backups/pre-migration: separately retained dated copies. Both ignored by Git. |
| Tests | Django's manage.py test, including unittest-compatible calculation checks. |
| Logs | Operational IDs/status/errors; no secrets, receipt bodies, images, or model payloads in ordinary logs. |

The app uses loopback port 8001 because an unrelated local static server occupies 8000. Regular-use access is now through Caddy HTTPS at the configured private address; Waitress remains on loopback. Runserver is available only as a separate development profile.

Machine address, secret, pinned patches, tool installation paths, and model digest are setup values recorded during execution. They do not require another architecture choice.

Source access and processing are denied immediately at the 24-hour expiry. Deletion is attempted immediately on apply/cancel. If Windows temporarily locks a source that is being served, its access is denied and cleanup retries deletion; cleanup status is visible on Imports. Physical cleanup cannot run while the computer is off; startup deletes overdue sources before the worker can claim jobs. A successfully interpreted draft remains usable after its image is deleted. Retrying interpretation then requires re-upload.

Stop or abandon OCR/inference at 120 seconds and reject late publication. Three attempts means the initial attempt plus two retries. Invalid model output requires manual correction or an explicit retry, not an automatic retry loop.

## Server and phone TLS

Use Waitress for regular local operation because it supports Windows. Caddy terminates TLS with its internal CA and proxies to Waitress; it also serves collected static files. [Waitress documentation](https://docs.pylonsproject.org/projects/waitress/en/latest/), [Caddy TLS documentation](https://caddyserver.com/docs/caddyfile/directives/tls)

In M5:

1. Reserve the computer's private IP address in the home router and use that address as the phone URL.
2. Generate config/Caddyfile.example with an HTTPS site at that address, tls internal, a /static/ handler rooted at var/static, and reverse_proxy to 127.0.0.1:8001.
3. Install Caddy's public root certificate on the computer and phone, including the phone's explicit root-trust setting when required. Keep the CA private key only on the computer and out of Git.
4. Set DEBUG=False, explicit ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS, HTTPS secure session/CSRF cookies, and the trusted reverse-proxy scheme configuration.
5. Configure Waitress to trust proxy headers only from the loopback Caddy connection. Caddy supplies the scheme header; browser-supplied forwarding headers must not determine it.
6. Permit Caddy's required web ports only on the Windows private-network firewall profile. Leave Waitress and Ollama bound to loopback.
7. Verify phone login, certificate trust, CSRF confirmation, uploads, servings, cooking, and shopping using the actual phone.

Use Django runserver only for development on localhost. Do not apply HTTPS-only cookies to the separate HTTP development profile. [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)

Private CA trust is required for this settled local TLS approach. Device trust setup is a release check, not a request to choose another server. Away-from-home access is a later extension.

## Startup and shutdown

Implemented scripts/start-local.ps1, scripts/stop-local.ps1, and scripts/backup-local.ps1 use the repository's .venv interpreter and task-specific variables.

Startup:

1. Load ignored local settings; run system checks and verify required configuration.
2. Run source cleanup before processing. If data migrations are pending, take a pre-migration backup before applying them.
3. Run collectstatic when application assets have changed.
4. Start Waitress and Caddy without opening visible terminal windows unnecessarily.
5. Check local Ollama/Tesseract availability and start one process_imports worker. If either is unavailable, show that status and keep manual features available.
6. Schedule cleanup every 15 minutes and backup daily using Windows Task Scheduler; perform an overdue daily backup on startup.
7. Show desktop/phone URLs, processing status, and the last successful backup time in the app.

Shutdown stops new claims, finishes or abandons the current lease safely, stops the web access processes, and makes a consistent database backup. Unexpected termination still preserves transaction correctness and permits lease-based recovery.

Never keep a database transaction open during OCR, model calls, file reads, or waiting for the user. Backups and cleanup failures must be visible and must not mutate confirmed domain data.

## Backup, export, and restore

Implement backup_pantry with Python's SQLite backup API. Store timestamped copies under backups/daily and retain seven daily copies. Before every schema migration on an existing database, write a separate dated backup under backups/pre-migration; do not delete those through daily retention.

Keep at least one verified backup on a separate device/location under the user's control. Document the actual destination in local setup instructions. Daily backups cover the full database, including account records, history, review drafts, and job metadata. Source images are excluded.

Implement export_pantry as schema-version 1 JSON containing stable references for the ingredient/alias/package catalog, known/unknown stock, recipes, shortlist, event snapshots, and reversals. Represent quantities as documented canonical thousandths integers or null. Exclude credentials, sessions, source photos, drafts, and active job leases.

Implement import_pantry for a fresh database/account. Validate schema, quantities, references, uniqueness, and history links before writing. Map exported owner references to an explicitly selected local account; never import authentication credentials. Restore the domain import atomically and preserve historical snapshots.

Demonstrate both recovery paths:

1. Restore a database backup into a separate application directory with the same compatible schema/code.
2. Independently import a JSON export into a fresh database/account.
3. Compare quantity knowledge states, balances, recipe/selection contents, and immutable history.
4. Apply one new reviewed inventory change after each restoration.
5. Mark jobs with missing source files expired; keep already interpreted review drafts available.
6. Record backup/export timestamps, version, validation results, and restore instructions.

A live raw SQLite-file copy is not the backup procedure. Do not replace the only live database while proving a restore.

## Optional future cloud deployment

This extension starts only when remote access is requested after the local release.

1. Select hosting, PostgreSQL, and private object storage then; check their current prices, limits, inactivity behavior, and retention capabilities.
2. Take a local backup/export, pause local writes, import into PostgreSQL, compare domain data/history, and reset sequences if explicit IDs were imported.
3. Store source images in private expiring object storage; give the worker short-lived authenticated source access.
4. Add owner-scoped outbound claim, renewal, result, and failure APIs. Rotate worker credentials and validate attempt tokens.
5. Change the local worker's storage/queue adapter to fetch authenticated jobs from the cloud. Keep Ollama on the computer and bound to loopback.
6. Preserve web confirmation, atomic inventory events, and UUID idempotency. The worker produces candidates only.
7. Check computer-off queues, expired sources, duplicate results, reconnection, and PostgreSQL transaction behavior.
8. Cut over after parity/recovery checks and retain the original backup.

Cloud localhost does not reach the computer. Recognition waits while the computer is off, and a source older than 24 hours expires. Longer source lifetime requires an explicit architecture change. No hosting vendor or guaranteed free capacity is selected for this later extension.