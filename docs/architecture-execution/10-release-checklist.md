# Remaining execution and release checklist

Public documentation uses example LAN addresses and a placeholder Windows username; substitute your configured values.

Updated October 10, 2026. The architecture is finalized; release acceptance remains open. Use [03-execution-plan.md](03-execution-plan.md) for the full milestone sequence and [06-implementation-status.md](06-implementation-status.md) for delivered work.

## Settled deployment decisions

| Decision | Final selection | Execution status |
| --- | --- | --- |
| Phone | iPhone on the home Wi-Fi | User confirmed; actual-device walkthrough pending. |
| Stable address | Router DHCP reservation for 192.168.1.100 | User will reserve it; completion is unverified. |
| HTTPS | Caddy internal CA; https://192.168.1.100/ | Configured and certificate-checked from the computer; public root must be trusted on iPhone. |
| Trusted network | Windows Private profile, Caddy-only TCP/443 from LocalSubnet | User authorized; setup completed. |
| Backup mirror | C:\Users\<Windows-user>\KitcherBackups on the separate C: physical disk | Chosen, copied, checksum-verified, and recovery exercised. |
| Photo sources | Repository fridge/ and receipts/ folders | Four fridge views supplied. Receipts will arrive later. Originals remain unchanged. |

Receipt availability changes the execution schedule. The complete first-version release still requires receipt recognition and its acceptance evidence.

## Work that can proceed now

1. **Recognition implementation:** compare local preprocessing, prompts, or a replacement local model against the independently annotated originals. Preserve the exact model digest, configuration, raw output, and trial results. Do not include expected facts in inference prompts. The five-view trial was not adopted because it introduced false identifications and duplicates; see [07-recognition-report.md](07-recognition-report.md).
2. **Memory measurements:** use scripts/benchmark-local.ps1 with a schema-1 manifest. It pauses only the recorded import worker, uses a fresh database, records sampled process/GPU memory, and resumes the worker in finally. Web/manual access stays available. Keep the whole-GPU and sampling limitations with the results.
3. **Extend offline evidence:** the four supplied fridge views passed enforced program-scoped offline execution, with connectivity controls, cleanup, and normal-user worker restart verified. See [11-offline-evidence.md](11-offline-evidence.md). Repeat with representative fridge/receipt samples when supplied; retain model identity, job outcomes, latency, and zero automatic inventory changes.
4. **Human timing preparation:** use the implemented private worksheet/scorer and isolated desktop helper in [12-human-benchmark.md](12-human-benchmark.md). Follow its counterbalanced schedule and record actual stopwatch values and confirmation IDs; no human timings have been measured yet.
5. **Desktop walkthrough:** when browser control is available, use a separate demonstration database/account for artificial stock and recipes and complete the acceptance-plan workflow. Record browser, viewport, observations, and manual behavior with recognition stopped. The current browser controller fails initialization; HTTP checks provide separate evidence only.

## Work requiring the user's device or photos

| Input/action | Next execution step | Completion evidence |
| --- | --- | --- |
| Router reservation | Reserve this computer at 192.168.1.100 and verify it remains assigned after reconnection. | Router confirmation and matching computer address. |
| iPhone trust | Transfer only var/phone-root.cer; install the profile and enable full certificate trust. | Safari loads the HTTPS URL without a certificate warning and login persists. |
| iPhone walkthrough | Follow [09-iphone-and-startup.md](09-iphone-and-startup.md) and the full acceptance workflow. | Actual device/browser observations covering review, servings, shopping, cooking, history/correction, stale preview, and manual use with worker stopped. |
| Representative corpus | Supply ten fridge and ten receipt images from the actual phone/stores, including difficult cases. | Independent expected facts written before inference. The current four overlapping views do not substitute for this corpus. |
| Human timing | Time review/correction/final confirmation and manual entry/final confirmation for the same observations across all 20 samples. Follow [12-human-benchmark.md](12-human-benchmark.md) for the start/end definitions, assigned order, corrected fields, and confirmation evidence. | Both medians recorded; review median no greater than manual-entry median. |

## Reproduce a measured recognition trial

Install the pinned runtime and development requirements, start the recorded local recognition service, then run:

```powershell
.\scripts\benchmark-local.ps1 -Manifest var\recognition-corpus-corrected-20261009.json
```

The optional -DetailedFridge switch reproduces the unadopted v3 experiment. The normal runtime defaults to the v2 baseline. Reports, isolated credentials/database, and staged photo copies stay under ignored var/benchmarks/<UTC timestamp>/. Each staged source follows the 24-hour lifecycle when cleanup runs. Scheduled and startup cleanup now also runs ordinary cleanup_imports against isolated trial databases through scripts/cleanup-trials.py. Originals and structured reports are retained. Preserve raw structured reports and annotations independently of source deletion. Do not copy private trial directories into version control.

## Final release audit

- [ ] Complete ten-fridge/ten-receipt corpus; validated or explicitly unresolved results for every sample.
- [ ] At least 18 of 20 samples reach review within 60 seconds, including real retry delays.
- [ ] Human review median meets the manual-entry comparison; semantic errors and corrected fields are recorded.
- [ ] Memory and offline evidence covers the representative trial; exact local model/OCR/prompt configuration recorded.
- [ ] Desktop and actual iPhone product walkthroughs completed; manual features work with recognition stopped.
- [x] Independent SQLite-backup and JSON-export restorations demonstrated; new reviewed changes succeed in restored copies.
- [x] Windows startup, HTTPS, scheduling, source cleanup, verified local/mirrored backups, and status implemented and exercised.
- [ ] Re-run focused checks after final recognition changes and audit M0-M5 evidence together.

Keep M0, M4, and M5 in progress until these requirements pass. There are no further unresolved architecture selections in this list.