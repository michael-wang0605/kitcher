# Acceptance plan

Status: finalized for implementation on October 7, 2026. Architecture authority: [../architecture.md](../architecture.md).

These are checks to implement alongside the application milestones. Recorded results are in [06-implementation-status.md](06-implementation-status.md). The core and import checks are implemented; recognition and release walkthrough requirements remain open.

Use Django's built-in test runner for database/view checks and plain unittest-compatible tests for pure calculations. Application tests run through manage.py test. The existing pytest tooling remains available for unrelated standalone work. Prefer behavior and transaction checks over tests that merely mirror functions.

## Deterministic behavior

| Area / milestone | Scenario | Expected result |
| --- | --- | --- |
| Units / M1 | Convert 0.5 kg rice. | Exactly 500 g at the service boundary and 500000 stored thousandths. |
| Precision / M1 | Add 0.1 g ten times from confirmed zero. | Exactly 1 g; reload produces the same quantity. |
| Validation / M1 | Submit NaN, infinity, negative stock, incompatible units, or a value beyond storage bounds. | Validation error; no event or balance write. |
| Unknown / M1 | Ingredient has no pantry record; receipt proposes ADD 6 eggs. | Ask for current stock or explicit confirmed zero; never assume zero. |
| Known zero / M1 | Eggs are confirmed zero; purchase six. | Six eggs with before=0 and after=6. |
| Dimensions / M1 | Recipe input specifies cups of a mass-tracked ingredient without a conversion. | Incomplete requirement needing a compatible quantity. |
| Shopping / M2 | Recipes need 2 and 3 eggs; stock is 1. Rice requirements 200 and 300 g; stock is 100 g. | Buy 2 eggs and 200 g rice. |
| Aggregation / M2 | One recipe has two egg lines of 2 each; another needs 3; stock is 1. | First recipe total is 4; target is 4; buy 3. |
| Scaling / M2 | Recipe needs 200 g for 2 servings; choose 3; stock is 100 g. | Requirement 300 g; shortfall 200 g. |
| Counts / M2 | Scaled egg requirement is 1.5; stock is 1. | Exact shortfall 0.5; whole-item purchase advice is 1 egg. |
| Ranking / M2 | Ready recipe versus two recipes with equal shortages/coverage. | Ready first; other ties resolved by stable recipe ID. |
| Incomplete recipes / M2 | Favorite recipe has no ingredients or unresolved units. | Excluded from ready/suggestion results with an explanation. |
| Shortlist / M2 | Fewer than three valid favorites; then inventory changes. | Show available favorites; preserve saved membership after stock changes. |
| Checkoff / M2 | Check an item off the shopping list. | No pantry change or inventory event. |
| Cooking / M3 | Recipe is cooked at double servings; user adjusts one actual deduction. | Event uses reviewed actual values and chosen servings. |
| History / M3 | Edit/delete/archive the source recipe after cooking. | Archived or protected references; immutable event snapshot remains readable. |
| Undo / M3 | Purchase +6 from 4, then consume 2; reverse purchase. | Current 8 -> 2 after reviewed inverse -6. |
| Unsafe undo / M3 | Purchase +6 from 0, then consume 2; reverse purchase. | Current 4 cannot subtract 6; require reconciliation, no negative balance. |
| Unknown undo / M3 | Correct unknown rice to 500 g; request reversal. | Require knowledge-state reconciliation; do not invent an inverse delta. |
| Text / M4 | Bought six / used two / have six left. | ADD / SUBTRACT / SET shown explicitly in review. |
| Photo / M4 | Rice exists, photo shows only six eggs. | SET eggs=6; rice unchanged. |
| Receipt / M4 | Receipt has a price and abbreviated item name but no package amount. | Unresolved amount; price never treated as ingredient quantity. |
| Duplicate / M4 | Upload identical source twice; confirm the first draft twice. | Duplicate warning for second upload; first draft applies only once. |
| Model output / M4 | Invalid JSON, invented unit, negative amount, or unrecognized alias. | Failed or unresolved draft; no stock mutation. |

## Database and concurrency checks

Use a file-backed SQLite database and independent database connections for contention tests. Do not rely solely on ordinary Django TestCase transactions or an in-memory SQLite database.

- Identical request UUID and command applied twice returns the original event.
- Same UUID with different operations is rejected.
- Two simultaneous confirmations of the same draft produce exactly one event.
- Two previews at pantry revision N race to confirm: at most one commits; the other must review current state.
- A race to create the first pantry row produces one row and one valid revision sequence.
- Any failure on the last line of a multi-ingredient commit leaves all balances, revisions, and event rows unchanged.
- A failed response after successful commit can be retried without applying again.
- Editing a draft after its preview invalidates that preview.
- Editing a recipe after its cooking preview invalidates that preview.
- Undo is itself version checked and idempotent.
- Reusing a job after lease expiry cannot let an old attempt overwrite a new result.
- Owner A cannot read, select, confirm, or process records belonging to owner B. Use a second test account even though public signup is disabled.

## Processing and recovery checks

| Fault | Expected recovery |
| --- | --- |
| Worker exits after claim. | Lease expires; bounded retry is possible while the source exists. |
| User cancels while inference runs. | Result publication is rejected; source is deleted. |
| User edits a review draft. | Draft revision changes; stale confirmation is rejected. |
| Ollama is stopped or a request times out. | Job records a useful error; manual edits work; retry uses local processing only. |
| Source expires while queued. | Job becomes expired and asks for re-upload. |
| Source expires after successful interpretation. | Structured review remains editable; reprocessing requires re-upload. |
| Computer is off past source expiry. | Processing/access is denied at expiry; overdue sources are deleted on startup. |
| Job creation fails after upload write. | Immediate file cleanup or later orphan cleanup removes the source. |
| Application restarts with drafts pending. | Structured review data survives; processing status is not falsely successful. |
| Backup is restored into a clean directory. | Known/unknown quantities, recipes, selections, and event snapshots match. |

## Recognition benchmark

Use representative photos from the user's actual phone and stores. Record expected visible facts independently before inference. Required initial corpus: ten fridge and ten receipt images, including glare, hidden labels, partial containers, abbreviations, and multiple packages.

Capture model/version/digest, prompt/schema version, OCR configuration, median and slowest latency, peak memory, failed jobs, visible ingredient matches, unsupported quantities, and corrected fields per draft.

Release requires all 20 samples to produce validated candidates or explicitly unresolved observations without automatic inventory mutation; at least 18 of 20 must reach review within 60 seconds. Median human review/correction/confirmation time must be no greater than median manual entry/confirmation time for the same visible observations across the 20 samples. Record both measurements. Invalid or uncertain candidates must remain unresolved. If the baseline misses the gate or correction effort outweighs manual entry, improve local preprocessing/prompting or replace the local model and repeat the same corpus. The gate remains unmet until the results pass.

## Release walkthrough

On the desktop and actual phone: log in, seed pantry and three favorites, select servings, inspect shortages, record a purchase, import a fridge photo and receipt, resolve fields, cook one recipe, inspect history, and perform a reviewed correction. Stop the worker and confirm manual features remain usable.

Before declaring M5 complete, attach the focused check results, benchmark observations, and backup/restore evidence to the milestone record.

## Configuration acceptance

- Known stock is bounded to 1,000,000,000,000 canonical units; out-of-range computed requirements fail validation.
- Serving counts outside 0.001-1000, or precision finer than 0.001, are rejected.
- Uploads exceeding 10 MiB or 25,000,000 decoded pixels are rejected before processing.
- One job processes one JPEG/PNG; unsupported formats show actionable feedback.
- Processing stops or abandons results after 120 seconds; a 300-second lease protects result publication.
- A transient failure gets at most three total attempts, with 10/30-second delays; malformed results do not automatically retry.
- Source cleanup runs every 15 minutes while running and before claims on startup.
- Phone HTTPS works through Caddy with trusted public CA root; direct network access to Waitress/Ollama is blocked.
- Daily backup retention keeps seven daily copies and preserves pre-migration backups separately.