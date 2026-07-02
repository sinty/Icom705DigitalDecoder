"""Конфигурация декодера/дашборда Icom705DigitalDecoder."""
from dataclasses import dataclass


@dataclass
class Config:
    # --- CI-V ---
    port: str = "auto"           # /dev/ttyACM0 или auto (поиск опросом)
    baud: int = 115200
    radio_addr: int = 0xA4       # CI-V адрес IC-705
    ctrl_addr: int = 0xE0

    # --- S-метр (калибровка Icom: raw 0->S0, ~120->S9) ---
    s9_raw: int = 120
    per_s_unit: float = 13.3

    # --- Аудио/тракт ---
    speaker_sink: str = "alsa_output.platform-3f00b840.mailbox.stereo-fallback"
    sql_udp_port: int = 7356     # управление сквелчем в if_demod.py
    dsd_log: str = "/home/sinty/dsd_live_iq.log"   # лог dsd-fme для статуса декодера
    dsd_events_log: str = "/home/sinty/dsd_live_iq_events.log"  # журнал звонков dsd-fme (-J)
    calls_db: str = "/home/sinty/calls.db"         # SQLite: история услышанных операторов

    tune_step_hz: int = 12500    # округление частоты при клике по скопу (0 = без округления)

    # --- Веб ---
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    scope_history: int = 600     # сколько развёрток водопада хранить
