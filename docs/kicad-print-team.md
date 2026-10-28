# KiCad print team

The print team starts after a checked case has a verified 3MF. It plans what to print, asks the person through the lead, and keeps an append-only `print/events.jsonl` record. A planner and an independent reviewer examine the exact case revision and plate assignment. A later print request or confirmation goes to this team through a lead release note; the original board contract is not recreated. Physical printing does not hold `teamReady` open.

The person starts the job in Bambu Studio. A printer status observation means only that the machine reported a state. The team marks a part as printed, failed, or fit tested only from the person's explicit confirmation, tied to the part name, STL hash, revision, quantity, and plate. Case repairs read this inventory so already printed parts can be reused.

## Optional A1 status monitor

Bambu Studio can show the bound printer, but it does not expose a supported local account API to this app. Bambu says A1 Developer Mode can leave LAN MQTT available for third-party monitoring, while [the protocol itself is not officially supported](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/). The monitor in this PR is therefore optional and experimental. It reads a current LAN status snapshot; it does not fetch cloud history, send a print job, or control the printer.

Pair once on the Mac that runs the viewer:

1. Find the A1's local IP, serial, and LAN access code on the printer. If status access is restricted on your firmware, enabling Developer Mode is an optional printer setting; [Bambu says that mode opens MQTT but does not support its protocol](https://blog.bambulab.com/updates-and-third-party-integration-with-bambu-connect/).
2. From this checkout, run `node scripts/setup-bambu-monitor.mjs` in a macOS terminal. Enter the IP and serial. The script displays the certificate fingerprint found at that IP and asks you to confirm the IP belongs to your printer. This is trust on first use; subsequent connections reject a changed certificate.
3. Enter the LAN access code at the terminal's macOS `security` prompt. The setup stores the code in Keychain and saves only the IP, serial, display name, and pinned certificate fingerprint in `~/.autonomous-circuit/bambu-printers.json`.

The viewer reads status over TLS from that pinned printer on a private IPv4 address. A changed certificate, missing Keychain code, offline printer, or absent status message shows as unavailable; print inventory remains usable. The monitor never reads Bambu Studio credentials or writes printer commands. Pairing is global to the Mac, so later projects reuse it.

## Data boundaries

- `case/out/parts.json` and its verified 3MF report identify printable geometry and plates.
- `print/events.jsonl` is the print team's hash-chained journal. A printer observation never increments confirmed quantity.
- The Case tab shows per-plate and per-part progress. Its controls export/open 3MF only; the person decides when to press Print in Bambu Studio.
- `case/PRINTED-INVENTORY.md` stays case-owned; case workers use the verified print journal before repair and plate export.
