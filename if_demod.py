#!/usr/bin/env python3
"""IC-705 IF (I/Q, 12.14 кГц) -> демодулированное аудио для dsd-fme, через GNU Radio.

IC-705 в режиме USB AF/IF Output = IF выдаёт по USB-аудио квадратурную пару:
I в левом канале, Q в правом (проверено: сдвиг фаз 90°, корреляция ~0).
pw-record льёт в stdin стерео f32 interleaved — байт-в-байт это complex64,
поэтому file_descriptor_source читает его как комплексный сигнал напрямую.

Тракт: комплексный перенос с +12140 Гц на 0 (freq_xlating_fir_filter_ccf,
полное подавление зеркального канала), ЧМ-детектор (quadrature_demod_cf),
s16le mono 48k как TCP-сервер на 7355 — родной формат TCP-входа dsd-fme.

Запуск (run_pipeline.sh):
  pw-record --target <IF-источник> -a --rate 48000 --channels 2 --format f32 - \\
    | python3 if_demod.py
  dsd-fme -fs -i tcp -o pulse:<колонки>       # БЕЗ -xr: полярность прямая

Почему TCP, а не pw-play в null-sink: два независимых аудио-клиента без общей
обратной связи расходятся по часам, поток рвётся, декодер теряет sync.
TCP-потребитель читает с той скоростью, с какой данные приходят.

Метрики на эталонной записи (if_master.wav): audio_err=101, EMB=3
против 3307/35 при моно-обработке левого канала.
"""
import math
import signal
import socket
import threading

from gnuradio import gr, blocks, filter as gr_filter, analog, network


SAMPLE_RATE = 48000
IF_CENTER_HZ = 12140.0        # центр несущей в комплексном спектре I+jQ (измерено)
CHANNEL_HALF_BW_HZ = 3500.0   # полоса под DMR 4FSK (внешний символ 1944 Гц + скаты)
TRANSITION_HZ = 1500.0
MAX_DEVIATION_HZ = 1944.0     # номинальная макс. девиация DMR: внешний символ -> ±1.0
S16_SCALE = 10000.0           # ±1.0 float -> ±10000 s16 (запас от клиппинга, dsd сам AGC-ит)
TCP_PORT = 7355               # дефолтный порт TCP-входа dsd-fme
SQUELCH_DB = -16.0            # порог сквелча по мощности в канале:
                              # измерено на эталоне: сигнал -10.5 дБ, шум -22 дБ
CTRL_UDP_PORT = 7356          # управление на лету: "SQL -18.5" / "SQL?" -> ответ "SQL <дБ>"


class IF2TCP(gr.top_block):
    def __init__(self):
        gr.top_block.__init__(self, "IC705 IF IQ Demod to TCP")

        taps = gr_filter.firdes.low_pass(
            1.0, SAMPLE_RATE, CHANNEL_HALF_BW_HZ, TRANSITION_HZ)

        # stdin: стерео f32 interleaved (L,R)=(I,Q) == complex64
        src = blocks.file_descriptor_source(gr.sizeof_gr_complex, 0, False)

        xlate = gr_filter.freq_xlating_fir_filter_ccf(
            1, taps, IF_CENTER_HZ, SAMPLE_RATE)

        # сквелч по мощности в канале: ниже порога -> нули (тишина),
        # gate=False сохраняет непрерывность потока по темпу
        self.squelch = analog.pwr_squelch_cc(SQUELCH_DB, 1e-3, 0, False)
        squelch = self.squelch

        demod_gain = SAMPLE_RATE / (2 * math.pi * MAX_DEVIATION_HZ)
        demod = analog.quadrature_demod_cf(demod_gain)

        to_s16 = blocks.float_to_short(1, S16_SCALE)

        # sinkmode=2 (server): ждём подключения dsd-fme
        sink = network.tcp_sink(gr.sizeof_short, 1, "0.0.0.0", TCP_PORT, 2)

        self.connect((src, 0), (xlate, 0))
        self.connect((xlate, 0), (squelch, 0))
        self.connect((squelch, 0), (demod, 0))
        self.connect((demod, 0), (to_s16, 0))
        self.connect((to_s16, 0), (sink, 0))


def ctrl_loop(tb):
    """UDP-канал управления: 'SQL <дБ>' ставит порог, 'SQL?' возвращает текущий."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", CTRL_UDP_PORT))
    while True:
        data, addr = sock.recvfrom(64)
        try:
            parts = data.decode("ascii", "ignore").strip().split()
            if parts and parts[0].upper() == "SQL":
                if len(parts) > 1 and parts[1] != "?":
                    tb.squelch.set_threshold(float(parts[1]))
                reply = f"SQL {tb.squelch.threshold():.1f}\n"
                sock.sendto(reply.encode("ascii"), addr)
        except Exception:
            pass


def main():
    tb = IF2TCP()
    tb.start()

    threading.Thread(target=ctrl_loop, args=(tb,), daemon=True).start()

    def stop(*_):
        tb.stop()
        tb.wait()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    tb.wait()


if __name__ == "__main__":
    main()
