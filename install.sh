#!/bin/bash
# Установка Icom705DigitalDecoder на чистую Raspberry Pi OS Lite (Debian 12/13).
#
# Запуск под обычным пользователем (не root):
#   sudo apt-get install -y git
#   git clone https://github.com/sinty/Icom705DigitalDecoder.git
#   cd Icom705DigitalDecoder && bash install.sh
#
# Скрипт идемпотентен: повторный запуск обновляет файлы и перезапускает сервисы,
# готовые сборки (mbelib, dsd-fme) не пересобирает.
set -euo pipefail

USER_NAME=$(whoami)
if [ "$USER_NAME" = root ]; then
    echo "Запускать под обычным пользователем (sudo спросится где надо)"; exit 1
fi
HOME_DIR=$HOME
USER_UID=$(id -u)
SRC_DIR=$(cd "$(dirname "$0")" && pwd)

echo "== 1/7 apt-пакеты =="
sudo apt-get update
sudo apt-get install -y \
    pipewire pipewire-pulse wireplumber pipewire-audio-client-libraries pulseaudio-utils \
    git cmake build-essential pkg-config \
    libpulse-dev libasound2-dev libncurses-dev libusb-1.0-0-dev libsndfile1-dev \
    libfftw3-dev liblapack-dev libcodec2-dev socat \
    gnuradio python3-numpy python3-serial

echo "== 2/7 аудио-сессия пользователя =="
# linger: user-сервисы pipewire живут без интерактивного логина
sudo loginctl enable-linger "$USER_NAME"
systemctl --user start pipewire pipewire-pulse wireplumber 2>/dev/null || true
sudo usermod -aG dialout "$USER_NAME"   # доступ к CI-V (/dev/ttyACM*)
sleep 3

echo "== 3/7 сборка mbelib =="
if [ ! -f /usr/local/lib/libmbe.so ]; then
    [ -d "$HOME_DIR/mbelib" ] || git clone https://github.com/lwvmobile/mbelib "$HOME_DIR/mbelib"
    cd "$HOME_DIR/mbelib" && git checkout ambe_tones
    mkdir -p build && cd build
    cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS='-mcpu=cortex-a53' ..
    make -j"$(nproc)"
    sudo make install && sudo ldconfig
else
    echo "   уже установлен, пропускаю"
fi

echo "== 4/7 сборка dsd-fme =="
# ОБЯЗАТЕЛЬНО Release (-O3): сборка без оптимизации не успевает за реальным
# временем на Pi 3 (100% CPU, растущие очереди, артефакты звука)
if [ ! -x /usr/local/bin/dsd-fme ]; then
    [ -d "$HOME_DIR/dsd-fme" ] || git clone https://github.com/lwvmobile/dsd-fme "$HOME_DIR/dsd-fme"
    cd "$HOME_DIR/dsd-fme"
    mkdir -p build && cd build
    cmake -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_C_FLAGS='-mcpu=cortex-a53' -DCMAKE_CXX_FLAGS='-mcpu=cortex-a53' ..
    make -j"$(nproc)"
    sudo make install && sudo ldconfig
else
    echo "   уже установлен, пропускаю"
fi

echo "== 5/7 файлы проекта =="
cd "$SRC_DIR"
cp if_demod.py run_pipeline.sh web.py civ.py scope.py config.py "$HOME_DIR/"

# автодетект синка колонок (аналоговый джек Pi); имя зависит от модели,
# фолбэк — Pi 3. Если pipewire ещё не поднялся, детект просто не сработает.
SPEAKER=$(pactl list short sinks 2>/dev/null | awk '/alsa_output.platform/{print $2; exit}' || true)
SPEAKER=${SPEAKER:-alsa_output.platform-3f00b840.mailbox.stereo-fallback}
echo "   синк колонок: $SPEAKER"
sed -i "s|alsa_output.platform-3f00b840.mailbox.stereo-fallback|$SPEAKER|" "$HOME_DIR/config.py"
sed -i "s|/home/sinty|$HOME_DIR|g" "$HOME_DIR/config.py"

echo "== 6/7 systemd-юниты и logrotate =="
for u in icom-demod icom-dsd icom-web; do
    sed -e "s|User=sinty|User=$USER_NAME|" \
        -e "s|/home/sinty|$HOME_DIR|g" \
        -e "s|/run/user/1000|/run/user/$USER_UID|g" \
        -e "s|user@1000.service|user@$USER_UID.service|g" \
        -e "s|alsa_output.platform-3f00b840.mailbox.stereo-fallback|$SPEAKER|" \
        "deploy/$u.service" | sudo tee "/etc/systemd/system/$u.service" > /dev/null
done
sed "s|/home/sinty|$HOME_DIR|g" deploy/logrotate-icom | sudo tee /etc/logrotate.d/icom > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable icom-demod icom-dsd icom-web

echo "== 7/7 запуск =="
sudo systemctl restart icom-demod icom-dsd icom-web
sleep 6
systemctl is-active icom-demod icom-dsd icom-web

echo
echo "Готово. Дашборд: http://$(hostname -I | awk '{print $1}'):8080/"
echo "На IC-705 проверь: Connectors -> USB AF/IF Output -> AF/IF = IF, уровень 100%;"
echo "CI-V USB Baud Rate = Auto, CI-V Address = A4h."
