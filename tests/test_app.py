from pathlib import Path
import sys
sys.stdout.reconfigure(encoding="utf-8")
import tempfile
import threading
import json
import ssl
import http.client
import urllib.parse
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.exceptions import InvalidTag

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'nibe_local'))
import nibe_app as app
import nibe_web as web
RESULT=[]
def check(name,value):
    assert value,name
    RESULT.append('OK: '+name)

SERIAL='00000000000000'
TOKEN='mock-token-never-show'
fixture={'type':'uinumerical','parameterId':'100','text':{'text':'Temperatur'},'value':{'integerValue':200,'isOk':True},'metadata':{'isWritable':True,'divisor':10,'decimal':1,'minValue':100,'maxValue':300,'change':5,'unit':'°C'}}
row=json.loads(json.dumps(fixture))
patches=[];requests=[];pairing='pending';failpatch=False
class Mock(BaseHTTPRequestHandler):
    def log_message(self,*a):pass
    def respond(self,code,obj=None):
        data=json.dumps(obj).encode() if obj is not None else b''
        self.send_response(code);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        requests.append(('GET',self.path,self.headers.get('Authorization')))
        if self.path=='/devices':return self.respond(200,{'devices':[{'product':{'serialNumber':SERIAL,'name':'NIBE Mock'}}]})
        if self.headers.get('Authorization')!='Token '+TOKEN:return self.respond(401,{'token':TOKEN})
        if self.path==f'/devices/{SERIAL}':return self.respond(200,{'pairingState':pairing})
        if self.path==f'/devices/{SERIAL}/menu/711':return self.respond(200,{'id':711,'name':{'text':'Heizung'},'rows':[row]})
        if self.path==f'/devices/{SERIAL}/menu/0':return self.respond(200,{'id':0,'name':{'text':'Hauptmenü'},'rows':[{'type':'uilink','id':711,'text':{'text':'Heizung'}}]})
        return self.respond(404,{})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        requests.append(('POST',self.path,body))
        state=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)['state'][0]
        return self.respond(200,{'state':state,'token':TOKEN,'clientIdentifier':body['identifier']})
    def do_PATCH(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        patches.append((self.path,self.headers.get('Authorization'),body))
        if failpatch:return self.respond(403,{'secret':TOKEN})
        row['value']['integerValue']=int(body['100'])
        return self.respond(204)

with tempfile.TemporaryDirectory() as tmp:
    temp=Path(tmp)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    now=datetime.datetime.now(datetime.timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)).sign(key,hashes.SHA256()))
    (temp/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (temp/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    mock=ThreadingHTTPServer(('127.0.0.1',0),Mock)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(temp/'cert.pem',temp/'key.pem');mock.socket=context.wrap_socket(mock.socket,server_side=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    for srv in (mock,server):threading.Thread(target=srv.serve_forever,daemon=True).start()
    original_host=app.local_host
    app.local_host=lambda h:'127.0.0.1' if h=='127.0.0.1' else original_host(h)
    app.STORE=app.EncryptedStore(temp/'data')
    web.CFG.update(ingress=True,auth=None,cache=5,scheme='https')
    def request(path,method='GET',form=None):
        c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
        headers={'X-Ingress-Path':'/api/hassio_ingress/mock'}
        body=None
        if form is not None:
            body=urllib.parse.urlencode(form);headers['Content-Type']='application/x-www-form-urlencoded'
        c.request(method,path,body=body,headers=headers);r=c.getresponse();result=(r.status,dict(r.getheaders()),r.read().decode());c.close();return result
    def post(path,**fields):
        values={'csrf':app.CSRF,'epoch':app.WRITE_EPOCH,'menu':'711'};values.update(fields)
        return request(path,'POST',values)
    try:
        check('Default: Änderungen gesperrt',app.WRITES_LOCKED)
        check('Direkter Zugriff trotz gefälschtem Ingress-Header gesperrt',request('/setup')[0]==403)
        web.INGRESS_PEERS=('127.0.0.1',) # Test only: emulate gateway.
        check('Einrichtung ohne manuelles Token erreichbar',request('/')[0]==200)
        devices=app.device_list('127.0.0.1',mock.server_port)
        check('Seriennummer automatisch einschließlich führender Null',devices[0]['serial']==SERIAL)
        cid=app.remember(devices[0])
        check('CSRF vor Kopplung erforderlich',request('/setup/pair','POST',{'device':cid})[0]==403)
        check('Pairing durchgeführt',post('/setup/pair',device=cid)[0]==303)
        body=next(x[2] for x in requests if x[0]=='POST')
        check('Kopplungsname exakt Home Assistant',body['name']=='Home Assistant')
        check('Eigene 16-stellige Hex-ID und Android-Typ',len(body['identifier'])==16 and int(body['identifier'],16)>=0 and body['type']=='Android')
        check('Ausstehende Freigabe wird angezeigt','freigeben' in app.STATUS)
        check('Token im HTML nicht sichtbar',TOKEN not in request('/setup')[2])
        check('Token-Datei enthält keinen Klartext',TOKEN.encode() not in app.STORE.cipherfile.read_bytes())
        check('Schlüsseldatei mit 256 Bit',len(app.STORE.keyfile.read_bytes())==32)
        saved=app.STORE.load()
        check('Kopplung nach neuem Store-Objekt entschlüsselbar',app.EncryptedStore(temp/'data').load()==saved)
        cipher=app.STORE.cipherfile.read_bytes();app.STORE.save(saved)
        check('Neuer Nonce bei erneutem Speichern',cipher!=app.STORE.cipherfile.read_bytes())
        blob=app.STORE.cipherfile.read_bytes();app.STORE.cipherfile.write_bytes(blob[:-1]+bytes([blob[-1]^1]))
        try:app.STORE.load();raise AssertionError('Tamper accepted')
        except app.StorageError:check('Manipulierter Ciphertext wird abgewiesen',True)
        app.STORE.cipherfile.write_bytes(blob)
        keybytes=app.STORE.keyfile.read_bytes();app.STORE.keyfile.unlink()
        try:app.STORE.load();raise AssertionError('Missing key accepted')
        except app.StorageError:check('Fehlender Schlüssel wird nicht ersetzt',not app.STORE.keyfile.exists())
        app.STORE.keyfile.write_bytes(keybytes)
        r=request('/menu/711')
        check('Haftungshinweis und unmarkierte Pflicht-Checkbox sichtbar','soweit gesetzlich' in r[2] and "name='ack' value='yes' required" in r[2] and ' checked' not in r[2])
        check('Eingabefeld bei Sperre verborgen',"name='value'" not in r[2])
        check('Schreibversuch bei aktiver Sperre abgewiesen',post('/write/value',parameter='100',value='21',expected='200')[0]==403 and not patches)
        check('Entsperren ohne aktive Bestätigung abgewiesen',post('/write/unlock')[0]==403 and app.WRITES_LOCKED)
        old_epoch=app.WRITE_EPOCH
        check('Entsperren mit Bestätigung',post('/write/unlock',ack='yes')[0]==303 and not app.WRITES_LOCKED)
        check('Altes Formular nach Freigabe ungültig',post('/write/value',epoch=old_epoch,parameter='100',value='21',expected='200')[0]==409 and not patches)
        check('Eingabefeld nach Entsperren sichtbar',"name='value'" in request('/menu/711')[2])
        check('Grenze serverseitig geprüft',post('/write/value',parameter='100',value='31',expected='200')[0]==400 and not patches)
        check('Schrittweite serverseitig geprüft',post('/write/value',parameter='100',value='20.1',expected='200')[0]==400 and not patches)
        check('Unbekannter Parameter abgewiesen',post('/write/value',parameter='999',value='21',expected='200')[0]==400 and not patches)
        row['metadata']['isWritable']=False
        check('Schreibbarkeit frisch von WP geprüft',post('/write/value',parameter='100',value='21',expected='200')[0]==400 and not patches)
        row['metadata']['isWritable']=True
        check('Veralteter Ausgangswert abgewiesen',post('/write/value',parameter='100',value='21',expected='195')[0]==409 and not patches)
        check('Gültiger Wert als PATCH geschrieben',post('/write/value',parameter='100',value='21,5',expected='200')[0]==303)
        check('Exakter Rohwert und Authentifizierung',patches[-1]==(f'/devices/{SERIAL}/points','Token '+TOKEN,{'100':'215'}))
        check('Aktueller Wert nach Änderung lesbar','21,5 °C' in request('/api/menu/711')[2])
        failpatch=True
        r=post('/write/value',parameter='100',value='22',expected='215')
        check('WP-403 ohne Token-Leak behandelt',r[0]==502 and '403' in r[2] and TOKEN not in r[2])
        count=len(patches)
        check('Erneutes Sperren ohne weitere Bestätigung',post('/write/lock')[0]==303 and app.WRITES_LOCKED)
        check('Nach Sperren kein PATCH möglich',post('/write/value',parameter='100',value='22',expected='215')[0]==403 and len(patches)==count)
        for method in ('PUT','PATCH','DELETE'):
            check('Direktes '+method+' nicht erlaubt',request('/api/menu/711',method)[0]==405)
        check('HEAD ohne Inhalt',request('/menu/711','HEAD')[2]=='')
        check('JSON-Ansicht ohne Token',TOKEN not in request('/api/menu/711')[2])
        check('Zahlenformat: ungültige Sonderwerte abgewehrt',all(app.raw_value(fixture,x)=='215' for x in ('21.5','21,5')))
        for bad in ('NaN','Infinity','1e20','20.11'):
            try:app.raw_value(fixture,bad);raise AssertionError(bad)
            except ValueError:check('Ungültige Eingabe '+bad+' abgewehrt',True)
        boolrow={'type':'uiboolean','value':{'integerValue':0},'metadata':{'isWritable':True,'minValue':0,'maxValue':1,'change':1,'divisor':1}}
        check('Boolescher Wert validiert',app.raw_value(boolrow,'1')=='1')
        enumrow=dict(boolrow,type='uidropdown',options=[{'value':0,'text':{'text':'Aus'}},{'value':2,'text':{'text':'Heizen'}}],metadata={'isWritable':True,'minValue':0,'maxValue':2,'change':1,'divisor':1})
        try:app.raw_value(enumrow,'1');raise AssertionError('enum')
        except ValueError:check('Nicht angebotener Auswahlwert abgewehrt',True)
    finally:
        server.shutdown();server.server_close();mock.shutdown();mock.server_close()
print('\n'.join(RESULT))
