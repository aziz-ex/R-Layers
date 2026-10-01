#!/usr/bin/env python3
"""R-Layers local web UI.
 
Run from the project folder:  python3 rlayers_web.py
It opens http://127.0.0.1:8765 in the default browser.
 
On start-up it reads the system kernel and language, then decides whether the
interface needs translating. English is the source language, so no translation
is needed there. To add a language, drop locales/<code>.json next to this
script (same keys as STRINGS["en"]; missing keys fall back to English).
"""
import contextlib
import io
import json
import os
import platform
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
 
os.chdir(Path(__file__).resolve().parent)
from layer import PackageAbstractionLayer  # noqa: E402
 
HOST, PORT = "127.0.0.1", 8765
TOKEN = secrets.token_urlsafe(16)
LOCK = threading.Lock()
RTL = {"ar", "he", "fa", "ur"}
layer = PackageAbstractionLayer()
 
STRINGS = {
    "en": {
        "title": "R-Layers", "subtitle": "Package and app manager",
        "language": "Language", "auto": "System default",
        "kernel": "Kernel", "arch": "Architecture", "syslang": "System language",
        "translation": "Translation",
        "mode_none": "Source language (English), no translation needed",
        "mode_translated": "Translated automatically",
        "mode_fallback": "No translation for this language yet, showing English",
        "apps": "Bundled apps", "no_apps": "No apps registered. Run python3 layer.py first.",
        "download": "Download", "verify": "Verify", "remove": "Remove",
        "installed": "Downloaded", "not_installed": "Not downloaded", "verified": "Verified",
        "system_p": "Protected", "removable_p": "Removable",
        "locked_hint": "Protected app. Remove it from the terminal with force=True.",
        "confirm_remove": "Remove {n}?", "working": "Working:", "log_title": "Last result",
    },
    "ar": {
        "title": "R-Layers", "subtitle": "مدير الحزم والتطبيقات",
        "language": "اللغة", "auto": "حسب النظام",
        "kernel": "النواة", "arch": "المعمارية", "syslang": "لغة النظام",
        "translation": "الترجمة",
        "mode_translated": "تمت الترجمة تلقائياً",
        "apps": "التطبيقات المضمّنة", "no_apps": "لا توجد تطبيقات مسجّلة. شغّل python3 layer.py أولاً.",
        "download": "تنزيل", "verify": "تحقق", "remove": "حذف",
        "installed": "منزّل", "not_installed": "غير منزّل", "verified": "تم التحقق",
        "system_p": "محمي", "removable_p": "قابل للحذف",
        "locked_hint": "تطبيق محمي. احذفه من الطرفية باستخدام force=True.",
        "confirm_remove": "حذف {n}؟", "working": "جارٍ التنفيذ:", "log_title": "آخر نتيجة",
    },
}
 
 
def detect_system():
    raw = ""
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        if os.environ.get(var):
            raw = os.environ[var]
            break
    code = raw.split(":")[0].split(".")[0].split("_")[0].lower()
    if code in ("", "c", "posix"):
        code = "en"
    return {"kernel": platform.system(), "release": platform.release(),
            "machine": platform.machine(), "lang": code}
 
 
def load_strings(code):
    """Return (strings, mode): mode is none, translated or fallback."""
    strings = dict(STRINGS["en"])
    if code == "en":
        return strings, "none"
    table = dict(STRINGS.get(code, {}))
    extra = Path("locales") / f"{code}.json"
    if extra.exists():
        try:
            table.update(json.loads(extra.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    if not table:
        return strings, "fallback"
    strings.update(table)
    return strings, "translated"
 
 
def state(lang=""):
    system = detect_system()
    code = lang or system["lang"]
    strings, mode = load_strings(code)
    with LOCK:
        layer.packages = layer._load_cache()  # pick up terminal-side changes
        apps = [{"name": n, "license": i.get("license", ""), "credit": i.get("credit_to", ""),
                 "protection": i.get("protection", "removable"),
                 "installed": bool(i.get("installed")), "verified": bool(i.get("verified")),
                 "commit": (i.get("commit") or "")[:7]}
                for n, i in layer.packages.items() if i.get("type") == "bundled_app"]
    return {"system": system, "lang": code, "mode": mode, "rtl": code in RTL,
            "t": strings, "apps": apps}
 
 
PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>R-Layers</title>
<style>
:root{--ink:#17202A;--mute:#5B6673;--bg:#F2F4F7;--panel:#fff;--line:#D5DAE1;--acc:#0E7C86;--acc-ink:#fff;--bad:#B42318;--ok:#1B7F4B}
@media (prefers-color-scheme:dark){:root{--ink:#E4E8EC;--mute:#9AA5B1;--bg:#12171D;--panel:#1A2128;--line:#2C353F;--acc:#4FB3BF;--acc-ink:#0b1216;--bad:#F08A80;--ok:#5CC98C}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,"Segoe UI","Noto Sans","Noto Sans Arabic",sans-serif}
main{max-width:900px;margin:0 auto;padding:28px 16px 48px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:20px}
h1{margin:0;font-size:28px;letter-spacing:-.01em}
h2{font-size:15px;margin:24px 0 8px}
.sub{color:var(--mute);margin:2px 0 0}
select,button{font:inherit;color:inherit}
select{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 10px}
.sys{display:flex;flex-wrap:wrap;gap:6px 28px;padding:12px 14px;background:var(--panel);border:1px solid var(--line);border-radius:6px}
.app{display:grid;grid-template-columns:1fr auto;gap:8px 16px;align-items:center;background:var(--panel);border:1px solid var(--line);border-inline-start:5px solid var(--acc);padding:12px 14px;margin-bottom:8px;border-radius:6px}
.app.removable{border-inline-start:5px dashed var(--mute)}
.name{font-weight:600;font-size:16px}
.meta{color:var(--mute);font-size:13px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tag{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line)}
.tag.ok{color:var(--ok);border-color:var(--ok)}
button{border:1px solid var(--acc);background:transparent;color:var(--acc);border-radius:6px;padding:6px 14px;cursor:pointer}
button.primary{background:var(--acc);color:var(--acc-ink)}
button.danger{border-color:var(--bad);color:var(--bad)}
button:disabled{opacity:.5;cursor:wait}
button:focus-visible,select:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.lock{color:var(--mute);font-size:13px;max-width:230px}
pre{direction:ltr;text-align:left;white-space:pre-wrap;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px;min-height:64px;margin:0}
@media (max-width:560px){.app{grid-template-columns:1fr}}
</style></head>
<body><main>
<header>
  <div><h1>R-Layers</h1><p class="sub" id="sub"></p></div>
  <label class="sub"><span id="lang-label"></span> <select id="lang"></select></label>
</header>
<div class="sys" id="sys"></div>
<h2 id="apps-title"></h2><div id="list"></div>
<h2 id="log-title"></h2><pre id="log"></pre>
</main>
<script>
const TOKEN="__TOKEN__";
let S, forced="", busy=false;
const $=id=>document.getElementById(id);
function el(tag,props,...kids){const e=document.createElement(tag);Object.assign(e,props||{});e.append(...kids);return e}
function btn(label,fn,cls){return el("button",{textContent:label,className:cls,onclick:fn})}
 
async function load(){
  const r=await fetch("/api/state"+(forced?"?lang="+encodeURIComponent(forced):""));
  S=await r.json();render();
}
function render(){
  const t=S.t, sys=S.system;
  document.documentElement.lang=S.lang;
  document.documentElement.dir=S.rtl?"rtl":"ltr";
  document.title=t.title;
  $("sub").textContent=t.subtitle;
  $("lang-label").textContent=t.language;
  $("lang").replaceChildren(...[["",t.auto],["en","English"],["ar","العربية"]]
    .map(([v,l])=>el("option",{value:v,textContent:l,selected:v===forced})));
  const kv=(k,v)=>el("span",{},el("b",{textContent:k+": "}),v);
  $("sys").replaceChildren(kv(t.kernel,sys.kernel+" "+sys.release),kv(t.arch,sys.machine),
    kv(t.syslang,sys.lang),kv(t.translation,t["mode_"+S.mode]));
  $("apps-title").textContent=t.apps;
  $("log-title").textContent=t.log_title;
  $("list").replaceChildren(...(S.apps.length?S.apps.map(row):[el("p",{className:"sub",textContent:t.no_apps})]));
}
function row(a){
  const t=S.t, locked=a.protection==="system";
  const tags=[el("span",{className:"tag",textContent:a.license}),
              el("span",{className:"tag",textContent:a.installed?t.installed:t.not_installed})];
  if(a.verified)tags.push(el("span",{className:"tag ok",textContent:t.verified}));
  tags.push(el("span",{className:"meta",textContent:locked?t.system_p:t.removable_p}));
  if(a.commit)tags.push(el("span",{className:"meta",textContent:a.commit}));
  const acts=el("div",{className:"row"});
  acts.append(a.installed?btn(t.verify,()=>act("verify",a.name),""):btn(t.download,()=>act("download",a.name),"primary"));
  acts.append(locked?el("span",{className:"lock",textContent:t.locked_hint}):btn(t.remove,()=>act("remove",a.name),"danger"));
  return el("div",{className:"app "+(locked?"system":"removable")},
    el("div",{},el("div",{className:"name",textContent:a.name}),el("div",{className:"meta",textContent:a.credit}),el("div",{className:"row"},...tags)),acts);
}
async function act(action,name){
  if(busy)return;
  if(action==="remove"&&!confirm(S.t.confirm_remove.replace("{n}",name)))return;
  busy=true;document.querySelectorAll("button").forEach(b=>b.disabled=true);
  $("log").textContent=S.t.working+" "+action+" "+name;
  try{
    const r=await fetch("/api/action",{method:"POST",headers:{"Content-Type":"application/json","X-Token":TOKEN},
      body:JSON.stringify({action,name})});
    const d=await r.json();$("log").textContent=d.log||d.error||"";
  }catch(e){$("log").textContent=String(e)}
  busy=false;load();
}
$("lang").onchange=e=>{forced=e.target.value;load()};
load();
</script></body></html>"""
 
 
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
 
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
 
    def _local(self):
        return self.headers.get("Host", "").split(":")[0] in ("127.0.0.1", "localhost")
 
    def do_GET(self):
        if not self._local():
            return self._send(403, "forbidden", "text/plain")
        url = urlparse(self.path)
        if url.path == "/":
            return self._send(200, PAGE.replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if url.path == "/api/state":
            lang = parse_qs(url.query).get("lang", [""])[0][:8]
            return self._send(200, json.dumps(state(lang), ensure_ascii=False))
        self._send(404, "not found", "text/plain")
 
    def do_POST(self):
        if not self._local() or self.headers.get("X-Token") != TOKEN:
            return self._send(403, json.dumps({"error": "forbidden"}))
        if self.path != "/api/action":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            size = min(int(self.headers.get("Content-Length", 0)), 4096)
            req = json.loads(self.rfile.read(size))
            action, name = req["action"], req["name"]
        except (ValueError, KeyError, TypeError):
            return self._send(400, json.dumps({"error": "bad request"}))
        funcs = {"download": layer.download_app, "verify": layer.verify_app, "remove": layer.remove_app}
        buf = io.StringIO()
        with LOCK:
            layer.packages = layer._load_cache()
            if action not in funcs or name not in layer.packages:
                return self._send(400, json.dumps({"error": "unknown action or app"}))
            with contextlib.redirect_stdout(buf):
                try:
                    # force is never passed from the browser: protected apps stay terminal-only
                    ok = funcs[action](name)
                except Exception as exc:  # report instead of crashing the server
                    ok = False
                    print(f"Error: {exc}")
        self._send(200, json.dumps({"ok": bool(ok), "log": buf.getvalue().strip()}, ensure_ascii=False))
 
 
if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"R-Layers UI running at {url} (Ctrl+C to stop)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
 