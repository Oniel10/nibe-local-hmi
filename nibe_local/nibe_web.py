#!/usr/bin/env python3
"""Shared NIBE menu rendering and HTTP helpers. The HA app starts nibe_app.py."""
import base64, hmac, html, json, os, re, ssl, sys, threading, time
import urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CFG = {}
_lock = threading.Lock()          # WP bekommt immer nur eine Anfrage gleichzeitig
_cache = {}                       # id -> (zeitpunkt, daten)
_ctx = ssl.create_default_context()
_ctx.check_hostname = False       # wie die App: Hostname egal
_ctx.verify_mode = ssl.CERT_NONE  # TODO später: gegen nibe_jpi_root_ca_bundle.pem prüfen


class WpError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


def wp_menu(mid):
    """Menü von der WP holen (mit kurzem Cache, seriell)."""
    now = time.time()
    hit = _cache.get(mid)
    if hit and now - hit[0] < CFG["cache"]:
        return hit[1]
    with _lock:
        hit = _cache.get(mid)
        if hit and time.time() - hit[0] < CFG["cache"]:
            return hit[1]
        url = f'{CFG["scheme"]}://{CFG["host"]}:{CFG["port"]}/devices/{CFG["serial"]}/menu/{mid}'
        req = urllib.request.Request(url, headers={"Authorization": "Token " + CFG["token"],
                                                   "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, context=_ctx, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise WpError(502, "Die WP lehnt das Token ab (401). Erneut koppeln?")
            if e.code == 404:
                raise WpError(404, "Dieses Menü gibt es auf der WP nicht.")
            raise WpError(502, f"WP antwortet mit HTTP {e.code}.")
        except Exception:
            raise WpError(502, "WP nicht erreichbar.")
        _cache[mid] = (time.time(), data)
        return data


def clean(s):
    return (s or "").replace("­", "").strip()   # weiche Trennstriche entfernen


def label(row):
    return clean((row.get("text") or {}).get("text"))


def fmt_value(row):
    """Wert einer Zeile menschenlesbar machen. Gibt (text, einheit_enthalten) zurück."""
    v = row.get("value")
    if not isinstance(v, dict):
        return None
    md = row.get("metadata") or {}
    iv, sv = v.get("integerValue"), v.get("stringValue")
    if v.get("isOk") is False:
        return "–"
    for o in row.get("options") or []:
        if o.get("value") == iv:
            return clean((o.get("text") or {}).get("text")) or str(iv)
    if row.get("type") == "uiboolean":
        return "Ein" if iv else "Aus"
    if sv:
        return clean(sv)
    if iv is None:
        return "–"
    div = md.get("divisor") or 1
    dec = md.get("decimal") or 0
    txt = f"{iv / div:.{dec}f}".replace(".", ",")
    unit = clean(md.get("unit") or md.get("shortUnit"))
    return f"{txt} {unit}".strip()


def visible_rows(menu):
    out = []
    for r in menu.get("rows") or []:
        if r.get("visible") is False or r.get("hideLocal"):   # wie die App im lokalen Modus
            continue
        out.append(r)
    return out


CSS = """
button,.button,input,select{font:inherit}button,.button{border:1px solid var(--line);background:var(--bg);color:var(--fg);padding:.45rem .7rem;border-radius:7px;cursor:pointer;text-decoration:none}button:hover,.button:hover{border-color:var(--acc)}
input,select{background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:.4rem .55rem;max-width:100%;min-width:0}input[type=number]{width:7rem}input[type=text],input[type=password]{width:15rem}input:focus-visible,button:focus-visible,a:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.menu-nav{display:flex;align-items:center;gap:1rem;flex-wrap:wrap}.edit-row{margin:0;align-items:center;flex-wrap:wrap}.row-label{flex:1 1 12rem}.row-editor{display:flex;align-items:center;justify-content:flex-end;gap:.5rem;flex:1 1 20rem;flex-wrap:wrap}.field-help{flex-basis:100%;text-align:right;color:var(--mut);font-size:.8rem}.secret-control{display:inline-flex;align-items:center;gap:.3rem;max-width:100%}.eye{padding:.4rem .55rem}.note p{margin:.5rem 0}.edit-row .row-editor select{max-width:15rem}
@media(max-width:480px){.row-editor{justify-content:flex-start}.field-help{text-align:left}.row{gap:.5rem}main{padding:12px}}

:root{--bg:#f6f7f9;--fg:#1c2330;--mut:#667085;--card:#fff;--line:#e3e6eb;--acc:#0b6bcb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1319;--fg:#e6e9ef;--mut:#9aa4b2;--card:#171c25;--line:#262d3a;--acc:#5aa9ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:1.25rem;margin:.2rem 0 .1rem}.num{color:var(--mut);font-weight:400}
nav a{color:var(--acc);text-decoration:none}nav{margin-bottom:.6rem;font-size:.95rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:.6rem 0;overflow:hidden}
.row{display:flex;justify-content:space-between;gap:12px;padding:.7rem .9rem;border-top:1px solid var(--line)}
.row:first-child{border-top:0}.row .v{font-variant-numeric:tabular-nums;text-align:right;font-weight:600}
a.row{color:inherit;text-decoration:none}a.row:hover{background:var(--bg)}a.row::after{content:"›";color:var(--mut)}
.hdr{padding:.5rem .9rem;background:var(--bg);color:var(--mut);font-size:.8rem;text-transform:uppercase;letter-spacing:.04em}
.mut{color:var(--mut);font-size:.85rem}.ro{font-size:.75rem;color:var(--mut);margin-left:.4rem}
details{margin:.5rem 0;color:var(--mut);font-size:.9rem}.note{padding:1rem;color:var(--mut)}
"""


def page(title, body):
    return ("<!doctype html><html lang=de><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<meta http-equiv=refresh content=30>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")


def render_menu(mid, menu, base=""):
    e = html.escape
    name = clean((menu.get("name") or {}).get("text")) or f"Menü {mid}"
    num = clean(menu.get("number"))
    parts = [f"<nav><a href='{base}/menu/0'>Hauptmenü</a> · <a href='{base}/setup'>Verbindung</a></nav>",
             f"<h1>{e(name)} <span class=num>{e(num)}</span></h1>"]
    helptext = clean((menu.get("help") or {}).get("text"))
    if helptext:
        parts.append(f"<details><summary>Hilfe</summary>{e(helptext).replace(chr(10), '<br>')}</details>")
    rows = visible_rows(menu)
    if not rows:
        parts.append("<div class='card note'>Dieses Menü ist ein Sonderbildschirm der App "
                     "(z. B. Zeit, Alarmlog, Zeitplan) oder leer und wird hier nicht dargestellt.</div>")
        return page(name, "".join(parts))
    parts.append("<div class=card>")
    for r in rows:
        t, lab = r.get("type"), e(label(r))
        if t == "uiheader":
            parts.append(f"<div class=hdr>{lab}</div>")
        elif t == "uilink":
            if r.get("isMenuChain"):
                parts.append(f"<div class=row><span>{lab}</span><span class=mut>Menükette (nicht unterstützt)</span></div>")
            else:
                parts.append(f"<a class=row href='{base}/menu/{int(r.get('id', 0))}'><span>{r.get('number') and e(str(r['number']) + '  ') or ''}{lab}</span></a>")
        elif t in ("uievent", "uiaction"):
            parts.append(f"<div class=row><span>{lab}</span><span class=mut>Aktion, hier nicht verfügbar</span></div>")
        else:
            val = fmt_value(r)
            if val is None:
                txt = clean((r.get("value") or {}).get("stringValue")) if isinstance(r.get("value"), dict) else ""
                parts.append(f"<div class=row><span>{lab}</span><span class='v mut'>{e(txt) or '·'}</span></div>")
            else:
                ro = "" if (r.get("metadata") or {}).get("isWritable") else "<span class=ro>nur lesen</span>"
                parts.append(f"<div class=row><span>{lab}{ro}</span><span class=v>{e(val)}</span></div>")
    parts.append("</div><p class=mut>Nur-Lese-Ansicht · aktualisiert sich alle 30 s</p>")
    return page(name, "".join(parts))


def api_menu(menu):
    out = []
    for r in visible_rows(menu):
        t = r.get("type")
        item = {"type": t, "label": label(r)}
        if t == "uilink":
            item["id"] = r.get("id")
        elif isinstance(r.get("value"), dict):
            md = r.get("metadata") or {}
            item.update(parameterId=r.get("parameterId"), value=fmt_value(r),
                        raw=r["value"].get("integerValue"), unit=clean(md.get("unit")),
                        divisor=md.get("divisor"), writable=bool(md.get("isWritable")))
        out.append(item)
    return {"id": menu.get("id"), "number": menu.get("number"),
            "name": clean((menu.get("name") or {}).get("text")), "rows": out}


class H(BaseHTTPRequestHandler):
    server_version = "nibe-web"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):           # keine Pfade/Header ins Log, schon gar keine Tokens
        sys.stderr.write("NIBE HTTP %s\n" % (a[1] if len(a) > 1 else "request"))

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(b)

    def _authed(self):
        if not CFG["auth"]:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(h[6:]).decode("utf-8").partition(":")
                ok_u = hmac.compare_digest(u.encode(), CFG["auth"][0].encode())
                ok_p = hmac.compare_digest(p.encode(), CFG["auth"][1].encode())
                return ok_u and ok_p
            except Exception:
                return False
        return False

    def _base(self):
        """Ingress-Präfix von Home Assistant (nur wenn es genau das erwartete Format hat)."""
        p = self.headers.get("X-Ingress-Path", "")
        return p if CFG["ingress"] and re.fullmatch(r"/api/hassio_ingress/[A-Za-z0-9_-]{1,128}", p) else ""

    def do_GET(self):
        if CFG["ingress"] and self.client_address[0] not in INGRESS_PEERS:
            return self._send(403, page("Verboten", "<p>Nur über Home Assistant erreichbar.</p>"))
        if not self._authed():
            return self._send(401, page("Anmeldung nötig", "<p>Anmeldung nötig.</p>"),
                              extra={"WWW-Authenticate": 'Basic realm="NIBE", charset="UTF-8"'})
        base = self._base()
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._send(302, "", extra={"Location": base + "/menu/0"})
        m = re.fullmatch(r"/(api/)?menu/(\d{1,9})", path)
        if not m:
            return self._send(404, page("Nicht gefunden", f"<p>Nicht gefunden. <a href='{base}/menu/0'>Hauptmenü</a></p>"))
        try:
            menu = wp_menu(m.group(2))
        except WpError as e:
            if m.group(1):
                return self._send(e.status, json.dumps({"error": e.msg}), "application/json")
            return self._send(e.status, page("Fehler", f"<nav><a href='{base}/menu/0'>Hauptmenü</a></nav><p>{html.escape(e.msg)}</p>"))
        if m.group(1):
            return self._send(200, json.dumps(api_menu(menu), ensure_ascii=False), "application/json; charset=utf-8")
        return self._send(200, render_menu(m.group(2), menu, base))

    do_HEAD = do_GET

    def _no_write(self):
        self.close_connection = True
        self._send(405, page("Nur lesen", "<p>Diese Oberfläche ist nur lesend.</p>"), extra={"Allow": "GET, HEAD"})
    do_POST = do_PUT = do_PATCH = do_DELETE = _no_write


INGRESS_PEERS = ("172.30.32.2",)   # Supervisor ingress gateway only


def main():
    envget = os.environ.get
    opt = {}
    opt_file = envget("NIBE_OPTIONS", "/data/options.json")     # Optionen der Home-Assistant-App
    if os.path.isfile(opt_file):
        try:
            with open(opt_file, encoding="utf-8") as f:
                opt = json.load(f)
        except Exception:
            sys.exit("Optionsdatei nicht lesbar.")

    def env(name, optname=None, default=None):
        v = envget(name)
        if v:
            return v
        if optname and opt.get(optname) not in (None, ""):
            return str(opt[optname])
        return default

    for name, o in (("NIBE_HOST", "host"), ("NIBE_SERIAL", "serial"), ("NIBE_TOKEN", "token")):
        if not env(name, o):
            sys.exit(f"{name} fehlt (Umgebungsvariable bzw. App-Option '{o}' setzen).")
    CFG.update(host=env("NIBE_HOST", "host"), serial=env("NIBE_SERIAL", "serial"),
               token=env("NIBE_TOKEN", "token"), port=env("NIBE_PORT", "port", "8888"),
               scheme=env("NIBE_SCHEME", None, "https"),
               cache=float(env("CACHE_SECONDS", "cache_seconds", "5")),
               ingress=envget("INGRESS") == "1")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", CFG["host"]):
        sys.exit("Host muss eine IPv4-Adresse oder ein DNS-Name sein, ohne URL oder Port.")
    if CFG["scheme"] not in ("https", "http"):
        sys.exit("Ungueltiges API-Protokoll.")
    try:
        if not 1 <= int(CFG["port"]) <= 65535 or not 1 <= CFG["cache"] <= 300:
            raise ValueError
    except ValueError:
        sys.exit("Port oder Cache-Dauer ungueltig.")
    if any(ord(c) < 32 or ord(c) > 126 for c in CFG["token"]):
        sys.exit("Token enthaelt unerlaubte Zeichen.")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", CFG["serial"]):
        sys.exit("Seriennummer enthält unerlaubte Zeichen.")
    if CFG["ingress"]:       # Home-Assistant-App: Login übernimmt HA, erreichbar nur über das Ingress-Gateway
        bind, port, CFG["auth"] = envget("WEB_BIND", "0.0.0.0"), int(envget("WEB_PORT", "8099")), None
        print(f"NIBE-Web (nur lesend) als HA-Ingress auf {bind}:{port}")
    else:
        bind, port = envget("WEB_BIND", "127.0.0.1"), int(envget("WEB_PORT", "8080"))
        u, p = envget("WEB_USER"), envget("WEB_PASS")
        CFG["auth"] = (u, p) if u and p else None
        if bind not in ("127.0.0.1", "::1", "localhost") and not CFG["auth"]:
            sys.exit("Bei WEB_BIND außerhalb von localhost sind WEB_USER und WEB_PASS Pflicht.")
        print(f"NIBE-Web (nur lesend) auf http://{bind}:{port}  ·  Auth: {'an' if CFG['auth'] else 'aus (nur localhost)'}")
    ThreadingHTTPServer((bind, port), H).serve_forever()


if __name__ == "__main__":
    main()
