#!/bin/bash
set -u
IN_DEV="alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.analog-stereo"

# стерео f32: (L,R) = (I,Q) — if_demod.py читает поток как complex64
pw-record --target "$IN_DEV" -a --rate 48000 --channels 2 --format f32 - \
  | python3 "$HOME/if_demod.py"
