# Setup and operation

## Requirements

- Home Assistant OS or another supported Supervisor installation with Apps.
- amd64 or aarch64. Runtime verification so far covers amd64 only.
- A NIBE controller exposing the myUplink PRO local token-pairing/menu API on TCP 8888.
- Controller and HA reachable on a trusted network; mDNS multicast must cross the relevant network segment.
- Host port 8099 available. The app uses host networking for discovery.

Do not assume compatibility from another project's model or firmware list: different local
NIBE API endpoints and authentication methods exist. This app uses token pairing and menu
endpoints, not manually entered Basic-Auth credentials for the controller's separate local API.

## Install and pair

Add `https://github.com/Oniel10/nibe-local-hmi` under Settings → Apps → Install app →
⋮ → Repositories. Install NIBE Local HMI and start it. The first local image build
needs internet access to obtain the Python base image and pinned dependencies.

Open Web UI. The app scans for `_nibejpi-restapi._tcp.local.` for about eight seconds.
Choose the intended controller and click **Koppeln**. The app generates its own random
16-character hexadecimal identifier and pairs under the client name **Home Assistant**.
Enable or confirm local pairing at the controller if required, then use **Status prüfen**.

If multicast discovery is blocked, open the fallback section and enter only the private IPv4
address. The app attempts to read the serial number and obtain its own token. No token needs
to be copied or posted anywhere. IPv6-only controllers and public hostnames are not supported.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `cache_seconds` | `5` | Controller menu cache lifetime, from 1 to 300 seconds. |

No IP address, serial number or pairing token is entered in the HA configuration.
The app intentionally does not provide a general-purpose external HA sensor API.

## Menu navigation and editing

Back uses the menu trail from that browser view and returns one menu level. It preserves the
trail after saves and lock changes. A direct menu link without a trail falls back to the root.
Values are edited directly in their original row. Use **Werte aktualisieren** to refresh.
Menu pages do not automatically reload while you are entering values.

Writes are locked on startup and every restart. Open **Änderungen freigeben …**, read the
risk warning, check its initially empty acknowledgement box and confirm. **Änderungen sperren**
locks again without further acknowledgement. This is a shared app lock, not a per-user lock.
The HA entry is admin-only. An already-running write finishes before a lock request completes.

Only supported types with `metadata.isWritable=true` and enabled, visible rows are offered.
Numeric values are converted exactly with their divisor; controller bounds, step and choices
are checked from a freshly loaded menu. Text fields respect the controller's length and regex
rules when present. Local REST password field 61601 additionally requires 12–32 ASCII letters
or digits, bounded further by any stricter `maxLen` supplied by the controller. Inconsistent
controller constraints may therefore reduce the usable maximum; its own validation also applies.
An unchanged starting value is required to avoid silently overwriting another change.

Unsupported fields remain read-only. Graphical schedules, unknown complex custom
screens and arbitrary string parameter types are not fully supported. Local clock editing,
event actions and second-based time fields are available in 0.6.0. Do not use this app as a
safety control or as a replacement for professional commissioning.

## Passwords and encryption

Recognized password fields use `********` or a masked input by default. Their plaintext is not
embedded in the initial page, hidden form fields, or normal JSON menu response. Pressing the eye
makes a CSRF-protected request for the controller-provided value. Some controllers may return
only a masked value themselves; the app cannot recover a password the controller does not expose.
Passwords typed for a change go directly to the selected controller and are not saved in app options.

Pairing credentials are stored as AES-256-GCM ciphertext in `/data/credentials.enc` with a fresh
96-bit random nonce per save. The random 256-bit key is stored separately in
`/data/keys/credentials.key`. Linux permissions are 0700 for directories and 0600 for these files.
Writes replace the encrypted file atomically. Missing keys or invalid ciphertext fail closed.

The key remains locally available so the app can reconnect after restart. Anyone who can read
both files, including a complete unprotected backup, can decrypt. This does not protect against
root access or a compromised HA host. Protect backups and restore the key and ciphertext together.

## Network and security

HTTPS to the controller currently uses no certificate validation, matching the prototype's local
pairing method. This leaves controller impersonation possible on an untrusted network. Hostname,
CA and fingerprint verification are not implemented. Use only a trusted LAN.

The app uses host networking for multicast discovery, with no Supervisor API, HA API, Docker
socket or host-directory access. Its host port 8099 must be available; requests are accepted only
from the Supervisor ingress gateway 172.30.32.2. There is no extra host port mapping. Do not create
a port-forward or put this listener on the public internet. HA Ingress provides browser authentication.

Outbound controller requests do not follow redirects or use system proxies, are serialized,
and use bounded response sizes. Logs contain HTTP statuses, not request paths, headers or form values.

## Troubleshooting

- App missing from the store: confirm the repository URL and reload the store.
- Start fails: check whether host port 8099 is already in use.
- No discovery: check multicast/VLAN/firewall, controller availability and local pairing mode.
- HTTP 401: controller rejected the pairing. Check that its approval is complete or token is still valid.
- HTTP 403 on a setting change: the controller restricted it; the UI acknowledgement cannot override that.
- Edited field stays unavailable: its type, enabled flag or metadata may not permit safe editing.
- Lost encryption key: the app cannot decrypt old credentials. Do not delete data blindly; restore a complete backup.
- Revoked token: this release has no in-UI re-pair/reset workflow. Preserve backup/old install and report the issue.

Before reporting bugs, remove serial numbers, IP addresses, pairing identifiers, tokens, usernames
and passwords from screenshots or logs. Never upload `/data`, credentials files, options.json or app backups.
Use the issue templates at https://github.com/Oniel10/nibe-local-hmi/issues.

## Updates and migration

Use the app's normal Update button. Updates retain its private data; the write lock is always
re-enabled. Keep a suitable backup. A repository installation is separate from a previous local
installation: it has a new Supervisor app ID and its own data. See `docs/MIGRATION.md` in the repo.

Licensed for free non-commercial use of unmodified versions only; see LICENSE. Experimental independent community project, with no claimed vendor support.


## Erweiterte lokale Funktionen ab 0.6.0

Die Startseite zeigt Betriebswerte und Geräteinformationen. Über die Navigation erreichst du
Meldungen, Anlageninformationen, Datum/Uhrzeit, Netzwerk, Firmware und die Funktionsübersicht.
Die Meldungsansicht zeigt nur das, was `/notifications` liefert; sie behauptet keinen vollständigen Alarmverlauf.
Quittieren, Geräteaktionen, Netzwerk-/Uhrzeitänderungen und Assistenten brauchen eine entsperrte
Schreibfreigabe sowie eine eigene aktive Bestätigung. Nicht angekreuzte Bestätigungen bleiben gesperrt.
Enterprise-WLAN verlangt die vom Netzwerkadministrator festgelegten EAP-/Phase-2-Werte.
Netzwerkänderungen können den Zugriff unterbrechen; gegebenenfalls am WP-Display wiederherstellen.

## Firmware herunterladen und lokal übertragen

1. „Firmware“ öffnen. Der öffentliche Download von https://www.nibe.at/software ist optional,
   wird nur nach einem bewussten Klick gestartet und benötigt Internet, aber kein myUplink-Konto.
   Die lokale Gerätebedienung benötigt diesen Download nicht. Es gibt keine automatische Update-Abfrage.
2. Alternativ eine vorhandene, für deinen Regler bestimmte `.swu`-Datei bereitstellen.
   ZIPs der offiziellen Quelle werden begrenzt gelesen; es wird nur eine eindeutige SWU ausgewählt.
   Archivpfade werden niemals auf das Dateisystem entpackt. Maximale Datei-/Downloadgröße: 512 MiB.
3. Name, Größe und SHA-256 prüfen. Die bereitgestellte Datei kann auch auf deinen Rechner
   heruntergeladen werden. Bis hier wird nichts an der WP geändert.
4. Änderungen bewusst entsperren. Die zusätzliche Update-Bestätigung aktiv markieren und
   „Firmware an WP übertragen“ wählen. Die WP muss `features.fwUpdate=true` melden.
5. Der Fortschritt zeigt nur die Dateiübertragung. Nach Annahme durch die WP den Installationszustand
   am Gerätebildschirm prüfen. Ein Neustart/Verbindungsabbruch kann zum Update gehören; die App
   wiederholt nichts automatisch. Während des Updates die Stromversorgung nicht unterbrechen.

Die Datei wird auf CPIO/SWU-Grundstruktur mit erstem Eintrag `sw-description` geprüft. Die
Prüfsumme erkennt Änderungen nach dem Bereitstellen, bestätigt aber weder Herstellersignatur
noch Gerätekompatibilität. Der öffentliche S-Regler-Download ist kein automatischer Modellabgleich.
Es gibt keine automatische Downgrade-/Rollback-Funktion. Derselbe Übertragungsversuch ist innerhalb
derselben Schreibfreigabe gesperrt; vor einem neuen Versuch WP-Zustand prüfen und bewusst neu freigeben.
Bereitgestellte Firmware liegt unter `/data/firmware`, unverschlüsselt (sie ist kein Zugangstoken),
mit beschränkten Dateirechten. Schlüssel und Kopplungsdaten bleiben unverändert verschlüsselt.
Beim Staging oder Download bleibt die alte gültige Datei erhalten, falls die neue Übertragung ungültig ist.
„Datei aus der App entfernen“ gibt den Speicher wieder frei. Während einer Übertragung App und HA
nicht neu starten. Ingress-Streaming ist für große Dateien eingeschaltet; kleine Formulare und
große Uploads werden beide begrenzt und auf eindeutige Nachrichtenlängen geprüft.

Das originale Hydraulikbild und unbekannte grafische Zeitpläne sind noch keine vollständige
PRO-Nachbildung. Die App zeigt verfügbare Anlagen-/Custom-Daten transparent und erfindet keine
hydraulischen Verbindungen. Einzelheiten: `docs/LOCAL_API.md` im Projekt-Repository.


## Schematische Anlage ab 0.6.1

„Anlage“ zeigt aus verfügbaren lokalen Betriebswerten eine anklickbare Funktionsübersicht.
Wärmequelle, Heizkreis, Warmwasser und Kühlung erscheinen nur mit tatsächlich gelieferten Werten.
Die gepunkteten Linien zeigen Funktionszuordnung, keine hydraulischen Rohre oder nachgewiesene
Energieflüsse. Das originale Hydraulikbild ist auf der untersuchten NIBE-Firmware unter dem
beobachteten lokalen systemFlow-Endpunkt nicht verfügbar (HTTP 404).
Firmwareübertragung prüft zusätzlich den Dateinamen gegen die aktuell gemeldete Firmwarefamilie.
Dies ersetzt weder Gerätekompatibilitätsprüfung noch Hersteller-Signaturprüfung.
