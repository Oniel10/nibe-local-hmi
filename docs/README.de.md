# NIBE Local HMI

Experimentelle Home-Assistant-App für die lokale Menübedienung kompatibler NIBE-Regler.
Menübaum, Werte und unterstützte Einstellungen erscheinen direkt in Home Assistant.

## Installation

1. Einstellungen → Apps → App installieren → ⋮ → Repositories öffnen.
2. `https://github.com/Oniel10/nibe-local-hmi` hinzufügen.
3. NIBE Local HMI installieren, starten und die Web-Oberfläche öffnen.
4. Gefundene WP auswählen, **Koppeln** drücken und ggf. am WP-Display freigeben.
5. Menü öffnen. Änderungen sind zunächst gesperrt.

Zum Entsperren den Risikohinweis lesen und die leere Checkbox aktiv bestätigen.
Danach sind unterstützte Werte direkt in ihrer Menüzeile bearbeitbar. „Zurück“ geht eine
Menüebene zurück. Passwortfelder sind maskiert; das Auge fordert den Wert separat an.
Nach jedem Neustart ist die Schreibsperre wieder aktiv.

Die Kopplung erfolgt lokal als „Home Assistant“. Das Token wird automatisch bezogen und
AES-256-GCM-verschlüsselt gespeichert. Es muss nicht kopiert werden. Eine lokale Schlüsseldatei
erlaubt den automatischen Neustart: Wer Schlüssel und verschlüsselte Datei gemeinsam lesen
kann, kann entschlüsseln. Backups und HA-Host entsprechend schützen.

**Grenzen:** Experimentell; bislang eine amd64-Installation mit realem Menü-Lesezugriff geprüft.
Die aarch64-Laufzeit und eine breite Geräte-/Firmwareliste sind nicht bestätigt. Die TLS-Zertifikate
der WP werden derzeit nicht geprüft. Nur im vertrauenswürdigen Heimnetz verwenden. Host-Netzwerk
für mDNS und freier Host-Port 8099 erforderlich. Es entstehen keine MQTT-/HA-Sensorentitäten.
Die Menübezeichnungen kommen von der WP; die Bedienelemente sind derzeit deutsch.

[Ausführliche Dokumentation](../nibe_local/DOCS.md) · [Umstieg von lokaler Installation](MIGRATION.md)
· [Sicherheitsmodell](../SECURITY.md) · [Fehler melden](https://github.com/Oniel10/nibe-local-hmi/issues)

Unabhängiges Projekt ohne Verbindung zu NIBE; nur kostenlose, nichtkommerzielle Nutzung unveränderter Versionen; siehe LICENSE.

Lizenz: kostenlose, nichtkommerzielle Nutzung unveränderter Versionen. Verkauf, kommerzielle Nutzung und eigene veränderte Versionen benötigen eine schriftliche Erlaubnis. Kein Open Source. Öffentliche GitHub-Kopien/Forks und zwingende gesetzliche Rechte bleiben unberührt. Maßgeblich ist [LICENSE](../LICENSE). Diese individuelle Lizenz wurde nicht anwaltlich geprüft.


## Neue lokale Ansichten (0.6.0)

Betriebsübersicht, Meldungen, Datum/Uhrzeit, Geräteaktionen, Netzwerk und Firmware sind über
„Übersicht“ erreichbar. Firmware lässt sich ausdrücklich von der öffentlichen NIBE-Österreich-Seite
herunterladen oder als vorhandene SWU bereitstellen. Erst ein getrennt bestätigter Vorgang bei
entsperrten Änderungen überträgt sie lokal zur WP. Die Übertragung ist kein Nachweis für eine
abgeschlossene Installation; das WP-Display prüfen. Der Download benötigt Internet, kein Cloud-Konto.

Noch keine vollständige PRO-Nachbildung: siehe [Funktionsmatrix und Grenzen](LOCAL_API.md).
