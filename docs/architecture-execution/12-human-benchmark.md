# Human review versus manual entry

Updated October 11, 2026. This procedure supplies the human timing evidence required by [04-acceptance-plan.md](04-acceptance-plan.md). It uses a separate trial database. Recognition processing time remains a separate measurement.

## Current evidence

The worksheet/scorer and isolated desktop helper are implemented. Fourteen focused checks passed, using synthetic timing values in temporary test databases. The full 125-test suite also passed on October 11. These checks establish tool behavior only.

A private worksheet and initial score were prepared under `var/offline-checks/20261010T1802118124474Z/`. The score contains four fridge samples, zero receipts, zero completed pairs, null medians, and an **unmeasured** criterion. No human timing has been supplied.

The isolated server passed authenticated read-only HTTP checks at 22:39 UTC on October 10: pantry, ingredients, imports, history, CSS, and all four original draft review pages returned 200. Its database retained zero inventory events. The recorded server was stopped afterward. Browser-controller initialization still failed; this is not visual or iPhone acceptance.

## Prepare a fresh trial

1. Supply ten representative fridge and ten receipt originals. Independently annotate identities, visible quantities, ambiguities, and expected unresolved observations before inference. Record corrections to annotations with their reason; preserve prior evidence.
2. Run the baseline recognition benchmark with the schema-1 manifest, following [10-release-checklist.md](10-release-checklist.md). Preserve its original `results.json`. Worksheet preparation requires every included sample to have reached `needs_review`; a failed sample needs investigation and a fresh complete run, rather than being dropped from the corpus.
3. Use the resulting timestamped directory below. Create a new worksheet filename. Keep JSON, credentials, and reports under ignored `var/`.

```powershell
$trialDirectory = (Resolve-Path -LiteralPath 'var\benchmarks\REPLACE_WITH_ACTUAL_TIMESTAMP').Path
.\.venv\Scripts\python.exe manage.py human_benchmark prepare --report "$trialDirectory\results.json" --worksheet "$trialDirectory\human-timing.json"
.\scripts\start-human-benchmark.ps1 -TrialDirectory $trialDirectory
```

The helper checks that the trial has its own existing database and current schema. It opens `http://127.0.0.1:8012/` for desktop use and prints the credential file location. Normal home-network access remains the configured HTTPS application. The helper does not migrate, clone, reset, or switch a running trial. Its stop command stops only the recorded helper process tree.

Complete measurements while original draft sources remain available within their 24-hour retention period. Do not extend retention to obtain a benchmark. After expiry, preserve structured evidence and run a fresh trial when the operator is ready.

## Establish equal tasks

Before timing, use the isolated app to establish the same ingredient catalog, initial pantry quantities, and independently determined target observations for both methods. Document these conditions in each sample's `target_and_baseline_notes`. Use the same person, computer, browser, and input method throughout.

Determine amounts from the real food, package labels, or other independently justified observations. Preserve unknown amounts as unknown; leave an unresolved measurement incomplete when it cannot be confirmed honestly. Container counts do not establish remaining food weight.

Before each assigned task, restore that sample's agreed baseline through explicit, reviewed changes outside the timer. Include these reset confirmations in history, but exclude their IDs from the timed task. Restore the whole agreed baseline, including unknown quantities and reconciliation choices. Setup must not pre-fill manual amounts or edit model suggestions to remove work from the timed phase. Both methods must produce the same ingredient identities, units, starting and final quantities, and unknown-stock reconciliation.

## Follow the prepared schedule

The worksheet assigns alternating first methods separately for fridge and receipt samples, shuffles the first pass, then reverses the sample order for the second method. With ten of each type, five of each type start with each method. Follow all 40 tasks in the recorded `schedule`; sample numbers are one-based positions in `samples`.

Counterbalancing reduces order bias; it does not eliminate familiarity from repeated observations. Record interruptions or departures, and rerun affected evidence honestly rather than adjusting times.

| Method | Timed task | Required confirmation evidence |
| --- | --- | --- |
| `review` | Open the original recognized draft, inspect the photo, correct all suggestions, review quantities/reconciliation, preview, and confirm. | Its single atomic photo confirmation, linked to the original draft and image fingerprint. |
| `manual` | Inspect the same photo and enter the same observations using manual inventory forms. Fridge observations use SET; receipt purchases use ADD. | All successful manual confirmation IDs in chronological order, including corrections within the timed task. |

Start the stopwatch when the assigned photo/draft or manual form is first inspected. Stop after the **last successful confirmation response**. Include reading, corrections, missing-item entry, unit choice, reconciliation, previews, retries, and all manual confirmations. Exclude advance setup, baseline resets, and automated recognition processing.

For review, the original draft UUID is in `results.json`; open `/imports/<UUID>/` on the isolated origin. Find each confirmed inventory event in History; the detail URL `/history/<ID>/` supplies its ID. Do not reuse events across methods or samples. Avoid concurrent inventory work in the isolated account during a task.

## Fill the worksheet

Preserve report path/hash, sample identities, expected facts, assigned first methods, and schedule. Edit only the measurement and attestation fields:

- `operator`: the person who performed the measurements.
- `human_stopwatch_measurements`: `true` only after actual timed tasks.
- `same_catalog_baseline_and_targets`: `true` only after verifying the equal conditions above.
- Per sample, `review.seconds` and `manual.seconds`: positive decimal **strings**, such as `"42.125"`, with at most three decimal places. Never copy recognition latency into these fields.
- Each method's `event_ids`: the actual positive integer IDs of all its timed confirmations, chronologically ordered.
- `target_and_baseline_notes`: the agreed catalog/baseline/targets, quantity evidence, reconciliation, and any relevant limitations.
- `expected_facts_checked`: `true` only after the operator compared the corrected result with the independent expected facts.
- `corrected_fields`: entries with `ingredient`, `field`, `before`, `after`, and `reason`, all strings. Allowed fields are `ingredient`, `amount`, `unit`, `operation`, `reconciliation`, `remove`, and `add`. Use `[]` only when no suggestions required correction. Record every semantic correction, including missing or invented items.

Leave missing timings/IDs/corrections as null. Missing evidence remains unmeasured. The scorer does not independently establish whether a stopwatch reading or expected-fact judgment is truthful.

## Score against the same trial database

Run this in a separate PowerShell session so the database settings apply only to scoring:

```powershell
$trialDirectory = (Resolve-Path -LiteralPath 'var\benchmarks\REPLACE_WITH_ACTUAL_TIMESTAMP').Path
$env:KITCHER_DATA_DIR = $trialDirectory
$env:KITCHER_DATABASE = Join-Path $trialDirectory 'kitcher.sqlite3'
.\.venv\Scripts\python.exe manage.py human_benchmark score --worksheet "$trialDirectory\human-timing.json" --output "$trialDirectory\human-score-v1.json" --username owner
```

Scoring reads inventory evidence without writing inventory. It refuses existing outputs or an output that would overwrite its inputs. Keep the original recognition report unchanged, and use a new score filename after revising a worksheet.

The scorer verifies owner, actual draft/event/image linkage, method sources, chronological order, distinct event IDs, matching affected quantities and reconciliation, and plausible confirmation spans. Whole-catalog conditions, observations, and stopwatch readings remain operator attestations.

A complete 20-sample, ten-of-each comparison passes the **human timing criterion** only when every pair is complete and the median review time is no greater than the median manual time. Partial results remain unmeasured for that criterion. The score always leaves `release_gate` unmeasured: latency, semantic quality, offline/memory coverage, recovery, and desktop/iPhone walkthrough requirements need their own evidence and the final audit.

Stop the helper when finished:

```powershell
.\scripts\stop-human-benchmark.ps1
```

Keep M0/M4/M5 open until the complete [release checklist](10-release-checklist.md) passes.
