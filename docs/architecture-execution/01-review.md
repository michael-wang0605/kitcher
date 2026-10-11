# Architecture review and adopted changes

Date: October 7, 2026.

Disposition: all findings below have been incorporated into [the finalized architecture](../architecture.md) and its execution milestones. This document preserves the rationale; it does not contain pending approvals or unresolved architecture choices.

## Assessment

Proceed with the proposed Django monolith. The strongest parts of the original architecture plan are its explicit shopping rule, separation of interpretation from confirmation, partial-photo semantics, recipe history snapshots, and local inference cost constraint. A separate frontend, microservices, Redis, and cloud infrastructure are unnecessary for the initial workload.

This is a design review of a project that has not yet been implemented. Findings below identify missing contracts and implementation hazards, rather than bugs observed in running application code.

## Adopted changes

| Priority | Original section | Gap and consequence | Adopted refinement |
| --- | --- | --- | --- |
| P1 | Data model / shopping | Unknown quantities are described, but their storage and mutation rules are unspecified. An untracked ingredient could accidentally become confirmed zero or six new eggs could overwrite an unknown existing balance. | Nullable quantity: null means unknown, zero means confirmed absent. A missing record also means unverified. Add/subtract on unknown stock requires explicit reconciliation before confirmation. |
| P1 | Consistency and corrections | A version check followed by an ordinary save can still lose a concurrent phone or desktop update. | Check and change revisions inside the same transaction using conditional updates. Add a per-owner pantry revision so absent rows and multi-ingredient changes are covered. |
| P1 | Data model / SQLite | Decimal arithmetic in Python does not make SQLite decimal storage exact. | Persist integer thousandths of canonical units; convert at the service boundary and calculate using Python Decimal. SQLite has no native decimal type. [Django database notes](https://docs.djangoproject.com/en/5.2/ref/databases/#decimal-handling) |
| P1 | Implementation sequence | Pantry editing is planned before update history and confirmation safeguards. Early edits would bypass the eventual audit contract. | Deliver the event ledger, idempotency, preview, and atomic commit with the first pantry editor. Keep direct pantry/event admin mutation disabled. |
| P1 | Imports / local processing | Long inference in a web request can stall the interface. Temporary files alone do not provide restart recovery. | One local management-command worker, database job records, and private local spool storage with a retention policy. Processing creates a review draft; it never applies inventory. |
| P1 | Cooking / undo | Delta-only event lines cannot fully explain absolute corrections or transitions from unknown stock. | Record operation, before/after quantity and knowledge state, actual delta when defined, recipe snapshot, and reversal link. A correction or reversal must preview against current stock. |
| P2 | Ingredient / package size | Catalog ownership, alias collisions, and package units are unspecified. | Scope ingredients, aliases, and packages to the account. Normalize aliases in Python and enforce uniqueness per owner. Require package sizes compatible with the ingredient's canonical dimension. |
| P2 | Recipe suggestions / shopping | Repeated ingredient lines, incomplete recipes, recipe edits, and fractional counts need rules. | Aggregate repeated lines before comparison; exclude incomplete recipes; preserve shortlist membership; invalidate stale cooking previews; distinguish exact count shortfall from whole-item purchase advice. |
| P2 | Photo interpretation | Receipt images, job failures, draft edits, and duplicate warnings lack concrete states. | Separate job state from draft state. Bound uploads and execution, validate results, version drafts, and allow corrected retry without duplicate inventory application. |
| P2 | Device access / backups | Network authentication and backups are stated without an execution procedure. | Define a regular-use server, TLS for phone login, startup/shutdown, consistent SQLite backup, and a restore exercise before the first release. |
| P2 | Local model evaluation | Recognition quality is a first-version dependency but is evaluated late. | Run a small representative trial in M0. Record uncertainty, edits, latency, memory, model digest, and offline behavior before integrating imports. |

P1 items belong in the first relevant implementation milestone. P2 items are required before the complete first version is released.

SQLite also ignores `select_for_update()`. Use short explicit transactions and conditional revision changes rather than assuming row locking exists locally. An `IMMEDIATE` SQLite transaction mode can simplify local writer serialization; model/OCR calls must stay outside it. [Django SQLite transaction documentation](https://docs.djangoproject.com/en/5.2/ref/databases/#transactions-behavior)

## Repository observations

- `README.md` and `pyproject.toml` establish Python 3.12 and Ruff. `requirements-dev.txt` contains tooling, but no Django or other application dependencies.
- The file inventory contains no application source, migrations, or tests.
- `config/hypercorn.toml` contains configuration mentioning a separate Codex application and an import batch size. Its presence is not evidence of a Kitcher server. Create and document Kitcher-specific server configuration during implementation.
- A `config/secret_key` path exists. Its contents were not read. Generate a separate Kitcher secret through the application's configuration procedure.
- The draft's hardware and installation observations were recorded earlier. They were not remeasured in this review; M0 must establish the actual local processing baseline.

## Framework decision

Use the latest patched Django 5.2 LTS release for this conservative personal app. As of this review, Django lists 5.2.18 with extended support through April 2028; Django 6.1 is also supported, but its listed support ends earlier. Pin the selected patch in the runtime requirements and use the matching documentation. Recheck the patch when implementation starts. Django 5.2 LTS is the selected implementation series. [Django supported releases](https://www.djangoproject.com/download/)

## Product behavior to preserve

- Selected recipes are alternatives: use the maximum ingredient requirement across them.
- Fridge photos set observed balances and leave unseen ingredients unchanged.
- Receipts add purchases only after quantities and matches are reviewed.
- All interpreted changes require confirmation.
- Saved shortlist membership changes only when the user refreshes or swaps recipes.
- Photo inference runs on the user's computer without paid-provider fallback.
- Photo input remains part of the intended first release.

See [the finalized architecture](../architecture.md) for the adopted contracts and [the execution plan](03-execution-plan.md) for their delivery order.