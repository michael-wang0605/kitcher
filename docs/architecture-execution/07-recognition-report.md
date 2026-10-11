# Preliminary recognition report

Updated October 10, 2026. M0/M4 recognition acceptance remains incomplete.

## Verified baseline

| Component | Recorded value |
| --- | --- |
| Runtime | Ollama 0.40.0, official Windows archive verified against its release SHA-256. |
| Endpoint | 127.0.0.1:11435; startup log confirms cloud disabled. The existing WSL service at 11434 is separate. |
| Model | Gemma3:4b, 4.3B, Q4_K_M, explicitly selected local llama.cpp manifest. |
| Manifest digest | c30276f7dffec93a536d283e15088990454b8d21cf180a3d6eea4959e4147291 |
| OCR | Tesseract 5.5.3.20260724; English; baseline psm6. |
| Interpretation | Prompt kitcher.observations.v2; candidate schema 1; temperature 0; context 8192. |
| Image preparation | Bounded JPEG/PNG decode; primary frame for phone JPEG auxiliary-image containers; orientation normalization; baseline image at most 1600 by 1600. |

The application addresses the exact local manifest tag, verifies its digest/backend before inference, and checks the loaded digest/backend afterward. A mismatch produces an unresolved failed draft and no inventory change.

## Annotations and sample scope

The user supplied four overlapping views of one fridge. They do not establish recognition quality on a representative corpus. Expected facts are not passed to the model.

Full-resolution reinspection on October 9 corrected sample 4's label: GUILLERMO'S SALSA MEDIUM, previously incorrectly annotated as a mole-paste product. The label does not establish current contents or remaining quantity. Sample 3's visible bread label was also added. Old annotations and diagnostic runs remain unchanged; the corrected manifest was written before the new trials:

- Original annotations: var/recognition-corpus.json.
- Corrected annotations: var/recognition-corpus-corrected-20261009.json, SHA-256 a4aeeb421fb387747fb37c4ec53ece0aae225960c81443187455916f2403bc46.
- Earlier verified v2 run: var/recognition-trial-v4-20261008.json.
- New measured v2 run: var/benchmarks/20261009T1422339490362Z/results.json.
- Detail-view experiment: var/benchmarks/20261009T1425111811756Z/results.json.

Private annotations, raw output, credentials, and source copies stay in ignored local directories. Originals under fridge/ remain unchanged.

## Measured October 9 comparison

Both runs used fresh, independent databases and the real upload/claim/interpret/publish path. The primary worker paused gracefully and resumed afterward; web access remained running. Each sample succeeded on its first attempt and reached needs_review. Inventory event counts remained 0/0 in both databases.

| Sample | v2 baseline upload-to-review | v3 detail-view trial upload-to-review |
| --- | --- | --- |
| IMG_5221.jpeg | 11.062 seconds | 13.141 seconds |
| IMG_5222.jpeg | 4.375 seconds | 9.734 seconds |
| IMG_5223.jpeg | 4.657 seconds | 8.594 seconds |
| IMG_5224.jpeg | 3.797 seconds | 9.625 seconds |
| Median | 4.516 seconds | 9.680 seconds |
| Slowest | 11.062 seconds | 13.141 seconds |

All four reached review within 60 seconds in each run. The earlier October 8 v2 run had median 3.734 seconds and slowest 7.219 seconds. These are different trials, not pooled release results; model loading/cache state and background GPU users can differ.

The v3 trial used one overview plus four overlapping 60%-width/height crops, each bounded to 1280 pixels, sparse English OCR psm11, and generic label/deduplication/unknown-quantity instructions. Images were prepared in memory without editing the originals. Ollama receives base64 images through its documented [vision API](https://docs.ollama.com/capabilities/vision).

**Decision: keep v2 as the runtime baseline.** The opt-in v3 trial recovered roast beef in sample 1 but introduced unsupported kiwi/ice/pickles, duplicated vegetable observations, still confused bread with butter, and failed to read the salsa label reliably. It did not establish lower correction effort. Neither configuration has passed release recognition acceptance.

## Memory measurements and limits

Development-only psutil 7.2.2 and nvidia-ml-py 13.615.71 measured the benchmark process/descendants and identity-verified Kitcher Ollama process/descendants. Requested sampling interval was 100 ms. Maximum observed gaps were 187 ms for v2 and 141 ms for v3; no sampling errors were reported.

| Maximum sampled metric across the four images | v2 baseline | v3 trial |
| --- | --- | --- |
| Sum of monitored process working sets | 2,267,627,520 bytes | 3,690,176,512 bytes |
| Sum of monitored process private commit | 6,936,141,824 bytes | 8,316,485,632 bytes |
| Whole RTX 2070 SUPER memory used | 8,066,830,336 bytes | 8,045,727,744 bytes |
| GPU capacity | 8,589,934,592 bytes | 8,589,934,592 bytes |

These are sampled maxima and may miss brief spikes. Summed working sets can count shared pages twice; private commit is a distinct metric. Whole-board GPU use includes other applications and already-loaded models. Trial order/cache state differs, so this is not a controlled memory comparison. Per-process OS lifetime peaks are stored separately and are not presented as trial-window peaks. The model's reported 3,029,157,806-byte VRAM allocation is also a separate metric. Peak memory on the required 20-image corpus remains open.

## Quality findings

The v2 baseline misses visible roast-beef, honey-wheat bread, and pesto labels, misidentifies the orange salsa tub, and proposes unsupported container/produce counts. Structurally valid output is insufficient evidence of correct recognition.

Every photo observation requires explicit ingredient-amount review before inventory preview. Users can correct ingredient, amount, unit, and reconciliation, or remove observations. Saving reviewed values clears unresolved fields; final balance confirmation remains separate and atomic. Ambiguous identity and quantities remain unresolved; no unseen ingredient is removed.

Human review/confirmation time, manual-entry time, and corrected fields have not been measured. Automated job latency is not human review time.

## Benchmark tooling and earlier diagnostics

scripts/benchmark-local.ps1 reproduces a measured baseline in a fresh database; -DetailedFridge selects the unadopted v3 trial. benchmark_recognition validates the entire manifest before staging, claims only its own draft, includes actual retry waits, records attempt/model/resource metadata and source/manifest hashes, and leaves human metrics and release acceptance explicitly unmeasured.

Earlier runs diagnosed text "null" instead of JSON null and model alias/backend identity reporting. The schema/prompt and immutable digest/backend checks were corrected. Those diagnostic records do not establish release quality.

## Remaining release requirements

- Ten representative fridge images and ten receipts from the actual phone/stores, with independently written expected facts. Receipts are not available yet.
- Validated or explicitly unresolved observations for all samples; no automatic inventory changes.
- At least 18 of 20 samples reach review within 60 seconds, including retries.
- Median human review/correction/confirmation time no greater than median manual entry/confirmation time for the same observations.
- Corrected fields, semantic errors, representative peak memory, receipt OCR quality, and offline execution on the full corpus recorded. Four available fridge views already passed enforced offline execution; see [11-offline-evidence.md](11-offline-evidence.md).
- Further local tuning or model replacement measured against the same corpus if the gate fails.

See [10-release-checklist.md](10-release-checklist.md) for the remaining execution sequence. Four overlapping views and fast local responses do not satisfy the complete gate.