"""Чтение водопада (панорамы) IC-705 через CI-V Scope Waveform Data Output.

Формат вычислен экспериментально на живом IC-705 и сверен с исходниками
проекта wfview (открытый CAT-контроллер Icom, gitlab.com/eliggett/wfview):

  27 11 01 / 27 11 00  — включить/выключить поток кадров водопада
  27 00                — сами кадры (шлются радио САМИ, без опроса, ~4/сек)

Кадр после "27 00": [receiver, seq(BCD), seqMax(BCD)=11, ...]
  seq==1:      [mode, center(5б BCD-freq), halfspan(5б BCD-freq), out_of_range]
  seq==2..11:  сырые байты амплитуды (0x00..0xA0), 50 на сегмент (25 на последнем)
  всего 475 точек на развёртку (SpectrumLenMax из rigs/IC-705.rig проекта wfview)

Два способа использования:
  SpectrumAssembler — чистая сборка кадров в развёртки, без своего соединения.
    Используется в Station: кадры прилетают через общий поток-читатель CIV
    (civ.set_scope_callback), порт один на всех, коллизий нет.
  ScopeReader — standalone-режим для отдельного прототипа (waterfall.py):
    сам открывает CAT-порт. НЕ запускать одновременно с Station/echo.py/web.py
    на том же порту (коллизия шины).
"""
import threading
import time
from collections import deque

import serial

from civ import autodetect_port

CMD_ENABLE = bytes([0x27, 0x11, 0x01])
CMD_DISABLE = bytes([0x27, 0x11, 0x00])
POINTS = 475


def _bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def _bcd_freq(five_bytes):
    digits = "".join("%02X" % x for x in reversed(five_bytes))
    return int(digits)


class SpectrumAssembler:
    """Собирает кадры '27 00' (тело БЕЗ этих двух байт) в развёртки по 475 точек."""

    def __init__(self, history=600):
        self._row = [0] * POINTS
        self._pos = 0
        self.center_freq = None
        self.span_hz = None
        self.seq_counter = 0                # монотонный счётчик готовых развёрток
        self.rows = deque(maxlen=history)   # список (seq_counter, row)
        self._lock = threading.Lock()

    def feed(self, body):
        """body = [receiver, seq(BCD), seqMax(BCD), ...] — вызывать на каждый кадр."""
        if len(body) < 3:
            return
        seq = _bcd(body[1])
        seq_max = _bcd(body[2])
        payload = body[3:]

        if seq == 1:
            if len(payload) < 12:
                return
            mode = payload[0]
            c = _bcd_freq(payload[1:6])
            half = _bcd_freq(payload[6:11])
            if mode == 0:  # center mode: значения = центр и полуспан
                self.center_freq = c
                self.span_hz = half * 2
            self._row = [0] * POINTS
            self._pos = 0
        else:
            end = min(POINTS, self._pos + len(payload))
            self._row[self._pos:end] = list(payload[:end - self._pos])
            self._pos = end
            if seq == seq_max:
                with self._lock:
                    self.seq_counter += 1
                    self.rows.append((self.seq_counter, list(self._row)))

    def rows_since(self, seq):
        """Развёртки с seq_counter > seq (для инкрементальной отдачи в веб)."""
        with self._lock:
            return [(s, r) for s, r in self.rows if s > seq]


class ScopeReader:
    """Standalone: сам открывает CAT-порт (для отдельного прототипа waterfall.py)."""

    def __init__(self, cfg, history=600):
        self.cfg = cfg
        port = cfg.port
        if port in (None, "", "auto"):
            port = autodetect_port(cfg.baud, cfg.radio_addr, cfg.ctrl_addr)
            if port is None:
                raise RuntimeError("CAT-порт не найден")
        self.port = port
        self.radio = cfg.radio_addr
        self.ctrl = cfg.ctrl_addr
        self.ser = serial.Serial(port, cfg.baud, timeout=0.2)

        self._buf = bytearray()
        self.assembler = SpectrumAssembler(history)
        self._stop = threading.Event()

    # --- прокси для совместимости с прежним API (waterfall.py) ---
    @property
    def rows(self):
        return self.assembler.rows

    @property
    def seq_counter(self):
        return self.assembler.seq_counter

    @property
    def center_freq(self):
        return self.assembler.center_freq

    @property
    def span_hz(self):
        return self.assembler.span_hz

    def rows_since(self, seq):
        return self.assembler.rows_since(seq)

    # --- низкий уровень ---
    def _frame(self, cmd):
        return bytes([0xFE, 0xFE, self.radio, self.ctrl]) + cmd + bytes([0xFD])

    def _send_raw(self, cmd):
        self.ser.write(self._frame(cmd))
        self.ser.flush()

    def enable(self):
        self.ser.reset_input_buffer()
        self._send_raw(CMD_ENABLE)
        time.sleep(0.2)
        self.ser.read(64)  # проглотить ACK

    def disable(self):
        try:
            self._send_raw(CMD_DISABLE)
        except Exception:
            pass

    # --- разбор потока ---
    def _drain(self):
        while True:
            i = self._buf.find(b"\xfe\xfe")
            if i < 0:
                self._buf.clear()
                return
            if i > 0:
                del self._buf[:i]
            j = self._buf.find(b"\xfd", 2)
            if j < 0:
                return  # кадр ещё не пришёл целиком
            frame = bytes(self._buf[2:j])
            del self._buf[:j + 1]
            if len(frame) >= 2 and frame[0] == self.ctrl and frame[1] == self.radio:
                body = frame[2:]
                if len(body) >= 2 and body[0] == 0x27 and body[1] == 0x00:
                    self.assembler.feed(body[2:])

    def run(self):
        self.enable()
        try:
            while not self._stop.is_set():
                chunk = self.ser.read(4096)
                if chunk:
                    self._buf.extend(chunk)
                    self._drain()
        finally:
            self.disable()

    def start_background(self):
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t

    def close(self):
        self._stop.set()
        try:
            self.ser.close()
        except Exception:
            pass
