#!/bin/sh
set -e

export DISPLAY="${DISPLAY:-:99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1365}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-768}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

rm -f "/tmp/.X${DISPLAY#:}-lock" "/tmp/.X11-unix/X${DISPLAY#:}"

Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 &
sleep 2
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -nopw -listen 0.0.0.0 -xkb >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc/ 0.0.0.0:"$NOVNC_PORT" localhost:5900 >/tmp/novnc.log 2>&1 &

exec python -m services.browser_service.main
