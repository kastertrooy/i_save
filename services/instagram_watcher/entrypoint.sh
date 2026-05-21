#!/bin/sh
set -eu

if [ "${MANUAL_BROWSER:-0}" = "1" ]; then
    export DISPLAY="${DISPLAY:-:99}"
    Xvfb "$DISPLAY" -screen 0 "${XVFB_SCREEN:-1280x820x24}" >/tmp/xvfb.log 2>&1 &
    fluxbox >/tmp/fluxbox.log 2>&1 &
    x11vnc -display "$DISPLAY" -forever -shared -nopw -listen 0.0.0.0 -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
    websockify --web=/usr/share/novnc/ "${NOVNC_PORT:-6080}" localhost:5900 >/tmp/novnc.log 2>&1 &
fi

exec python -m services.instagram_watcher.main
