from pathlib import Path
import sys,threading,http.client,urllib.parse,json,copy,re
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'nibe_local'))
import nibe_app as app
import nibe_web as web
from http.server import ThreadingHTTPServer
checks=[]
def check(name,condition):
 assert condition,name
 checks.append('OK: '+name)
username={'type':'uitextsetting','parameterId':'61600','enabled':True,'text':{'text':'User name'},'value':{'isOk':True,'stringValue':'olduser'},'metadata':{'isWritable':True,'variableType':'string'},'maxLen':32,'regex':'[A-Za-z0-9_]{1,32}','inputHelper':'Buchstaben, Zahlen und Unterstrich'}
password={'type':'uitextsetting','parameterId':'61601','enabled':True,'text':{'text':'Password'},'value':{'isOk':True,'stringValue':'OldPassword123'},'metadata':{'isWritable':True,'variableType':'string'},'maxLen':32,'regex':'[A-Za-z0-9]{12,32}'}
num={'type':'uinumerical','parameterId':'100','text':{'text':'Heating'},'value':{'isOk':True,'integerValue':0},'metadata':{'isWritable':True,'divisor':1,'minValue':-10,'maxValue':10,'change':1}}
menu={'id':732,'name':{'text':'Local REST API'},'rows':[{'type':'uiheader','text':{'text':'Zugangsdaten'}},username,password,num]}
patches=[]
def fake_api(host,port,path,token=None,body=None,method=None):
 if method=='PATCH':
  patches.append(copy.deepcopy(body))
  for row in menu['rows']:
   if str(row.get('parameterId')) in body:
    k='stringValue' if row.get('type')=='uitextsetting' else 'integerValue'
    row['value'][k]=body[str(row['parameterId'])] if k=='stringValue' else int(body[str(row['parameterId'])])
  return None
 return copy.deepcopy(menu)
app.api=fake_api;app.activate({'host':'192.168.50.10','port':8888,'serial':'00000000000000','token':'test-only'})
web.CFG.update(ingress=True,auth=None,cache=5)
web.INGRESS_PEERS=('127.0.0.1',)
server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
def request(path,method='GET',fields=None):
 headers={'X-Ingress-Path':'/api/hassio_ingress/test'};body=None
 if fields is not None:
  body=urllib.parse.urlencode(fields);headers['Content-Type']='application/x-www-form-urlencoded'
 c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);c.request(method,path,body,headers);r=c.getresponse();out=(r.status,dict(r.getheaders()),r.read().decode());c.close();return out
def post(path,**values):
 fields={'csrf':app.CSRF,'epoch':app.WRITE_EPOCH,'menu':'732','trail':'0,160,302'};fields.update(values);return request(path,'POST',fields)
try:
 result=request('/menu/732?trail=0,160,302')
 check('Zurück führt genau eine Ebene nach 302',"href='/api/hassio_ingress/test/menu/302?trail=0,160'" in result[2])
 check('Passwort weder im HTML noch im Hidden-Feld',password['value']['stringValue'] not in result[2])
 check('Passwort auch in JSON maskiert',password['value']['stringValue'] not in request('/api/menu/732')[2] and '********' in request('/api/menu/732')[2])
 check('Passwortauge mit Beschriftung vorhanden',"data-eye data-menu='732' data-parameter='61601'" in result[2])
 check('Passwortabruf ohne CSRF abgewiesen',request('/secret/reveal','POST',{'menu':'732','parameter':'61601'})[0]==403)
 reveal=post('/secret/reveal',parameter='61601')
 check('Passwort nur auf expliziten Abruf geliefert',reveal[0]==200 and json.loads(reveal[2])['value']=='OldPassword123')
 check('Passwortabruf schreibt keine WP-Werte',not patches)
 check('Passwortabruf für normalen Parameter nicht erlaubt',post('/secret/reveal',parameter='61600')[0]==404)
 result=post('/write/unlock',ack='yes')
 check('Navigation bleibt beim Entsperren erhalten',result[1]['Location'].endswith('/menu/732?trail=0,160,302'))
 result=request('/menu/732?trail=0,160,302')
 check('Genau ein Editor pro Wert in der Menüliste',result[2].count("class='row edit-row'")==3 and result[2].count('>Heating<')==1)
 check('Editoren stehen unter Menüüberschrift',result[2].index('<h1>') < result[2].index("class='row edit-row'"))
 check('Passwortfeld leer statt Klartext-Vorbelegung',re.search(r"type='password'[^>]*name='value'[^>]*value=''", result[2]) is not None and 'OldPassword123' not in result[2])
 check('Username übernommen',"value='olduser'" in result[2])
 check('Unveränderte Texte serverseitig mit HMAC abgeglichen',len(app.expected_value(username))==64 and app.expected_value(username)!='olduser')
 result=post('/write/value',parameter='61600',value='new_user',expected=app.expected_value(username))
 check('Username als String geschrieben',result[0]==303 and patches[-1]=={'61600':'new_user'})
 before=len(patches)
 check('Username-Regel geprüft',post('/write/value',parameter='61600',value='bad user!',expected=app.expected_value(username))[0]==400 and len(patches)==before)
 check('Zu kurzes Passwort abgewiesen',post('/write/value',parameter='61601',value='short',expected=app.expected_value(password))[0]==400 and len(patches)==before)
 check('Leeres Passwort abgewiesen',post('/write/value',parameter='61601',value='',expected=app.expected_value(password))[0]==400 and len(patches)==before)
 check('Zu langes Passwort abgewiesen',post('/write/value',parameter='61601',value='A'*33,expected=app.expected_value(password))[0]==400 and len(patches)==before)
 result=post('/write/value',parameter='61601',value='NewPassword12345',expected=app.expected_value(password))
 check('Gültiges Passwort als String geschrieben',result[0]==303 and patches[-1]=={'61601':'NewPassword12345'})
 check('Keine Passwortübernahme in Weiterleitung','NewPassword12345' not in str(result))
 before=len(patches)
 check('Geänderten Text-Ausgangswert erkannt',post('/write/value',parameter='61600',value='neweruser',expected='0'*64)[0]==409 and len(patches)==before)
 check('Ungültiger Navigationspfad abgewehrt',request('/menu/732?trail=0,../')[0]==400)
 js=request('/static/app.js')
 check('Browser-Skript ausgeliefert',js[0]==200 and 'application/javascript' in js[1]['Content-Type'])
 check('CSP erlaubt nur eigenes Skript',"script-src 'self'" in js[1]['Content-Security-Policy'] and "script-src 'unsafe-inline'" not in js[1]['Content-Security-Policy'])
 post('/write/lock')
 before=len(patches)
 check('Textänderung bei Sperre abgewiesen',post('/write/value',parameter='61600',value='nope',expected=app.expected_value(username))[0]==403 and len(patches)==before)
 check('Deaktivierte Felder nicht editierbar',"type='text'" not in app.editable_menu('732',dict(menu,rows=[dict(username,enabled=False)])))
finally:
 server.shutdown();server.server_close()
print('\n'.join(checks))
