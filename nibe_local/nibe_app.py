"""HA Ingress setup, local discovery and encrypted pairing for NIBE Local."""
import html
import hashlib
import hmac
from decimal import Decimal, InvalidOperation
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import nibe_web as web
import nibe_hmi as hmi
import nibe_http
import sys

SERVICE = '_nibejpi-restapi._tcp.local.'
AAD = b'NIBE-Local:credentials:v1'
STATE_LOCK = threading.RLock()
PAIR_LOCK = threading.Lock()
CSRF = secrets.token_urlsafe(32)
VALUE_KEY = secrets.token_bytes(32)
DEVICES = {}
STATUS = 'Noch nicht verbunden.'
SCANNING = False
SCAN_ERROR = ''
SESSION = None
STORE = None
API_LOCK = threading.Lock()
WRITE_LOCK = threading.RLock()
WRITES_LOCKED = True
WRITE_EPOCH = secrets.token_urlsafe(24)
DISCLAIMER = ('Änderungen können den Betrieb der Wärmepumpe beeinflussen und bei ungeeigneten '
              'Einstellungen zu erhöhtem Energieverbrauch, Betriebsstörungen oder Schäden führen. '
              'Nimm nur Änderungen vor, deren Auswirkungen du verstehst. Du handelst auf eigene '
              'Verantwortung. Eine Haftung für Folgen der Änderungen wird, soweit gesetzlich '
              'zulässig, ausgeschlossen.')
ACKNOWLEDGEMENT = ('Ich habe den Hinweis gelesen, verstehe die Risiken und möchte Änderungen '
                   'auf eigene Verantwortung freigeben.')


class StorageError(Exception):
    pass


class EncryptedStore:
    """One random installation key; no credential plaintext is written to disk."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.keyfile = self.directory / 'keys' / 'credentials.key'
        self.cipherfile = self.directory / 'credentials.enc'
        self.lock = threading.RLock()

    @staticmethod
    def private_dir(path):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)

    def key(self, create=False):
        self.private_dir(self.directory)
        self.private_dir(self.keyfile.parent)
        if self.keyfile.exists():
            self.keyfile.chmod(0o600)
            key = self.keyfile.read_bytes()
            if len(key) != 32:
                raise StorageError('Schluesseldatei ist beschaedigt.')
            return key
        if not create or self.cipherfile.exists():
            raise StorageError('Schluesseldatei fehlt. Vorhandene Daten bleiben erhalten.')
        key = AESGCM.generate_key(bit_length=256)
        fd = os.open(self.keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(key)
            f.flush()
            os.fsync(f.fileno())
        return key

    def save(self, session):
        with self.lock:
            key = self.key(create=True)
            nonce = secrets.token_bytes(12)
            payload = json.dumps(session, ensure_ascii=False).encode('utf-8')
            blob = b'NBL1' + nonce + AESGCM(key).encrypt(nonce, payload, AAD)
            temp = self.directory / ('credentials.' + secrets.token_hex(8) + '.tmp')
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as f:
                    f.write(blob)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp, self.cipherfile)
                self.cipherfile.chmod(0o600)
            finally:
                temp.unlink(missing_ok=True)

    def load(self):
        with self.lock:
            if not self.cipherfile.exists():
                return None
            try:
                blob = self.cipherfile.read_bytes()
                if blob[:4] != b'NBL1' or len(blob) < 32:
                    raise ValueError
                self.cipherfile.chmod(0o600)
                raw = AESGCM(self.key()).decrypt(blob[4:16], blob[16:], AAD)
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError
                return value
            except StorageError:
                raise
            except Exception:
                raise StorageError('Gespeicherte Kopplung kann nicht entschluesselt werden. Daten bleiben erhalten.') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_host(host):
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        raise ValueError('Bitte eine lokale IPv4-Adresse eingeben.') from None
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
        raise ValueError('Bitte eine private IPv4-Adresse der Waermepumpe eingeben.')
    return str(address)


def serial_number(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', value):
        raise ValueError('Keine gueltige Seriennummer gefunden.')
    return value


def api(host, port, path, token=None, body=None, method=None, raw=False):
    """No system proxy, no redirects, bounded response, no upstream body in errors."""
    url = 'https://%s:%d%s' % (local_host(host), int(port), path)
    headers = {'Accept': 'application/json'}
    if token:
        if not isinstance(token, str) or not re.fullmatch(r'[\x21-\x7e]{1,4096}', token):
            raise ValueError('WP lieferte ein ungueltiges Token.')
        headers['Authorization'] = 'Token ' + token
    data = None
    if body is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                         urllib.request.HTTPSHandler(context=web._ctx))
    try:
        with API_LOCK:
            with opener.open(req, timeout=15) as r:
                response_bytes = r.read(2 * 1024 * 1024 + 1)
        if len(response_bytes) > 2 * 1024 * 1024:
            raise ValueError
        if raw:
            return response_bytes
        return json.loads(response_bytes) if response_bytes.strip() else None
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise web.WpError(502, 'WP antwortet mit HTTP %d.' % code) from None
    except (ValueError, OSError, urllib.error.URLError):
        raise web.WpError(502, 'WP nicht erreichbar oder Antwort ungueltig.') from None


def device_list(host, port):
    data = api(host, port, '/devices')
    rows = data.get('devices', []) if isinstance(data, dict) else []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        product = row.get('product') or {}
        try:
            sn = serial_number(product.get('serialNumber'))
        except ValueError:
            continue
        result.append({'host': host, 'port': port, 'serial': sn,
                       'name': str(product.get('name') or 'NIBE')[:120]})
    return result


def remember(device):
    key = device['host'] + ':' + str(device['port']) + ':' + device['serial']
    with STATE_LOCK:
        for cid, old in DEVICES.items():
            if old['host'] + ':' + str(old['port']) + ':' + old['serial'] == key:
                return cid
        cid = secrets.token_urlsafe(16)
        DEVICES[cid] = device
        return cid


def discover():
    global SCANNING, SCAN_ERROR
    with STATE_LOCK:
        if SCANNING:
            return
        SCANNING = True
        SCAN_ERROR = ''
    try:
        from zeroconf import Zeroconf, ServiceBrowser, IPVersion
        class Listener:
            def add_service(self, zc, service_type, name):
                self.resolve(zc, service_type, name)
            def update_service(self, zc, service_type, name):
                self.resolve(zc, service_type, name)
            def remove_service(self, zc, service_type, name):
                pass
            def resolve(self, zc, service_type, name):
                info = zc.get_service_info(service_type, name, timeout=1500)
                if not info:
                    return
                props = {k.decode('utf-8', 'replace'): v.decode('utf-8', 'replace')
                         for k, v in info.properties.items() if v is not None}
                for address in info.parsed_addresses(IPVersion.V4Only):
                    try:
                        host = local_host(address)
                        sn = serial_number(props.get('serialNumber'))
                        remember({'host': host, 'port': int(info.port), 'serial': sn,
                                  'name': props.get('name', 'NIBE')[:120]})
                    except ValueError:
                        continue
        with Zeroconf(ip_version=IPVersion.V4Only) as zc:
            browser = ServiceBrowser(zc, SERVICE, Listener())
            time.sleep(8)
            browser.cancel()
    except Exception:
        with STATE_LOCK:
            SCAN_ERROR = 'Automatische Suche nicht verfuegbar. Du kannst die WP-IP als Ausweichweg eingeben.'
    finally:
        with STATE_LOCK:
            SCANNING = False


def session_status():
    global STATUS
    with STATE_LOCK:
        session = dict(SESSION) if SESSION else None
    if not session:
        return False
    try:
        data = api(session['host'], session['port'], '/devices/' + session['serial'], session['token'])
        state = data.get('pairingState') if isinstance(data, dict) else None
        ready = str(state).lower() == 'success'
        with STATE_LOCK:
            STATUS = 'Verbunden.' if ready else 'Bitte die Kopplung an der Waermepumpe freigeben. Status: ' + (
                str(state) if state in ('pending', 'none', 'invalid', 'PENDING', 'NONE', 'INVALID') else 'noch nicht bestaetigt')
        return ready
    except (web.WpError, ValueError):
        with STATE_LOCK:
            STATUS = 'Verbindung noch nicht bestaetigt. WP und Freigabe pruefen und erneut pruefen.'
        return False


def activate(session):
    global SESSION
    with STATE_LOCK:
        SESSION = session
        web.CFG.update(host=session['host'], port=session['port'], serial=session['serial'], token=session['token'])
        web._cache.clear()


def pair(device):
    global STATUS
    if not PAIR_LOCK.acquire(blocking=False):
        raise ValueError('Eine Kopplung laeuft bereits.')
    try:
        with STATE_LOCK:
            if SESSION:
                raise ValueError('Es besteht bereits eine Kopplung. Erst Status pruefen.')
        # Make sure storage is writable before registering a client on the WP.
        STORE.key(create=True)
        state = secrets.token_hex(16)
        identifier = secrets.token_hex(8)
        sn = serial_number(device['serial'])
        data = api(device['host'], device['port'], '/devices/' + sn + '?state=' + state,
                   body={'identifier': identifier, 'name': 'Home Assistant', 'type': 'Android'})
        if not isinstance(data, dict) or data.get('state') != state:
            raise ValueError('WP lieferte keine passende Kopplungsantwort.')
        token = data.get('token')
        if not isinstance(token, str) or not re.fullmatch(r'[\x21-\x7e]{1,4096}', token):
            raise ValueError('WP lieferte kein gueltiges Token.')
        session = dict(device, token=token, identifier=identifier)
        try:
            STORE.save(session)
        except Exception:
            # Keep token in memory for this run; never write a plaintext fallback.
            activate(session)
            STATUS = 'Gekoppelt, aber verschluessertes Speichern fehlgeschlagen. App nicht neu starten; Datentraeger pruefen.'
            return
        activate(session)
        session_status()
    finally:
        PAIR_LOCK.release()


def get_menu(mid, fresh=False):
    with STATE_LOCK:
        session = dict(SESSION) if SESSION else None
    if not session:
        raise web.WpError(503, 'Bitte zuerst die Waermepumpe koppeln.')
    with web._lock:
        hit = web._cache.get(mid)
        if hit and not fresh and time.monotonic() - hit[0] < web.CFG['cache']:
            return hit[1]
        data = api(session['host'], session['port'], '/devices/' + session['serial'] + '/menu/' + mid, session['token'])
        if not isinstance(data, dict) or not isinstance(data.get('rows', []), list):
            raise web.WpError(502, 'WP lieferte ungueltige Menue-Daten.')
        web._cache[mid] = (time.monotonic(), data)
        return data


web.wp_menu = get_menu


def setup_page(base, message=''):
    e = html.escape
    with STATE_LOCK:
        candidates = list(DEVICES.items())
        connected = SESSION is not None
        status, scanning, scanerror = STATUS, SCANNING, SCAN_ERROR
    def form(action, content):
        return (f"<form method='post' action='{base}/setup/{action}'>"
                f"<input type='hidden' name='csrf' value='{CSRF}'>{content}</form>")
    body = '<h1>NIBE Local HMI</h1><h2>Verbindung einrichten</h2>'
    if message:
        body += '<p role=alert>' + e(message) + '</p>'
    body += '<p>' + e(status) + '</p>'
    if connected:
        body += form('check', '<button>Status pruefen</button>')
        body += f"<p><a href='{base}/menu/0'>WP-Menues oeffnen</a></p>"
        body += '<p>Die Kopplung ist intern gespeichert. Das Token wird hier nicht angezeigt.</p>'
    else:
        body += '<p>Aktiviere gegebenenfalls die lokale Kopplung an deiner Waermepumpe. Suche sie hier und waehle Koppeln. Eine Freigabe am WP-Display kann erforderlich sein.</p>'
        body += form('discover', '<button>Erneut nach Waermepumpen suchen</button>')
        if scanning:
            body += '<p>Suche im Heimnetz laeuft. Die Seite aktualisiert sich automatisch.</p>'
        if scanerror:
            body += '<p>' + e(scanerror) + '</p>'
        for cid, device in candidates:
            title = e(device['name'] + ' · ' + device['serial'] + ' · ' + device['host'])
            body += '<div class=card><div class=note>' + title
            body += form('pair', f"<input type='hidden' name='device' value='{cid}'><button>Koppeln</button>") + '</div></div>'
        if not candidates and not scanning:
            body += '<p>Keine WP gefunden. Sie muss im selben Multicast-Netz erreichbar sein und die lokale API anbieten.</p>'
        body += '<details><summary>Falls die automatische Suche nichts findet</summary><p>Nur die IP eingeben. Seriennummer und Token bezieht die App selbst.</p>'
        body += form('probe', "<label>WP-IP <input name='host' required placeholder='192.168.1.100' maxlength='15'></label> <button>Geraet abrufen</button>") + '</details>'
    body += '<p class=mut>Änderungen sind standardmäßig gesperrt. Kopplungsdaten werden verschlüsselt gespeichert.</p>'
    # Setup has no auto refresh while entering fallback IP; discovery results can be reloaded.
    body += f"<p><a href='{base}/setup'>Ansicht aktualisieren</a></p>"
    result = web.page('NIBE einrichten', body)
    return result.replace('<meta http-equiv=refresh content=30>', '<meta http-equiv=refresh content=10>' if scanning else '')


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Ungültige numerische Metadaten.')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or abs(result) > Decimal('1e18'):
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('Ungültige Zahl.') from None


def rules(row):
    md = row.get('metadata') or {}
    if row.get('enabled') is False:
        raise ValueError('Dieses Feld ist von der WP deaktiviert.')
    if md.get('isWritable') is not True:
        raise ValueError('Dieser Wert ist nicht schreibbar.')
    if row.get('type') not in ('uinumerical', 'uislider', 'uiboolean', 'uidropdown', 'uioptiongroup', 'uisetting', 'uitimesetting'):
        raise ValueError('Dieser Eingabetyp wird noch nicht unterstützt.')
    value = row.get('value') or {}
    if not isinstance(value, dict) or value.get('isOk') is False or not isinstance(value.get('integerValue'), int):
        raise ValueError('Kein gültiger aktueller Zahlenwert verfügbar.')
    div = number(md.get('divisor', 1))
    if div <= 0:
        raise ValueError('Ungültiger Divisor.')
    lo, hi, step = (number(md.get(key)) for key in ('minValue', 'maxValue', 'change'))
    if lo > hi or step <= 0:
        raise ValueError('Grenzen oder Schrittweite fehlen.')
    options = row.get('options') or []
    if options:
        allowed = {number(o.get('value')) for o in options if o.get('enabled') is not False and o.get('visible') is not False and not o.get('hideLocal')}
    elif row.get('type') == 'uiboolean':
        allowed = {Decimal(0), Decimal(1)}
    else:
        allowed = None
    return div, lo, hi, step, allowed


def raw_value(row, entered):
    div, lo, hi, step, allowed = rules(row)
    text = str(entered).strip()
    if row.get('type') == 'uitimesetting':
        if div != 1 or not re.fullmatch(r'\d\d:\d\d(?::\d\d)?', text):
            raise ValueError('Bitte eine gültige Uhrzeit eingeben.')
        components = [int(v) for v in text.split(':')]
        hour, minute, second = components + ([0] if len(components) == 2 else [])
        if hour > 23 or minute > 59 or second > 59:
            raise ValueError('Ungültige Uhrzeit.')
        text = str(hour * 3600 + minute * 60 + second)
    if not re.fullmatch(r'-?\d{1,18}(?:[.,]\d{1,9})?', text):
        raise ValueError('Bitte eine gültige Zahl eingeben.')
    raw = number(text.replace(',', '.')) * div
    if raw != raw.to_integral_value():
        raise ValueError('Der Wert kann nicht exakt als WP-Rohwert dargestellt werden.')
    if raw < lo or raw > hi:
        raise ValueError('Der Wert liegt außerhalb der WP-Grenzen.')
    if (raw - lo) % step != 0:
        raise ValueError('Der Wert passt nicht zur Schrittweite der WP.')
    if allowed is not None and raw not in allowed:
        raise ValueError('Dieser Auswahlwert ist nicht erlaubt.')
    return str(int(raw))


def hidden(name, value):
    return "<input type='hidden' name='%s' value='%s'>" % (html.escape(name), html.escape(str(value), quote=True))


def lock_panel(base, mid):
    with WRITE_LOCK:
        locked, epoch = WRITES_LOCKED, WRITE_EPOCH
    common = hidden('csrf', CSRF) + hidden('menu', mid) + hidden('epoch', epoch)
    if locked:
        return ("<section class='card note'><strong>🔒 Änderungen gesperrt</strong>"
                "<p>Die Werte können angesehen werden. Zum Freigeben ist eine aktive Bestätigung erforderlich.</p>"
                f"<form method='post' action='{base}/write/unlock'>{common}"
                '<details><summary>Änderungen freigeben …</summary>'
                f"<p>{html.escape(DISCLAIMER)}</p><label><input type='checkbox' name='ack' value='yes' required> "
                f"{html.escape(ACKNOWLEDGEMENT)}</label><p><button>Bestätigen und entsperren</button></p>"
                '</details></form></section>')
    return ("<section class='card note'><strong>🔓 Änderungen freigegeben</strong>"
            "<p>Die Freigabe gilt für alle geöffneten Ansichten dieser App bis zum Sperren oder Neustart.</p>"
            f"<form method='post' action='{base}/write/lock'>{common}<button>Änderungen sperren</button></form></section>")


def is_secret(row):
    text = (web.label(row) + ' ' + str((row.get('metadata') or {}).get('type', ''))).lower()
    return str(row.get('parameterId')) == '61601' or bool(re.search(r'password|passwort|passwd|passphrase|secret|kennwort|pin.code', text))


def text_rules(row):
    md = row.get('metadata') or {}
    if row.get('type') != 'uitextsetting' or md.get('isWritable') is not True or row.get('enabled') is False:
        raise ValueError('Dieses Textfeld ist nicht bearbeitbar.')
    value = row.get('value')
    if not isinstance(value, dict) or value.get('isOk') is False or not isinstance(value.get('stringValue'), str):
        raise ValueError('Kein gültiger Textwert verfügbar.')
    maximum = row.get('maxLen')
    if maximum is None:
        maximum = 256
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 4096:
        raise ValueError('Ungültige Textlänge.')
    pattern = row.get('regex') or ''
    if str(row.get('parameterId')) == '61601' and not pattern:
        pattern = r'[A-Za-z0-9]{12,32}'
    if not isinstance(pattern, str) or len(pattern) > 1024:
        raise ValueError('Ungültige Eingaberegel.')
    if pattern:
        try:
            re.compile(pattern)
        except re.error:
            raise ValueError('Die Eingaberegel dieses Textfelds wird noch nicht unterstützt.') from None
    return maximum, pattern


def text_value(row, entered):
    maximum, pattern = text_rules(row)
    if is_secret(row) and entered == '':
        raise ValueError('Bitte ein Passwort eingeben. Ein leeres Passwort wird nicht gespeichert.')
    if not isinstance(entered, str) or len(entered) > maximum:
        raise ValueError('Die Eingabe überschreitet die erlaubte Textlänge.')
    if any(ord(c) < 32 or ord(c) == 127 for c in entered):
        raise ValueError('Steuerzeichen sind nicht erlaubt.')
    if pattern and re.fullmatch(pattern, entered) is None:
        raise ValueError('Die Eingabe entspricht nicht den Regeln der Wärmepumpe. Bitte den Hinweis am Feld beachten.')
    return entered


def expected_value(row):
    if row.get('type') == 'uitextsetting':
        raw = (row.get('value') or {}).get('stringValue', '')
        return hmac.new(VALUE_KEY, str(raw).encode('utf-8'), hashlib.sha256).hexdigest()
    return str((row.get('value') or {}).get('integerValue'))


_original_fmt = web.fmt_value
_original_api_menu = web.api_menu


def safe_fmt(row):
    return '********' if is_secret(row) else _original_fmt(row)


def safe_api_menu(menu):
    result = _original_api_menu(menu)
    for item, row in zip(result['rows'], web.visible_rows(menu)):
        if is_secret(row):
            item['value'] = '********'
            item['raw'] = None
        if row.get('type') == 'uitextsetting':
            item['maxLen'] = row.get('maxLen')
            item['regex'] = row.get('regex')
            item['inputHelper'] = row.get('inputHelper')
            item['enabled'] = row.get('enabled')
    return result


web.fmt_value = safe_fmt
web.api_menu = safe_api_menu


def trail_list(value):
    if not value:
        return []
    if not isinstance(value, str) or len(value) > 320:
        raise ValueError('Ungültiger Navigationspfad.')
    ids = value.split(',')
    if len(ids) > 32 or any(re.fullmatch(r'\d{1,9}', x) is None for x in ids):
        raise ValueError('Ungültiger Navigationspfad.')
    return ids


def menu_url(base, mid, trail, origin=''):
    if origin and origin not in dict(hmi.PAGES):
        raise ValueError('Ungültige Ausgangsansicht.')
    query = (['trail=' + ','.join(trail)] if trail else []) + (['from=' + origin] if origin else [])
    return base + '/menu/' + str(mid) + ('?' + '&'.join(query) if query else '')


def secret_control(row, mid, base, editable):
    pid = str(row['parameterId'])
    maximum = text_rules(row)[0] if editable else 4096
    attrs = "name='value' required" if editable else 'readonly'
    placeholder = 'Neues Passwort' if editable else '********'
    if editable and pid == '61601':
        attrs += " minlength='12'"
    return (f"<span class='secret-control'><input type='password' {attrs} value='' placeholder='{placeholder}' "
            f"maxlength='{maximum}' autocomplete='new-password' aria-label='Passwort' data-secret-input>"
            f"<button type='button' class='eye' data-eye data-menu='{mid}' data-parameter='{html.escape(pid, quote=True)}' "
            "aria-label='Passwort anzeigen' aria-pressed='false' title='Passwort anzeigen'>👁</button></span>")


def edit_row(row, mid, base, trail, epoch, index):
    e = html.escape
    pid = str(row.get('parameterId', ''))
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}', pid):
        raise ValueError('Ungültiger Parameter.')
    is_text = row.get('type') == 'uitextsetting'
    inputs = hidden('csrf', CSRF) + hidden('epoch', epoch) + hidden('menu', mid) + hidden('parameter', pid)
    inputs += hidden('expected', expected_value(row)) + hidden('trail', ','.join(trail))
    helper = ''
    if is_text:
        maximum, pattern = text_rules(row)
        if is_secret(row):
            control = secret_control(row, mid, base, True)
        else:
            current = row['value']['stringValue']
            control = f"<input type='text' name='value' value='{e(current, quote=True)}' maxlength='{maximum}' autocomplete='off' aria-label='{e(web.label(row), quote=True)}'>"
        helper = str(row.get('inputHelper') or '')
        if pid == '61601':
            helper = f'12–{min(maximum, 32)} Zeichen, A–Z, a–z, 0–9' + (' · ' + helper if helper else '')
        helper = (helper + (' · ' if helper else '') + f'Maximal {maximum} Zeichen')
    else:
        div, lo, hi, step, allowed = rules(row)
        current = number(row['value']['integerValue']) / div
        if row.get('type') == 'uitimesetting':
            if div != 1 or not 0 <= current <= 86399:
                raise ValueError('Unbekanntes Uhrzeitformat.')
            seconds = int(current)
            timestamp = '%02d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)
            control = f"<input type='time' name='value' value='{timestamp}' step='{step}' required aria-label='{e(web.label(row), quote=True)}'>"
        elif allowed is not None:
            options = []
            for raw in sorted(allowed):
                title = next((web.clean((o.get('text') or {}).get('text')) for o in row.get('options') or [] if number(o.get('value')) == raw), '')
                title = title or (('Ein' if raw else 'Aus') if row.get('type') == 'uiboolean' else format(raw/div, 'f'))
                selected = ' selected' if raw == number(row['value']['integerValue']) else ''
                options.append(f"<option value='{raw/div}'{selected}>{e(title)}</option>")
            control = "<select name='value' aria-label='Wert'>" + ''.join(options) + '</select>'
        else:
            control = f"<input type='number' name='value' value='{current}' min='{lo/div}' max='{hi/div}' step='{step/div}' required aria-label='{e(web.label(row), quote=True)}'>"
        unit = web.clean((row.get('metadata') or {}).get('unit') or (row.get('metadata') or {}).get('shortUnit'))
        if unit:
            control += '<span class=unit>' + e(unit) + '</span>'
        helper = f'Bereich {lo/div} bis {hi/div}, Schrittweite {step/div}'
    rid = 'row-' + str(index)
    return (f"<form id='{rid}' class='row edit-row' method='post' action='{base}/write/value'>{inputs}"
            f"<span class='row-label'>{e(web.label(row))}</span><span class='row-editor'>{control}"
            f"<button type='submit'>Speichern</button><span class='field-help'>{e(helper)}</span></span></form>")


def editable_menu(mid, menu, base='', trail=None, origin=''):
    trail = trail or []
    e = html.escape
    name = web.clean((menu.get('name') or {}).get('text')) or 'Menü ' + str(mid)
    number_text = str(menu.get('number') or '')
    back = menu_url(base, trail[-1], trail[:-1], origin) if trail else base + '/hmi/' + origin if origin else base + '/menu/0'
    parts = [f"<nav class='menu-nav'><a class='button' href='{back}' aria-label='Eine Menüebene zurück'>← Zurück</a>"
             f"<a href='{base}/hmi/overview'>Übersicht</a><a href='{base}/menu/0'>Hauptmenü</a><a href='{base}/setup'>Verbindung</a></nav>",
             f"<h1>{e(name)} <span class=num>{e(number_text)}</span></h1>"]
    panel = lock_panel(base, mid).replace(hidden('menu', mid), hidden('menu', mid) + hidden('trail', ','.join(trail)))
    parts.append(panel)
    parts.append(hmi.menu_extra(mid, menu, base, trail))
    helptext = web.clean((menu.get('help') or {}).get('text'))
    if helptext:
        parts.append('<details><summary>Hilfe</summary>' + e(helptext).replace('\n','<br>') + '</details>')
    with WRITE_LOCK:
        locked, epoch = WRITES_LOCKED, WRITE_EPOCH
    rows = web.visible_rows(menu)
    if rows:
        parts.append('<div class=card>')
    else:
        parts.append("<div class='card note'>Dieses Menü ist leer oder ein noch nicht unterstützter Sonderbildschirm.</div>")
    for index, row in enumerate(rows):
        kind, label = row.get('type'), e(web.label(row))
        if kind == 'uiheader':
            parts.append(f'<div class=hdr>{label}</div>')
            continue
        if kind == 'uimenuchain' or row.get('isMenuChain'):
            parts.append(hmi.chain_row(row, mid, base, trail))
            continue
        if kind == 'uievent':
            action = hmi.event_row(row, mid, base, trail)
            if action:
                parts.append(action)
                continue
        if kind in ('uichart', 'uichartintseries', 'uichartstringseries'):
            parts.append(hmi.chart(row))
            continue
        if kind == 'uilink' and not row.get('isMenuChain'):
            try:
                target = str(int(row.get('id', 0)))
            except (ValueError, TypeError):
                continue
            child_trail = (trail + [str(mid)])[-32:]
            url = menu_url(base, target, child_trail, origin)
            num = e(str(row.get('number') or ''))
            parts.append(f"<a class=row href='{url}'><span>{num + ' ' if num else ''}{label}</span></a>")
            continue
        if not locked:
            try:
                parts.append(edit_row(row, mid, base, trail, epoch, index))
                continue
            except (ValueError, KeyError, TypeError):
                pass
        if kind in ('uievent','uiaction'):
            val = 'Kein eindeutig unterstützter Aktionsaufruf verfügbar'
        elif is_secret(row) and row.get('parameterId') is not None:
            val = secret_control(row, mid, base, False)
            parts.append(f"<div class=row><span>{label}</span><span class=v>{val}</span></div>")
            continue
        else:
            val = web.fmt_value(row)
            if val is None:
                val = '·'
        parts.append(f"<div class=row><span>{label}</span><span class=v>{e(str(val))}</span></div>")
    if rows:
        parts.append('</div>')
    parts.append(f"<p><a href='{menu_url(base, mid, trail, origin)}'>Werte aktualisieren</a></p>")
    parts.append(f"<p role='status' aria-live='polite' id='ui-status'></p><div id='ui-context' data-base='{base}' data-csrf='{CSRF}' hidden></div>")
    parts.append(f"<script defer src='{base}/static/app.js'></script>")
    body = ''.join(parts)
    if origin:
        body = body.replace(hidden('menu', mid), hidden('menu', mid) + hidden('origin', origin))
    return web.page(name, body).replace('<meta http-equiv=refresh content=30>', '')


web.render_menu = editable_menu


def handle_write(handler, path, form, base):
    global WRITES_LOCKED, WRITE_EPOCH
    mid = form.get('menu', ['0'])[0]
    if not re.fullmatch(r'\d{1,9}', mid):
        return handler._send(400, 'Ungültiges Menü.')
    try:
        trail = trail_list(form.get('trail', [''])[0])
        origin = form.get('origin', [''])[0]
        menu_url(base, mid, trail, origin)  # Validate the same-origin return target before any mutation.
    except ValueError:
        return handler._send(400, 'Ungültiger Navigationspfad.')
    def error(code, text):
        body = '<p role=alert>' + html.escape(text) + '</p>' + f"<p><a href='{menu_url(base, mid, trail, origin)}'>Zurück zum Menü</a></p>"
        return handler._send(code, web.page('Änderung nicht ausgeführt', body))
    if path != '/write/lock' and hmi.firmware.JOB.get('state') == 'running' and hmi.firmware.JOB.get('operation') == 'upload':
        return error(423, 'Während der Firmwareübertragung sind weitere Geräteänderungen gesperrt.')
    with WRITE_LOCK:
        if not secrets.compare_digest(form.get('epoch', [''])[0], WRITE_EPOCH):
            return error(409, 'Die Freigabe wurde zwischenzeitlich geändert. Seite neu laden.')
        if path == '/write/lock':
            WRITES_LOCKED = True
            WRITE_EPOCH = secrets.token_urlsafe(24)
        elif path == '/write/unlock':
            if form.get('ack') != ['yes']:
                return error(403, 'Ohne aktive Bestätigung des Risikohinweises bleiben Änderungen gesperrt.')
            if not SESSION:
                return error(409, 'Bitte zuerst die Wärmepumpe koppeln.')
            WRITES_LOCKED = False
            WRITE_EPOCH = secrets.token_urlsafe(24)
        else:
            if WRITES_LOCKED:
                return error(403, 'Änderungen sind gesperrt.')
            pid = form.get('parameter', [''])[0]
            try:
                menu = get_menu(mid, fresh=True)
                matches = [r for r in web.visible_rows(menu) if str(r.get('parameterId')) == pid]
                if len(matches) != 1:
                    return error(400, 'Der Parameter ist in diesem Menü nicht eindeutig verfügbar.')
                row = matches[0]
                raw = text_value(row, form.get('value', [''])[0]) if row.get('type') == 'uitextsetting' else raw_value(row, form.get('value', [''])[0])
                current = expected_value(row)
                if current != form.get('expected', [''])[0]:
                    return error(409, 'Der WP-Wert wurde inzwischen geändert. Bitte den aktuellen Wert prüfen und erneut eingeben.')
                session = dict(SESSION)
                api(session['host'], session['port'], '/devices/' + session['serial'] + '/points',
                    session['token'], body={pid: raw}, method='PATCH')
                web._cache.clear()
            except ValueError as exc:
                return error(400, str(exc))
            except web.WpError as exc:
                return error(502, exc.msg + ' Änderung nicht bestätigt. Vor einem erneuten Versuch den WP-Wert prüfen.')
    destination = menu_url(base, mid, trail, origin)
    return_page = form.get('return_page', [''])[0]
    if path in ('/write/lock', '/write/unlock') and return_page in dict(hmi.PAGES):
        destination = base + '/hmi/' + return_page
    return handler._send(303, '', extra={'Location': destination})


class Handler(web.H):
    def allowed(self):
        return self.client_address[0] in web.INGRESS_PEERS

    def _send(self, code, body, ctype='text/html; charset=utf-8', extra=None):
        # Allow same-origin setup forms while keeping scripts disabled.
        super()._send(code, body, ctype, extra)

    def do_GET(self):
        if not self.allowed():
            return self._send(403, web.page('Verboten', '<p>Nur ueber Home Assistant erreichbar.</p>'))
        path = self.path.split('?', 1)[0]
        base = self._base()
        if path == '/static/hmi.js':
            return self._send(200, Path(__file__).with_name('nibe_hmi.js').read_text(encoding='utf-8'), 'application/javascript; charset=utf-8')
        if hmi.route_get(self, path, base):
            return
        if path == '/' and SESSION:
            return self._send(302, '', extra={'Location': base + '/hmi/overview'})
        if path == '/static/app.js':
            return self._send(200, Path(__file__).with_name('nibe_ui.js').read_text(encoding='utf-8'), 'application/javascript; charset=utf-8')
        match = re.fullmatch(r'/menu/(\d{1,9})', path)
        if match:
            try:
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, max_num_fields=4)
                trail = trail_list(query.get('trail', [''])[0])
                origin = query.get('from', [''])[0]
                menu_url(base, match.group(1), trail, origin)
                menu = get_menu(match.group(1))
                return self._send(200, editable_menu(match.group(1), menu, base, trail, origin))
            except ValueError:
                return self._send(400, 'Ungültiger Navigationspfad.')
            except web.WpError as exc:
                return self._send(exc.status, web.page('Fehler', '<p>' + html.escape(exc.msg) + '</p>'))
        if path == '/setup' or path == '/' and not SESSION:
            return self._send(200, setup_page(base))
        return super().do_GET()

    do_HEAD = do_GET

    def do_POST(self):
        self.close_connection = True
        if not self.allowed():
            return self._send(403, 'Nur ueber Home Assistant erreichbar.')
        base = self._base()
        path = self.path.split('?', 1)[0]
        if path == '/hmi/firmware/stage':
            return hmi.stage_request(self)
        if path not in ('/setup/discover', '/setup/probe', '/setup/pair', '/setup/check', '/write/lock', '/write/unlock', '/write/value', '/secret/reveal', '/hmi/action'):
            return self._send(405, 'Dieser Endpunkt erlaubt keine Änderungen.', extra={'Allow': 'GET, HEAD'})
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                raise ValueError
            self.connection.settimeout(30)
            form = urllib.parse.parse_qs(nibe_http.form_bytes(self, 16384).decode('utf-8'), keep_blank_values=True, max_num_fields=20)
            if any(len(values) != 1 for values in form.values()):
                raise ValueError
            csrf = form.get('csrf', [''])[0]
            if not secrets.compare_digest(csrf, CSRF):
                return self._send(403, 'Formular abgelaufen. Seite neu laden.')
        except Exception:
            return self._send(400, 'Ungueltiges Formular.')
        try:
            if path == '/hmi/action':
                try:
                    target = hmi.perform(hmi.single(form, 'operation'), form, base)
                    return self._send(303, '', extra={'Location': target})
                except (ValueError, PermissionError, web.WpError) as exc:
                    message = exc.msg if isinstance(exc, web.WpError) else str(exc)
                    code = 403 if isinstance(exc, PermissionError) else 502 if isinstance(exc, web.WpError) else 400
                    message += ' Bei einer fehlgeschlagenen Geräteaktion zuerst den WP-Zustand prüfen; es erfolgt keine automatische Wiederholung.'
                    return self._send(code, hmi.page(base, 'overview', '<p role="alert">' + html.escape(message) + '</p>'))
            if path == '/secret/reveal':
                mid = form.get('menu', [''])[0]
                if not re.fullmatch(r'\d{1,9}', mid):
                    return self._send(400, 'Ungültiges Menü.')
                menu = get_menu(mid, fresh=True)
                rows = [r for r in web.visible_rows(menu) if str(r.get('parameterId')) == form.get('parameter', [''])[0] and is_secret(r)]
                if len(rows) != 1 or not isinstance((rows[0].get('value') or {}).get('stringValue'), str):
                    return self._send(404, 'Passwortwert nicht verfügbar.')
                return self._send(200, json.dumps({'value': rows[0]['value']['stringValue']}), 'application/json; charset=utf-8')
            if path.startswith('/write/'):

                return handle_write(self, path, form, base)
            if path == '/setup/discover':
                threading.Thread(target=discover, daemon=True).start()
            elif path == '/setup/probe':
                host = local_host(form.get('host', [''])[0].strip())
                devices = device_list(host, 8888)
                if not devices:
                    raise ValueError('Die WP lieferte keine Seriennummer. Lokale API und Freigabe pruefen.')
                for device in devices:
                    remember(device)
            elif path == '/setup/pair':
                with STATE_LOCK:
                    device = DEVICES.get(form.get('device', [''])[0])
                if not device:
                    raise ValueError('Bitte zuerst ein gefundenes Geraet auswaehlen.')
                pair(device)
            else:
                session_status()
        except (ValueError, web.WpError, StorageError) as exc:
            return self._send(400, setup_page(base, exc.msg if isinstance(exc, web.WpError) else str(exc)))
        except Exception:
            return self._send(500, setup_page(base, 'Vorgang fehlgeschlagen. Keine Zugangsdaten wurden ausgegeben.'))
        return self._send(303, '', extra={'Location': base + '/setup'})


hmi.bind(sys.modules[__name__])


def main():
    global STORE, STATUS, WRITES_LOCKED, WRITE_EPOCH
    WRITES_LOCKED = True
    WRITE_EPOCH = secrets.token_urlsafe(24)
    os.umask(0o077)
    STORE = EncryptedStore('/data')
    options = json.loads(Path('/data/options.json').read_text(encoding='utf-8'))
    cache = int(options.get('cache_seconds', 5))
    if not 1 <= cache <= 300:
        raise SystemExit('Ungueltige Cache-Dauer.')
    # Old options are never used. Remove token from HA configuration when upgrading 0.1.
    if options.get('token'):
        raise SystemExit('Altes Klartext-Token in der App-Konfiguration entfernen; danach neu koppeln.')
    web.CFG.update(ingress=True, auth=None, cache=cache, scheme='https')
    try:
        session = STORE.load()
        if session:
            local_host(session['host'])
            serial_number(session['serial'])
            if not isinstance(session.get('token'), str):
                raise ValueError
            activate(session)
            STATUS = 'Gespeicherte Kopplung geladen. Bei Bedarf Status pruefen.'
    except Exception:
        raise SystemExit('Kopplung konnte nicht geladen werden. Schluessel und Datensicherung pruefen; Daten bleiben erhalten.') from None
    threading.Thread(target=discover, daemon=True).start()
    print('NIBE Local HMI 0.6.2: Ingress auf Port 8099, Änderungen gesperrt.')
    ThreadingHTTPServer(('0.0.0.0', 8099), Handler).serve_forever()


if __name__ == '__main__':
    main()
