# Implementation status

## October 11 shipping snapshot

The user requested submission of the current local Windows version and closure of this development cycle. The shipped scope is the implemented app with experimental recognition; the full architecture acceptance gates below remain future work and are not claimed complete.

For this snapshot, all 125 tests passed in 85.020 seconds; Ruff, migration consistency, and dependency checks passed. Chrome successfully rendered a separate demonstration database. Pantry quantities, recipe/shopping output, an inventory preview from two to six eggs, and its confirmed history event were inspected. The [screenshot walkthrough](../demo.md) records that limited desktop check. It does not replace the complete desktop acceptance sequence or the actual iPhone walkthrough.

Public documentation uses example LAN addresses and a placeholder Windows username; substitute your configured values.

Updated October 11, 2026. The goal remains execution of the complete [M0-M5 plan](03-execution-plan.md).

## Current milestone evidence

| Milestone | Status | Evidence / remaining work |
| --- | --- | --- |
| M0 | In progress | Scaffold, runtime pins, local account, settings, migrations, and login implemented. Local OCR/vision installed and four real fridge photos trialled. Two isolated four-photo trials now record sampled process/GPU memory. Representative corpus, receipt measurements, human timings, representative memory, and representative receipt/offline evidence remain open. Four supplied fridge views succeeded under verified program-scoped offline enforcement. |
| M1 | Core acceptance complete | Catalog, nullable stock, fixed-point arithmetic, signed review, atomic confirmation, history, read-only inventory admin, and independent-connection contention checks pass. |
| M2 | Core acceptance complete | Recipe editor, favorites, unresolved quantities, ranking, saved selections/swaps/servings, maximum-target shopping, and service-backed recipe admin pass. |
| M3 | Core acceptance complete | Actual cooking usage, immutable snapshots, stale recipe checks, inverse-delta reversals, and linked corrections pass. |
| M4 | In progress | Text/photo drafts, bounded private uploads, local OCR/vision, worker claims, bounded retries, cancellation, expiry, cleanup, versioned manual review, and atomic draft confirmation implemented. Recognition quality and complete photo acceptance remain open. |
| M5 | In progress | Verified D:/C: backups, JSON export/import, representative and live restores, new changes after each restore, Waitress/Caddy HTTPS, startup/shutdown/restart, maintenance tasks, and Status implemented. Router reservation, iPhone trust/walkthrough, and desktop visual walkthrough remain open. |

Core checks do not establish release readiness. The required 20-image and actual-phone gates remain unchanged.

## Recorded checks

Latest full run: `.venv/Scripts/python.exe manage.py test --noinput`.

- Latest complete run: 125 tests passed in 62.876 seconds on October 11; Django system checks reported no issues. All 16 focused recovery checks also passed.
- Ruff lint passed. Migration consistency reported no changes; pip reported no broken requirements. Architecture-folder links and all project PowerShell syntax checks also passed on October 11. The full test output and exit status are preserved privately in var/acceptance-20261011.log and var/acceptance-20261011.json.
- Migrations 0001-0003 are applied to the local database.
- Inventory and import races use independent connections against a file-backed SQLite test database.
- Import checks cover duplicate/stale confirmation, source limits/orientation/phone JPEG auxiliary frames, owner isolation, lease recovery, retries, edited/cancelled draft publication, malformed output, expiry, cleanup, and verified model identity.
- A Windows file-lock deletion failure preserves the committed cancellation; source access is denied and cleanup retries deletion.
- Existing catalog, recipe, shopping, cooking, history, and admin checks continue to pass.

Tests are in [../../tests/](../../tests/). An actual-phone walkthrough has not been completed. HTTP test-client settings are isolated from production TLS; dedicated HTTPS tests exercise secure cookies, redirects, and CSRF.

## Recognition baseline and evidence

See [07-recognition-report.md](07-recognition-report.md) for verified measurements and limitations.

- Python 3.12; pinned Django 5.2.18, Waitress 3.0.2, Pillow 12.3.0 and transitive dependencies.
- RAM baseline: 34,276,933,632 bytes; CPU: AMD Ryzen 9 3950X.
- NVIDIA GeForce RTX 2070 SUPER: driver check confirmed 8192 MiB and driver 566.36.
- Ollama 0.40.0 installed under ignored `var/tools/ollama`; dedicated loopback endpoint `127.0.0.1:11435`, cloud disabled, one parallel inference.
- Tesseract 5.5.3.20260724 installed under ignored `var/tools/tesseract`; English and orientation data available.
- Gemma3:4b Q4_K_M baseline uses the explicitly pinned local llama.cpp manifest. Loaded digest is verified after inference.
- The October 9 v2 baseline reached review in 3.797-11.062 seconds (median 4.516); the opt-in five-view trial took 8.594-13.141 seconds (median 9.680). Each used an isolated database; event counts remained 0/0.
- Sampled process working-set/private-commit and whole-GPU maxima are recorded with scope and sampling limits in the recognition report. The detail-view trial introduced false identifications/duplicates and was not adopted; runtime defaults to v2.
- Full-resolution reinspection corrected a mistaken salsa-label annotation before both October 9 trials. Old annotations/results were preserved.
- Recognition still misidentifies labels and suggests unsupported food amounts from container counts. Every photo observation requires explicit quantity review.
- The four samples do not replace the representative ten-fridge/ten-receipt corpus. Receipt photos are not available yet.

Original images under `fridge/` remain unchanged. Expiring application copies are separate under `var/imports/`.

## Regular-use build and recovery

Regular access is https://192.168.1.100/ on the home network. Waitress listens only on 127.0.0.1:8001 behind Caddy. Credentials remain in ignored var/initial-account.txt. The user confirmed iPhone and the trusted home network and will reserve that address in the router; reservation completion has not been verified.

- Caddy 2.11.7 installed from a checksum-verified official ZIP. Private CA keys stay in ignored var/caddy-data; the public phone certificate is var/phone-root.cer.
- Windows Wi-Fi was changed to Private with the user's authorization; administrator setup completed the Caddy-only private-LAN TCP/443 rule.
- Startup/shutdown/restart succeeded. Recorded process trees account for Windows virtual-environment launcher children and preserve unrelated listeners.
- Current-user tasks are registered for 15-minute cleanup, daily backup, and logon startup. Registered cleanup, backup, and startup actions each reported task result 0. Production HTTPS probes succeeded again after the final web refresh.
- Local full backups are under D:\kitcher\backups and checksum-verified mirrors under C:\Users\<Windows-user>\KitcherBackups, confirmed to be a separate physical disk.
- Representative and actual-live SQLite/JSON restores succeeded in independent directories. Each accepted a new reviewed change only in its restored copy. The representative sample covers known/unknown stock, recipes, selection, immutable cooking snapshots, correction/reversal links, and history versions.
- Production HTTP probes succeeded again at 18:12 UTC on October 10 after the corrected offline trial. They validated the generated CA/host, login, secure/HttpOnly session cookies, secure CSRF cookies, all primary routes and CSS, missing-CSRF rejection, and Caddy scheme-header handling. These probes do not establish visual or actual-phone acceptance.
- Browser/desktop visual automation repeatedly failed initialization with "trusted Node process exited unexpectedly"; no visual walkthrough is claimed.

See [08-recovery-evidence.md](08-recovery-evidence.md) and [09-iphone-and-startup.md](09-iphone-and-startup.md) for commands, actual setup, evidence, and outstanding device checks.

## Next work

1. Verify the router reservation and the actual iPhone's root trust, Safari login, and complete walkthrough; obtain desktop visual evidence when the controller works.
2. Improve recognition against the same annotated samples; add representative fridge/receipt samples when supplied.
3. Measure human review versus manual entry; extend measured memory and verified offline execution to the representative fridge/receipt corpus.
4. Audit every release requirement after these checks before marking the goal complete. M0, M4, and M5 remain open.

Follow [10-release-checklist.md](10-release-checklist.md) for settled deployment choices, measured-trial reproduction, user/device inputs, and the final audit.

## Offline execution and trial-source lifecycle

The corrected October 10 run in [11-offline-evidence.md](11-offline-evidence.md) blocked all non-loopback destinations for the dedicated test Python and owned Ollama/llama-server/Tesseract executables. IPv4/IPv6 controls failed with Windows access denied throughout inference and succeeded after verified rule removal. Four samples reached review in 6.078-42.343 seconds, median 7.359, with zero inventory events. Only the firewall helper runs elevated; the normal-user worker was verified after restart.

Scheduled and startup cleanup now includes isolated benchmark/offline trial databases. Expired October 9 source copies were removed; originals and structured annotations/reports were retained. Browser/native-controller initialization was retried and still failed; visual acceptance remains open.

Independent administrator firewall enumeration at 21:45 UTC confirmed zero temporary offline rules. Certificate-checked HTTPS probes passed at 18:12 UTC with successful owner login, secure cookies, primary routes/CSS 200, CSRF rejection, and proxy-header handling. These checks retain their read-only HTTP scope; actual-device visual acceptance remains open.

## Human measurement tooling

Private human-benchmark worksheets and a read-only scorer are implemented, with a counterbalanced schedule and confirmation evidence tied to the original photo drafts. Fourteen focused checks passed in 5.874 seconds using synthetic fixtures. The existing four-photo worksheet has zero completed pairs, null medians, and an unmeasured criterion; no actual human comparison is claimed.

The desktop-only helper starts an existing isolated trial database on 127.0.0.1:8012. Authenticated read-only checks passed at 22:39 UTC on October 10, including all four draft review pages, with inventory events unchanged at zero. The helper was stopped; startup failure now also cleans up a server it started. Browser initialization still fails, so visual/device acceptance remains open. See [12-human-benchmark.md](12-human-benchmark.md) for the operator procedure and scoring limitations.
