"""Обёртка CI-V для IC-705.

Тонкий слой поверх pyserial: кадры CI-V, чтение частоты/режима/сквелча/S-метра,
управление PTT. Порт использует ОДИН общий поток-читатель (см. CIV._reader_loop) —
он разбирает входящий поток байт на кадры и раздаёт их по адресу: обычные
ответы на наши запросы уходят в очередь (_txn их забирает), а несолиситные
кадры водопада (0x27 0x00, Scope Waveform Data) — в колбэк для scope.py.
Без этого второй потребитель порта (водопад) сталкивался бы с Detector'ом
на той же шине и путал бы ответы (проверено эмпирически).
"""
import queue
import serial
import serial.tools.list_ports
import threading
import time

MODES = {0: "LSB", 1: "USB", 2: "AM", 3: "CW", 4: "RTTY", 5: "FM",
         6: "WFM", 7: "CW-R", 8: "RTTY-R", 23: "DV"}
MODE_BY_NAME = {v: k for k, v in MODES.items()}


ICOM_VID = 0x0C26   # USB Vendor ID Icom


def autodetect_port(baud, radio, ctrl):
    """Найти CAT-порт IC-705, опросив USB-порты командой чтения частоты.

    Пропускаем Bluetooth-порты (их открытие на Windows может подвесить) и
    сначала пробуем устройства Icom (VID 0x0C26). Кросс-платформенно:
    Windows COMx / Linux /dev/ttyACM*. Возвращает имя порта или None.
    """
    probe = bytes([0xFE, 0xFE, radio, ctrl, 0x03, 0xFD])
    reply = bytes([0xFE, 0xFE, ctrl, radio])

    def is_usb(p):
        hw = (p.hwid or "").upper()
        return p.vid is not None or "USB" in hw or "ACM" in (p.device or "").upper()

    ports = [p for p in serial.tools.list_ports.comports() if is_usb(p)]
    # приоритет портам Icom
    ports.sort(key=lambda p: 0 if (p.vid == ICOM_VID) else 1)

    for p in ports:
        try:
            s = serial.Serial(p.device, baud, timeout=0.3, write_timeout=0.3)
            s.reset_input_buffer()
            s.write(probe)
            s.flush()
            time.sleep(0.2)
            r = s.read(64)
            s.close()
            if reply in r:
                return p.device
        except Exception:
            try:
                s.close()
            except Exception:
                pass
            continue
    return None


class CIV:
    def __init__(self, cfg):
        self.cfg = cfg
        self.radio = cfg.radio_addr
        self.ctrl = cfg.ctrl_addr
        port = cfg.port
        if port in (None, "", "auto"):
            port = autodetect_port(cfg.baud, self.radio, self.ctrl)
            if port is None:
                raise RuntimeError("CAT-порт IC-705 не найден (autodetect)")
            print(f"Автоопределён CAT-порт: {port}")
        self.port = port
        self.ser = serial.Serial(port, cfg.baud, timeout=0.1)

        self._buf = bytearray()
        self._reply_q = queue.Queue()
        self._txn_lock = threading.Lock()   # сериализует запрос-ответ (по одному за раз)
        self._scope_cb = None
        self._stop = threading.Event()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def close(self):
        self._stop.set()
        try:
            self._reader_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass

    # --- общий поток-читатель порта ---
    def _reader_loop(self):
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(4096)
            except Exception:
                break
            if chunk:
                self._buf.extend(chunk)
                self._drain()

    def _drain(self):
        """Разобрать буфер на кадры FE FE ... FD, раздать по адресу."""
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
                if len(body) >= 2 and body[0] == 0x27 and body[1] == 0x00 and self._scope_cb:
                    self._scope_cb(body[2:])   # кадр водопада — мимо очереди ответов
                else:
                    self._reply_q.put(body)

    def set_scope_callback(self, cb):
        """cb(body) вызывается из потока-читателя на каждый кадр водопада.

        body — байты после '27 00': [receiver, seq(BCD), seqMax(BCD), ...].
        """
        self._scope_cb = cb

    def set_scope_output(self, enabled: bool):
        """Включить/выключить поток кадров Scope Waveform Data (27 11)."""
        self._txn([0x27, 0x11, 0x01 if enabled else 0x00], wait=0.3)

    # --- низкий уровень ---
    def _frame(self, cmd):
        return bytes([0xFE, 0xFE, self.radio, self.ctrl]) + bytes(cmd) + bytes([0xFD])

    def _txn(self, cmd, wait=0.15):
        """Отправить команду, вернуть тело ответа (без преамбулы/FD) или None.

        Ответ сопоставляется с командой (эхо ведущих байт, либо одиночный
        ACK/NAK 0xFB/0xFA для set-команд) — устаревшие ответы на предыдущий
        протухший по таймауту запрос отбрасываются, а не возвращаются как наш.
        """
        cmd = bytes(cmd)
        with self._txn_lock:
            while not self._reply_q.empty():
                try:
                    self._reply_q.get_nowait()
                except queue.Empty:
                    break
            self.ser.write(self._frame(cmd))
            self.ser.flush()
            deadline = time.monotonic() + wait
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    body = self._reply_q.get(timeout=remaining)
                except queue.Empty:
                    return None
                if len(body) == 1 and body[0] in (0xFB, 0xFA):
                    return body
                if body[:len(cmd)] == cmd:
                    return body
                # устаревший ответ на предыдущий запрос — ждём актуальный дальше

    # --- чтение состояния ---
    def read_frequency(self):
        b = self._txn([0x03])
        if b and b[0] == 0x03 and len(b) >= 6:
            digits = "".join("%02X" % x for x in reversed(b[1:6]))
            return int(digits)
        return None

    def read_mode(self):
        b = self._txn([0x04])
        if b and b[0] == 0x04:
            return MODES.get(b[1], "?")
        return None

    # --- запись состояния ---
    def set_frequency(self, hz):
        """Выставить рабочую частоту (Гц). Формат — 5 байт BCD, little-endian."""
        digits = "%010d" % int(hz)          # 10 десятичных цифр = 5 байт BCD
        bcd = bytes(int(digits[i:i + 2], 16) for i in range(8, -1, -2))
        b = self._txn([0x05] + list(bcd), wait=0.08)
        return b is not None

    def set_mode(self, name):
        """Выставить режим по имени ('FM', 'USB', ...)."""
        code = MODE_BY_NAME.get(name.upper())
        if code is None:
            return False
        b = self._txn([0x06, code], wait=0.08)
        return b is not None

    def read_band_stack(self, band, reg=1):
        """Band-stacking регистр (1A 01): (freq_hz, mode) последнего
        использования диапазона. Коды IC-705 (проверено опросом):
        1..10 = 160/80/40/30/20/17/15/12/10/6м, 11=WFM, 12=AIR, 13=2м, 14=70см."""
        def bcd(n):
            return ((n // 10) << 4) | (n % 10)
        b = self._txn([0x1A, 0x01, bcd(band), bcd(reg)], wait=0.4)
        if not b or len(b) < 10 or b[:2] != bytes([0x1A, 0x01]):
            return None
        digits = "".join("%02X" % x for x in reversed(b[4:9]))
        return int(digits), MODES.get(b[9], "FM")

    def read_squelch_open(self):
        """True = сквелч открыт (сигнал есть), False = закрыт, None = нет ответа."""
        b = self._txn([0x15, 0x01])
        if b and b[:2] == bytes([0x15, 0x01]) and len(b) >= 3:
            return b[2] == 1
        return None

    def read_smeter_raw(self):
        """Сырое значение S-метра 0..255, или None."""
        b = self._txn([0x15, 0x02])
        if b and b[:2] == bytes([0x15, 0x02]) and len(b) >= 4:
            return int("%02X%02X" % (b[2], b[3]))
        return None

    # --- PTT ---
    def set_ptt(self, tx: bool):
        # 1C 00 <00 RX | 01 TX>
        self._txn([0x1C, 0x00, 0x01 if tx else 0x00], wait=0.05)

    def read_ptt(self):
        b = self._txn([0x1C, 0x00])
        if b and b[:2] == bytes([0x1C, 0x00]) and len(b) >= 3:
            return b[2] == 1
        return None


def s_units(raw, cfg):
    """raw S-метра -> строка вида S7 / S9 / S9+20dB."""
    if raw is None or raw < 0:
        return "?"
    if raw <= cfg.s9_raw:
        return "S%d" % max(0, round(raw / cfg.per_s_unit))
    return "S9+%ddB" % round((raw - cfg.s9_raw) / 2.0)
