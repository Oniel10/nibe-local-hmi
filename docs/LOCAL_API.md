# Lokale PRO-Funktionen und Umsetzungsstand

Stand 0.6.2, experimentell. Grundlage: Protokollanalyse von myUplink PRO 1.42,
APK SHA-256 `efe6ee7eae6c7c3f05d808166f950132b202fe39f5001a0dc6a4bc16cde9d306`.
Die APK sowie dekompilierte Implementierungen und Hersteller-Grafiken werden nicht verteilt.
Die nachfolgende Liste beschreibt beobachtete Protokollfakten und unsere eigene Umsetzung.
Es wird weder eine vollständige API-Dokumentation noch Unterstützung durch jede NIBE-Firmware behauptet.
Alle Gerätepfade unten beginnen mit `/devices/{serial}`; Autorisierung lokal mit `Token`.

| Funktion | Beobachteter lokaler Aufruf | Umsetzung 0.6.2 |
| --- | --- | --- |
| Gerätesuche | GET `/devices`, mDNS | vorhanden |
| Koppeln | POST Gerätepfad mit state und Clientdaten | vorhanden; Name Home Assistant, verschlüsselte Speicherung |
| Status und Fähigkeiten | GET Gerätepfad | Übersicht, Fähigkeiten und maskierte JSON-Ansicht |
| Menübaum/Betriebswerte | GET `/menu/{id}` | vorhanden, verschachtelte Zeilen, Ein-Schritt-Zurück |
| Parameter ändern | PATCH `/points`, Map aus ID zu Rohwert-String | validierte Zahlen, Auswahl, Boolean, Text und Uhrzeit in Sekunden |
| Einzelnen Parameter lesen | GET `/points/{id}` | nur mit sichtbarem eindeutigem, nicht geheimem Menüparameter |
| Uhr/Zeitzonen | GET/PATCH `/datetime`, GET `/timezones` | eigene Ansicht mit aktiver Bestätigung; lokale ISO-Zeit, utc=0 |
| Meldungen | GET/DELETE `/notifications` | anzeigen/quittieren; kein zugesicherter vollständiger Verlauf |
| Geräteaktion | POST `/events/{eventId}` | nur frische sichtbare/aktive uievent-Zeile, eigene Bestätigung |
| Menüassistent | GET/DELETE `/menuchain/{id}` | ausdrücklich öffnen/beenden, sichtbare Schritte; keine automatische Schritt-Ausführung |
| WLAN | GET `/wifi-networks`, PATCH Gerätepfad | Suche und open/psk/IEEE8021X mit Passworteingabe |
| Mobilfunk | PATCH Gerätepfad `{auth: cellular}` | nur bei gemeldeter cellular-Fähigkeit, aktive Bestätigung |
| Not-/Hilfsbetrieb | POST `/aidmode` | on/off bei bekanntem Geräte-Zustand, aktiver Bestätigung und Zustandsprüfung |
| Firmware übertragen | POST `/firmware`, Multipart-Feld `file` | SWU-Staging, Hash, getrennte Freigabe, Fortschritt, keine Wiederholung |
| Parameterkatalog | GET `/haystack` | begrenzte Metadatenansicht für TRIO-artige Daten; kein geheimer Rohdatenexport |
| Anlagenschema | GET `/systemFlow` | verfügbare Antwortdaten plus schematische Funktionsgruppen aus echten Betriebswerten; kein Original-Hydraulikbild |
| Offline-/Cloud-Verbindungstoken | GET/POST `/offlinetoken`, `/token` | bewusst ausgeschlossen: Cloud-Kopplung außerhalb des lokalen Bedienumfangs |

## Firmware-Download

Die lokale API der APK enthält einen Firmware-Upload, keinen allgemeinen Firmware-Downloadkatalog.
Die PRO-App lädt Firmware mit einem Cloud-Bearer-Token. Dieser Cloud-Anmeldeweg wird hier nicht verwendet.
Stattdessen kann der Nutzer ausdrücklich die öffentliche [NIBE-Österreich-Downloadseite](https://www.nibe.at/software)
aufrufen bzw. den S-Regler-Download in der App starten. Die App ermittelt den Downloadlink auf dieser Seite;
sie kodiert keine vermeintlich neueste Versionsnummer fest. Nur HTTPS auf www.nibe.at, keine Weiterleitungen,
keine Tokens oder Cloud-Anmeldung. Alternativ eine vorhandene SWU-Datei hochladen.

Geprüft wurden der reale öffentliche Downloadlink und der Dateianfang eines dort angebotenen ZIPs:
CPIO 070702 mit erstem Eintrag sw-description. Vollständiges Herunterladen/Entpacken, Staging,
Export und Multipart-Übertragung wurden mit synthetischen Dateien und einer HTTPS-Test-WP geprüft.
Es wurde für diese Validierung keine Firmware auf echte Heiztechnik übertragen.

Dateiformat und Hash bestätigen keine Modellkompatibilität oder Herstellersignatur. Die WP muss
den Inhalt selbst prüfen. Nach Annahme am Display kontrollieren: die lokale PRO-API bietet hier
keinen separat beobachteten Installationsfortschritt oder Rollback-Befehl.

## Grafische und unbekannte Sonderbildschirme

Die APK kennt zusätzliche Zeilentypen und Custom-Menükennungen. Ihr Vorhandensein bedeutet nicht,
dass die PRO-App diese lokal vollständig bedient. Beispielsweise blendet ihr Konverter mehrere
Custom-Seiten aus; die Datums-/Uhrzeitseite hat die Kennung 60015. Der analysierte lokale
SystemFlow-Aufruf nutzt sogar die Herstellerzuordnung CLIMATEMASTER und einen festen Parameter.
Diese Werte werden nicht blind auf NIBE übertragen. Der vorhandene Endpunkt kann bei NIBE fehlen.

Heizkurvenwerte bleiben über normale Menüzeilen bedienbar. Ein Diagramm wird nur aus expliziten,
numerischen x/y-Punkten gezeichnet, niemals aus einer geratenen Heizkurvenformel. Andere
Diagrammformate, grafische Zeitpläne, unbekannte Custom-Aktionen und das vollständige Hydraulikbild
sind offen. Dafür werden lokale, geheimnisbereinigte Beispielantworten und Tests je Format benötigt.
Die Funktionsansicht in der App nennt diese Grenzen ausdrücklich.

## Was nicht lokal nachgebildet wird

Cloud-Konten, Cloud-Verbindungsfreigaben, zentrale Anlagenverwaltung, Abonnements,
Cloud-Verläufe/Cloud-Zeitpläne, Cloud-Pushmeldungen und Cloud-Firmware-Steuerung gehören nicht
zum Umfang. Ein vollständig lokaler Betrieb kann weiterhin die Internetverbindung der WP getrennt halten;
der optionale öffentliche Firmware-Download benötigt Internet nur auf dem HA-Host.

Netzwerkänderungen, Notbetrieb und Firmware sind neue, nur gegen Mocks validierte Funktionen.
Geräte-Fähigkeiten sind Voraussetzungen, aber keine Garantie für eine bestimmte Firmwareversion.


## Beim echten lesenden Abgleich bestätigt

Die API verwendet die JSON-Feldnamen `alarms` und `networks` (die gleichnamigen APK-Modellfelder
heißen intern alarmList/wifiNetworks). Diese SerializedName-Zuordnungen werden in der App beachtet.
Die konkrete untersuchte NIBE-Firmware liefert für `/systemFlow` HTTP 404 und für die Heiz-/Kühlkurven
normale Einstellungszeilen, aber keine expliziten x/y-Diagrammpunkte. Die neue Funktionsübersicht
ordnet sichtbare Betriebswerte schematisch an. Ihre gepunkteten Linien sind Datenzuordnungen,
keine hydraulischen Leitungen oder behaupteten aktiven Energieflüsse. Fehlt ein Wert, wird er
nicht erfunden. Rücksprung aus angeklickten Funktionsgruppen bleibt bis nach dem Speichern erhalten.
Der zusätzliche Parameterkatalog ist durchsuchbar und wird mit 200 Einträgen pro Seite angezeigt.
Die Metadatenlesung beachtet beliebig lange Trennzeilen aus Bindestrichen gemäß
[Trio-Format](https://project-haystack.org/doc/docHaystack/Trio). Aktuelle Werte und eingerückte
Mehrzeilen-Inhalte werden nicht übernommen.

Zusätzlich wird vor Firmwareübertragung die frisch gemeldete `product.firmwareId` gegen den
Dateinamen abgeglichen. Das erkennt offensichtliche Familienverwechslungen, beweist aber nicht,
dass eine umbenannte Datei kompatibel oder herstellersigniert wäre. Originaldateien verwenden.
