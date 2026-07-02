#!/usr/bin/env python3
"""Оффлайн-вариант IF-демодулятора: wav (стерео/моно s16, 48к) -> wav (моно s16 48к).

Тот же тракт, что в if_demod.py (перенос частоты + ЧМ-детектор), но файл в файл
и с параметрами из CLI — для перебора центра/полосы на эталонной записи без
живого эфира:

  python3 if_demod_offline.py in.wav out.wav <center_hz> <half_bw_hz> <transition_hz>
"""
import math
import sys
import wave

import numpy as np
from gnuradio import gr, blocks, filter as gr_filter, analog

SAMPLE_RATE = 48000
MAX_DEVIATION_HZ = 1944.0
S16_SCALE = 10000.0


def wav_to_f32(path):
    w = wave.open(path, "rb")
    n, rate, ch = w.getnframes(), w.getframerate(), w.getnchannels()
    assert rate == SAMPLE_RATE, f"ожидался 48к wav, получен {rate}"
    data = np.frombuffer(w.readframes(n), dtype=np.int16)
    if ch == 2:
        data = data.reshape(-1, 2)[:, 0]
    return (data.astype(np.float32) / 32768.0)


def f32_raw_to_wav(raw_path, wav_path):
    y = np.fromfile(raw_path, dtype=np.int16)
    w = wave.open(wav_path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SAMPLE_RATE)
    w.writeframes(y.tobytes())
    w.close()


class Demod(gr.top_block):
    def __init__(self, in_f32, out_s16, center, half_bw, transition):
        gr.top_block.__init__(self, "IF Demod Offline")
        taps = gr_filter.firdes.low_pass(1.0, SAMPLE_RATE, half_bw, transition)
        src = blocks.file_source(gr.sizeof_float, in_f32, False)
        xlate = gr_filter.freq_xlating_fir_filter_fcf(1, taps, center, SAMPLE_RATE)
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

    x = wav_to_f32(in_wav)
    x.tofile("/tmp/demod_in.f32")

    tb = Demod("/tmp/demod_in.f32", "/tmp/demod_out.s16", center, half_bw, transition)
    tb.run()

    f32_raw_to_wav("/tmp/demod_out.s16", out_wav)
    print(f"OK center={center} bw={half_bw} trans={transition} -> {out_wav}")


if __name__ == "__main__":
    main()
