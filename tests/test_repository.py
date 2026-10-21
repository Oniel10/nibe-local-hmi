from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
app=ROOT/'nibe_local'
c=json.loads((app/'config.yaml').read_text(encoding='utf-8'))
assert c['version']==(ROOT/'VERSION').read_text().strip()
assert c['name']=='NIBE Local HMI' and c['slug']=='nibe_local'
assert c['stage']=='experimental' and c['ingress'] and c['panel_admin']
assert c['host_network'] and c['ingress_port']==8099
assert c['ingress_stream'] is True
assert not c.get('hassio_api') and not c.get('supervisor_api') and not c.get('docker_api')
assert not c.get('map') and not c.get('privileged')
assert c['schema']=={'cache_seconds':'int(1,300)'}
assert 'image' not in c
for name in ('nibe_app.py','nibe_web.py','nibe_hmi.py','nibe_firmware.py','nibe_hmi.js','nibe_ui.js','requirements.txt','Dockerfile','README.md','DOCS.md','CHANGELOG.md','LICENSE','icon.png','logo.png'):
 assert (app/name).is_file(),name
for name in ('nibe_app.py','nibe_web.py','nibe_hmi.py','nibe_firmware.py','nibe_http.py'):
 compile((app/name).read_text(encoding='utf-8'),name,'exec')
for p in ROOT.rglob('*'):
 if not p.is_file() or any(x in p.parts for x in ('.git','__pycache__','.venv')):continue
 assert p.suffix.lower() not in ('.key','.enc','.apk','.dex','.zip','.gz'),p
 assert p.name not in ('options.json','INSTALLATIONSSTATUS.txt','TESTERGEBNIS.txt'),p
assert c['version'] in (app/'Dockerfile').read_text()
assert c['version'] in (app/'nibe_app.py').read_text(encoding='utf-8')
print('OK: repository structure, version, experimental stage, least privileges and distribution hygiene')
