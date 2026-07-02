#!/usr/bin/env python3
"""Разведка band-stacking регистров IC-705 (CI-V 1A 01): band -> частота/мода."""
from config import Config
from civ import CIV, MODES


def bcd(n):
    return ((n // 10) << 4) | (n % 10)


def main():
    civ = CIV(Config())
    try:
        for band in range(1, 15):
            body = civ._txn([0x1A, 0x01, bcd(band), 0x01], wait=0.4)
            if not body or len(body) < 10:
                print(f"band {band:2d}: нет ответа / короткий: {body.hex() if body else None}")
                continue
            # body: 1A 01 <band> <reg> <freq 5B BCD LE> <mode> <filter> ...
            freq_digits = "".join("%02X" % x for x in reversed(body[4:9]))
            mode = MODES.get(body[9], f"?{body[9]}")
            print(f"band {band:2d}: {int(freq_digits)/1e6:12.6f} MHz  {mode}   raw={body.hex()}")
    finally:
        civ.close()


if __name__ == "__main__":
    main()
