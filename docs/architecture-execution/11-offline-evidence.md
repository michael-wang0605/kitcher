# Offline recognition evidence

Recorded October 10, 2026. Four supplied fridge views passed this isolated offline exercise. The full ten-fridge/ten-receipt recognition gate remains open.

## Enforced condition

The computer remained on its normal network. A dedicated, checksum-verified copy of the project's Python 3.12 executable ran the trial database and interpretation child processes. Temporary Windows Firewall outbound rules blocked every non-loopback IPv4/IPv6 destination for:

- var/offline-runtime/python.exe.
- var/tools/ollama/ollama.exe.
- var/tools/ollama/lib/ollama/llama-server.exe.
- var/tools/tesseract/tesseract.exe.

The web server, Caddy, unrelated Python applications, and the separate WSL model service retained their existing network access. All firewall profiles were already enabled; the test did not change network category or global firewall settings.

The helper validated effective ActiveStore rules: exact executable paths/hashes, enabled, Outbound, Block, Any profile, and complete non-loopback destination ranges. Windows reported PrimaryStatus OK and enforcement for active profiles. [Microsoft firewall rule documentation](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule)

The recorded recognition process trees contained the dedicated Python, Ollama, llama-server, Tesseract, and Windows conhost.exe. The console host supplies the local process console; it is outside the recognition network-policy scope. The pinned inference implementation uses the explicitly configured loopback service, with ambient HTTP proxies disabled.

## Controls and measured result

Authoritative corrected-run evidence is under ignored var/offline-checks/20261010T1802118124474Z/:

| Artifact | Evidence |
| --- | --- |
| before.json | IPv4 and IPv6 TCP connections to Cloudflare's public addresses succeeded; loopback Ollama 0.40.0 reachable. No photo or credentials were sent in these connection probes. |
| firewall-audit.json | Exact executable hashes, rule identifiers, active scope, and final trial/restoration status. |
| firewall-state.json | Effective rule snapshots and verified removal time, 18:03:48 UTC. |
| blocked-before.json / blocked-after.json | External IPv4 and IPv6 connects both failed with PermissionError, Windows error 10013; loopback stayed reachable before and after all inference. |
| results.json | Four real upload/claim/interpret/publish results, source/annotation hashes, pinned model identity, latency, sampled memory, and zero automatic inventory changes. |
| restored.json | External IPv4 and IPv6 connectivity and loopback succeeded after removing the temporary rules. |

The corrected manifest SHA-256 was a4aeeb421fb387747fb37c4ec53ece0aae225960c81443187455916f2403bc46. Inference never received its expected facts. Every sample used the pinned Gemma3:4b llama.cpp digest c30276f7dffec93a536d283e15088990454b8d21cf180a3d6eea4959e4147291, v2 prompt, schema 1, and English psm6 OCR.

| Sample | Upload through review | Result |
| --- | --- | --- |
| IMG_5221.jpeg | 42.343 seconds | needs_review; succeeded on first attempt. |
| IMG_5222.jpeg | 7.421 seconds | needs_review; succeeded on first attempt. |
| IMG_5223.jpeg | 7.297 seconds | needs_review; succeeded on first attempt. |
| IMG_5224.jpeg | 6.078 seconds | needs_review; succeeded on first attempt. |

Median 7.359 seconds; slowest 42.343 seconds; four of four within 60 seconds. Inventory events before/after: 0/0. Human timing and semantic acceptance remain unmeasured.

Maximum sampled process working-set sum was 2,241,323,008 bytes; private-commit sum 6,936,166,400 bytes; whole-GPU use 5,966,729,216 bytes of 8,589,934,592. The maximum observed sampling gap was 312 ms with a requested 100 ms interval; no sampling errors were reported. Sampling can miss spikes, working sets can share pages, and GPU use includes other applications. These values are not a controlled comparison with earlier runs.

## Reproduce and recover

Run from a normal, non-administrator PowerShell in the repository:

```powershell
.\.venv\Scripts\python.exe scripts\prepare-offline-runtime.py
.\scripts\check-offline-local.ps1 -Manifest var\recognition-corpus-corrected-20261009.json
```

Only scripts/offline-firewall-helper.ps1 requests administrator approval. It validates a fixed set of project-owned executables, holds the temporary rules during the trial, and removes them when the normal-user orchestrator releases them. A 40-minute limit removes rules if the orchestrator fails to release them; a run that outlasts enforcement cannot pass. The orchestrator rejects an administrator launch so the ordinary worker stays under the signed-in user's normal identity.

The primary worker pauses gracefully; the test uses its own database and does not alter live pantry inventory. Finally, it releases and waits for verified rule removal, probes restored connectivity, restores environment values, and resumes the normal-user worker. Its identity was verified afterward. An independent administrator read-only enumeration at 21:45 UTC confirmed zero remaining KitcherOffline rules; var/offline-cleanup-verification.json records the successful enumeration. A normal-user query was denied and its empty result was not accepted as proof.

If a process crash interrupts cleanup, run the exact-rule cleanup script in an elevated PowerShell with that run's audit path:

```powershell
.\scripts\clear-offline-rules.ps1 -Audit D:\kitcher\var\offline-checks\<run>\firewall-audit.json
```

It checks the recorded group/name/program/action/direction and removes only matching temporary rules. Inspect firewall-state.json and run the allowed connectivity probe before claiming restoration. Scheduled/startup cleanup now also applies source expiry to isolated trial databases through scripts/cleanup-trials.py; structured reports remain available.

## Diagnostics preserved

The initial successful offline run at 17:53 UTC reached review in 4.266-37.422 seconds, but its elevated orchestrator restarted the worker as administrator. The owned elevated worker was stopped after identity verification, then resumed under the normal user. The tooling was changed to elevate only the firewall helper. A subsequent setup attempt failed before rule creation because a typed PowerShell parameter collided with its parsed audit variable; no rules remained. The corrected run above verifies the full trial, cleanup, and normal-user restart together. Diagnostic directories are preserved privately.

## Remaining acceptance

Repeat the enforced offline condition with representative fridge and receipt images when supplied. Recognition semantic quality, corrected fields, human review versus manual-entry timing, and actual desktop/iPhone walkthroughs remain required. This evidence establishes offline execution on the four available fridge images; it does not complete M0, M4, or M5.