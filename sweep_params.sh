#!/bin/bash
# Перебор параметров демодулятора на эталонной IF-записи.
# Для каждой комбинации: демод -> dsd-fme (файл) -> метрики качества.
set -u
IN=~/if_master.wav
OUTDIR=~/sweep
mkdir -p "$OUTDIR"

echo "center bw trans VC FEC_ERR EMB_ERR"
for CENTER in 12050 12100 12140 12180 12222; do
  for BW in 3000 3500 4200 5000; do
    TRANS=$(( BW / 2 ))
    TAG="c${CENTER}_b${BW}"
    python3 ~/if_demod_offline.py "$IN" "$OUTDIR/demod_$TAG.wav" "$CENTER" "$BW" "$TRANS" > /dev/null 2>&1
    LOG="$OUTDIR/dsd_$TAG.log"
    dsd-fme -fs -xr -i "$OUTDIR/demod_$TAG.wav" -o null -w "$OUTDIR/voice_$TAG.wav" > "$LOG" 2>&1
    VC=$(grep -cE 'VC[0-9]' "$LOG")
    FEC=$(grep -c 'FEC ERR' "$LOG")
    EMB=$(grep -c 'VOICE CACH/EMB ERR' "$LOG")
    echo "$CENTER $BW $TRANS $VC $FEC $EMB"
  done
done
