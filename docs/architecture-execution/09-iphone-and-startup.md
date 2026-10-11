# Windows startup and iPhone access

Public documentation uses example LAN addresses and a placeholder Windows username; substitute your configured values.

Updated October 9, 2026. Architecture authority: [../architecture.md](../architecture.md).

## Local setup selected

- Phone: iPhone, confirmed by the user.
- Current computer address: 192.168.1.100; router: 192.168.1.1. The user will reserve that address in DHCP. Reservation completion is still unverified.
- Desktop/phone URL: https://192.168.1.100/ on the home network.
- Waitress: loopback 127.0.0.1:8001; Ollama: loopback 127.0.0.1:11435; Caddy administration: loopback 127.0.0.1:20191.
- Caddy HTTPS binds the configured LAN address on port 443. Static files are collected under var/static; private images are not in that root.
- Caddy 2.11.7 is installed from its official Windows release. The pinned ZIP SHA-256 is 0a1edc0b799512051c57071ce0e798d3f2cf67dc98171366f1d2b326072e3b06. See scripts/fetch_caddy.py.

The user confirmed this is the trusted home network. Administrator setup completed on October 8 at 20:31 local time: Wi-Fi became Private and the Caddy-only inbound TCP/443 rule was configured for the Private profile, the configured local address, and LocalSubnet clients. Waitress/Ollama were not exposed to the LAN. The script records completion or failure in var/phone-network-setup.log. Changing Windows network/firewall settings requires administrator elevation; normal startup does not.

Ignored var/local.env contains the actual allowed host, HTTPS CSRF origin, HTTPS-only cookies, production DEBUG=0, backup mirror, phone URL, and LAN address. config/Caddyfile is ignored; its checked-in example uses environment values. CA keys stay in ignored var/caddy-data/pki/authorities/local.

Waitress trusts only the loopback proxy and only the scheme header, clearing other untrusted forwarding headers. Caddy supplies the actual request scheme. See [Waitress proxy arguments](https://docs.pylonsproject.org/projects/waitress/en/latest/arguments.html) and [Caddy internal TLS](https://caddyserver.com/docs/caddyfile/directives/tls).

## Start, stop, and maintenance

```powershell
.\scripts\start-local.ps1
.\scripts\stop-local.ps1
.\scripts\backup-local.ps1
```

Startup validates configuration, private address assignment and process ownership; checks migrations; backs up before any pending migration; cleans sources; collects static files; takes a verified backup; and starts Waitress/Caddy plus available local recognition. Manual features remain usable if recognition is unavailable. Use -WithoutRecognition to omit starting recognition. It does not stop an already running worker.

A lifecycle mutex prevents simultaneous startup/shutdown. Process records include PID, creation ticks, and executable path. Windows virtual-environment Python launchers spawn a child interpreter; ownership checks follow the verified launcher's child tree. Scripts refuse unrelated listeners and stop only the recorded process tree, checking identities again before termination. Existing unrelated services on ports 8000/11434 are untouched.

Shutdown closes HTTPS access, asks the worker to stop claiming jobs, allows its active bounded job to finish, and terminates an overdue owned process tree safely. A lease can recover from forced termination. It stops web/recognition, cleans overdue photos, and makes a verified shutdown backup. Startup/shutdown/restart were exercised on October 9; app, worker, and recognition restarted successfully.

Installed current-user scheduled tasks:

| Task | Schedule | Behavior |
| --- | --- | --- |
| KitcherCleanup | Every 15 minutes | Primary and isolated-trial source cleanup continues when the import worker is stopped. |
| KitcherBackup | Daily at 20:00 local time | Verified D: backup and separate C: mirror. |
| KitcherStart | Current user's logon | Start the configured local app and recognition. |

Tasks use the signed-in user's interactive token and store no account password. They run while that user is signed in; StartWhenAvailable and startup catch up after missed work. The computer cannot delete images or make backups while off. Startup cleanup denies/deletes overdue sources before recognition. Reinstall the tasks with scripts/install-local-tasks.ps1 if the repository moves.

The authenticated /status/ page reports worker check-ins, cleanup status, successful backup/mirror times, errors, and the phone URL. The HTTP unit-test profile is separate from production; dedicated HTTPS tests cover redirect/cookie/CSRF behavior.

## Install the public root on iPhone

The public certificate prepared for transfer is D:\kitcher\var\phone-root.cer. Its DER SHA-256 is f27c5e23d41883b72ece34e22e679da57402fc47a3cbffee5db0a9d26c81489b. The desktop user's trust store has this root installed.

1. Transfer only phone-root.cer to the iPhone using a method you control, such as your Files/iCloud Drive workflow. Do not transfer root.key or intermediate.key.
2. Open the certificate on the iPhone and install the downloaded profile in Settings > General > VPN & Device Management. Follow the device's profile-install prompts.
3. Open Settings > General > About > Certificate Trust Settings and enable full trust for the installed Caddy Local Authority root. Manually installing a profile alone does not establish SSL trust. [Apple certificate trust instructions](https://support.apple.com/en-us/102390)
4. Join the same home Wi-Fi, open Safari, and visit https://192.168.1.100/. It must open without a certificate warning. Sign in with the local owner credentials from var/initial-account.txt.
5. Complete the checks below. Report the exact step and displayed message if something fails.

If the computer's address changes, fix the DHCP reservation and update allowed hosts, CSRF origin, phone URL, and LAN address together. If the private CA is regenerated, install and trust its new public root on the iPhone.

## Actual-device walkthrough still required

The following are release checks, not evidence already obtained:

- [ ] Router reservation for 192.168.1.100 verified.
- [ ] Public root installed and explicitly trusted on this iPhone.
- [ ] Safari loads the page without a certificate warning and login persists.
- [ ] Pantry shows unknown versus zero correctly and a reviewed edit confirms once.
- [ ] A fridge upload reaches structured/manual review; editing it never applies inventory before confirmation.
- [ ] Recipe servings change shopping requirements; selection membership persists.
- [ ] Cooking records reviewed actual usage once, with readable history/correction.
- [ ] Shopping and status remain usable at the phone's normal display size.
- [ ] A desktop/phone stale preview safely refreshes instead of overwriting another change.

Use a separate demonstration account/database for artificial inventory examples; do not add fictional food to the live owner. Record actual device/browser and observations when the walkthrough is completed.

Certificate-checked local HTTPS login and static CSS probes succeeded before restart. Actual browser visual inspection remains open: the browser/desktop controller repeatedly failed initialization with “trusted Node process exited unexpectedly.” HTTP checks and unit tests do not substitute for the real iPhone or desktop visual walkthrough.
Production probes on October 9 also confirmed authenticated primary routes, secure cookies, missing-CSRF rejection, and forwarding-header handling after restart. The private report is var/http-check-20261009T133723.json; reproduce with scripts/check-local-http.py. Registered cleanup, backup, and startup actions each reported result 0. Final refreshed-app HTTP probes are recorded in var/http-check-20261009T135040.json.
