"""Local HMI views and guarded operations, independently implemented from protocol facts."""
import copy
from datetime import datetime
import gzip
import hashlib
import html
import json
import re
import secrets
import threading
import time
import urllib.parse
import nibe_firmware as firmware
from nibe_http import BodyReader

APP = None
READS = {'device': '', 'alarms': '/notifications', 'clock': '/datetime',
         'timezones': '/timezones', 'flow': '/systemFlow', 'network': '/wifi-networks'}
PAGES = [('overview', 'Übersicht'), ('alarms', 'Meldungen'), ('flow', 'Anlage'),
         ('clock', 'Datum & Uhrzeit'), ('network', 'Netzwerk'), ('firmware', 'Firmware'),
         ('capabilities', 'Funktionen')]
CHAIN_LOCK = threading.RLock()
ACTIVE_CHAINS = {}


def bind(app):
    global APP
    APP = app


def esc(value):
    return html.escape(str(value), quote=True)


def session():
    with APP.STATE_LOCK:
        s = dict(APP.SESSION or {})
    if not s:
        raise ValueError('Bitte zuerst die Wärmepumpe koppeln.')
    return s


def call(suffix, body=None, method=None, raw=False):
    s = session()
    return APP.api(s['host'], s['port'], '/devices/' + s['serial'] + suffix,
                   s['token'], body=body, method=method, **({'raw': True} if raw else {}))


def safe(value, depth=0):
    """Never reflect credentials from unknown response schemas or nested menu rows."""
    if depth > 20:
        return '[zu tief verschachtelt]'
    if isinstance(value, dict):
        metadata = value.get('metadata')
        secret_row = 'parameterId' in value and APP.is_secret(dict(value, metadata=metadata if isinstance(metadata, dict) else {}))
        result = {}
        for key, child in value.items():
            if re.search(r'password|passwort|passphrase|token|secret|authorization|private.?key', str(key), re.I) and not isinstance(child, bool):
                result[key] = '********'
            elif secret_row and key in ('value', 'dateValue', 'timeValue', 'data', 'metadata'):
                result[key] = '********'
            elif secret_row and key in ('stringValue', 'integerValue', 'pendingValue'):
                result[key] = '********'
            else:
                result[key] = safe(child, depth + 1)
        return result
    if isinstance(value, list):
        return [safe(v, depth + 1) for v in value[:2000]]
    if isinstance(value, str):
        if value.startswith(('https://', 'http://')):
            return value.split('?', 1)[0].split('#', 1)[0]
        return value[:20000]
    return value


def pretty(value):
    return '<pre class="api-data">' + esc(json.dumps(safe(value), ensure_ascii=False, indent=2)) + '</pre>'


def text(value):
    return APP.web.clean(value.get('text')) if isinstance(value, dict) else str(value or '')


def signature(value):
    # An opaque, restart-bound proof; never place a secret initial value in a form.
    import hmac
    return hmac.new(APP.VALUE_KEY, json.dumps(value, sort_keys=True, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()


def form(base, operation, fields, caption, ack=None, mutation=True, page=''):
    page = page or {'notifications-clear': 'alarms', 'clock-save': 'clock', 'wifi-save': 'network',
                    'cellular-save': 'network', 'aidmode': 'overview'}.get(operation, 'firmware')
    with APP.WRITE_LOCK:
        locked, epoch = APP.WRITES_LOCKED, APP.WRITE_EPOCH
    hidden = APP.hidden('csrf', APP.CSRF) + APP.hidden('epoch', epoch) + APP.hidden('operation', operation) + APP.hidden('page', page)
    disabled = ' disabled' if mutation and locked else ''
    if ack:
        fields += '<label class="ack"><input type="checkbox" name="ack" value="yes" required> ' + esc(ack) + '</label>'
    return (f'<form method="post" action="{base}/hmi/action" class="hmi-form">{hidden}'
            f'<fieldset{disabled}>{fields}<button type="submit">{esc(caption)}</button></fieldset></form>')


def nav(base):
    return '<nav class="menu-nav">' + ''.join(f'<a href="{base}/hmi/{key}">{title}</a>' for key, title in PAGES) + f'<a href="{base}/menu/0">Menübaum</a><a href="{base}/setup">Verbindung</a></nav>'


def page(base, key, body):
    title = dict(PAGES).get(key, 'NIBE Local HMI')
    if firmware.JOB.get('state') == 'running' and firmware.JOB.get('operation') == 'upload':
        # Display without waiting for the long transfer's write lock. The POST handler validates again.
        fields = APP.hidden('csrf', APP.CSRF) + APP.hidden('epoch', APP.WRITE_EPOCH) + APP.hidden('menu', '0') + APP.hidden('return_page', key)
        lock = f'<div class="card note"><p>Firmwareübertragung läuft. Kein weiterer Gerätebefehl wird vorgemerkt.</p><form method="post" action="{base}/write/lock">{fields}<button>Nach laufender Übertragung Änderungen sperren</button></form></div>'
    else:
        lock = APP.lock_panel(base, '0').replace(APP.hidden('menu', '0'), APP.hidden('menu', '0') + APP.hidden('return_page', key))
    content = nav(base) + f'<h1>{esc(title)}</h1>' + lock + body
    content += f'<p role="status" id="ui-status"></p><div id="ui-context" data-base="{base}" data-csrf="{APP.CSRF}" hidden></div>'
    content += f'<script defer src="{base}/static/app.js"></script><script defer src="{base}/static/hmi.js"></script>'
    return APP.web.page(title, content).replace('<meta http-equiv=refresh content=30>', '')


def read_page(key):
    try:
        value = call(READS[key])
        if not isinstance(value, dict):
            return None, '<p role="alert">Die WP liefert für diese Ansicht ein unbekanntes Antwortformat.</p>'
        return value, ''
    except APP.web.WpError as exc:
        return None, '<p role="alert">' + esc(exc.msg) + ' Diese Funktion ist möglicherweise in dieser Geräte-Firmware nicht verfügbar.</p>'


def cards(rows, base, mid):
    result = ['<div class="metric-grid">']
    for row in rows:
        if row.get('parameterId') is None or APP.is_secret(row):
            continue
        value = APP.web.fmt_value(row)
        if value is not None:
            result.append(f'<a class="metric card" href="{base}/menu/{mid}?from=overview"><span>{esc(APP.web.label(row))}</span><strong>{esc(value)}</strong></a>')
    result.append('</div>')
    return ''.join(result)


def overview(base):
    device, error = read_page('device')
    body = error
    if isinstance(device, dict):
        product = device.get('product') or {}
        body += '<div class="card note"><h2>' + esc(product.get('name') or product.get('model') or 'Wärmepumpe') + '</h2>'
        labels = {'pairingState': 'Lokale Kopplung', 'onlineState': 'Netzwerk', 'cloudState': 'Vom Gerät gemeldete Cloud-Verbindung',
                  'wifiState': 'WLAN', 'cellularState': 'Mobilfunk', 'aidMode': 'Not-/Hilfsbetrieb'}
        states = {'success': 'Verbunden', 'connected': 'Verbunden', 'disconnected': 'Getrennt', 'online': 'Online',
                  'offline': 'Offline', 'pending': 'Bestätigung ausstehend', 'on': 'Ein', 'off': 'Aus', 'unknown': 'Unbekannt'}
        for key in ('pairingState', 'onlineState', 'cloudState', 'wifiState', 'cellularState', 'aidMode'):
            if key in device:
                body += '<p>' + labels[key] + ': <strong>' + esc(states.get(str(device[key]).lower(), device[key])) + '</strong></p>'
        body += '</div>'
        if device.get('aidMode') in ('on', 'off'):
            fields = APP.hidden('expected', signature(device['aidMode']))
            fields += '<label>Not-/Hilfsbetrieb <select name="aidmode"><option value="off">Aus</option><option value="on">Ein</option></select></label>'
            body += form(base, 'aidmode', fields, 'Not-/Hilfsbetrieb ändern', ack='Ich kenne die Auswirkungen des Not-/Hilfsbetriebs meiner Anlage und möchte diesen Zustand ändern.')
    # These are controller-provided menu IDs on the supported S-series tree; missing menus are shown honestly.
    try:
        menu = APP.get_menu('40')
        body += '<h2>Betriebswerte</h2>' + cards(APP.web.visible_rows(menu), base, '40')
    except APP.web.WpError:
        body += '<p>Betriebsübersicht auf diesem Gerät nicht unter Menü 40 verfügbar. Werte über den Menübaum öffnen.</p>'
    body += '<h2>Weitere Betriebsinformationen</h2><div class="card">'
    try:
        menus = APP.get_menu('38')
        for row in APP.web.visible_rows(menus):
            if row.get('type') == 'uilink' and str(row.get('id', '')).isdigit():
                body += f'<a class="row" href="{base}/menu/{row["id"]}?from=overview">{esc(APP.web.label(row))}</a>'
    except APP.web.WpError:
        body += '<p>Betriebsinformationen über den Menübaum auswählen.</p>'
    body += '</div><details><summary>Geräteinformationen</summary>' + pretty(device) + '</details>'
    return body


def alarms(base):
    data, error = read_page('alarms')
    body = error
    if data is None:
        return body
    items = alarm_items(data)
    if items is None:
        return '<p>Unbekanntes Meldungsformat.</p>' + pretty(data)
    if not items:
        body += '<div class="card note">Die WP meldet aktuell keine Einträge.</div>'
    for item in items[:500]:
        if not isinstance(item, dict):
            continue
        body += '<article class="card note"><h2>' + esc(item.get('header') or 'Meldung') + '</h2>'
        body += '<p>' + esc(item.get('description') or '') + '</p><p>' + esc(item.get('time') or '') + '</p>'
        body += '<p>Gerät: ' + esc(item.get('equipName') or '–') + ' · ID: ' + esc(item.get('alarmId', '–')) + ' · Schweregrad: ' + esc(item.get('severity', '–')) + '</p></article>'
    if items:
        body += form(base, 'notifications-clear', APP.hidden('expected', signature(data)), 'Meldungen quittieren',
                     ack='Ich möchte alle von dieser Schnittstelle gemeldeten Meldungen quittieren/löschen. Das behebt keine Störungsursache.')
    body += '<p>Dies sind die von /notifications gelieferten Meldungen. Ein vollständiger historischer Alarmverlauf wird von dieser Schnittstelle nicht zugesichert.</p>'
    return body


def alarm_items(data):
    if not isinstance(data, dict):
        return None
    value = data.get('alarms', data.get('alarmList'))
    return value if isinstance(value, list) else None


def zones_list(data):
    result = []
    if isinstance(data, dict):
        for region, zones in data.items():
            if isinstance(zones, list):
                for zone in zones:
                    if isinstance(zone, str) and len(region) + len(zone) < 160:
                        result.append((str(region), zone))
    return result


def clock(base):
    data, error = read_page('clock')
    zones, zone_error = read_page('timezones')
    if not isinstance(data, dict):
        return error or '<p>Kein unterstütztes Datumsformat.</p>'
    body = '<div class="card note"><p>Lokale WP-Zeit: <strong>' + esc(data.get('dateTimeLocal', '–')) + '</strong></p><p>UTC: ' + esc(data.get('dateTimeUTC', '–')) + '</p></div>'
    choices = zones_list(zones)
    if not choices:
        return body + zone_error + '<p>Ohne gültige Zeitzonenliste ist die Änderung gesperrt.</p>'
    options = ''.join('<option value="' + esc(r + '/' + z) + '"' + (' selected' if r == data.get('region') and z == data.get('timezone') else '') + '>' + esc(r + '/' + z) + '</option>' for r, z in choices)
    current = str(data.get('dateTimeLocal') or '')[:19]
    fields = APP.hidden('expected', signature({k: data.get(k) for k in ('automaticUpdate', 'region', 'timezone', 'timeFormat')}))
    fields += '<label>Datum und Uhrzeit <input type="datetime-local" name="datetime" step="1" value="' + esc(current) + '" required></label>'
    fields += '<label>Zeitzone <select name="zone">' + options + '</select></label>'
    fields += '<label>Automatisch aktualisieren <select name="automatic"><option value="0"' + (' selected' if data.get('automaticUpdate') == 0 else '') + '>Aus</option><option value="1"' + (' selected' if data.get('automaticUpdate') == 1 else '') + '>Ein</option></select></label>'
    fields += '<label>Zeitformat <select name="timeformat">' + ''.join('<option' + (' selected' if v == data.get('timeFormat') else '') + '>' + v + '</option>' for v in ('24hr', '12hr')) + '</select></label>'
    body += form(base, 'clock-save', fields, 'WP-Zeit speichern', ack='Ich möchte Datum, Uhrzeit und Zeiteinstellungen der WP ändern.')
    return body


def flow(base):
    data, error = read_page('flow')
    body = functional_flow(base)
    if data is None:
        return body + '<details><summary>Originales Anlagenschema</summary>' + error + '<p>Diese WP stellt das originale Hydraulikbild hier nicht lokal bereit.</p></details>'
    body += '<p>Zusätzliche von der lokalen Schnittstelle gelieferte Anlageninformationen. Es werden keine Cloud-Grafiken geladen.</p>'
    if isinstance(data, dict):
        body += '<div class="metric-grid">'
        labels = {'controlUnitName': 'Regler', 'controlUnit': 'Reglerkennung', 'operatingMode': 'Betriebsart', 'controlUnitType': 'Reglertyp'}
        for key in ('controlUnitName', 'controlUnit', 'operatingMode', 'controlUnitType'):
            if key in data:
                body += '<div class="metric card"><span>' + labels[key] + '</span><strong>' + esc(data[key]) + '</strong></div>'
        body += '</div>'
        # Never invent a hydraulic connection or load vendor imageUri URLs from a browser.
        params = data.get('haystackParams')
        if params:
            body += '<h2>Anlagenkomponenten und Parameter</h2>' + pretty(params)
    body += '<p>Das originalgetreue Hydraulikbild benötigt zusätzlich die zum Regler gehörende Grafik und Parameterzuordnung. Solange diese lokal nicht vorliegen, zeigt diese Ansicht die gelieferten Daten ohne erfundene Leitungsverbindungen.</p>'
    return body + '<details><summary>Weitere Anlagendaten</summary>' + pretty(data) + '</details>'


def functional_flow(base):
    """Associate observed S-series operating values; no inferred hydraulic topology or active flows."""
    try:
        rows = APP.web.visible_rows(APP.get_menu('40'))
    except APP.web.WpError:
        return '<p>Keine lokalen Betriebswerte für die Funktionsübersicht verfügbar.</p>'
    by_id = {str(r.get('parameterId')): r for r in rows if r.get('parameterId') is not None and not APP.is_secret(r)}
    def val(pid, label):
        row = by_id.get(str(pid))
        if not row:
            return None
        value = APP.web.fmt_value(row)
        if value is None or value == '–':
            return None
        return label + ': ' + str(value)
    nodes = []
    def node(title, x, y, mid, values):
        lines = [v for v in values if v]
        if lines:
            nodes.append({'title': title, 'x': x, 'y': y, 'mid': mid, 'lines': lines})
    node('Wärmequelle', 5, 165, '41', [val('13', 'Eintritt'), val('14', 'Austritt')])
    node('Außen', 250, 5, '40', [val('4', 'Temperatur')])
    node('Heizkreis', 495, 65, '41', [val('8', 'Vorlauf'), val('10', 'Rücklauf'), val('55002', 'Soll')])
    node('Warmwasser', 495, 215, '42', [val('12', 'Ladung')])
    node('Kühlung', 495, 365, '43', [val('1522', 'Vorlauf'), val('55475', 'Soll')])
    center_lines = [v for v in (val('23675', 'Status'), val('55000', 'Priorität')) if v]
    if not nodes and not center_lines:
        return '<p>Die lokale Betriebsübersicht liefert keine eindeutig zugeordneten Funktionswerte.</p>'
    def box(item, width=220):
        x,y = item['x'],item['y']
        url = f'{base}/menu/{item["mid"]}?from=flow'
        parts = [f'<a href="{url}" aria-label="{esc(item["title"])} öffnen"><rect x="{x}" y="{y}" width="{width}" height="110" rx="12" fill="var(--card)" stroke="var(--line)"/>',
                 f'<text x="{x+12}" y="{y+25}" fill="var(--fg)" font-size="16" font-weight="600">{esc(item["title"])}</text>']
        for index,line in enumerate(item['lines'][:3]):
            # Label/value content is escaped and bounded, never interpreted as SVG markup.
            parts.append(f'<text x="{x+12}" y="{y+49+index*23}" fill="var(--mut)" font-size="13">{esc(line[:31])}</text>')
        parts.append('</a>')
        return ''.join(parts)
    parts = ['<h2>Lokale Funktionsübersicht</h2><p>Aktuelle WP-Betriebswerte, schematisch angeordnet. Linien zeigen die Zuordnung der Funktionsgruppen, keinen hydraulischen Rohrverlauf und keine bestätigten Energieflüsse. Gruppen anklicken, um ihre Werte zu öffnen.</p>',
             '<svg viewBox="0 0 720 490" role="img" aria-label="Schematische Funktionsgruppen mit aktuellen Wärmepumpenwerten">']
    for item in nodes:
        end_x = item['x'] + (220 if item['x'] < 250 else 110 if item['x'] == 250 else 0)
        end_y = item['y'] + (110 if item['x'] == 250 else 55)
        parts.append(f'<path d="M360 235 L{end_x} {end_y}" stroke="var(--line)" stroke-width="3" stroke-dasharray="7 5" fill="none"/>')
    parts.append(box({'title':'Wärmepumpe','x':250,'y':180,'mid':'45','lines':center_lines or ['Status nicht gemeldet']}))
    parts.extend(box(item) for item in nodes)
    parts.append('</svg><p class="mut">Momentaufnahme aus der lokalen Betriebsübersicht. Durch erneutes Öffnen werden die Werte aktualisiert.</p>')
    return ''.join(parts)


def network(base):
    data, error = read_page('network')
    device, device_error = read_page('device')
    body = error + device_error
    if isinstance(data, dict):
        body += '<h2>WLAN-Netze</h2><div class="card">'
        networks = data.get('networks', data.get('wifiNetworks', []))
        if not isinstance(networks, list):
            networks = []
        if not networks:
            body += '<p class="note">Die WP liefert aktuell keine WLAN-Netze.</p>'
        for net in networks[:200]:
            if not isinstance(net, dict):
                continue
            body += '<div class="row"><span>' + esc(net.get('ssid', '')) + '</span><span>' + esc(net.get('auth', '')) + ' · ' + esc(net.get('rssi', net.get('strength', '–'))) + '</span></div>'
        body += '</div>'
    if not isinstance(device, dict):
        return body
    body += '<p>Änderungen am WLAN können die Verbindung zu dieser App unterbrechen. Das WP-Display bleibt der Rückweg zur Netzwerkeinrichtung.</p>'
    features = device.get('features') or {}
    if features.get('cellular') is True:
        body += form(base, 'cellular-save', '', 'Auf Mobilfunk umstellen', ack='Ich möchte die WP auf Mobilfunk umstellen. Die bisherige Netzwerkverbindung kann abbrechen.')
    if features.get('wifiNetworks') is not True:
        return body + '<p>Die WP meldet keine lokale WLAN-Konfigurationsfunktion.</p>'
    fields = '<label>SSID <input name="ssid" maxlength="32" required></label>'
    fields += '<label>Sicherheit <select name="auth"><option value="psk">WPA/WPA2 PSK</option><option value="open">Offen</option></select></label>'
    fields += '<label>WLAN-Passwort <span class="secret-control"><input type="password" name="passphrase" id="wifi-pass" autocomplete="new-password" maxlength="64"><button type="button" data-local-eye="wifi-pass" aria-label="Passwort anzeigen" aria-pressed="false">◉</button></span></label>'
    body += form(base, 'wifi-save', fields, 'WLAN-Konfiguration an WP senden', ack='Ich möchte das WLAN der WP ändern und kann die Verbindung gegebenenfalls am WP-Display wiederherstellen.')
    enterprise = '<label>SSID <input name="ssid" maxlength="32" required></label>'
    enterprise += '<label>EAP-Methode <input name="eap" maxlength="40" required placeholder="Wert gemäß Netzwerkkonfiguration"></label>'
    enterprise += '<label>Phase 2 <input name="phase2" maxlength="40"></label>'
    enterprise += '<label>Identität <input name="identity" maxlength="128" required></label><label>Anonyme Identität <input name="anonymousIdentity" maxlength="128"></label>'
    enterprise += '<label>Passwort <span class="secret-control"><input type="password" name="passphrase" id="eap-pass" maxlength="128" autocomplete="new-password" required><button type="button" data-local-eye="eap-pass" aria-label="Passwort anzeigen" aria-pressed="false">◉</button></span></label>'
    body += '<details><summary>Enterprise-WLAN (IEEE 802.1X)</summary><p>Nur mit den vom Netzwerkadministrator vorgegebenen EAP-/Phase-2-Werten verwenden. Die lokale API bietet hier keine separate Zertifikatsdatei-Übertragung.</p>'
    body += form(base, 'enterprise-save', enterprise, 'Enterprise-WLAN konfigurieren', ack='Ich möchte die IEEE-802.1X-Konfiguration senden und kann den Netzwerkzugang am WP-Display wiederherstellen.', page='network') + '</details>'
    return body


def capabilities(base):
    device, error = read_page('device')
    body = error + '<h2>Vom Gerät gemeldete Fähigkeiten</h2>' + pretty((device or {}).get('features', {}))
    body += '<h2>Lokaler Funktionsumfang</h2><ul>'
    for line in ('Menübaum und Betriebswerte, Zahlen/Text/Auswahl mit WP-Grenzen', 'Meldungen anzeigen und nach Bestätigung quittieren',
                 'Datum/Uhrzeit, Zeitzonen und Zeitformat', 'WLAN-Suche, offene/PSK/Enterprise-Netze und Mobilfunk bei Gerätefreigabe',
                 'Ereignisaktionen aus frischen sichtbaren Menüzeilen', 'Menüassistenten öffnen, besuchen und ausdrücklich beenden',
                 'Firmware bereitstellen, herunterladen und lokal übertragen'):
        body += '<li>' + esc(line) + '</li>'
    body += '</ul><h2>Grenzen und noch offene Funktionen</h2><p>Kein vollständiger Ersatz der PRO-App: freie Hydraulikgrafik, unbekannte Custom-Menüs, grafische Zeitpläne und Heizkurven benötigen zusätzliche lokale Datenformate. Vorhandene Werte bleiben im Menübaum sichtbar und bearbeitbar, soweit validierbare Metadaten vorliegen.</p>'
    body += '<p>Kein Cloud-Konto, Cloud-Token, Fernwartungs- oder Cloud-Zeitplan-API. Keine Cloud-Firmware-Abfrage. Firmware-Download ausschließlich durch ausdrücklichen Aufruf der öffentlichen NIBE-Seite.</p>'
    body += '<p><a href="' + base + '/hmi/parameters">Zusätzlichen lokalen Parameterkatalog lesen</a></p>'
    body += f'<form method="get" action="{base}/hmi/point"><label>Menü-ID <input name="menu" inputmode="numeric" pattern="[0-9]{{1,9}}" required></label><label>Parameter-ID <input name="parameter" pattern="[A-Za-z0-9._-]{{1,100}}" required></label><button>Einzelnen Parameter live lesen</button></form>'
    return body


def catalog_metadata(raw):
    """Read root-level Trio metadata only. Dashes of any length delimit records."""
    allowed = {'id', 'dis', 'displayName', 'unit', 'kind', 'minVal', 'maxVal', 'writable', 'point', 'sensor', 'cmd', 'equipRef'}
    entries = []
    record = {}
    for line in raw.decode('utf-8').splitlines():
        if re.fullmatch(r'-+', line.strip()):
            if record:
                entries.append(record)
            record = {}
        else:
            # Indented lines may contain arbitrary multi-line strings or nested data, including secrets.
            if not line or line[0].isspace() or line.startswith('//'):
                continue
            key, sep, value = line.partition(':')
            key = key.strip()
            if key in allowed:
                if key == 'id' and 'id' in record:
                    entries.append(record)
                    record = {}
                value = value.strip()[:500] if sep else True
                if isinstance(value, str):
                    if value.startswith('"'):
                        try:
                            decoded = json.loads(value)
                            if isinstance(decoded, str):
                                value = APP.web.clean(decoded)
                        except ValueError:
                            pass
                    elif value in ('T', 'true'):
                        value = True
                    elif value in ('F', 'false'):
                        value = False
                record[key] = value
    if record:
        entries.append(record)
    return entries


def parameters(base='', query=None):
    raw = call('/haystack', raw=True)
    if raw[:2] == b'\x1f\x8b':
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:
            raw = f.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError('Parameterkatalog zu groß.')
    entries = catalog_metadata(raw)
    if not entries:
        return '<p>Der Katalog verwendet ein bisher unbekanntes Format. Rohdaten werden nicht angezeigt, da sie Geheimnisse enthalten könnten.</p>'
    query = query or {}
    term, number = single(query, 'q'), single(query, 'p', '1')
    if len(term) > 80 or not re.fullmatch(r'\d{1,6}', number):
        raise ValueError('Ungültige Katalogsuche.')
    total = len(entries)
    if term:
        entries = [r for r in entries if any(term.casefold() in str(r.get(key, '')).casefold() for key in ('id', 'dis', 'displayName', 'unit'))]
    pages = max(1, (len(entries) + 199) // 200)
    current = min(max(int(number), 1), pages)
    start = (current - 1) * 200
    body = '<h2>Lokaler Parameterkatalog</h2><p>Nur Metadaten, keine geheimen oder ungeprüften Rohwerte.</p>'
    body += f'<form method="get" action="{base}/hmi/parameters"><label>Bezeichnung oder ID <input name="q" maxlength="80" value="{esc(term)}"></label><button>Suchen</button></form>'
    body += f'<p>{len(entries)} Treffer aus {total} Katalogeinträgen · Seite {current} von {pages}</p><div class="catalog"><table><thead><tr><th>ID</th><th>Bezeichnung</th><th>Einheit</th><th>Bereich</th><th>Schreibbar</th></tr></thead><tbody>'
    for r in entries[start:start+200]:
        writable = 'Ja' if r.get('writable') is True else 'Nein' if r.get('writable') is False else '–'
        bounds = str(r.get('minVal', '–')) + ' … ' + str(r.get('maxVal', '–'))
        body += '<tr>' + ''.join('<td>'+esc(v)+'</td>' for v in (r.get('id', ''), r.get('dis') or r.get('displayName') or '', r.get('unit', ''), bounds, writable)) + '</tr>'
    body += '</tbody></table></div><nav class="menu-nav">'
    for target, caption in ((current-1, 'Vorherige Seite'), (current+1, 'Nächste Seite')):
        if 1 <= target <= pages:
            url = base + '/hmi/parameters?' + urllib.parse.urlencode({'q':term, 'p':target})
            body += '<a href="' + esc(url) + '">' + caption + '</a>'
    return body + '</nav>'


def event_row(row, mid, base, trail):
    if row.get('type') != 'uievent' or not re.fullmatch(r'\d{1,18}', str(row.get('eventId', ''))):
        return None
    fields = APP.hidden('menu', mid) + APP.hidden('trail', ','.join(trail)) + APP.hidden('event', row['eventId']) + APP.hidden('expected', signature(row))
    warning = text(row.get('warnHeader')) + ' ' + text(row.get('warnText'))
    if warning.strip():
        fields += '<p role="note">' + esc(warning.strip()) + '</p>'
    if row.get('enabled') is False:
        return '<div class="row">' + esc(APP.web.label(row)) + ' · Von der WP deaktiviert</div>'
    return '<div class="row"><span>' + esc(APP.web.label(row)) + '</span>' + form(base, 'event', fields, 'Aktion ausführen', ack='Ich möchte diese Geräteaktion ausdrücklich ausführen.', page='overview') + '</div>'


def chart(row):
    """Render explicit numeric points only; never guess a heating curve formula."""
    data = row.get('data')
    pts = data.get('points') if isinstance(data, dict) else None
    if not isinstance(pts, list) or not 2 <= len(pts) <= 500:
        return '<div class="row">' + esc(APP.web.label(row)) + ' · Diagrammdatenformat noch nicht unterstützt</div>'
    try:
        xy = [(float(APP.number(p['x'])), float(APP.number(p['y']))) for p in pts]
    except (ValueError, KeyError, TypeError):
        return '<p>Diagrammpunkte ungültig.</p>'
    lo_x, hi_x = min(x for x,y in xy), max(x for x,y in xy)
    lo_y, hi_y = min(y for x,y in xy), max(y for x,y in xy)
    coords = ' '.join('%.2f,%.2f' % (40 + (x-lo_x)/(hi_x-lo_x or 1)*520, 210-(y-lo_y)/(hi_y-lo_y or 1)*180) for x,y in xy)
    return ('<figure><figcaption>' + esc(APP.web.label(row)) + '</figcaption>'
            '<svg viewBox="0 0 600 250" role="img" aria-label="' + esc(APP.web.label(row)) + '">'
            '<path d="M40 25V210H570" fill="none" stroke="currentColor"/><polyline points="' + coords + '" fill="none" stroke="var(--acc)" stroke-width="3"/>'
            f'<text x="40" y="240" fill="currentColor">{lo_x:g}</text><text x="520" y="240" fill="currentColor">{hi_x:g}</text>'
            f'<text x="0" y="210" fill="currentColor">{lo_y:g}</text><text x="0" y="30" fill="currentColor">{hi_y:g}</text></svg></figure>')


def menu_extra(mid, menu, base, trail):
    extra = ''
    for key in ('intro', 'note'):
        if text(menu.get(key)):
            extra += '<p class="note">' + esc(text(menu[key])) + '</p>'
    if str(menu.get('menuTypeId')) == '60015':
        extra += '<p><a class="button" href="' + base + '/hmi/clock">Datum und Uhrzeit öffnen</a></p>'
    if menu.get('data'):
        extra += '<details><summary>Zusätzliche lokale Bildschirmdaten</summary>' + pretty(menu['data']) + '</details>'
    return extra


def single(form_data, key, default=''):
    values = form_data.get(key, [default])
    if len(values) != 1:
        raise ValueError('Mehrdeutiges Formularfeld.')
    return values[0]


def perform(operation, fields, base):
    """Called after CSRF validation; every WP mutation is serialized with the write lock."""
    page_key = single(fields, 'page', 'overview')
    if page_key not in dict(PAGES):
        raise ValueError('Ungültige Zielseite.')
    destination = base + '/hmi/' + page_key
    if single(fields, 'ack') != 'yes':
        raise ValueError('Bitte den jeweiligen Hinweis aktiv bestätigen.')
    if operation == 'firmware-download':
        firmware.start_job(APP, 'download')
        return destination
    if operation == 'firmware-remove':
        if not firmware.JOB_LOCK.acquire(blocking=False):
            raise ValueError('Firmware-Vorgang läuft bereits.')
        try:
            with firmware.STAGE_LOCK:
                meta = firmware.staged(APP)
                if not meta or not secrets.compare_digest(meta['sha256'], single(fields, 'sha256')):
                    raise ValueError('Bereitgestellte Datei wurde inzwischen geändert.')
                directory = firmware.folder(APP)
                (directory / 'staged.json').unlink(missing_ok=True)
                (directory / 'staged.swu').unlink(missing_ok=True)
        finally:
            firmware.JOB_LOCK.release()
        return destination
    if firmware.JOB.get('state') == 'running' and firmware.JOB.get('operation') == 'upload':
        raise PermissionError('Während der Firmwareübertragung sind weitere Geräteänderungen gesperrt.')
    with APP.WRITE_LOCK:
        if APP.WRITES_LOCKED or not secrets.compare_digest(single(fields, 'epoch'), APP.WRITE_EPOCH):
            raise PermissionError('Änderungen sind gesperrt oder die Freigabe ist abgelaufen.')
        session()
        if operation == 'event':
            mid, event = single(fields, 'menu'), single(fields, 'event')
            if not re.fullmatch(r'\d{1,9}', mid) or not re.fullmatch(r'\d{1,18}', event):
                raise ValueError('Ungültige Geräteaktion.')
            rows = [r for r in APP.web.visible_rows(APP.get_menu(mid, fresh=True)) if r.get('type') == 'uievent' and str(r.get('eventId')) == event and r.get('enabled') is not False]
            if len(rows) != 1 or not secrets.compare_digest(single(fields, 'expected'), signature(rows[0])):
                raise ValueError('Geräteaktion wurde inzwischen geändert. Menü erneut öffnen.')
            destination = APP.menu_url(base, mid, APP.trail_list(single(fields, 'trail')), single(fields, 'origin'))
            call('/events/' + event, method='POST')
        elif operation == 'notifications-clear':
            data = call('/notifications')
            if not alarm_items(data) or not secrets.compare_digest(single(fields, 'expected'), signature(data)):
                raise ValueError('Meldungen inzwischen geändert. Erneut prüfen.')
            call('/notifications', method='DELETE')
        elif operation == 'clock-save':
            current = call('/datetime')
            if not isinstance(current, dict):
                raise ValueError('Unbekanntes Datumsformat.')
            expected = signature({k: current.get(k) for k in ('automaticUpdate', 'region', 'timezone', 'timeFormat')})
            if not secrets.compare_digest(single(fields, 'expected'), expected):
                raise ValueError('Zeiteinstellungen inzwischen geändert.')
            zone = single(fields, 'zone')
            choices = {(r + '/' + z): (r, z) for r, z in zones_list(call('/timezones'))}
            if zone not in choices:
                raise ValueError('Zeitzone nicht von der WP angeboten.')
            date_time = single(fields, 'datetime')
            if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?', date_time):
                raise ValueError('Ungültige lokale Datums-/Zeitangabe.')
            value = datetime.fromisoformat(date_time)
            if not 2000 <= value.year <= 2099:
                raise ValueError('Jahr muss zwischen 2000 und 2099 liegen.')
            automatic, time_format = single(fields, 'automatic'), single(fields, 'timeformat')
            if automatic not in ('0', '1') or time_format not in ('12hr', '24hr'):
                raise ValueError('Ungültiges Zeitformat.')
            region, timezone = choices[zone]
            call('/datetime', method='PATCH', body={'automaticUpdate': int(automatic), 'dateTime': value.isoformat(timespec='seconds'), 'region': region, 'timezone': timezone, 'timeFormat': time_format, 'utc': 0})
        elif operation == 'wifi-save':
            device = call('')
            if (device.get('features') or {}).get('wifiNetworks') is not True:
                raise ValueError('WLAN-Konfiguration wird nicht angeboten.')
            ssid, auth, password = single(fields, 'ssid'), single(fields, 'auth'), single(fields, 'passphrase')
            if not 1 <= len(ssid.encode('utf-8')) <= 32 or any(ord(c) < 32 for c in ssid):
                raise ValueError('Ungültige SSID.')
            if auth not in ('open', 'psk'):
                raise ValueError('Unbekannte WLAN-Sicherheit.')
            body = {'ssid': ssid, 'auth': auth}
            if auth == 'psk':
                if not (8 <= len(password) <= 63 and all(32 <= ord(c) <= 126 for c in password) or re.fullmatch(r'[a-fA-F0-9]{64}', password)):
                    raise ValueError('PSK benötigt 8–63 ASCII-Zeichen oder 64 Hex-Zeichen.')
                body['passphrase'] = password
            elif password:
                raise ValueError('Für ein offenes WLAN kein Passwort eingeben.')
            call('', method='PATCH', body=body)
        elif operation == 'aidmode':
            device = call('')
            state = single(fields, 'aidmode')
            if state not in ('on', 'off') or device.get('aidMode') not in ('on', 'off') or not secrets.compare_digest(single(fields, 'expected'), signature(device['aidMode'])):
                raise ValueError('Not-/Hilfsbetrieb nicht verfügbar oder inzwischen geändert.')
            call('/aidmode', method='POST', body={'aidMode': state})
        elif operation == 'cellular-save':
            device = call('')
            if (device.get('features') or {}).get('cellular') is not True:
                raise ValueError('Mobilfunk wird von diesem Gerät nicht angeboten.')
            call('', method='PATCH', body={'auth': 'cellular'})
        elif operation == 'enterprise-save':
            device = call('')
            if (device.get('features') or {}).get('wifiNetworks') is not True:
                raise ValueError('WLAN wird von diesem Gerät nicht angeboten.')
            body = {'auth': 'IEEE8021X'}
            for key in ('ssid', 'eap', 'phase2', 'identity', 'anonymousIdentity', 'passphrase'):
                value = single(fields, key)
                maximum = 32 if key == 'ssid' else 40 if key in ('eap', 'phase2') else 128
                if len(value.encode('utf-8')) > maximum or any(ord(c) < 32 for c in value):
                    raise ValueError('Ungültige Enterprise-WLAN-Eingabe.')
                if key in ('ssid', 'eap', 'identity', 'passphrase') and not value:
                    raise ValueError('Erforderliche Enterprise-WLAN-Eingabe fehlt.')
                if key in ('eap', 'phase2') and value and not re.fullmatch(r'[A-Za-z0-9._=-]{1,40}', value):
                    raise ValueError('Ungültiges EAP-/Phase-2-Format.')
                body[key] = value
            call('', method='PATCH', body=body)
        elif operation == 'firmware-upload':
            expected = single(fields, 'sha256')
            if not re.fullmatch(r'[a-f0-9]{64}', expected):
                raise ValueError('Ungültige Firmware-Prüfsumme.')
            firmware.start_job(APP, 'upload', sha256=expected, epoch=APP.WRITE_EPOCH)
        elif operation == 'chain-open':
            mid, chain = single(fields, 'menu'), single(fields, 'chain')
            if not re.fullmatch(r'\d{1,9}', mid) or not re.fullmatch(r'\d{1,18}', chain):
                raise ValueError('Ungültiger Assistent.')
            rows = [r for r in APP.web.visible_rows(APP.get_menu(mid, fresh=True)) if (r.get('type') == 'uimenuchain' or r.get('isMenuChain')) and str(r.get('id')) == chain and r.get('enabled') is not False]
            if len(rows) != 1 or not secrets.compare_digest(single(fields, 'expected'), signature(rows[0])):
                raise ValueError('Assistent nicht mehr verfügbar.')
            data = call('/menuchain/' + chain)
            if not isinstance(data, dict) or not isinstance(data.get('chain'), list):
                raise ValueError('Unbekanntes Assistentenformat.')
            with CHAIN_LOCK:
                minutes = data.get('timeoutMinutes')
                seconds = min(float(minutes) * 60, 7200) if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and 0 < minutes <= 120 else 60
                ACTIVE_CHAINS[chain] = time.monotonic() + seconds
            destination = base + '/hmi/chain/' + chain
        elif operation == 'chain-close':
            chain = single(fields, 'chain')
            with CHAIN_LOCK:
                if chain not in ACTIVE_CHAINS:
                    raise ValueError('Kein aktiver Assistent.')
                call('/menuchain/' + chain, method='DELETE')
                ACTIVE_CHAINS.pop(chain, None)
        else:
            raise ValueError('Unbekannter Vorgang.')
        APP.web._cache.clear()
    return destination


def chain_row(row, mid, base, trail):
    chain = str(row.get('id', ''))
    if not re.fullmatch(r'\d{1,18}', chain):
        return '<p>Assistent ohne gültige Kennung.</p>'
    fields = APP.hidden('menu', mid) + APP.hidden('chain', chain) + APP.hidden('expected', signature(row))
    return '<div class="row"><span>' + esc(APP.web.label(row)) + '</span>' + form(base, 'chain-open', fields, 'Assistent öffnen', ack='Ich möchte diesen Geräteassistenten öffnen. Er kann den Betriebsablauf beeinflussen.', page='overview') + '</div>'


def chain_page(base, chain):
    with CHAIN_LOCK:
        if chain not in ACTIVE_CHAINS or time.monotonic() >= ACTIVE_CHAINS[chain]:
            ACTIVE_CHAINS.pop(chain, None)
            raise ValueError('Assistent zuerst ausdrücklich im Menü öffnen.')
    data = call('/menuchain/' + chain)
    body = '<h2>' + esc(text(data.get('name')) or 'Geräteassistent') + '</h2><div class="card">'
    for index, item in enumerate(data.get('chain', [])[:100]):
        mid = str(item.get('menuId', ''))
        if item.get('visible') is False or not re.fullmatch(r'\d{1,9}', mid):
            continue
        body += f'<a class="row" href="{base}/menu/{mid}">Schritt {index+1} · Menü {mid}</a>'
    body += '</div><p>Die Reihenfolge und Sichtbarkeit stammen vom Gerät. Nach jedem Schritt hier neu laden.</p>'
    body += form(base, 'chain-close', APP.hidden('chain', chain), 'Assistent ausdrücklich beenden', ack='Ich möchte den laufenden Geräteassistenten beenden.', page='overview')
    return body


def route_get(handler, path, base):
    if not path.startswith('/hmi/'):
        return False
    try:
        if path == '/hmi/firmware/status':
            handler._send(200, json.dumps(firmware.JOB, ensure_ascii=False), 'application/json; charset=utf-8')
            return True
        if path == '/hmi/firmware/file':
            with firmware.STAGE_LOCK:
                meta = firmware.staged(APP, verify=True)
                if not meta:
                    raise ValueError('Keine gültige Firmware bereitgestellt.')
                source = (firmware.folder(APP) / 'staged.swu').open('rb')
            try:
                handler.connection.settimeout(60)
                handler.send_response(200)
                for k,v in {'Content-Type': 'application/octet-stream', 'Content-Length': str(meta['size']), 'Content-Disposition': 'attachment; filename="' + meta['name'] + '"', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}.items():
                    handler.send_header(k,v)
                handler.end_headers()
                if handler.command != 'HEAD':
                    import shutil
                    shutil.copyfileobj(source, handler.wfile, firmware.CHUNK)
            finally:
                source.close()
            return True
        if path.startswith('/hmi/api/'):
            key = path.removeprefix('/hmi/api/')
            match = re.fullmatch(r'menu/(\d{1,9})', key)
            if match:
                handler._send(200, json.dumps(safe(APP.get_menu(match.group(1))), ensure_ascii=False), 'application/json; charset=utf-8')
            elif key not in READS:
                handler._send(404, 'Nicht verfügbar.')
            else:
                handler._send(200, json.dumps(safe(call(READS[key])), ensure_ascii=False), 'application/json; charset=utf-8')
            return True
        key = path.removeprefix('/hmi/')
        if key == 'point':
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query, max_num_fields=2)
            mid, pid = single(query, 'menu'), single(query, 'parameter')
            if not re.fullmatch(r'\d{1,9}', mid) or not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', pid):
                raise ValueError('Ungültiger Parameter.')
            rows = [r for r in APP.web.visible_rows(APP.get_menu(mid, fresh=True)) if str(r.get('parameterId')) == pid]
            if len(rows) != 1 or APP.is_secret(rows[0]):
                raise ValueError('Nur ein eindeutiger sichtbarer, nicht geheimer Menüparameter kann hier gelesen werden.')
            body = '<h2>' + esc(APP.web.label(rows[0])) + '</h2>' + pretty(call('/points/' + pid))
            key = 'capabilities'
        elif key.startswith('chain/') and re.fullmatch(r'chain/\d{1,18}', key):
            body = chain_page(base, key.split('/')[1])
            key = 'overview'
        elif key == 'parameters':
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query, max_num_fields=2)
            body = parameters(base, query)
            key = 'capabilities'
        elif key == 'firmware':
            body = firmware.page(APP, base)
        elif key in dict(PAGES):
            body = {'overview': overview, 'alarms': alarms, 'clock': clock, 'flow': flow, 'network': network, 'capabilities': capabilities}[key](base)
        else:
            handler._send(404, 'Ansicht nicht gefunden.')
            return True
        handler._send(200, page(base, key, body))
    except (ValueError, OSError, APP.web.WpError) as exc:
        msg = exc.msg if isinstance(exc, APP.web.WpError) else str(exc)
        if isinstance(exc, OSError):
            msg = 'Lokale Daten konnten nicht gelesen werden.'
        code = 502 if isinstance(exc, APP.web.WpError) else 400
        if path.startswith('/hmi/api/'):
            handler._send(code, json.dumps({'error':msg},ensure_ascii=False), 'application/json; charset=utf-8')
        else:
            handler._send(code, page(base, 'overview', '<p role="alert">' + esc(msg) + '</p>'))
    return True


def stage_request(handler):
    if not secrets.compare_digest(handler.headers.get('X-CSRF-Token', ''), APP.CSRF):
        return handler._send(403, 'Formular abgelaufen.')
    if handler.headers.get('Content-Type') != 'application/octet-stream':
        return handler._send(400, 'Ungültige Dateiübertragung.')
    if not firmware.JOB_LOCK.acquire(blocking=False):
        return handler._send(409, 'Firmware-Vorgang läuft bereits.')
    try:
        length = int(handler.headers.get('X-File-Length', handler.headers.get('Content-Length', '0')))
        if not 110 < length <= firmware.MAX_BYTES:
            raise ValueError
        name = urllib.parse.unquote(handler.headers.get('X-Filename', ''))
        handler.connection.settimeout(90)
        stream = BodyReader(handler, length)
        meta = firmware.stage_stream(APP, stream, length, name)
        return handler._send(200, json.dumps(meta), 'application/json; charset=utf-8')
    except (ValueError, OSError):
        return handler._send(400, 'Keine gültige, vollständige .swu-Datei (maximal 512 MiB).')
    finally:
        firmware.JOB_LOCK.release()
