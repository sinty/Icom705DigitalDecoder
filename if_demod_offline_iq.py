#!/usr/bin/env python3
"""Оффлайн IQ-демодулятор: стерео wav (I=L, Q=R) -> моно wav 48к s16 для dsd-fme.

Правильная обработка IF-выхода IC-705: он квадратурный (I/Q по стерео-паре),
а не моно. Комплексный тракт полностью подавляет зеркальный канал:

  python3 if_demod_offline_iq.py in.wav out.wav <center_hz> <half_bw_hz> <transition_hz>
"""
import math
import sys
import wave

import numpy as np
from gnuradio import gr, blocks, filter as gr_filter, analog

SAMPLE_RATE = 48000
MAX_DEVIATION_HZ = 1944.0
S16_SCALE = 10000.0


def wav_to_iq(path):
    w = wave.open(path, "rb")
    n, rate, ch = w.getnframes(), w.getframerate(), w.getnchannels()
    assert rate == SAMPLE_RATE and ch == 2, f"нужен стерео 48к wav (I/Q), получено rate={rate} ch={ch}"
    data = np.frombuffer(w.readframes(n), dtype=np.int16).reshape(-1, 2)
    z = (data[:, 0].astype(np.float32) + 1j * data[:, 1].astype(np.float32)) / 32768.0
    return z.astype(np.complex64)


def s16_raw_to_wav(raw_path, wav_path):
    y = np.fromfile(raw_path, dtype=np.int16)
    w = wave.open(wav_path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SAMPLE_RATE)
    w.writeframes(y.tobytes())
    w.close()


class Demod(gr.top_block):
    def __init__(self, in_iq, out_s16, center, half_bw, transition):
        gr.top_block.__init__(self, "IF IQ Demod Offline")
        taps = gr_filter.firdes.low_pass(1.0, SAMPLE_RATE, half_bw, transition)
        src = blocks.file_source(gr.sizeof_gr_complex, in_iq, False)
        xlate = gr_filter.freq_xlating_fir_filter_ccf(1, taps, center, SAMPLE_RATE)
        demod = analog.quadrature_demod_cf(
            SAMPLE_RATE / (2 * math.pi * MAX_DEVIATION_HZ))
        to_s16 = blocks.float_to_short(1, S16_SCALE)
        sink = blocks.file_sink(gr.sizeof_short, out_s16, False)
        self.connect(src, xlate, demod, to_s16, sink)


def main():
    in_wav, out_wav = sys.argv[1], sys.argv[2]
    center = float(sys.argv[3])
    half_bw = float(sys.argv[4])
    transition = float(sys.argv[5])

    z = wav_to_iq(in_wav)
    z.tofile("/tmp/demod_in.iq")

    tb = Demod("/tmp/demod_in.iq", "/tmp/demod_out.s16", center, half_bw, transition)
    tb.run()

    s16_raw_to_wav("/tmp/demod_out.s16", out_wav)
    print(f"OK IQ center={center} bw={half_bw} trans={transition} -> {out_wav}")


if __name__ == "__main__":
    main()
