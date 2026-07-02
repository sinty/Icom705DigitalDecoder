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
import sqlite3
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
   <div class="row" id="slotccRow" style="display:none"><span>Таймслот / CC</span><b id="slotcc">—</b></div>
   <div class="row" id="tgidRow" style="display:none"><span>TG / ID</span><b id="tgid">—</b></div>
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
<div class="card" style="margin-top:12px"><h2>Журнал операторов</h2>
 <div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">
  <thead><tr>
   <th style="text-align:left;padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:500">Время</th>
   <th style="text-align:left;padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:500">Частота</th>
   <th style="text-align:left;padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:500">Вид</th>
   <th style="text-align:left;padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:500">Позывной</th>
   <th style="text-align:left;padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:500">ID</th>
   <th style="text-align:left;padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut);font-weight:500">Параметры</th>
  </tr></thead><tbody id="calls"></tbody>
 </table></div>
 <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
  <button id="newer" style="background:#0c101a;border:1px solid var(--ln);color:var(--fg);border-radius:6px;padding:4px 12px;cursor:pointer">‹ новее</button>
  <span id="pageinfo" style="color:var(--mut);font-size:13px">—</span>
  <button id="older" style="background:#0c101a;border:1px solid var(--ln);color:var(--fg);border-radius:6px;padding:4px 12px;cursor:pointer">старее ›</button>
 </div>
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
 if(d.dsd_state==='digital'){el.className='badge b-dig';el.textContent='ЦИФРА'+(d.dsd_proto?' ('+d.dsd_proto+')':'');}
 else if(d.dsd_state==='analog'){el.className='badge b-analog';el.textContent='АНАЛОГ';}
 else {el.className='badge b-idle';el.textContent='ТИШИНА';}
 document.getElementById('alias').textContent=d.talker_alias||'—';
 // Таймслот/CC — только для DMR
 var isDmr=d.dsd_proto==='DMR';
 document.getElementById('slotccRow').style.display=isDmr?'':'none';
 if(isDmr){
   var sc='—';
   if(d.dmr_slots&&d.dmr_slots.length){sc='TS'+d.dmr_slots.join('+TS')+(d.dmr_cc!=null?' · CC'+d.dmr_cc:'');}
   else if(d.dmr_cc!=null){sc='CC'+d.dmr_cc;}
   document.getElementById('slotcc').textContent=sc;
 }
 // TG/ID — для DMR и P25
 var hasId=d.src_id!=null||d.tgt_id!=null;
 document.getElementById('tgidRow').style.display=hasId?'':'none';
 if(hasId){document.getElementById('tgid').textContent=(d.tgt_id||'?')+' / '+(d.src_id||'?');}
 if(!volDrag && d.volume_pct!=null){volEl.value=d.volume_pct;
   document.getElementById('volv').textContent=d.volume_pct+'%';}
 if(!sqlDrag && d.sql_db!=null){sqlEl.value=d.sql_db;
   document.getElementById('sqlv').textContent=d.sql_db+' дБ';}
}
tick();setInterval(tick,600);

// --- журнал операторов (пагинация по 10) ---
var callsOffset=0, callsTotal=0;
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
async function loadCalls(){
 try{var r=await fetch('/api/calls?offset='+callsOffset);var d=await r.json();}catch(e){return;}
 callsTotal=d.total;
 document.getElementById('calls').innerHTML=d.rows.map(function(c){
  var f=c.freq_hz?(c.freq_hz/1e6).toFixed(4):'—';
  var t=(c.ts||'').slice(5);  // MM-DD HH:MM:SS
  return '<tr>'+
   '<td style="padding:5px 8px;border-bottom:1px solid var(--ln);white-space:nowrap">'+esc(t)+'</td>'+
   '<td style="padding:5px 8px;border-bottom:1px solid var(--ln)">'+f+'</td>'+
   '<td style="padding:5px 8px;border-bottom:1px solid var(--ln)">'+esc(c.proto)+'</td>'+
   '<td style="padding:5px 8px;border-bottom:1px solid var(--ln)">'+esc(c.callsign||'—')+'</td>'+
   '<td style="padding:5px 8px;border-bottom:1px solid var(--ln)">'+esc(c.radio_id||'—')+'</td>'+
   '<td style="padding:5px 8px;border-bottom:1px solid var(--ln);color:var(--mut)">'+esc(c.details||'')+'</td>'+
  '</tr>';}).join('');
 var page=Math.floor(callsOffset/10)+1, pages=Math.max(1,Math.ceil(callsTotal/10));
 document.getElementById('pageinfo').textContent='стр. '+page+' из '+pages+' ('+callsTotal+')';
}
document.getElementById('newer').onclick=function(){callsOffset=Math.max(0,callsOffset-10);loadCalls();};
document.getElementById('older').onclick=function(){if(callsOffset+10<callsTotal)callsOffset+=10;loadCalls();};
loadCalls();
setInterval(function(){if(callsOffset===0)loadCalls();},3000);  // автообновление первой страницы

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


class CallDB:
    """SQLite-журнал услышанных операторов (по строке на передачу)."""

    def __init__(self, path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("""CREATE TABLE IF NOT EXISTS calls(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,            -- время передачи
            freq_hz INTEGER,    -- частота приёма (NULL для импорта истории)
            proto TEXT,         -- DMR / D-STAR / P25 / YSF
            callsign TEXT,      -- позывной (Talker Alias / D-STAR SRC), если был
            radio_id TEXT,      -- цифровой ID, если был
            details TEXT)""")   # слот/CC/TG и прочие атрибуты вида связи
        self._conn.commit()

    def empty(self):
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0

    def insert(self, ts, freq_hz, proto, callsign, radio_id, details):
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO calls(ts, freq_hz, proto, callsign, radio_id, details) "
                "VALUES(?,?,?,?,?,?)", (ts, freq_hz, proto, callsign, radio_id, details))
            self._conn.commit()
            return cur.lastrowid

    def set_callsign(self, call_id, callsign):
        with self._lock:
            self._conn.execute("UPDATE calls SET callsign=? WHERE id=?",
                               (callsign, call_id))
            self._conn.commit()

    def page(self, offset, limit):
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, freq_hz, proto, callsign, radio_id, details FROM calls "
                "ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            total = self._conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
        return {"total": total, "rows": [
            {"ts": r[0], "freq_hz": r[1], "proto": r[2], "callsign": r[3],
             "radio_id": r[4], "details": r[5]} for r in rows]}


# --- разбор строк событийного лога dsd-fme (-J) ---
# "2026-07-02 19:34:32 DMR TGT: 00002501; SRC: 02502766; CC: 01; Group;  Slot 1;"
# вид звонка ("Group;") бывает опущен
RE_DMR = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) DMR TGT: (\S+?); SRC: (\S+?); CC: (\d+);(?:\s+(\w+);)?\s+Slot (\d)")
# "2026-07-02 19:37:14 DSTAR TGT: CQCQCQ   SRC: RA0XXX   ID52"
RE_DSTAR = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) DSTAR TGT: (\S+)\s+SRC: (\S+)\s*(.*)$")
# любой другой вид с датой в начале — пишем как есть
RE_OTHER = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (\S+) (.*)$")
RE_ALIAS = re.compile(r"^\s*Talker Alias:\s*(.+?);?\s*$")


def parse_event_line(line):
    """Строка события -> ("alias", позывной) | ("call", запись) | None.

    Строка звонка пишется dsd-fme в КОНЦЕ передачи, а Talker Alias —
    отдельной строкой СЛЕДОМ за ней, поэтому alias относится к
    предыдущей записи звонка.
    """
    m = RE_ALIAS.match(line)
    if m:
        return ("alias", m.group(1).strip())
    m = RE_DMR.match(line)
    if m:
        ts, tgt, src, cc, kind, slot = m.groups()
        details = f"TS{slot} CC{int(cc)} TG:{int(tgt)}" + (f" {kind}" if kind else "")
        return ("call", (ts, "DMR", None, str(int(src)), details))
    m = RE_DSTAR.match(line)
    if m:
        ts, tgt, src, rest = m.groups()
        details = f"DST:{tgt}" + (f" {rest.strip()}" if rest.strip() else "")
        return ("call", (ts, "D-STAR", src, None, details))
    m = RE_OTHER.match(line)
    if m:
        ts, proto, rest = m.groups()
        if proto in ("DSD-FME", "Any"):   # служебные строки старта
            return None
        return ("call", (ts, proto, None, None, rest.strip()))
    return None


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
        self.dsd_proto = None       # DMR | D-STAR | P25p1 | P25p2 | YSF | ...
        self.talker_alias = None
        self.dmr_slots = []         # слоты с голосом сейчас: [1], [2] или [1, 2]
        self.dmr_cc = None          # Color Code текущей передачи
        self.src_id = None          # ID источника (DMR/P25)
        self.tgt_id = None          # группа/адресат (DMR/P25)

        self._sql_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sql_sock.settimeout(0.5)

        self.calls = CallDB(cfg.calls_db)
        self._last_call_id = None
        if self.calls.empty():
            self._import_events_history()

    def _handle_event_line(self, line, freq_hz):
        """Обработать строку событийного лога: звонок -> insert, alias -> update
        предыдущей записи (dsd-fme пишет alias следом за строкой звонка)."""
        ev = parse_event_line(line.rstrip("\n"))
        if ev is None:
            return
        kind, payload = ev
        if kind == "call":
            ts, proto, callsign, radio_id, details = payload
            self._last_call_id = self.calls.insert(
                ts, freq_hz, proto, callsign, radio_id, details)
        elif kind == "alias" and self._last_call_id is not None:
            self.calls.set_callsign(self._last_call_id, payload)

    def _import_events_history(self):
        """Разовый импорт существующего событийного лога (частота неизвестна)."""
        try:
            with open(self.cfg.dsd_events_log, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    self._handle_event_line(line, None)
        except OSError:
            pass

    def events_tail_loop(self):
        """Тейлер событийного лога dsd-fme: новые звонки -> SQLite (с текущей частотой)."""
        pos = None
        while True:
            try:
                with open(self.cfg.dsd_events_log, encoding="utf-8", errors="ignore") as f:
                    f.seek(0, os.SEEK_END)
                    if pos is None or pos > f.tell():
                        pos = f.tell()      # первый запуск или файл усечён
                    f.seek(pos)
                    for line in f:
                        self._handle_event_line(line, self.freq)
                    pos = f.tell()
            except OSError:
                pass
            time.sleep(1.0)

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
        # последняя строка с Sync: определяет цифру и протокол
        state, proto = "idle", None
        for line in reversed(lines):
            if "Sync:" in line:
                if "no sync" in line:
                    state = "analog_or_idle"
                else:
                    state = "digital"
                    m = re.search(r"Sync:\s*[+-]?([A-Za-z0-9]+)", line)
                    if m:
                        proto = {"DSTAR": "D-STAR"}.get(m.group(1), m.group(1))
                break
        # атрибуты (alias/слоты/CC/ID) ищем ТОЛЬКО в текущей непрерывной цифровой
        # сессии — от последнего "no sync" до конца, иначе ложный синк на шуме
        # (например YSF) вытаскивает alias давно прошедшей DMR-передачи
        cur = lines
        for i in range(len(lines) - 1, -1, -1):
            if "no sync" in lines[i]:
                cur = lines[i + 1:]
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
        self.dsd_proto = proto if state == "digital" else None
        # DMR: активные голосом слоты и Color Code из свежих строк.
        # Слот текущей строки взят в [...], КАПС ([SLOT2]) = в нём голос (VC-кадр).
        slots, cc = set(), None
        if state == "digital" and proto == "DMR":
            for line in cur[-40:]:
                if "Sync:" not in line or "DMR" not in line:
                    continue
                m = re.search(r"Color Code=(\d+)", line)
                if m:
                    cc = int(m.group(1))
                m = re.search(r"\[(SLOT[12])\].*VC\d", line)
                if m:
                    slots.add(int(m.group(1)[-1]))
        self.dmr_slots = sorted(slots)
        self.dmr_cc = cc
        # ID источника и группы — у DMR и P25 общий формат "TGT=... SRC=..."
        src, tgt = None, None
        if state == "digital" and (proto == "DMR" or (proto or "").startswith("P25")):
            for line in reversed(cur[-40:]):
                m = re.search(r"TGT=(\d+)\s+SRC=(\d+)", line)
                if m:
                    tgt, src = m.group(1), m.group(2)
                    break
        self.src_id = src
        self.tgt_id = tgt
        # позывной — только свежий (последние 60 строк) и своего протокола:
        # DMR несёт Talker Alias, D-STAR — поле SRC
        alias = None
        if state == "digital":
            pattern = r"SRC:\s*(\S+)" if proto == "D-STAR" else r"Talker Alias:\s*(\S+)"
            for line in reversed(cur[-60:]):
                m = re.search(pattern, line)
                if m:
                    alias = m.group(1)
                    break
        self.talker_alias = alias

    def snapshot(self):
        return {
            "freq_mhz": self.freq / 1e6 if self.freq else None,
            "mode": self.mode,
            "smeter_raw": self.smeter_raw,
            "s_units": s_units(self.smeter_raw, self.cfg),
            "volume_pct": self.volume_pct,
            "sql_db": self.sql_db,
            "dsd_state": self.dsd_state,
            "dsd_proto": self.dsd_proto,
            "talker_alias": self.talker_alias,
            "dmr_slots": self.dmr_slots,
            "dmr_cc": self.dmr_cc,
            "src_id": self.src_id,
            "tgt_id": self.tgt_id,
        }

    def start_background(self):
        threading.Thread(target=self.poll_loop, daemon=True).start()
        threading.Thread(target=self.events_tail_loop, daemon=True).start()

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
            elif path == "/api/calls":
                qs = {}
                if "?" in self.path:
                    for kv in self.path.split("?", 1)[1].split("&"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            qs[k] = v
                try:
                    offset = max(0, int(qs.get("offset", 0)))
                except ValueError:
                    offset = 0
                body = json.dumps(dash.calls.page(offset, 10),
                                  ensure_ascii=False).encode("utf-8")
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
