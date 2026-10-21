"""Explicit firmware staging and local multipart transfer; no automatic flashing."""
import hashlib
import html
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import threading
import urllib.parse
import urllib.request
import zipfile

PORTAL = 'https://www.nibe.at/software'
MAX_BYTES = 512 * 1024 * 1024
CHUNK = 256 * 1024
JOB_LOCK = threading.Lock()
STAGE_LOCK = threading.RLock()
JOB = {'state': 'idle', 'message': 'Noch kein Vorgang gestartet.', 'bytes': 0}
ATTEMPTS = set()


class FirmwareError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def official_url(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.hostname != 'www.nibe.at' or p.port not in (None, 443) or p.username or p.password or p.fragment:
        raise FirmwareError('Nur der offizielle NIBE-Österreich-Download ist zugelassen.')
    return url


def open_official(url):
    official_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    return opener.open(urllib.request.Request(url, headers={'User-Agent': 'NIBE-Local-HMI', 'Accept-Encoding': 'identity'}), timeout=60)


class DownloadLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if urllib.parse.unquote(urllib.parse.urlsplit(href).path).lower().endswith('/software-s-serie.zip'):
                self.links.append(official_url(urllib.parse.urljoin(PORTAL, href)))


def catalog_url():
    with open_official(PORTAL) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise FirmwareError('Downloadseite ist zu groß.')
    parser = DownloadLinks()
    parser.feed(raw.decode('utf-8'))
    links = sorted(set(parser.links))
    if len(links) != 1:
        raise FirmwareError('Kein eindeutiger S-Regler-Download gefunden. Offizielle Downloadseite prüfen.')
    return links[0]


def folder(app):
    if not app.STORE:
        raise FirmwareError('Datenspeicher noch nicht bereit.')
    p = app.STORE.directory / 'firmware'
    app.STORE.private_dir(p)
    return p


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def validate_file(path):
    size = path.stat().st_size
    with path.open('rb') as f:
        header = f.read(110)
        try:
            # SWUpdate requires sw-description as the first CPIO entry.
            if header[:6] not in (b'070701', b'070702'):
                raise ValueError
            fields = [int(header[6+i*8:14+i*8], 16) for i in range(13)]
            namesize, filesize = fields[11], fields[6]
            if not 1 <= namesize <= 256 or not 1 <= filesize <= min(size, 2 * 1024 * 1024):
                raise ValueError
            if f.read(namesize) != b'sw-description\x00' or 110 + namesize + filesize > size:
                raise ValueError
        except (ValueError, IndexError):
            raise FirmwareError('Keine unterstützte SWUpdate-Datei mit sw-description als erstem Eintrag.') from None
    if not 110 < size <= MAX_BYTES:
        raise FirmwareError('Keine unterstützte SWUpdate-Datei (.swu / CPIO).')
    return size


def stage_stream(app, stream, length, name, source='local-file', progress=lambda n: None):
    """Store one exact SWU, without paths supplied by the browser or archive."""
    name = Path(name.replace('\\', '/')).name
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.swu', name, re.I):
        raise FirmwareError('Bitte eine .swu-Datei mit einfachem Dateinamen wählen.')
    if not isinstance(length, int) or not 110 < length <= MAX_BYTES:
        raise FirmwareError('Update-Datei ist leer oder größer als 512 MiB.')
    directory = folder(app)
    if shutil.disk_usage(directory).free < length + 64 * 1024 * 1024:
        raise FirmwareError('Nicht genug freier Speicher für die Firmware-Datei.')
    temporary = directory / ('pending-' + secrets.token_hex(12))
    with STAGE_LOCK:
        try:
            with temporary.open('xb') as out:
                remaining = length
                while remaining:
                    block = stream.read(min(CHUNK, remaining))
                    if not block:
                        raise FirmwareError('Datei unvollständig empfangen.')
                    out.write(block)
                    remaining -= len(block)
                    progress(length - remaining)
                out.flush()
                os.fsync(out.fileno())
            if hasattr(stream, 'ensure_complete'):
                stream.ensure_complete()
            size = validate_file(temporary)
            metadata = {'name': name, 'size': size, 'sha256': digest_file(temporary), 'source': source}
            # Remove the old manifest first: an interrupted replacement must never pair old metadata with new bytes.
            (directory / 'staged.json').unlink(missing_ok=True)
            os.replace(temporary, directory / 'staged.swu')
            (directory / 'staged.swu').chmod(0o600)
            manifest = directory / 'staged.json.tmp'
            manifest.write_text(json.dumps(metadata), encoding='utf-8')
            manifest.chmod(0o600)
            os.replace(manifest, directory / 'staged.json')
            return metadata
        finally:
            temporary.unlink(missing_ok=True)


def staged(app, verify=False):
    with STAGE_LOCK:
        directory = folder(app)
        try:
            meta = json.loads((directory / 'staged.json').read_text(encoding='utf-8'))
            path = directory / 'staged.swu'
            if not isinstance(meta, dict) or not re.fullmatch(r'[a-f0-9]{64}', meta.get('sha256', '')):
                raise ValueError
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.swu', meta.get('name', ''), re.I):
                raise ValueError
            if path.stat().st_size != meta['size']:
                raise ValueError
            if verify and (digest_file(path) != meta['sha256'] or validate_file(path) != meta['size']):
                raise ValueError
            return meta
        except (FileNotFoundError, ValueError, TypeError, KeyError):
            return None


def update_job(**values):
    # Assignment avoids publishing partially updated progress to polling requests.
    global JOB
    JOB = dict(JOB, **values)


def download(app):
    """Only user-triggered; no cloud account, cookies, or local WP token leave the LAN."""
    directory = folder(app)
    archive = directory / ('download-' + secrets.token_hex(12) + '.zip')
    try:
        url = catalog_url()
        update_job(message='Offizielle S-Regler-Datei wird heruntergeladen.', bytes=0)
        with open_official(url) as response, archive.open('xb') as out:
            expected = response.headers.get('Content-Length')
            if expected and not 0 < int(expected) <= MAX_BYTES:
                raise FirmwareError('Firmware-Download ist zu groß.')
            if expected and shutil.disk_usage(directory).free < int(expected) + 64 * 1024 * 1024:
                raise FirmwareError('Nicht genug freier Speicher für den Download.')
            total = 0
            while chunk := response.read(CHUNK):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise FirmwareError('Firmware-Download ist zu groß.')
                if shutil.disk_usage(directory).free < 64 * 1024 * 1024:
                    raise FirmwareError('Download wegen knappem Speicher beendet.')
                out.write(chunk)
                update_job(bytes=total)
            if expected and total != int(expected):
                raise FirmwareError('Firmware-Download unvollständig.')
        with zipfile.ZipFile(archive) as z:
            candidates = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith('.swu')]
            if len(candidates) != 1:
                raise FirmwareError('Archiv enthält keine eindeutige .swu-Datei. Datei manuell auswählen.')
            info = candidates[0]
            if not 110 < info.file_size <= MAX_BYTES:
                raise FirmwareError('Entpackte Firmware ist zu groß.')
            # Never extract archive paths to the filesystem.
            with z.open(info) as source:
                meta = stage_stream(app, source, info.file_size, info.filename, source=url)
        update_job(state='done', message='Download bereit. Es wurde nichts an die Wärmepumpe übertragen.', bytes=meta['size'])
    finally:
        archive.unlink(missing_ok=True)


def upload(app, expected_sha, epoch):
    """Local wire format observed in the APK: multipart/form-data, part name=file."""
    with app.WRITE_LOCK, STAGE_LOCK:
        if app.WRITES_LOCKED or not secrets.compare_digest(epoch, app.WRITE_EPOCH):
            raise FirmwareError('Freigabe geändert oder Änderungen gesperrt.')
        meta = staged(app, verify=True)
        if not meta or not secrets.compare_digest(meta['sha256'], expected_sha):
            raise FirmwareError('Die bereitgestellte Firmware wurde geändert. Erneut prüfen.')
        attempt = (epoch, expected_sha)
        if attempt in ATTEMPTS:
            raise FirmwareError('Diese Übertragung wurde mit dieser Freigabe bereits versucht. WP-Zustand prüfen und Änderungen bewusst neu freigeben, bevor ein erneuter Versuch möglich ist.')
        session = dict(app.SESSION or {})
        if not session:
            raise FirmwareError('Zuerst koppeln.')
        device = app.api(session['host'], session['port'], '/devices/' + session['serial'], session['token'])
        if not isinstance(device, dict) or (device.get('features') or {}).get('fwUpdate') is not True:
            raise FirmwareError('Die WP meldet keine lokale Firmware-Update-Freigabe.')
        ATTEMPTS.add(attempt)
        boundary = 'NibeHMI' + secrets.token_hex(20)
        prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{meta["name"]}"\r\n'
                  'Content-Type: application/octet-stream\r\n\r\n').encode('ascii')
        suffix = f'\r\n--{boundary}--\r\n'.encode('ascii')
        connection = http.client.HTTPSConnection(app.local_host(session['host']), int(session['port']),
                                                 timeout=180, context=app.web._ctx)
        try:
            with app.API_LOCK:
                connection.putrequest('POST', '/devices/' + session['serial'] + '/firmware')
                connection.putheader('Authorization', 'Token ' + session['token'])
                connection.putheader('Content-Type', 'multipart/form-data; boundary=' + boundary)
                connection.putheader('Content-Length', str(len(prefix) + meta['size'] + len(suffix)))
                connection.putheader('Accept', '*/*')
                connection.endheaders()
                connection.send(prefix)
                sent = 0
                with (folder(app) / 'staged.swu').open('rb') as f:
                    while chunk := f.read(CHUNK):
                        connection.send(chunk)
                        sent += len(chunk)
                        update_job(bytes=sent, message='Firmware wird lokal an die WP übertragen.')
                connection.send(suffix)
                response = connection.getresponse()
                response.read(65536)
                if not 200 <= response.status < 300:
                    raise FirmwareError('WP hat die Übertragung mit HTTP %d abgelehnt. Nicht automatisch wiederholen.' % response.status)
            update_job(state='done', message='WP hat die Übertragung angenommen. Das bestätigt noch keine abgeschlossene Installation. Status am WP-Display prüfen.', bytes=sent)
        except (OSError, http.client.HTTPException):
            raise FirmwareError('Verbindung während der Übertragung unterbrochen. Ausgang unklar: zuerst WP-Display prüfen, nicht automatisch wiederholen.') from None
        finally:
            connection.close()


def start_job(app, operation, **kwargs):
    if not JOB_LOCK.acquire(blocking=False):
        raise FirmwareError('Es läuft bereits ein Firmware-Vorgang.')
    update_job(state='running', operation=operation, message='Vorgang gestartet.', bytes=0)
    def run():
        try:
            if operation == 'download':
                download(app)
            else:
                upload(app, kwargs['sha256'], kwargs['epoch'])
        except Exception as exc:
            message = str(exc) if isinstance(exc, FirmwareError) else 'Vorgang fehlgeschlagen. Vor einem erneuten Versuch den Zustand prüfen.'
            update_job(state='error', message=message)
        finally:
            JOB_LOCK.release()
    threading.Thread(target=run, daemon=True).start()


def page(app, base):
    e = html.escape
    if JOB.get('state') == 'running' and JOB.get('operation') == 'upload':
        return ('<h2>Lokale Firmwareübertragung läuft</h2><p>Die WP kann anschließend ein Update und einen Neustart ausführen. Stromversorgung nicht unterbrechen.</p>'
                '<p role="status" id="firmware-status">' + e(JOB['message']) + '</p><p>Weitere Geräteänderungen sind während der Übertragung gesperrt. Eine Sperranfrage wird nach Abschluss der laufenden Übertragung bestätigt.</p>')
    meta = staged(app)
    body = '<h2>Firmware der Wärmepumpe</h2><p>Download und Übertragung sind getrennte Schritte. Keine automatische Installation.</p>'
    body += '<p>Der Download nutzt die offizielle NIBE-Webseite im Internet, ohne myUplink-Konto. Die Bedienung und Übertragung zur WP bleiben lokal.</p>'
    body += f'<p><a href="{PORTAL}" target="_blank" rel="noopener noreferrer">Offizielle Downloadseite und Anleitung</a></p>'
    body += app.hmi.form(base, 'firmware-download', '', 'S-Regler-Update herunterladen', ack='Ich möchte die S-Regler-Firmware von NIBE Österreich herunterladen.', mutation=False)
    body += (f'<form id="firmware-file" data-base="{base}"><p>Oder eine vorhandene .swu-Datei bereitstellen (max. 512 MiB):</p>'
             '<input type="file" name="firmware" accept=".swu" required aria-label="Firmware-Datei">'
             '<button type="submit">Datei bereitstellen</button></form>')
    if meta:
        body += '<div class="card note"><h3>Bereitgestellte Datei</h3>'
        body += f'<p>{e(meta["name"])} · {meta["size"] / 1048576:.1f} MiB</p><p class="hash">SHA-256: {e(meta["sha256"])}</p>'
        body += f'<a class="button" href="{base}/hmi/firmware/file">Datei auf diesen Rechner herunterladen</a>'
        body += '<p>Dateiformat und Prüfsumme werden geprüft. Gerätekompatibilität und Hersteller-Signatur kann diese App nicht bestätigen. Nur eine ausdrücklich für deinen Regler bestimmte Datei übertragen.</p>'
        body += app.hmi.form(base, 'firmware-upload', app.hidden('sha256', meta['sha256']), 'Firmware an WP übertragen',
                             ack='Die Datei ist für meinen Regler bestimmt. Ich möchte die Übertragung starten; sie kann ein Update und einen Neustart auslösen. Währenddessen die Stromversorgung nicht unterbrechen.')
        body += app.hmi.form(base, 'firmware-remove', app.hidden('sha256', meta['sha256']), 'Datei aus der App entfernen',
                             ack='Ich möchte nur die bereitgestellte Update-Datei aus der App löschen. Die WP und die Kopplung bleiben unverändert.', mutation=False)
        body += '</div>'
    body += '<p role="status" id="firmware-status">' + e(JOB['message']) + '</p>'
    body += '<p>Die PRO-App kennt lokal keinen separaten Update-Status-Endpunkt. Übertragungsfortschritt ist keine Installationsanzeige.</p>'
    return body
