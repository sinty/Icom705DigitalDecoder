"""Веб-дашборд декодера (stdlib http.server, без внешних зависимостей).

Показывает: водопад (панораму) IC-705 по CI-V, живой S-метр, частоту/режим,
статус dsd-fme (аналог/цифра, Talker Alias), регуляторы громкости динамика
(pactl) и порога сквелча (UDP в if_demod.py).

Запуск:  python3 web.py      Открыть: http://<ip-raspberry>:8080/
Тракт (run_pipeline.sh + dsd-fme) должен работать отдельно.
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import Config
from civ import CIV, s_units
from scope import SpectrumAssembler

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IC-705 Decoder</title><style>
:root{--bg:#0f1420;--card:#1a2130;--ln:#2a3348;--fg:#e6ecf5;--mut:#8b98b0;--acc:#4ea1ff;--ok:#39d98a;--hot:#ff6b6b}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
.wrap{max-width:960px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 12px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.card{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:14px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);margin:0 0 10px}
.big{font-size:24px;font-weight:600}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-weight:600;font-size:13px}
.b-idle{background:#233;color:var(--mut)}.b-analog{background:#1c3050;color:var(--acc)}.b-dig{background:#123a2a;color:var(--ok)}
.meter{height:22px;background:#0c101a;border-radius:6px;overflow:hidden;border:1px solid var(--ln)}
.meter>div{height:100%;background:linear-gradient(90deg,#2e7d5b,#4ea1ff,#ff6b6b);transition:width .2s}
.row{display:flex;justify-content:space-between;align-items:center;margin:6px 0}
input[type=range]{width:100%}
.val{color:var(--acc);font-weight:600}
#specCv{height:80px;border-bottom:1px solid var(--ln)}
#wfCv{height:260px}
#wfCv,#specCv{display:block;width:100%}
</style></head><body><div class="wrap">
<h1>IC-705 Digital Decoder</h1>
<div class="grid">
 <div class="card"><h2>Радио</h2>
   <div class="big" id="freq">—</div>
   <div class="row"><span>Режим</span><b id="mode">—</b></div>
 </div>
 <div class="card"><h2>S-метр</h2>
   <div class="big" id="su" style="margin-bottom:8px">S0</div>
   <div class="meter"><div id="mbar" style="width:0%"></div></div>
 </div>
 <div class="card"><h2>Декодер</h2>
   <div class="big"><span id="dsd" class="badge b-idle">—</span></div>
   <div class="row"><span>Talker Alias</span><b id="alias">—</b></div>
 </div>
 <div class="card"><h2>Управление</h2>
   <div class="row"><span>Громкость</span><span class="val" id="volv">—%</span></div>
   <input type="range" id="vol" min="0" max="100" step="1">
   <div class="row"><span>Сквелч</span><span class="val" id="sqlv">— дБ</span></div>
   <input type="range" id="sql" min="-40" max="0" step="0.5">
 </div>
</div>
<div class="card" style="margin-top:12px;padding:0;overflow:hidden">
 <div style="padding:14px 14px 0"><h2 style="margin-bottom:6px">Водопад <span id="wfHdr" style="text-transform:none;color:var(--mut);font-weight:400"></span></h2></div>
 <canvas id="specCv" width="475" height="80"></canvas>
 <canvas id="wfCv" width="475" height="260"></canvas>
</div>
</div><script>
function debounce(f,ms){var t;return function(){clearTimeout(t);var a=arguments;t=setTimeout(function(){f.apply(null,a)},ms)}}
var volEl=document.getElementById('vol'), sqlEl=document.getElementById('sql');
var volDrag=false, sqlDrag=false;
volEl.addEventListener('pointerdown',()=>volDrag=true);
volEl.addEventListener('pointerup',()=>volDrag=false);
sqlEl.addEventListener('pointerdown',()=>sqlDrag=true);
sqlEl.addEventListener('pointerup',()=>sqlDrag=false);
volEl.addEventListener('input',function(){document.getElementById('volv').textContent=this.value+'%';});
sqlEl.addEventListener('input',function(){document.getElementById('sqlv').textContent=this.value+' дБ';});
volEl.addEventListener('input',debounce(function(){
 fetch('/api/volume',{method:'POST',body:JSON.stringify({pct:+volEl.value})});},150));
sqlEl.addEventListener('input',debounce(function(){
 fetch('/api/sql',{method:'POST',body:JSON.stringify({db:+sqlEl.value})});},150));
async function tick(){
 try{var r=await fetch('/api/state');var d=await r.json();}catch(e){return;}
 document.getElementById('freq').textContent=d.freq_mhz?d.freq_mhz.toFixed(4)+' MHz':'—';
 document.getElementById('mode').textContent=d.mode||'—';
 document.getElementById('su').textContent=d.s_units||'S0';
 document.getElementById('mbar').style.width=Math.min(100,(d.smeter_raw||0)/255*100)+'%';
 var el=document.getElementById('dsd');
 if(d.dsd_state==='digital'){el.className='badge b-dig';el.textContent='ЦИФРА (DMR)';}
 else if(d.dsd_state==='analog'){el.className='badge b-analog';el.textContent='АНАЛОГ';}
 else {el.className='badge b-idle';el.textContent='ТИШИНА';}
 document.getElementById('alias').textContent=d.talker_alias||'—';
 if(!volDrag && d.volume_pct!=null){volEl.value=d.volume_pct;
   document.getElementById('volv').textContent=d.volume_pct+'%';}
 if(!sqlDrag && d.sql_db!=null){sqlEl.value=d.sql_db;
   document.getElementById('sqlv').textContent=d.sql_db+' дБ';}
}
tick();setInterval(tick,600);

// --- водопад (палитра/отрисовка как в RadioEcho) ---
(function(){
 var wf=document.getElementById('wfCv'), wctx=wf.getContext('2d');
 var sp=document.getElementById('specCv'), sctx=sp.getContext('2d');
 var W=wf.width, WH=wf.height, SH=sp.height;
 wctx.fillStyle='#000'; wctx.fillRect(0,0,W,WH);
 function palette(v){
   var t=Math.max(0,Math.min(1,v/160));
   var stops=[[0,0,0],[0,0,140],[0,160,200],[0,220,80],[240,220,0],[240,40,20]];
   var n=stops.length-1, f=t*n, i=Math.min(n-1,Math.floor(f)), k=f-i;
   var a=stops[i], b=stops[i+1];
   return [a[0]+(b[0]-a[0])*k, a[1]+(b[1]-a[1])*k, a[2]+(b[2]-a[2])*k];
 }
 var pal=new Array(161);
 for(var v=0;v<=160;v++){ var c=palette(v); pal[v]=[c[0]|0,c[1]|0,c[2]|0]; }
 function pushRow(row){
   sctx.fillStyle='#0a0e17'; sctx.fillRect(0,0,W,SH);
   sctx.strokeStyle='#4ea1ff'; sctx.lineWidth=1; sctx.beginPath();
   for(var x=0;x<row.length;x++){
     var y=SH-(row[x]/160)*SH;
     if(x===0) sctx.moveTo(x,y); else sctx.lineTo(x,y);
   }
   sctx.stroke();
   wctx.drawImage(wf,0,0,W,WH-1,0,1,W,WH-1);
   var img=wctx.createImageData(W,1);
   for(var x=0;x<row.length && x<W;x++){
     var p=pal[Math.max(0,Math.min(160,row[x]))], o=x*4;
     img.data[o]=p[0]; img.data[o+1]=p[1]; img.data[o+2]=p[2]; img.data[o+3]=255;
   }
   wctx.putImageData(img,0,0);
 }
 function connect(){
   var es=new EventSource('/events');
   es.onerror=function(){};
   es.onmessage=function(e){
     var d=JSON.parse(e.data);
     pushRow(d.row);
     if(d.center_mhz){
       document.getElementById('wfHdr').textContent=
         '· '+d.center_mhz.toFixed(4)+' MHz · спан '+(d.span_khz?d.span_khz.toFixed(0):'?')+' кГц';
     }
   };
 }
 connect();
})();
</script></body></html>"""


class Dashboard:
    """Фоновая часть: CI-V (частота/режим/S-метр + водопад), громкость, SQL, статус dsd."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.civ = CIV(cfg)
        self.scope = SpectrumAssembler(cfg.scope_history)
        self.civ.set_scope_callback(self.scope.feed)
        self.civ.set_scope_output(True)

        self.freq = None
        self.mode = None
        self.smeter_raw = 0
        self.volume_pct = None
        self.sql_db = None
        self.dsd_state = "idle"     # idle | analog | digital
        self.talker_alias = None

        self._sql_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sql_sock.settimeout(0.5)

    # --- опрос ---
    def poll_loop(self):
        n = 0
        while True:
            raw = self.civ.read_smeter_raw()
            if raw is not None:
                self.smeter_raw = raw
            if n % 5 == 0:   # частота/режим/громкость/sql — реже
                f = self.civ.read_frequency()
                if f:
                    self.freq = f
                m = self.civ.read_mode()
                if m:
                    self.mode = m
                self.volume_pct = self._get_volume()
                self._refresh_sql()
            self._refresh_dsd_state()
            n += 1
            time.sleep(0.2)

    def _get_volume(self):
        try:
            out = subprocess.run(
                ["pactl", "get-sink-volume", self.cfg.speaker_sink],
                capture_output=True, text=True, timeout=2).stdout
            m = re.search(r"(\d+)%", out)
            return int(m.group(1)) if m else None
        except Exception:
            return None

    def set_volume(self, pct):
        pct = max(0, min(100, int(pct)))
        subprocess.run(["pactl", "set-sink-volume", self.cfg.speaker_sink, f"{pct}%"],
                       timeout=2)
        self.volume_pct = pct

    def _refresh_sql(self):
        try:
            self._sql_sock.sendto(b"SQL ?", ("127.0.0.1", self.cfg.sql_udp_port))
            data, _ = self._sql_sock.recvfrom(64)
            self.sql_db = float(data.split()[1])
        except Exception:
            pass

    def set_sql(self, db):
        db = max(-60.0, min(0.0, float(db)))
        try:
            self._sql_sock.sendto(f"SQL {db}".encode(), ("127.0.0.1", self.cfg.sql_udp_port))
            data, _ = self._sql_sock.recvfrom(64)
            self.sql_db = float(data.split()[1])
        except Exception:
            pass

    def _refresh_dsd_state(self):
        """Статус декодера по хвосту лога dsd-fme."""
        try:
            with open(self.cfg.dsd_log, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 8192))
                tail = f.read().decode("utf-8", "ignore")
        except OSError:
            self.dsd_state = "idle"
            return
        lines = tail.splitlines()
        # последняя строка с Sync: определяет цифру; свежесть — по росту файла
        state = "idle"
        for line in reversed(lines):
            if "Sync:" in line:
                if "no sync" in line:
                    state = "analog_or_idle"
                else:
                    state = "digital"
                break
        # цифра "протухает": если файл не менялся 3с, а последний Sync старый — не цифра
        try:
            age = time.time() - os.path.getmtime(self.cfg.dsd_log)
        except OSError:
            age = 999
        if state == "digital" and age > 3:
            state = "analog_or_idle"
        if state == "analog_or_idle":
            # сквелч открыт (S-метр выше порога) -> аналог, иначе тишина
            state = "analog" if self.smeter_raw > 5 else "idle"
        self.dsd_state = state
        for line in reversed(lines):
            m = re.search(r"Talker Alias:\s*(\S+)", line)
            if m:
                self.talker_alias = m.group(1)
                break

    def snapshot(self):
        return {
            "freq_mhz": self.freq / 1e6 if self.freq else None,
            "mode": self.mode,
            "smeter_raw": self.smeter_raw,
            "s_units": s_units(self.smeter_raw, self.cfg),
            "volume_pct": self.volume_pct,
            "sql_db": self.sql_db,
            "dsd_state": self.dsd_state,
            "talker_alias": self.talker_alias,
        }

    def start_background(self):
        threading.Thread(target=self.poll_loop, daemon=True).start()

    def close(self):
        try:
            self.civ.set_scope_output(False)
        except Exception:
            pass
        self.civ.close()


def make_handler(dash):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE.encode("utf-8"))
            elif path == "/api/state":
                body = json.dumps(dash.snapshot(), ensure_ascii=False).encode("utf-8")
                self._send(200, "application/json; charset=utf-8", body)
            elif path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                last = dash.scope.seq_counter
                try:
                    while True:
                        for seq, row in dash.scope.rows_since(last):
                            last = seq
                            payload = {
                                "row": row,
                                "center_mhz": (dash.scope.center_freq / 1e6)
                                              if dash.scope.center_freq else None,
                                "span_khz": (dash.scope.span_hz / 1e3)
                                            if dash.scope.span_hz else None,
                            }
                            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(0.05)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send(400, "text/plain", b"bad json")
                return
            if self.path == "/api/volume" and "pct" in body:
                dash.set_volume(body["pct"])
            elif self.path == "/api/sql" and "db" in body:
                dash.set_sql(body["db"])
            else:
                self._send(404, "text/plain", b"not found")
                return
            self._send(200, "application/json", b"{}")
    return Handler


def main():
    cfg = Config()
    dash = Dashboard(cfg)
    dash.start_background()
    srv = ThreadingHTTPServer((cfg.web_host, cfg.web_port), make_handler(dash))
    print(f"Дашборд: http://0.0.0.0:{cfg.web_port}/ (в LAN — по IP Raspberry)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dash.close()
        srv.shutdown()


if __name__ == "__main__":
    main()
