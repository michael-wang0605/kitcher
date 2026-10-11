# Execution milestones

Public documentation uses example LAN addresses and a placeholder Windows username; substitute your configured values.

Status: finalized for implementation on October 7, 2026. Architecture authority: [../architecture.md](../architecture.md).

Each milestone should be delivered as a small, reviewable change set. Use the acceptance examples in [04-acceptance-plan.md](04-acceptance-plan.md) as completion criteria. The checkbox list below tracks required work; checked tasks have recorded implementation evidence in [06-implementation-status.md](06-implementation-status.md). Complete the architecture's contracts, not only the milestone's happy-path example.

The order puts transaction correctness before recommendation features and runs the recognition trial early. M0's scaffold must pass before M1; recognition measurements may continue during M1-M3 but must pass before M4 completes. One developer can follow the sequence without separate infrastructure. Durations are not estimated until the actual recognition benchmark and first working inventory slice are available.

## M0 - Confirm the baseline and scaffold

Depends on: no earlier milestone.

- [x] Recheck local Python, GPU availability, Ollama, Tesseract, and free disk space. Treat the hardware notes in the draft as historical observations.
- [x] Select the latest patched Django 5.2 LTS version and lock runtime requirements separately from the existing development tool pins.
- [x] Add the Django project, one domain app, configuration via environment, and ignored local data directories.
- [x] Configure SQLite with short explicit transactions, IMMEDIATE mode, and a 5-second lock timeout. Keep whole-request transactions disabled.
- [x] Configure the existing account, login/logout, CSRF-protected forms, owner-scoped access, and an initial navigation shell.
- [x] Configure Waitress on 127.0.0.1:8001 and plan Caddy internal-CA HTTPS/static serving. Keep runserver for development.
- [ ] Prepare a small representative recognition corpus: 10 fridge photos and 10 receipt photos, with handwritten expected observations and unresolved quantities.
- [ ] Trial gemma3:4b and Tesseract locally. Record latency, memory, uncertainty handling, corrected fields, and behavior without internet after models are downloaded.
- [ ] Record the gemma3:4b digest, Ollama version, Tesseract English OCR version, prompt/schema version, and measurements. If local tuning/model replacement is necessary, record evidence against the same corpus.

Deliverables: runnable scaffold, configuration example without secrets, account setup instructions, dependency pins, and a benchmark report under this folder or a linked benchmark directory.

Completion criteria:

- Login works and unauthenticated requests cannot inspect pantry/import data.
- The local app starts from the repository's Python 3.12 environment.
- At least one fridge image and one receipt reach a validated candidate format through local tools.
- The benchmark exposes ambiguous quantities instead of presenting invented amounts as confirmed.
- If the baseline misses the acceptance gate, tune preprocessing/prompting or replace the local model using the same corpus; M4 remains incomplete until it passes. M1-M3 can proceed after the M0 scaffold is accepted.

## M1 - Ingredient catalog and reliable pantry edits

Depends on: M0 application scaffold.

- [x] Add Ingredient, IngredientAlias, PackageSize, PantryState, PantryItem, InventoryEvent, and InventoryEventLine models/migrations.
- [x] Add owner/ingredient uniqueness, normalized alias uniqueness, nonnegative known quantities, positive packages, and owner/request UUID uniqueness.
- [x] Implement Decimal parsing, dimension validation, fixed-point conversion, bounds, precision, and visible rounding.
- [x] Build the atomic inventory service with preview, revision checks, request hashing, event lines, rollback, and duplicate-success lookup.
- [x] Capture source input, normalized operation, reconciliation basis, before/after balances, and quantity knowledge state.
- [x] Build responsive pantry listing and explicit SET/ADD/SUBTRACT forms. Unknown balances require reconciliation for ADD/SUBTRACT.
- [x] Allow catalog editing through admin; make pantry balances and event history read-only there.
- [x] Show basic update history from the same first edit.
- [x] Add focused transaction and quantity checks from the acceptance plan as part of implementation.

Deliverables: first useful application slice: login, catalog, pantry, preview/confirm, and history.

Completion criteria:

- Add six eggs, use two, and set the observed balance to three through the interface; each change has an event.
- Unknown, zero, and a positive quantity display distinctly.
- Repeat the same confirmation and get the original event without another balance change.
- A phone/desktop stale confirmation changes no quantities and presents a refreshed preview.
- A multi-ingredient failure rolls back balances, revisions, and event creation together.

## M2 - Recipe book, shortlist, and shopping

Depends on: M1 quantity/catalog services.

- [x] Add Recipe, RecipeIngredient, RecipeSelection, and SelectionEntry models/migrations.
- [x] Build recipe editing, favorite toggling, default servings, and incomplete-recipe feedback. Use recipe revision checks for edits.
- [x] Implement per-recipe ingredient aggregation, serving scaling, deterministic ranking, and coverage explanations.
- [x] Save shortlist membership and ordering with an explicit selection revision.
- [x] Add refresh/swap actions, selected servings, and sensible empty/fewer-than-three states.
- [x] Calculate maximum targets across selected recipe alternatives and known-stock shortfalls.
- [x] Show unknown-stock verification, exact fractional count shortfalls, and whole-item purchase advice.
- [x] Preserve shortlist membership after inventory and recipe edits while updating readiness.
- [x] Add the deterministic ranking/shopping examples from the acceptance plan.

Deliverables: usable recipe book and grocery view.

Completion criteria:

- The original eggs/rice example produces buy quantities of two eggs and 200 g rice.
- Repeated lines within one recipe are summed before taking a maximum across recipes.
- Serving changes scale requirements and affect shopping immediately.
- Checking a grocery item off does not mutate inventory.
- Recipe edits change current requirements but do not silently replace selected recipes.

## M3 - Cooking, corrections, and undo

Depends on: M1 atomic inventory and M2 recipes.

- [x] Add cooking preview at chosen servings, with editable actual ingredient usage.
- [x] Capture immutable recipe title/revision, default/chosen servings, normalized requirements, and actual deductions on the event.
- [x] Reject stale pantry or recipe revisions before applying a cooking preview.
- [x] Provide explicit reconciliation for unknown or insufficient stock; show the final reconciled and deducted balance.
- [x] Add history details, correction reasons, reversal previews, and a unique original-event reversal link.
- [x] Require a linked correction when a knowledge-state transition or intervening change prevents a safe exact reversal.
- [x] Add cooking/history/reversal checks from the acceptance plan.

Deliverables: a complete manual loop: stock -> recipe -> shopping -> purchase -> cook -> updated stock -> history/correction.

Completion criteria:

- Cooking at adjusted servings deducts the reviewed actual quantities exactly once.
- Later recipe edits leave the cooking event readable and unchanged.
- Negative stock cannot be committed.
- Undo after another update is previewed against today's balance and never restores an old balance silently.
- A second reversal of the same event is refused.

## M4 - Text, receipt, and fridge-photo review

Depends on: M0 recognition trial and M1-M3 deterministic services.

- [x] Add ImportDraft/ImportJob models and versioned candidate schemas.
- [x] Implement explicit text patterns for bought/used/have-left, with review for ambiguity.
- [x] Implement authenticated JPEG/PNG uploads, 10 MiB byte limit and 25,000,000-pixel decoded limit, orientation normalization, and private spool paths.
- [x] Persist jobs and introduce one process_imports management-command worker, with 300-second claim leases, attempt tokens, a 120-second total timeout, three total transient-failure attempts with 10/30-second delays, and cancellation.
- [x] Add local receipt OCR and fridge candidate extraction, with reviewed alias/package resolution.
- [ ] Validate receipt/package recognition quality and the complete representative recognition gate.
- [x] Validate candidate operations and unresolved fields before they enter a draft.
- [x] Build draft edit/review, resulting-balance preview, duplicate warnings, and confirmation through the existing inventory service.
- [x] Add progress/error/status screens and manual correction when the model is unavailable.
- [x] Add cleanup_imports and 24-hour source expiry, cleanup every 15 minutes while running and on startup, and immediate deletion on apply/cancel.
- [x] Add restart, malformed result, duplicate, expired-file, and stale-draft examples from the acceptance plan.

Implementation note: pipeline checks pass and local tools are installed; the [preliminary report](07-recognition-report.md) covers measured baseline/detail-view comparisons on four fridge views. The detail-view variant remains an opt-in experiment. Receipt/package recognition quality, the representative corpus, human timing, and the complete recognition gate remain open. M4 is in progress. The M5 scheduler must also keep cleanup running when the worker is stopped.

Deliverables: all first-version input types use the same inventory rules; the recognition report meets every gate in the acceptance plan.

Completion criteria:

- Fridge photos set only reviewed visible amounts; unseen pantry stock is unchanged.
- Receipt ADD operations remain unresolved until ingredient amounts are known.
- Confirming one draft repeatedly creates one event.
- A stopped worker or Ollama instance leaves manual pantry and recipe features usable.
- Restart recovery cannot overwrite an edited draft or apply inventory automatically.
- Photos are removed according to the visible retention policy.

## M5 - Phone access and recovery

Depends on: M1-M4 features.

- [x] Implement backup_pantry with SQLite's backup API, seven retained daily backups plus separate pre-migration backups, and export_pantry/import_pantry with schema-versioned exports.
- [x] Document and exercise restore into a separate directory; preserve unknown values, event history, recipe snapshots, and selection membership.
- [x] Add scripts for web/worker startup and orderly shutdown on Windows.
- [x] Implement Caddy internal-CA HTTPS at 192.168.1.100 and document public-root trust, allowed hosts/origins, private-network firewall scope, and static serving.
- [ ] Verify the router DHCP reservation and install/trust only the public root certificate on the actual iPhone.
- [x] Add useful status indicators for worker availability and last successful backup.
- [ ] Exercise the full product loop on desktop and the actual phone.
- [x] Record known recognition limitations and upgrade procedures.
- [ ] Run the planned focused acceptance checks and document results before release.

Deliverables: first complete local version with phone workflow and recovery instructions.

Completion criteria:

- The phone can authenticate, review an upload, edit servings, record cooking, and view shopping.
- Ollama remains accessible only on loopback.
- Database recovery is demonstrated from a real backup into a clean app directory.
- Recognition service charges are $0 and there is no paid fallback.
- The app clearly explains that access and recognition depend on the computer running.

Recovery, startup/restart, scheduled maintenance, and status evidence are in [08-recovery-evidence.md](08-recovery-evidence.md) and [09-iphone-and-startup.md](09-iphone-and-startup.md). Caddy/private-network setup is implemented; DHCP reservation and actual iPhone trust/walkthrough are still open. M5 is in progress.

## Optional later milestone - Remote access

Trigger: an explicit decision to use the app away from the home computer/network.

Follow [05-operations.md](05-operations.md). Recheck hosting limits then, migrate local data with a verified export, use PostgreSQL plus private object storage, and replace local job database access with authenticated outbound worker requests. Do not treat a cloud deployment as a prerequisite for M5.

## Implementation workflow

Use one reviewable change set per milestone, splitting the larger milestone into schema/services/UI/checks when necessary. Never mark a later milestone complete to compensate for a failed earlier acceptance gate.

For each change set, record its delivered behavior, migration effects, relevant acceptance results, and operational instructions. M1 inventory changes require the event/transaction contract from their first commit. M4 recognition measurements require real local samples, not fabricated or assumed outcomes.

M0 artifacts include requirements-runtime.txt, configuration examples, and benchmark results. M5 artifacts include scripts/start-local.ps1, scripts/stop-local.ps1, scripts/backup-local.ps1, config/Caddyfile.example, and filled-in local startup/restore instructions. Keep machine secrets and private CA keys out of the repository.

Final release requires all M0-M5 statuses complete, focused acceptance evidence, a passing 20-image recognition report, an actual-phone walkthrough, and separate SQLite-backup and JSON-export restoration demonstrations. No release status is inferred from these finalized documents.

The final deployment selections and remaining execution actions are collected in [10-release-checklist.md](10-release-checklist.md). Delayed receipt photos defer their acceptance work; they do not reopen the architecture or remove the release gate.
