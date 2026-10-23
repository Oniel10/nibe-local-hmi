# Changelog

## 0.6.5 — Firmware-Antwort und bestätigter neuer Versuch

- Nach fehlgeschlagenem Firmware-Upload „Erneut versuchen?“ anbieten. Erst nach aktiver Bestätigung und Prüfung am WP-Display startet genau ein neuer Versuch. Keine automatische Wiederholung.
- Wiederholungsfreigaben sind einmalig und an den fehlgeschlagenen Vorgang und die Datei gebunden; Änderungssperre, CSRF, Prüfsumme und Geräteprüfung bleiben aktiv.
- HTTP-Antwort nach dem Senden abwarten und vollständige JSON-Struktur mit maskierten Zugangsdaten anzeigen, einschließlich bislang unbekannter Felder.
- Transportfehler mit Fehlerart, Phase, Laufzeit und gesendeter Datenmenge erfassen; eine eventuell vorliegende frühe WP-Antwort aus derselben Verbindung lesen.
- HTTP 2xx bestätigt keine abgeschlossene Installation. HTTP 4xx ist eine Ablehnung und wird nicht automatisch wiederholt.
- Lokaler HA-Test: vollständige Übertragung mit HTTP 200; anschließendes WP-Update von 4.13.12 auf 4.14.6 vom Nutzer bestätigt. Ein zuvor sporadischer Verbindungsabbruch bleibt ungeklärt.
- Wiederholungsdialog, Einmalfreigabe und JSON-Maskierung gegen Mock geprüft; keine zusätzliche reale Firmwareübertragung durch diese Änderung.

## 0.6.4 — Erneute Kopplung bei abgewiesenem Token

- Unter Verbindung eine ausdrücklich bestätigte neue Kopplungsanfrage an die gespeicherte WP ermöglichen, auch wenn bereits ein Token gespeichert ist.
- Bei HTTP 401 auf die erneute Kopplung hinweisen; Netzwerkfehler und andere Ablehnungen nicht als nachgewiesenen Token-Ablauf darstellen.
- Beim erneuten Koppeln Änderungen sperren und alte Schreibformulare ungültig machen. Neue Kopplungsdaten weiterhin verschlüsselt speichern.
- Scheitert die Anfrage, bleiben die bisherigen Kopplungsdaten erhalten. Keine automatischen Kopplungs- oder Schreibwiederholungen.
- Abgewiesenes Token, fehlgeschlagene und erfolgreiche neue Kopplungsanfrage gegen simulierte WP getestet; keine Änderungen an einer realen HA-Installation.

## 0.6.3 — Hinweis zur WLAN-Aktivierung

- Direkt bei der WLAN-Suche erklären, dass WLAN zuvor manuell am WP-Display unter Menü 5.2.1 aktiviert werden muss.
- Die Suche aktiviert WLAN nicht; nach dem Einschalten auf Netzwerke am Display warten und die Suche erneut starten.

## 0.6.2 — WLAN-Suche und GitHub-Releases

- WLAN-Ergebnisse wiederholt über den bestätigten lokalen GET-Endpunkt abfragen; leere Zwischenantworten beenden die Suche nicht mehr sofort.
- Suchstatus und erneute Suche ohne Verlust der Formulareingaben; vorhandene Ergebnisse bleiben bei leeren Antworten sichtbar.
- Begrenzte, sequenzielle Abfragen, Abbruch beim Verlassen und klare Verbindungsfehler.
- GitHub-Releases nach erfolgreichen Tests und beiden Container-Builds automatisch erstellen.
- Nur mit simulierten WLAN-Antworten geprüft; keine erneute Installation oder Prüfung an einer realen WP.

## 0.6.1 — Controller wire formats and schematic operating view

- Honor actual JSON names alarms/networks while accepting the earlier mock aliases.
- Parse every Trio record delimiter length and provide searchable, paginated parameter metadata without current values or multiline payloads.
- Keep boolean device capabilities visible while masking credential tokens.
- Add an interactive, explicitly schematic functional view from local operating values when the original systemFlow endpoint is unavailable.
- Preserve one-step return navigation from the overview and schematic through menu editing.
- Check the reported firmware family against the SWU filename before transfer; no signature or complete compatibility claim.
- Return bounded JSON errors from diagnostic JSON routes.
- HA app update and real read-only controller status/clock/menu access checked; no real device writes or firmware transfer.

## 0.6.0 — Extended local HMI and explicit firmware management

- Operating overview, local notifications, device status, clock/time zones and guarded event actions.
- Explicit menu-assistant lifecycle, nested rows and second-based time editors.
- WLAN scan, open/PSK/IEEE-802.1X configuration, cellular and aid-mode controls when device-supported.
- Public NIBE Austria S-regler download without cloud login; exact SWU staging/export.
- Separately acknowledged local firmware multipart transfer, progress, bounds, SHA-256, stale/replay guards and no automatic retries.
- Local parameter metadata and guarded single-point reads; no secret raw catalog export.
- Exact local API coverage matrix; graphical custom pages remain explicitly incomplete.
- Dedicated HTTPS-mock HMI/firmware regression suite. No firmware sent to real heating equipment for validation.

## 0.5.0 — First public experimental package

- Git-based Home Assistant app repository with installation badge and English/German documentation.
- Experimental status, explicit compatibility and security limitations, non-commercial, no-modification licensing and original app artwork.
- Reproducible controller mocks, browser eye-button checks and CI build workflows.
- No customer deployment records, pairing credentials or vendor APK content in the distribution.

## 0.4.1 — Private prototype

- NIBE Local HMI name, inline editing, one-level Back navigation, text fields and masked password reveal.
- Server-enforced write lock, active acknowledgement and restart defaults.
- Local pairing and encrypted credential storage inherited from the prototype.
