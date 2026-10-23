"""Behavioral HMI and firmware regression tests. All controller writes target a mock."""
from pathlib import Path
import copy
import datetime
import http.client
import io
import json
import ssl
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nibe_local'))
import nibe_app as app
import nibe_hmi as hmi
import nibe_firmware as firmware

checks = []
def check(name, value):
    assert value, name
    checks.append(name)

SN = '00000000000000'
TOKEN = 'mock-device-token-private'
SECRET = 'MockPasswordNeverRender123'
device = {'product': {'name': 'S-regler Test', 'firmwareId': 'mock'}, 'pairingState': 'success', 'aidMode': 'off',
          'features': {'fwUpdate': True, 'wifiNetworks': True, 'cellular': True}, 'offlineToken': TOKEN}
clock = {'automaticUpdate': 0, 'dateTimeLocal': '2026-10-21T10:00:00', 'dateTimeUTC': '2026-10-21T08:00:00',
         'region': 'Europe', 'timezone': 'Vienna', 'timeFormat': '24hr'}
alarms = {'alarmList': [{'alarmId': 1, 'header': '<script>alarm</script>', 'description': 'Testmeldung', 'severity': 1}]}
event = {'type': 'uievent', 'eventId': 99, 'enabled': True, 'text': {'text': 'Testaktion'}}
chain = {'type': 'uimenuchain', 'id': 7, 'enabled': True, 'text': {'text': 'Testassistent'}}
secret = {'type': 'uitextsetting', 'parameterId': '61601', 'text': {'text': 'Password'}, 'value': {'stringValue': SECRET}}
number = {'type': 'uinumerical', 'parameterId': '10', 'text': {'text': 'Temperatur'}, 'value': {'integerValue': 205, 'isOk': True},
          'metadata': {'divisor': 10, 'decimal': 1, 'unit': '°C', 'isWritable': False}}
menu = {'id': 50, 'name': {'text': 'Mock Menü'}, 'rows': [event, chain, {'type': 'uigroup', 'rows': [number, secret]}]}
writes = []
firmware_response = 204
firmware_body = None
upload_received = threading.Event()
upload_release = threading.Event()
block_upload = False
many_catalog = False

def swu_bytes():
    data = b'software = { version = "mock"; };'
    name = b'sw-description\x00'
    fields = [0, 0o100644, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name), 0]
    header = b'070701' + ''.join('%08x' % x for x in fields).encode()
    prefix = header + name
    prefix += b'\x00' * (-len(prefix) % 4)
    return prefix + data + b'\x00' * 128

class Mock(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def reply(self, status, body=None):
        data = b'' if body is None else json.dumps(body).encode()
        self.send_response(status); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.headers.get('Authorization') != 'Token ' + TOKEN:
            return self.reply(401, {'token': TOKEN})
        suffix = self.path.removeprefix('/devices/' + SN)
        if not suffix: return self.reply(200, device)
        if suffix == '/notifications': return self.reply(200, alarms)
        if suffix == '/datetime': return self.reply(200, clock)
        if suffix == '/timezones': return self.reply(200, {'Europe': ['Vienna', 'Berlin']})
        if suffix == '/wifi-networks': return self.reply(200, {'networks': [{'ssid': '<b>Mock WLAN</b>', 'auth': 'psk', 'rssi': -50}]})
        if suffix == '/systemFlow': return self.reply(404, {'secret': SECRET})
        if suffix == '/points/10': return self.reply(200, {'10':205})
        if suffix == '/haystack':
            raw=('id: @1001\ndis: "Temperature"\nunit: "°C"\nminVal: 0\nmaxVal: 100\nwritable\ncurVal: '+SECRET+'\n-\nid: @1002\ndis: "Voltage"\nsummary:\n  dis: '+SECRET+'\nunit: "V"\n----\nid: @1003\ndis: "Other"\n').encode()
            if many_catalog:
                raw='\n-\n'.join('id: @%d\ndis: "Synthetic %d"' % (i,i) for i in range(2001,2202)).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if suffix == '/menuchain/7': return self.reply(200, {'id': 7, 'name': {'text': 'Assistent'}, 'chain': [{'menuId': 50, 'visible': True}, {'menuId': 999, 'visible': False}]})
        if suffix in ('/menu/50', '/menu/40'): return self.reply(200, menu)
        if suffix == '/menu/38': return self.reply(200, {'rows': []})
        return self.reply(404)
    def change(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        writes.append((self.command, self.path, body, dict(self.headers)))
        if self.path.endswith('/firmware'):
            if block_upload:
                upload_received.set()
                upload_release.wait(10)
            if firmware_response == -1:
                self.close_connection = True
                self.connection.close()
                return
            return self.reply(firmware_response, firmware_body)
        return self.reply(204)
    do_POST = do_PATCH = do_DELETE = change

with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    (temp/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (temp/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    mock = ThreadingHTTPServer(('127.0.0.1', 0), Mock)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(temp/'cert.pem', temp/'key.pem')
    mock.socket = context.wrap_socket(mock.socket, server_side=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    for srv in (mock, server): threading.Thread(target=srv.serve_forever, daemon=True).start()
    original_host = app.local_host
    app.local_host = lambda host: '127.0.0.1' if host == '127.0.0.1' else original_host(host)
    app.STORE = app.EncryptedStore(temp/'data')
    app.activate({'host': '127.0.0.1', 'port': mock.server_port, 'serial': SN, 'token': TOKEN})
    app.web.CFG.update(ingress=True, auth=None, cache=5)
    app.web.INGRESS_PEERS = ('127.0.0.1',)

    def request(path, method='GET', fields=None, body=None, headers=None, chunked=False):
        h = {'X-Ingress-Path': '/api/hassio_ingress/test'}
        if fields is not None:
            body = urllib.parse.urlencode(fields); h['Content-Type'] = 'application/x-www-form-urlencoded'
        h.update(headers or {})
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        conn.request(method, path, body, h, encode_chunked=chunked); response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close(); return result
    def post(operation, **values):
        fields = {'csrf': app.CSRF, 'epoch': app.WRITE_EPOCH, 'operation': operation, 'page': 'overview', 'ack': 'yes'}
        fields.update(values)
        return request('/hmi/action', 'POST', fields=fields)
    def denied(fn, name):
        try: fn()
        except (ValueError, PermissionError): check(name, True)
        else: raise AssertionError(name)

    try:
        for view in ('overview', 'alarms', 'clock', 'network', 'firmware', 'capabilities'):
            response = request('/hmi/' + view)
            check('View ' + view + ' accessible', response[0] == 200)
            check('View ' + view + ' has no credential leak', TOKEN.encode() not in response[2] and SECRET.encode() not in response[2])
        check('Unavailable system flow falls back to explicitly schematic operating groups', request('/hmi/flow')[0] == 200 and b'<svg' in request('/hmi/flow')[2] and b'keinen hydraulischen Rohrverlauf' in request('/hmi/flow')[2])
        check('Schematic uses actual scaled fixture readings', 'Rücklauf: 20,5' in request('/hmi/flow')[2].decode())
        check('Unavailable diagnostic API returns bounded JSON rather than an HTML form', request('/hmi/api/flow')[0] == 502 and list(json.loads(request('/hmi/api/flow')[2])) == ['error'])
        check('Alarm markup escaped', b'&lt;script&gt;' in request('/hmi/alarms')[2])
        alarms['alarms']=alarms.pop('alarmList')
        check('Actual wire alias alarms supported', b'&lt;script&gt;' in request('/hmi/alarms')[2])
        check('Actual wire alias networks supported and escaped', b'&lt;b&gt;Mock WLAN&lt;/b&gt;' in request('/hmi/network')[2])
        catalog=request('/hmi/parameters')
        check('All Trio record separator lengths accepted and catalog is searchable', catalog[0] == 200 and b'3 Treffer aus 3 Katalogeintr' in catalog[2] and b'1001' in catalog[2] and b'1003' in catalog[2])
        check('Catalog omits current values and multiline payloads', SECRET.encode() not in catalog[2])
        selected=request('/hmi/parameters?q=Temperature')
        check('Catalog search filters by metadata', b'1 Treffer aus 3' in selected[2] and b'1002' not in selected[2])
        many_catalog=True
        second=request('/hmi/parameters?p=2')
        check('Catalog pagination retains entries beyond the first page', b'201 Treffer aus 201' in second[2] and b'2201' in second[2] and b'2001' not in second[2])
        many_catalog=False
        check('Boolean token capability stays visible but secret tokens masked', hmi.safe({'features':{'offlineToken':True},'token':TOKEN}) == {'features':{'offlineToken':True},'token':'********'})
        check('Root opens overview', request('/')[1]['Location'].endswith('/hmi/overview'))
        check('No mutation from view reads', not writes)
        check('Unknown API route not proxied', request('/hmi/api/token')[0] == 404)
        check('Device JSON token redacted', TOKEN.encode() not in request('/hmi/api/device')[2])
        check('Nested menu password redacted', SECRET.encode() not in request('/api/menu/50')[2])
        check('Full diagnostic menu also redacted', SECRET.encode() not in request('/hmi/api/menu/50')[2])
        check('Single point requires menu context', request('/hmi/point?menu=50&parameter=10')[0] == 200)
        check('Back from a graphical group returns to the graph', b"href='/api/hassio_ingress/test/hmi/flow'" in request('/menu/50?from=flow')[2])
        check('Arbitrary return URL rejected', request('/menu/50?from=https://example.invalid')[0] == 400)
        check('Secret point cannot bypass reveal control', request('/hmi/point?menu=50&parameter=61601')[0] == 400)
        check('Default lock blocks events', post('event', menu='50', event='99', expected=hmi.signature(event))[0] == 403 and not writes)
        check('CSRF blocks new actions', request('/hmi/action', 'POST', {'operation':'event'})[0] == 403)
        check('Unsupported method does not mutate', request('/hmi/action', 'PATCH')[0] == 405)
        check('Duplicate fields rejected', request('/hmi/action','POST',body='csrf='+app.CSRF+'&csrf=x',headers={'Content-Type':'application/x-www-form-urlencoded'})[0] == 400)
        check('Conflicting transfer framing rejected', request('/hmi/action','POST',body='a',headers={'Content-Type':'application/x-www-form-urlencoded','Content-Length':'1','Transfer-Encoding':'chunked'})[0] == 400)
        check('Unlock requires active confirmation', request('/write/unlock', 'POST', {'csrf':app.CSRF,'epoch':app.WRITE_EPOCH,'menu':'0'})[0] == 403)
        unlocked = request('/write/unlock','POST',{'csrf':app.CSRF,'epoch':app.WRITE_EPOCH,'menu':'0','ack':'yes','return_page':'firmware'})
        check('Confirmed unlock returns to originating view', unlocked[0] == 303 and unlocked[1]['Location'].endswith('/hmi/firmware'))
        chunk_form=urllib.parse.urlencode({'csrf':app.CSRF,'epoch':app.WRITE_EPOCH,'menu':'0','ack':'yes','return_page':'firmware'}).encode()
        check('Streaming Ingress chunked forms accepted', request('/write/unlock','POST',body=[chunk_form[:17],chunk_form[17:]],headers={'Content-Type':'application/x-www-form-urlencoded'},chunked=True)[0] == 303)
        check('Event needs additional acknowledgement', post('event', menu='50', event='99', expected=hmi.signature(event), ack='no')[0] == 400 and not writes)
        check('Stale event rejected', post('event', menu='50', event='99', expected='wrong')[0] == 400 and not writes)
        event['enabled'] = False
        check('Freshly disabled event rejected', post('event', menu='50', event='99', expected=hmi.signature(event))[0] == 400 and not writes)
        event['enabled'] = True
        check('Invalid event return path rejected before the device action', post('event',menu='50',event='99',expected=hmi.signature(event),origin='https://example.invalid')[0] == 400 and not writes)
        check('Event POST exact path, no invented body', post('event',menu='50',event='99',expected=hmi.signature(event))[0] == 303 and writes[-1][:3] == ('POST','/devices/'+SN+'/events/99',b''))
        n = len(writes)
        check('Meldungen changed since form rejected', post('notifications-clear', expected='stale')[0] == 400 and len(writes) == n)
        check('Meldungen explicit DELETE', post('notifications-clear',expected=hmi.signature(alarms))[0] == 303 and writes[-1][0] == 'DELETE')
        expected_clock = hmi.signature({k:clock[k] for k in ('automaticUpdate','region','timezone','timeFormat')})
        args = {'expected':expected_clock,'zone':'Europe/Vienna','datetime':'2026-10-21T12:34','automatic':'0','timeformat':'24hr'}
        check('Invalid date rejected', post('clock-save',**dict(args, datetime='2026-02-30T12:00'))[0] == 400)
        check('Unknown time zone rejected', post('clock-save',**dict(args, zone='Europe/Unknown'))[0] == 400)
        check('Clock PATCH request matches local PRO format', post('clock-save',**args)[0] == 303 and json.loads(writes[-1][2]) == {'automaticUpdate':0,'dateTime':'2026-10-21T12:34:00','region':'Europe','timezone':'Vienna','timeFormat':'24hr','utc':0})
        check('PSK too short rejected', post('wifi-save',ssid='Mock',auth='psk',passphrase='short')[0] == 400)
        check('UTF-8 SSID byte length bounded', post('wifi-save',ssid='ä'*20,auth='open')[0] == 400)
        check('PSK write does not echo password', post('wifi-save',ssid='Mock',auth='psk',passphrase='MockWifiPassword')[0] == 303 and json.loads(writes[-1][2])['passphrase'] == 'MockWifiPassword')
        check('Cellular exact payload', post('cellular-save')[0] == 303 and json.loads(writes[-1][2]) == {'auth':'cellular'})
        check('Enterprise exact auth encoding', post('enterprise-save',ssid='Mock',eap='PEAP',phase2='MSCHAPV2',identity='test',anonymousIdentity='',passphrase='MockEnterprise')[0] == 303 and json.loads(writes[-1][2])['auth'] == 'IEEE8021X')
        check('Aid mode fresh-state check', post('aidmode',expected='stale',aidmode='on')[0] == 400)
        check('Aid mode exact payload', post('aidmode',expected=hmi.signature('off'),aidmode='on')[0] == 303 and json.loads(writes[-1][2]) == {'aidMode':'on'})
        check('Chain not silently opened by GET', request('/hmi/chain/7')[0] == 400)
        check('Chain requires explicit opening', post('chain-open',menu='50',chain='7',expected=hmi.signature(chain))[0] == 303)
        chain_view = request('/hmi/chain/7')
        check('Chain respects hidden steps', chain_view[0] == 200 and b'/menu/50' in chain_view[2] and b'/menu/999' not in chain_view[2])
        check('Chain explicit close', post('chain-close',chain='7')[0] == 303 and writes[-1][0] == 'DELETE')
        check('Closed chain no longer navigable', request('/hmi/chain/7')[0] == 400)
        payload = swu_bytes()
        headers = {'Content-Type':'application/octet-stream','X-Filename':'mock.swu','X-CSRF-Token':app.CSRF}
        check('Firmware staging requires CSRF', request('/hmi/firmware/stage','POST',body=payload,headers=dict(headers,**{'X-CSRF-Token':'bad'}))[0] == 403)
        n = len(writes)
        check('Firmware staging succeeds without WP change', request('/hmi/firmware/stage','POST',body=payload,headers=headers)[0] == 200 and len(writes) == n)
        meta = firmware.staged(app,verify=True)
        stream_headers = dict(headers,**{'X-File-Length':str(len(payload))})
        check('Streaming Ingress chunked SWU staging preserves bytes', request('/hmi/firmware/stage','POST',body=[payload[:51],payload[51:]],headers=stream_headers,chunked=True)[0] == 200 and firmware.staged(app,verify=True)['sha256'] == meta['sha256'])
        check('Chunked extra bytes rejected without replacing good stage', request('/hmi/firmware/stage','POST',body=[payload,b'extra'],headers=stream_headers,chunked=True)[0] == 400 and firmware.staged(app,verify=True) == meta)
        check('Malformed chunk framing rejected', request('/hmi/firmware/stage','POST',body=b'zz\r\n',headers=dict(stream_headers,**{'Transfer-Encoding':'chunked'}))[0] == 400)
        check('Chunked upload requires declared file length', request('/hmi/firmware/stage','POST',body=[payload],headers=headers,chunked=True)[0] == 400)
        saved = request('/hmi/firmware/file')
        check('Firmware download preserves exact bytes', saved[0] == 200 and saved[2] == payload and 'attachment' in saved[1]['Content-Disposition'])
        check('Firmware HEAD has no body', request('/hmi/firmware/file','HEAD')[2] == b'')
        denied(lambda:firmware.stage_stream(app,io.BytesIO(b'<html>'+b'x'*200),206,'wrong.swu'),'HTML instead of firmware rejected')
        check('Failed staging retains previous valid file', firmware.staged(app,verify=True) == meta)
        denied(lambda:firmware.stage_stream(app,io.BytesIO(payload),len(payload)+1,'mock.swu'),'Truncated staging rejected')
        denied(lambda:firmware.stage_stream(app,io.BytesIO(payload),firmware.MAX_BYTES+1,'mock.swu'),'Oversized staging rejected')
        denied(lambda:firmware.official_url('https://www.nibe.at.evil.invalid/file.zip'),'Download host lookalike rejected')
        denied(lambda:firmware.official_url('http://www.nibe.at/file.zip'),'Plain HTTP download rejected')
        parser=firmware.DownloadLinks();parser.feed('<a href="/assets/Software-S-Serie.zip">S-Regler</a>')
        check('Only official S-series ZIP selected', parser.links == ['https://www.nibe.at/assets/Software-S-Serie.zip'])
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:z.writestr('../../mock.swu',payload)
        old_open,old_catalog=firmware.open_official,firmware.catalog_url
        class Response(io.BytesIO):
            headers={}
        firmware.open_official=lambda url:Response(archive.getvalue())
        firmware.catalog_url=lambda:'https://www.nibe.at/assets/Software-S-Serie.zip'
        try:firmware.download(app)
        finally:firmware.open_official,firmware.catalog_url=old_open,old_catalog
        check('Official ZIP stages exact SWU without extracting archive paths', firmware.staged(app,verify=True)['sha256'] == meta['sha256'] and not (temp/'mock.swu').exists())
        check('Download never transfers to WP', len(writes) == n)
        app.WRITES_LOCKED=True
        denied(lambda:firmware.upload(app,meta['sha256'],app.WRITE_EPOCH),'Firmware transfer blocked while locked')
        app.WRITES_LOCKED=False
        denied(lambda:firmware.upload(app,'0'*64,app.WRITE_EPOCH),'Changed firmware checksum rejected')
        device['features']['fwUpdate']=False
        denied(lambda:firmware.upload(app,meta['sha256'],app.WRITE_EPOCH),'Fresh firmware capability required')
        device['features']['fwUpdate']=True
        device['product']['firmwareId']='other-family'
        denied(lambda:firmware.upload(app,meta['sha256'],app.WRITE_EPOCH),'Firmware from a different reported family rejected')
        device['product']['firmwareId']='mock'
        # Model a controller that rejects before the complete file has been sent.
        real_connection=firmware.http.client.HTTPSConnection
        class EarlyResponse:
            status=413
            def read(self,n):return b'{"message":"mock upload limit"}'
        class EarlyConnection:
            def __init__(self,*args,**kwargs):self.sock=self;self.sends=0
            def putrequest(self,*args):pass
            def putheader(self,*args):pass
            def endheaders(self):pass
            def send(self,data):
                self.sends+=1
                if self.sends==2:raise BrokenPipeError(32,'synthetic send failure')
            def settimeout(self,value):pass
            def getresponse(self):return EarlyResponse()
            def close(self):pass
        original_epoch=app.WRITE_EPOCH
        original_api=app.api
        app.api=lambda *args,**kwargs:device
        app.WRITE_EPOCH='early-rejection-test'
        firmware.http.client.HTTPSConnection=EarlyConnection
        try:
            denied(lambda:firmware.upload(app,meta['sha256'],app.WRITE_EPOCH),'Early HTTP rejection is not reported as success')
            check('Early response survives send failure',firmware.JOB['reply']['http_status']==413)
            check('Transport cause and phase retained',firmware.JOB['transport']['error_type']=='BrokenPipeError' and firmware.JOB['transport']['phase']=='file' and firmware.JOB['transport']['errno']==32)
            check('Transport diagnostic excludes token',TOKEN not in json.dumps(firmware.JOB['transport']))
        finally:
            firmware.http.client.HTTPSConnection=real_connection
            app.WRITE_EPOCH=original_epoch
            app.api=original_api
        firmware.upload(app,meta['sha256'],app.WRITE_EPOCH)
        sent = writes[-1]
        check('Firmware multipart POST uses local endpoint and file part', sent[0] == 'POST' and sent[1].endswith('/firmware') and b'name="file"' in sent[2] and payload in sent[2])
        check('Local token sent only to mock controller', sent[3]['Authorization'] == 'Token '+TOKEN)
        n = len(writes)
        denied(lambda:firmware.upload(app,meta['sha256'],app.WRITE_EPOCH),'Repeated firmware attempt requires renewed acknowledgement')
        check('Replay produced no second upload', len(writes) == n)
        check('Status distinguishes acceptance from installation', 'noch keine abgeschlossene Installation' in firmware.JOB['message'])
        check('Empty firmware response recorded as HTTP only', firmware.JOB['reply']['http_status'] == 204 and firmware.JOB['reply']['body_bytes'] == 0)
        app.WRITE_EPOCH='new-test-epoch';firmware_response=403
        firmware_body={'message':'Mock: firmware not accepted '+TOKEN,'token':TOKEN}
        denied(lambda:firmware.upload(app,meta['sha256'],app.WRITE_EPOCH),'WP firmware rejection surfaced without retry')
        check('WP rejection not automatically retried', len(writes) == n+1)
        check('Firmware rejection response captured without token',firmware.JOB['reply']['http_status']==403 and 'Mock: firmware not accepted' in json.dumps(firmware.JOB['reply']) and TOKEN not in json.dumps(firmware.JOB['reply']))
        reply_page=request('/hmi/firmware')[2].decode()
        check('Firmware reply visible on page', 'Mock: firmware not accepted' in reply_page and TOKEN not in reply_page)
        firmware_body=None
        app.WRITE_EPOCH='another-test-epoch';firmware_response=-1
        try:firmware.upload(app,meta['sha256'],app.WRITE_EPOCH)
        except firmware.FirmwareError as exc:check('Lost response reported as unknown outcome', 'unklar' in str(exc))
        else:raise AssertionError('Lost response accepted')
        check('Lost response produced one upload only', len(writes) == n+2)
        corrupt=firmware.folder(app)/'staged.swu';corrupt.write_bytes(payload[:-1]+b'x')
        check('Tampered staged firmware not downloadable', request('/hmi/firmware/file')[0] == 400)
        check('Nested secrets scrubbed in generic responses', SECRET not in json.dumps(hmi.safe({'rows':[secret],'passphrase':SECRET,'token':TOKEN})))
        hidden_rows={'rows':[{'type':'uigroup','visible':False,'rows':[secret]}]}
        check('Hidden parent hides all descendants', not app.web.visible_rows(hidden_rows))
        disabled_group={'rows':[{'type':'uigroup','enabled':False,'rows':[event]}]}
        check('Disabled parent also disables nested actions', app.web.visible_rows(disabled_group)[1]['enabled'] is False and event['enabled'] is True)
        time_row={'type':'uitimesetting','parameterId':'20','value':{'integerValue':3600,'isOk':True},'metadata':{'isWritable':True,'minValue':0,'maxValue':86340,'change':60,'divisor':1}}
        check('Time input converts hours and minutes to exact seconds', app.raw_value(time_row,'12:34') == '45240')
        check('Time value rendered as clock', app.web.fmt_value(time_row) == '01:00:00')
        denied(lambda:app.raw_value(time_row,'24:00'),'Invalid 24-hour time rejected')
        denied(lambda:app.raw_value(time_row,'12:34:01'),'Time metadata step enforced')
        check('Staged firmware can be removed without WP writes', post('firmware-remove',sha256=meta['sha256'],page='firmware')[0] == 303 and firmware.staged(app) is None and len(writes) == n+2)
        check('Explicit point chart rendered without script injection', '<svg' in hmi.chart({'text':{'text':'<script>curve</script>'},'data':{'points':[{'x':-10,'y':30},{'x':10,'y':20}]}}) and '<script>' not in hmi.chart({'text':{'text':'<script>curve</script>'},'data':{'points':[{'x':-10,'y':30},{'x':10,'y':20}]}}))
        firmware.stage_stream(app,io.BytesIO(payload),len(payload),'mock.swu')
        app.WRITE_EPOCH='background-transfer-test';firmware_response=204;block_upload=True
        check('Background firmware starts only after confirmed POST', post('firmware-upload',sha256=meta['sha256'],page='firmware')[0] == 303 and upload_received.wait(2))
        before=time.monotonic();in_progress=request('/hmi/firmware')
        check('Firmware progress view remains responsive while transfer holds write lock', in_progress[0] == 200 and time.monotonic()-before < 2 and b'firmware-status' in in_progress[2])
        check('Progress API readable during transfer', json.loads(request('/hmi/firmware/status')[2])['state'] == 'running')
        count=len(writes)
        check('Other HMI writes rejected rather than queued during transfer', post('aidmode',expected=hmi.signature('off'),aidmode='on')[0] == 403 and len(writes) == count)
        check('Old menu writes rejected rather than queued during transfer', request('/write/value','POST',{'csrf':app.CSRF,'epoch':app.WRITE_EPOCH,'menu':'50','parameter':'10','value':'20'})[0] == 423 and len(writes) == count)
        lock_result=[]
        def relock():lock_result.append(request('/write/lock','POST',{'csrf':app.CSRF,'epoch':app.WRITE_EPOCH,'menu':'0'}))
        lock_thread=threading.Thread(target=relock);lock_thread.start();time.sleep(.1)
        check('Lock acknowledgement waits until the existing transfer finishes', not lock_result)
        upload_release.set();lock_thread.join(5)
        check('Lock becomes active after in-flight transfer', bool(lock_result) and lock_result[0][0] == 303 and app.WRITES_LOCKED)
        for _ in range(30):
            if not firmware.JOB_LOCK.locked():break
            time.sleep(.05)
        check('Background transfer completes once', firmware.JOB['state'] == 'done' and not firmware.JOB_LOCK.locked() and len(writes) == count)

        response=type('Response',(),{'status':200})()
        parsed=firmware.response_summary(app,response,json.dumps({'previouslyUnknown':{'value':7,'password':SECRET},'echo':TOKEN}).encode(),TOKEN)
        check('Unknown JSON fields retained with nested secrets masked',parsed['details']['previouslyUnknown']['value']==7 and SECRET not in json.dumps(parsed) and TOKEN not in json.dumps(parsed))
        app.WRITES_LOCKED=False;app.WRITE_EPOCH='retry-behavior-test';firmware_response=400;block_upload=False
        firmware.start_job(app,'upload',sha256=meta['sha256'],epoch=app.WRITE_EPOCH)
        for _ in range(100):
            if not firmware.JOB_LOCK.locked():break
            time.sleep(.03)
        check('Failed upload asks for explicit retry',firmware.JOB['state']=='error' and bool(firmware.JOB.get('retry_nonce')) and b'Erneut versuchen?' in request('/hmi/firmware')[2])
        retry=firmware.JOB['retry_nonce'];count=len(writes)
        time.sleep(.1)
        check('No automatic firmware retry',len(writes)==count)
        denied(lambda:firmware.start_job(app,'upload',sha256=meta['sha256'],epoch=app.WRITE_EPOCH,retry_nonce='wrong'),'Invalid retry proof rejected')
        check('Retry without acknowledgement blocked',post('firmware-upload',sha256=meta['sha256'],retry_nonce=retry,ack='',page='firmware')[0]==400 and len(writes)==count)
        firmware_response=204
        check('Explicit retry POST accepted',post('firmware-upload',sha256=meta['sha256'],retry_nonce=retry,page='firmware')[0]==303)
        for _ in range(100):
            if not firmware.JOB_LOCK.locked():break
            time.sleep(.03)
        check('Confirmed retry sends exactly one new upload',firmware.JOB['state']=='done' and len(writes)==count+1)
        denied(lambda:firmware.start_job(app,'upload',sha256=meta['sha256'],epoch=app.WRITE_EPOCH,retry_nonce=retry),'Consumed retry proof cannot replay')

    finally:
        upload_release.set()
        server.shutdown();server.server_close();mock.shutdown();mock.server_close()
for name in checks: print('OK:',name)
print('PASS:',len(checks),'HMI/firmware behavior checks; no real controller writes.')
